"""Causal queue-tracked fill simulation of a small LIP farmer on Kalshi, on recorder books + trades.

Markets: every recorder market that had an active liquidity programme while recorded (recorder_lip_overlap.parquet).
Per (ticker, side in {yes,no}, placement in {best, improve, ref}, q contracts):
  * Every REPEG_S seconds we read the latest book row (ts_recv <= t, causal) and choose a price:
      best    = our side's best bid; improve = best + 1c if it stays below our side's ask; ref = LIP reference level.
    A new price takes effect at t + LAT_S; the old order stays fillable until then.
    Queue ahead = displayed size at our price at decision time (0 if improving / new level).
  * Queue: at-level prints first drain the queue ahead; later book rows cap the queue at the displayed size
    (cancels ahead of us). Through-prints fill us before anyone at worse prices.
  * Fill model fill_share (1.0 = we get every contract that trades through/at us after the queue).
  * After a fill the quote is topped up to q LAT_S later, at the back of the queue (no instant refill inside a sweep);
    inventory is held to settlement and capped at inv_cap*q per side (then that side stops quoting).
  * Reward: at every repeg step our LIP share is computed from the visible book (top 10 levels) with our order
    inserted (klip_lip.side_score); reward accrues pool_day * share / 2 per day for the step, if the visible book
    reaches Target on both sides OR the snapshot round says the market qualifies.
  * PnL per fill: settlement value if finalized, else mark at the last recorded mid (flag) - price - maker fee.
Outputs data/vendor/klip/analysis/sim_<tag>.parquet (one row per ticker/side/placement/q) and fills_<tag>.parquet.
"""
from __future__ import annotations

import argparse
import glob
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.recorder.writer import BOOK_SCHEMA, TRADE_SCHEMA  # noqa: E402
import klip_lip as L  # noqa: E402

KL = ROOT / "data" / "vendor" / "klip"
OUT = KL / "analysis"
REPEG_S = 60
LAT_S = 1.0


def load(tickers):
    bf = sorted(glob.glob(str(ROOT / "data/live_books/kalshi/2026-10-1[234]/books-*.parquet")))
    tf = sorted(glob.glob(str(ROOT / "data/live_books/kalshi/2026-10-1[234]/trades-*.parquet")))
    cols_b = ["market_id", "ts_recv_ns", "bid_px", "bid_sz", "ask_px", "ask_sz"]
    B = []
    for f in bf:
        t = pq.read_table(f, schema=BOOK_SCHEMA, columns=cols_b).to_pandas()
        t["market_id"] = t.market_id.astype(str)
        B.append(t[t.market_id.isin(tickers)])
    T = []
    for f in tf:
        t = pq.read_table(f, schema=TRADE_SCHEMA, columns=["market_id", "trade_id", "ts_exchange_ns", "ts_recv_ns", "price", "size", "taker_side"]).to_pandas()
        t["market_id"] = t.market_id.astype(str); t["taker_side"] = t.taker_side.astype(str)
        T.append(t[t.market_id.isin(tickers)])
    B = pd.concat(B, ignore_index=True).sort_values(["market_id", "ts_recv_ns"])
    T = pd.concat(T, ignore_index=True).drop_duplicates("trade_id").sort_values(["market_id", "ts_recv_ns"])
    return B, T


def side_book(row, side):
    """Our side's resting bids (own-price terms) from a recorder row in YES terms."""
    if side == "yes":
        return np.asarray(row.bid_px, float), np.asarray(row.bid_sz, float)
    return np.round(1 - np.asarray(row.ask_px, float), 4), np.asarray(row.ask_sz, float)


def choose(px, sz, opp_px, placement, tgt):
    if len(px) == 0:
        return None
    good = sz >= 1
    px, sz = px[good], sz[good]
    if len(px) == 0:
        return None
    best = round(float(px.max()), 2)
    ask = 1 - float(opp_px.max()) if len(opp_px) else 1.0
    if placement == "best":
        return best
    if placement == "improve":
        p = round(best + 0.01, 2)
        return p if p < ask - 1e-9 else best
    o = np.argsort(-px); cum = 0.0
    for i in o:
        cum += sz[i]
        if cum >= tgt / 5:
            return round(float(px[i]), 2)
    return round(float(px[o[-1]]), 2)


def _bucket(close_ns, t):
    h = (close_ns - t) / 3600e9
    for i, (a, b) in enumerate(BUCKETS):
        if a <= h < b:
            return i
    return 0


def maker_fee(p, mult):
    return 0.0175 * mult * p * (1 - p)


BUCKETS = [(0, 1), (1, 6), (6, 24), (24, 72), (72, 1e9)]  # hours to close


def sim_one(bk, tr, side, placement, q, prog, fee_mult, qual_snap, share_fill=1.0, inv_cap=2.0, close_ns=None):
    """bk: book rows of one ticker (sorted); tr: trades of one ticker (sorted). prog: (t0_ns, t1_ns, pool_day, tgt, df)."""
    t0, t1, pool_day, tgt, df = prog
    bt = bk.ts_recv_ns.to_numpy(); tt = tr.ts_recv_ns.to_numpy()
    if len(bt) == 0:
        return None, []
    start = max(t0, bt[0]); end = min(t1, max(bt[-1], tt[-1] if len(tt) else bt[-1]))
    if end - start < 10 * 60 * 1e9:
        return None, []
    tp = tr.price.to_numpy(); ts_ = tr["size"].to_numpy(); tside = tr.taker_side.to_numpy()
    # own-price of each print and whether it hits our side's bids
    if side == "yes":
        own = np.round(tp, 4); hits = tside == "no"
    else:
        own = np.round(1 - tp, 4); hits = tside == "yes"
    rows = list(bk.itertuples(index=False))
    fills = []; reward = 0.0; rew2 = 0.0; rew3 = 0.0; rew_pre = 0.0; cap_pre = 0.0; rb = [0.0] * len(BUCKETS); cb = [0.0] * len(BUCKETS); t_split = start + 0.25 * (end - start); rest_cd = 0.0; cap_rest = 0.0; steps = 0; qual_steps = 0
    price = None; pending = None; queue = 0.0; inv = 0.0; rem = q; refill_at = None; cap_c = inv_cap * q
    j_tr = np.searchsorted(tt, start, "left"); j_bk = 0
    t = start
    while t < end:
        t_next = min(t + REPEG_S * 1e9, end)
        # latest book at t (causal)
        k = np.searchsorted(bt, t, "right") - 1
        if k < 0:
            t = t_next; continue
        row = rows[k]
        px, sz = side_book(row, side)
        opx, osz = side_book(row, "no" if side == "yes" else "yes")
        new = choose(px, sz, opx, placement, tgt)
        # reward for this step with our order at `new` (or the current price)
        p_score = new if new is not None else price
        if inv >= cap_c:
            break
        if p_score is not None:
            okm = sz.sum() >= tgt and osz.sum() >= tgt
            ok_s, o, tot = L.side_score(px, sz, tgt, df, p_score, q, mode="level")
            visible_short = not okm
            qual = okm or (visible_short and qual_snap)
            dt_d = (t_next - t) / 86400e9
            if qual and tot > 0:
                reward += pool_day * (o / tot) / 2 * dt_d
                if close_ns is not None:
                    rb[_bucket(close_ns, t)] += pool_day * (o / tot) / 2 * dt_d
                if t < t_split:
                    rew_pre += pool_day * (o / tot) / 2 * dt_d
                qual_steps += 1
                _, o2, t2 = L.side_score(px, sz * 2, tgt, df, p_score, q, mode="level")
                _, o3, t3 = L.side_score(np.append(px, p_score), np.append(sz, 3 * q), tgt, df, p_score, q, mode="level")
                rew2 += pool_day * (o2 / t2 if t2 > 0 else 0) / 2 * dt_d
                rew3 += pool_day * (o3 / t3 if t3 > 0 else 0) / 2 * dt_d
            rest_cd += q * dt_d; cap_rest += q * p_score * dt_d; steps += 1
            if close_ns is not None:
                cb[_bucket(close_ns, t)] += q * p_score * dt_d
            if t < t_split:
                cap_pre += q * p_score * dt_d
        # schedule repeg
        t_eff = t + LAT_S * 1e9
        if new is not None and (price is None or abs(new - price) > 1e-9):
            pending = (t_eff, new, float(sz[np.abs(px - new) < 1e-9].sum()) if placement != "improve" or new <= px.max() + 1e-9 else 0.0)
        # walk trades and book rows until t_next
        j_end = np.searchsorted(tt, t_next, "left")
        for j in range(j_tr, j_end):
            if pending is not None and tt[j] >= pending[0]:
                price, queue = pending[1], pending[2]; pending = None; rem = q; refill_at = None
            if refill_at is not None and tt[j] >= refill_at:
                rem = q; refill_at = None
                queue = float(sz[np.abs(px - price) < 1e-9].sum()) if price is not None else 0.0
            if rem < 1:
                continue
            if price is None or not hits[j]:
                continue
            if own[j] < price - 1e-9:  # through our level
                f = min(rem, ts_[j] * share_fill)
            elif abs(own[j] - price) < 1e-9:
                use = min(queue, ts_[j]); queue -= use
                f = min(rem, (ts_[j] - use) * share_fill)
            else:
                continue
            f = math.floor(min(f, cap_c - inv))
            if f >= 1:
                inv += f
                fills.append((int(tt[j]), side, price, f))
                rem -= f  # top-up to q is posted after LAT_S, at the back of the queue
                if refill_at is None:
                    refill_at = tt[j] + LAT_S * 1e9
                if inv >= cap_c:
                    break
        if pending is not None and pending[0] < t_next:
            price, queue = pending[1], pending[2]; pending = None; rem = q; refill_at = None
        # cancels ahead: cap queue at displayed size from book rows inside the step
        k2 = np.searchsorted(bt, t_next, "right") - 1
        if price is not None and k2 >= 0:
            p2, s2 = side_book(rows[k2], side)
            disp = float(s2[np.abs(p2 - price) < 1e-9].sum())
            queue = min(queue, disp)
        j_tr = j_end; t = t_next
    out = dict(**{f'rew_b{i}': rb[i] for i in range(len(BUCKETS))}, **{f'cap_b{i}': cb[i] for i in range(len(BUCKETS))}, reward=reward, reward_pre=rew_pre, cap_pre=cap_pre, t_split=t_split, t_end=end, reward_comp2=rew2, reward_rival3=rew3, rest_contract_days=rest_cd, rest_capital_days=cap_rest, steps=steps,
               qual_frac=qual_steps / max(steps, 1), hours=(end - start) / 3600e9)
    return out, fills


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", type=int, nargs="+", default=[100, 500])
    ap.add_argument("--tag", default="base")
    ap.add_argument("--share", type=float, default=1.0)
    ap.add_argument("--max-tickers", type=int, default=0)
    ap.add_argument("--inv-cap", type=float, default=2.0)
    a = ap.parse_args()
    ov = pd.read_parquet(KL / "recorder_lip_overlap.parquet")
    res = pd.read_parquet(sorted(glob.glob(str(KL / "results_*.parquet")))[-1]).set_index("ticker")
    ser = pd.read_parquet(ROOT / "data/vendor/kalshi_research/series.parquet").set_index("series_ticker")
    snaps = sorted(glob.glob(str(OUT / "shares_*.parquet")))
    qual = pd.concat([pd.read_parquet(f, columns=["ticker", "qualifies"]) for f in snaps]).groupby("ticker").qualifies.mean() if snaps else pd.Series(dtype=float)
    tick = sorted(ov.market_ticker.unique())
    if a.max_tickers:
        tick = tick[: a.max_tickers]
    B, T = load(set(tick))
    print("loaded", len(B), "book rows", len(T), "trades", flush=True)
    bg = dict(tuple(B.groupby("market_id"))); tg = dict(tuple(T.groupby("market_id")))
    rows = []; allf = []
    for tk in tick:
        bk = bg.get(tk); tr = tg.get(tk, T.iloc[:0])
        if bk is None:
            continue
        s = tk.split("-")[0]
        ft = ser.fee_type.get(s, "quadratic_with_maker_fees"); fm = float(ser.fee_multiplier.get(s, 1.0))
        mf = fm if ft == "quadratic_with_maker_fees" else 0.0
        r = res.loc[tk] if tk in res.index else None
        settled = r is not None and r["status"] == "finalized" and r["result"] in ("yes", "no")
        last = bk.iloc[-1]
        bb = last.bid_px[0] if len(last.bid_px) else np.nan; ba = last.ask_px[0] if len(last.ask_px) else np.nan
        mid_last = np.nanmean([bb, ba])
        yes_val = (1.0 if r["result"] == "yes" else 0.0) if settled else mid_last
        close_ns = pd.Timestamp(r["close_time"]).value if r is not None and r["close_time"] else None
        for pr in ov[ov.market_ticker == tk].itertuples():
            prog = (pr.start_date.value, pr.end_date.value, pr.usd / max(pr.dur_d, 1 / 96), float(pr.target_size_fp), pr.discount_factor_bps / 1e4)
            for side in ("yes", "no"):
                val = yes_val if side == "yes" else 1 - yes_val
                for placement in ("best", "improve", "ref"):
                    for q in a.q:
                        o, fills = sim_one(bk, tr, side, placement, q, prog, mf, bool(qual.get(tk, 0) >= 0.5), a.share, a.inv_cap, close_ns)
                        if o is None:
                            continue
                        fq = sum(f[3] for f in fills)
                        pnl = sum(f[3] * (val - f[2] - maker_fee(f[2], mf)) for f in fills) if np.isfinite(val) else np.nan
                        fees = sum(f[3] * maker_fee(f[2], mf) for f in fills)
                        notional = sum(f[3] * f[2] for f in fills)
                        rows.append(dict(ticker=tk, series=s, side=side, placement=placement, q=q, settled=settled,
                                         fill_contracts=fq, fill_notional=notional, fees=fees, fill_pnl=pnl, **o))
                        for f in fills:
                            allf.append(dict(ticker=tk, side=side, placement=placement, q=q, ts=f[0], price=f[2], qty=f[3], val=val, settled=settled))
        print(tk, len(rows), flush=True) if len(rows) % 300 == 0 else None
    df = pd.DataFrame(rows)
    df.to_parquet(OUT / f"sim_{a.tag}.parquet", index=False)
    pd.DataFrame(allf).to_parquet(OUT / f"fills_{a.tag}.parquet", index=False)
    print(df.groupby(["placement", "q"])[["reward", "fill_pnl", "fees", "rest_capital_days", "fill_notional"]].sum())


if __name__ == "__main__":
    main()

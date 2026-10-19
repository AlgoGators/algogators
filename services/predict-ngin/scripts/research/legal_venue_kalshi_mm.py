"""Kalshi market-making simulation on Probalytics top-3 books + the Kalshi trade tape (legal-venue test).

Inputs
  data/vendor/probalytics/books{,_inplay}/kalshi/{ticker}.parquet   ts, b1..b3/bs1..bs3, a1..a3/as1..as3 (YES terms)
  data/vendor/probalytics/fills{,_inplay}/kalshi/{ticker}.parquet   ts, outcome(yes/no), nprice (YES price), size
  data/vendor/probalytics/analysis/legal_venue_kalshi_{series,markets}.parquet (scripts/research/legal_venue_kalshi_meta.py)

Model (deliberately conservative on queue position, generous elsewhere)
  * We post a YES bid and/or a YES ask (= NO bid) of `quote_usd` collateral at the touch, at the BACK of the queue.
    Queue ahead = displayed size at our price when we join. It shrinks only by (a) trades printed AT our price and
    (b) the displayed size falling below it (cancels ahead of us). Adds always go behind us.
  * Fill only when a trade prints THROUGH our price (fill = min(our size, print size): the sweeping taker would
    have hit us first) or the queue ahead is exhausted by trades at our price.
  * Book updates lead the matching trade print by ~1 ms in this data (measured), so trade timestamps are shifted
    50 ms earlier: the trade is processed before the book update it caused (no double-counting the queue and no
    'cancel just before the sweep' look-ahead).
  * When the touch price changes we cancel and re-join the new touch at the back (lose priority).
  * Inventory cap: |position collateral| <= inv_mult * quote_usd; the side that would add stops quoting.
  * Inventory left at the end of the window is held to settlement (result) or, for unresolved markets, marked at
    the last mid. A 'mid-mark' PnL (inventory marked at the last pre-game/in-window mid) is reported too.
  * Maker fee = fee_multiplier * 0.0175 * C * p(1-p) on series with fee_type 'quadratic_with_maker_fees'
    (exact, i.e. generous; per-fill cent rounding reported as a sensitivity).
Modes: 0 touch both sides; 1 FLB (bid only when mid>=0.5, ask only when mid<0.5: always long the favourite);
       2 improve (one tick inside when spread >= 3c, queue ahead 0, else touch).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numba as nb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "vendor" / "probalytics"
A = D / "analysis"
NS = 1_000_000_000
SHIFT_NS = 50_000_000
HORIZ = {"1m": 60 * NS, "5m": 300 * NS, "1h": 3600 * NS}
MAKER_COEF = 0.0175
TAKER_COEF = 0.07


def family(ticker: str) -> str:
    s = ticker.split("-")[0]
    if s in ("KXBUNDESLIGAGAME", "KXEPLGAME", "KXLALIGAGAME", "KXMLSGAME", "KXUCLGAME"):
        return "game_soccer"
    if s.endswith("GAME"):
        return "game_" + s[2:-4].lower()
    if s == "KXFEDDECISION":
        return "fed"
    return "futures"


@nb.njit
def _lvl_size(px3, sz3, p):
    for k in range(3):
        if abs(px3[k] - p) < 1e-6 and sz3[k] > 0:
            return sz3[k]
    return 0.0


@nb.njit
def simulate(T, BP, BS, AP, AS, tt, tp, tsz, tdir, quote_usd, mode, inv_mult, mid_lo, mid_hi, fee_coef, lat_ns=0, snipe=False):
    """Event-driven maker simulation. Returns fills (t, side, px, qty) arrays, n_fills, and diagnostics.
    side +1 = our bid filled (we bought YES), -1 = our ask filled (we sold YES).
    Capital-time integral (resting collateral + |position| collateral) is accumulated in usd*seconds.
    lat_ns: our order-entry/cancel latency. A quote decision taken at book time t takes effect at t+lat; until then
    the old quote stays live and can be filled (stale-quote sniping); a new quote joins the queue at t+lat.
    snipe: adversarial counterfactual. While a cancel/replace of our quote is in flight and the new book mid is on
    the wrong side of our stale price (bid > mid or ask < mid), a sniper takes our whole remaining size at that book
    update. (The historical tape cannot show these trades: nobody's stale quote was there to snipe.)"""
    n = T.shape[0]
    m = tt.shape[0]
    cap = 2 * m + n + 16
    f_t = np.zeros(cap, np.int64)
    f_side = np.zeros(cap, np.int8)
    f_px = np.zeros(cap)
    f_q = np.zeros(cap)
    nf = 0
    bid_on = False
    ask_on = False
    bid_px = 0.0
    ask_px = 0.0
    bid_rem = 0.0
    ask_rem = 0.0
    bid_qa = 0.0
    ask_qa = 0.0
    pos = 0.0
    inv_cap = inv_mult * quote_usd
    i = 0
    j = 0
    cur = -1  # index of current book row
    last_t = T[0] if n > 0 else 0
    cap_time = 0.0
    quote_time = 0.0
    peak_cap = 0.0
    pb = False
    pb_t = 0
    pb_on = False
    pb_px = 0.0
    pa = False
    pa_t = 0
    pa_on = False
    pa_px = 0.0
    while i < n or j < m:
        take_trade = j < m and (i >= n or tt[j] <= T[i])
        t_ev = tt[j] if take_trade else T[i]
        # pending order actions that have reached the exchange
        if pb and pb_t <= t_ev and cur >= 0:
            pb = False
            bid_on = pb_on
            if pb_on:
                bid_px = pb_px
                bid_rem = quote_usd / pb_px
                bid_qa = _lvl_size(BP[cur], BS[cur], pb_px)
        if pa and pa_t <= t_ev and cur >= 0:
            pa = False
            ask_on = pa_on
            if pa_on:
                ask_px = pa_px
                ask_rem = quote_usd / (1.0 - pa_px)
                ask_qa = _lvl_size(AP[cur], AS[cur], pa_px)
        # capital integral over [last_t, t_ev)
        if cur >= 0 and t_ev > last_t:
            mid = 0.5 * (BP[cur, 0] + AP[cur, 0])
            c = abs(pos) * (mid if pos > 0 else 1.0 - mid)
            if bid_on and bid_rem > 0:
                c += bid_rem * bid_px
            if ask_on and ask_rem > 0:
                c += ask_rem * (1.0 - ask_px)
            dt = (t_ev - last_t) / 1e9
            cap_time += c * dt
            if (bid_on and bid_rem > 0) or (ask_on and ask_rem > 0):
                quote_time += dt
            if c > peak_cap:
                peak_cap = c
        last_t = t_ev
        if take_trade:
            p = tp[j]
            s = tsz[j]
            if cur >= 0:
                if tdir[j] < 0 and bid_on and bid_rem > 0:
                    q = 0.0
                    if p < bid_px - 1e-6:
                        q = min(bid_rem, s)
                    elif abs(p - bid_px) < 1e-6:
                        avail = s - bid_qa
                        bid_qa = max(0.0, bid_qa - s)
                        if avail > 0:
                            q = min(avail, bid_rem)
                    if q > 0:
                        f_t[nf] = tt[j] + SHIFT_NS
                        f_side[nf] = 1
                        f_px[nf] = bid_px
                        f_q[nf] = q
                        nf += 1
                        bid_rem -= q
                        pos += q
                elif tdir[j] > 0 and ask_on and ask_rem > 0:
                    q = 0.0
                    if p > ask_px + 1e-6:
                        q = min(ask_rem, s)
                    elif abs(p - ask_px) < 1e-6:
                        avail = s - ask_qa
                        ask_qa = max(0.0, ask_qa - s)
                        if avail > 0:
                            q = min(avail, ask_rem)
                    if q > 0:
                        f_t[nf] = tt[j] + SHIFT_NS
                        f_side[nf] = -1
                        f_px[nf] = ask_px
                        f_q[nf] = q
                        nf += 1
                        ask_rem -= q
                        pos -= q
            j += 1
            continue
        # ---- book row i
        cur = i
        b1 = BP[i, 0]
        a1 = AP[i, 0]
        i += 1
        ok = b1 > 0 and a1 > 0 and a1 < 1.0 and a1 > b1 + 1e-6
        mid = 0.5 * (b1 + a1)
        if ok and (mid < mid_lo or mid > mid_hi):
            ok = False
        want_bid = ok
        want_ask = ok
        if mode == 1:
            want_bid = ok and mid >= 0.5
            want_ask = ok and mid < 0.5
        # inventory caps (collateral terms)
        if pos > 0 and pos * b1 >= inv_cap:
            want_bid = False
        if pos < 0 and (-pos) * (1.0 - a1) >= inv_cap:
            want_ask = False
        bpx = b1
        apx = a1
        if mode == 2 and a1 - b1 >= 0.03 - 1e-6:
            bpx = b1 + 0.01
            apx = a1 - 0.01
        tnow = T[i - 1]
        # bid side
        live = bid_on and bid_rem > 0
        if want_bid:
            if live and abs(bid_px - bpx) < 1e-6:
                pb = False
                lv = _lvl_size(BP[i - 1], BS[i - 1], bid_px)
                if lv < bid_qa:
                    bid_qa = lv
            elif not (pb and pb_on and abs(pb_px - bpx) < 1e-6):
                pb = True
                pb_t = tnow + lat_ns
                pb_on = True
                pb_px = bpx
        else:
            if bid_on:
                if not (pb and not pb_on):
                    pb = True
                    pb_t = tnow + lat_ns
                    pb_on = False
            else:
                pb = False
        if pb and pb_t <= tnow:
            pb = False
            bid_on = pb_on
            if pb_on:
                bid_px = pb_px
                bid_rem = quote_usd / pb_px
                bid_qa = _lvl_size(BP[i - 1], BS[i - 1], pb_px)
        # ask side
        live = ask_on and ask_rem > 0
        if want_ask:
            if live and abs(ask_px - apx) < 1e-6:
                pa = False
                lv = _lvl_size(AP[i - 1], AS[i - 1], ask_px)
                if lv < ask_qa:
                    ask_qa = lv
            elif not (pa and pa_on and abs(pa_px - apx) < 1e-6):
                pa = True
                pa_t = tnow + lat_ns
                pa_on = True
                pa_px = apx
        else:
            if ask_on:
                if not (pa and not pa_on):
                    pa = True
                    pa_t = tnow + lat_ns
                    pa_on = False
            else:
                pa = False
        if pa and pa_t <= tnow:
            pa = False
            ask_on = pa_on
            if pa_on:
                ask_px = pa_px
                ask_rem = quote_usd / (1.0 - pa_px)
                ask_qa = _lvl_size(AP[i - 1], AS[i - 1], pa_px)
        if snipe and ok:
            if pb and bid_on and bid_rem > 0 and bid_px > mid + 1e-9 and nf < cap:
                f_t[nf] = tnow
                f_side[nf] = 1
                f_px[nf] = bid_px
                f_q[nf] = bid_rem
                nf += 1
                pos += bid_rem
                bid_rem = 0.0
            if pa and ask_on and ask_rem > 0 and ask_px < mid - 1e-9 and nf < cap:
                f_t[nf] = tnow
                f_side[nf] = -1
                f_px[nf] = ask_px
                f_q[nf] = ask_rem
                nf += 1
                pos -= ask_rem
                ask_rem = 0.0
    return f_t[:nf], f_side[:nf], f_px[:nf], f_q[:nf], pos, cap_time, quote_time, peak_cap


def load_market(sub_b: str, sub_f: str, ticker: str):
    fb = D / sub_b / "kalshi" / f"{ticker}.parquet"
    ff = D / sub_f / "kalshi" / f"{ticker}.parquet"
    if not fb.exists() or not ff.exists():
        return None
    b = pd.read_parquet(fb)
    f = pd.read_parquet(ff)
    if len(b) < 10:
        return None
    T = b.ts.values.astype("datetime64[ns]").astype("int64")
    BP = b[["b1", "b2", "b3"]].to_numpy(np.float64).round(4)
    BS = b[["bs1", "bs2", "bs3"]].to_numpy(np.float64)
    AP = b[["a1", "a2", "a3"]].to_numpy(np.float64).round(4)
    AS = b[["as1", "as2", "as3"]].to_numpy(np.float64)
    f = f[(f.ts >= b.ts.iloc[0]) & (f.ts <= b.ts.iloc[-1])].sort_values("ts")
    tt = f.ts.values.astype("datetime64[ns]").astype("int64") - SHIFT_NS
    tp = f.nprice.to_numpy(np.float64).round(4)
    tsz = f["size"].to_numpy(np.float64)
    tdir = np.where(f.outcome.values == "yes", 1, -1).astype(np.int8)
    o = np.argsort(tt, kind="stable")
    return T, BP, BS, AP, AS, tt[o], tp[o], tsz[o], tdir[o], float(f["size"].mul(f.nprice).sum())


def mid_asof(T, BP, AP, t):
    k = np.searchsorted(T, t, "right") - 1
    k = np.clip(k, 0, len(T) - 1)
    b, a = BP[k, 0], AP[k, 0]
    m = np.where((b > 0) & (a > 0) & (a < 1), 0.5 * (b + a), np.nan)
    return np.where(t <= T[-1], m, np.nan)


def fee_fn(p, q, coef, ceil_cents=False):
    f = coef * q * p * (1 - p)
    if ceil_cents:
        return np.ceil(f * 100 - 1e-9) / 100
    return f


CONFIGS = [(q, mode, lat, sn) for mode in (0, 1, 2) for q in (25.0, 100.0, 250.0)
           for lat, sn in ((0, False), (250, False), (1000, False), (250, True), (1000, True))]
MODE_NAME = {0: "touch", 1: "flb", 2: "improve"}


def run(regime: str, inv_mult: float, limit: int | None = None):
    sub_b, sub_f = ("books", "fills") if regime == "pregame" else ("books_inplay", "fills_inplay")
    ser = pd.read_parquet(A / "legal_venue_kalshi_series.parquet").set_index("series")
    mk = pd.read_parquet(A / "legal_venue_kalshi_markets.parquet").set_index("ticker")
    tickers = sorted(f.stem for f in (D / sub_b / "kalshi").glob("*.parquet"))
    if limit:
        tickers = tickers[:limit]
    mrows, frows = [], []
    t0 = time.time()
    for n_, tk in enumerate(tickers):
        x = load_market(sub_b, sub_f, tk)
        if x is None:
            continue
        T, BP, BS, AP, AS, tt, tp, tsz, tdir, tape_usd = x
        s = tk.split("-")[0]
        fee_type = ser.loc[s, "fee_type"] if s in ser.index else "quadratic_with_maker_fees"
        mult = float(ser.loc[s, "fee_multiplier"]) if s in ser.index else 1.0
        coef = MAKER_COEF * mult if fee_type == "quadratic_with_maker_fees" else 0.0
        info = mk.loc[tk] if tk in mk.index else None
        res = None if info is None else info["result"]
        mids_all = np.where(okm_ := (BP[:, 0] > 0) & (AP[:, 0] > 0) & (AP[:, 0] < 1), 0.5 * (BP[:, 0] + AP[:, 0]), np.nan)
        last_mid = float(mids_all[okm_][-1]) if okm_.any() else np.nan
        if res == "yes":
            settle = 1.0
        elif res == "no":
            settle = 0.0
        elif res == "scalar" and info is not None and info["settlement"] is not None:
            settle = float(info["settlement"])
        else:
            settle = last_mid
        resolved = res in ("yes", "no", "scalar")
        hours = (T[-1] - T[0]) / 3.6e12
        b1, a1 = BP[:, 0], AP[:, 0]
        okm = (b1 > 0) & (a1 > 0) & (a1 < 1) & (a1 > b1)
        dtw = np.diff(T, append=T[-1]).astype(float)
        spread_tw = float(np.sum(((a1 - b1) * dtw)[okm]) / max(dtw[okm].sum(), 1))
        for q_usd, mode, lat, sn in CONFIGS:
            ft, fs, fp, fq, pos, capt, qt, peak = simulate(T, BP, BS, AP, AS, tt, tp, tsz, tdir, q_usd, mode,
                                                           inv_mult, 0.03, 0.97, coef, lat * 1_000_000, sn)
            cash = float(np.sum(-fs * fp * fq))
            fee = float(np.sum(fee_fn(fp, fq, coef)))
            fee_ceil = float(np.sum(fee_fn(fp, fq, coef, True)))
            pnl_settle = cash + pos * settle - fee
            pnl_mid = cash + pos * (last_mid if np.isfinite(last_mid) else settle) - fee
            mrows.append(dict(regime=regime, ticker=tk, event=tk.rsplit("-", 1)[0], family=family(tk), series=s,
                              maker_fee=coef > 0, fee_mult=mult, resolved=resolved, settle=settle, last_mid=last_mid,
                              hours=hours, spread_tw=spread_tw, tape_usd=tape_usd, quote_usd=q_usd,
                              mode=MODE_NAME[mode], lat_ms=lat, snipe=sn, n_fills=len(fq), filled_usd=float(np.sum(fp * fq)),
                              filled_contracts=float(fq.sum()), fee=fee, fee_ceil=fee_ceil, end_pos=pos,
                              pnl_settle=pnl_settle, pnl_mid=pnl_mid, pnl_settle_ceil=pnl_settle - fee_ceil + fee,
                              cap_usd_s=capt, quote_s=qt, peak_cap=peak, t_start=int(T[0]), t_end=int(T[-1])))
            if len(fq) and q_usd == 100.0 and (lat == 0 or sn):
                mid0 = mid_asof(T, BP, AP, ft - SHIFT_NS - 1)  # book state just before the fill (book leads print ~1ms)
                rec = dict(regime=regime, ticker=tk, family=family(tk), mode=MODE_NAME[mode], lat_ms=lat, snipe=sn, t=ft, side=fs, px=fp,
                           q=fq, fee=fee_fn(fp, fq, coef), mid0=mid0, settle=np.full(len(fq), settle),
                           resolved=np.full(len(fq), resolved))
                for h, dh in HORIZ.items():
                    rec["mid_" + h] = mid_asof(T, BP, AP, ft + dh)
                frows.append(pd.DataFrame(rec))
        if n_ % 50 == 0:
            print(f"{regime} {n_}/{len(tickers)} {tk} {time.time() - t0:.0f}s", flush=True)
    M = pd.DataFrame(mrows)
    F = pd.concat(frows, ignore_index=True) if frows else pd.DataFrame()
    return M, F


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inv-mult", type=float, default=4.0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--regimes", nargs="+", default=["pregame", "inplay"])
    a = ap.parse_args()
    Ms, Fs = [], []
    for r in a.regimes:
        M, F = run(r, a.inv_mult, a.limit)
        Ms.append(M)
        Fs.append(F)
    M = pd.concat(Ms, ignore_index=True)
    F = pd.concat(Fs, ignore_index=True)
    sfx = "" if a.inv_mult == 4.0 else f"_inv{a.inv_mult:g}"
    M.to_parquet(A / f"legal_venue_mm_markets{sfx}.parquet")
    F.to_parquet(A / f"legal_venue_mm_fills{sfx}.parquet")
    print(len(M), len(F))


if __name__ == "__main__":
    main()

"""wx: observation-convergence. How fast does Kalshi price out a bracket once public METARs make it impossible?

Dead bracket (high): hi <= round(R) - 1 - margin, R = running METAR max (T-groups + 6h groups), info time =
METAR valid + 5 min (+60 s decision latency).  Lows: lo >= round(R_min) + 1 + margin.
tau = first time the bracket becomes dead.  For each dead market:
  * last YES print price before tau (did the market already know?)
  * prints after tau+60s where YES bid was hit (taker NO) or lifted at y >= 2c: count, contracts, price
  * realised outcome (win must be 0; any win = METAR/CLI mismatch loss)
Taker strategy (c): sell YES (buy NO at 1-y) on every NO-taker print after tau at y >= y_min, fill share s of
the print (the bid was there; we race the other seller). PnL per contract = y - fee - win.
Tape (2024-11 -> 2026-02) and fin_E 1-min candles (2026-08-15 -> 10-12, bid at tau + k minutes).
"""
from __future__ import annotations
import glob
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, ROOT, SERIES, TEST_START  # noqa: E402


def fee(q, y):
    return np.ceil(np.round(0.07 * q * y * (1 - y) * 100, 6)) / 100


def dead_times(mk, margin):
    """mk: code, measure, date, lo, hi, ticker. Returns tau (first obs ts_avail making it dead) per ticker."""
    o = pd.read_parquet(OUT / "obs.parquet")[["code", "date", "ts_avail", "run_max", "run_min"]]
    out = []
    for meas in ("high", "low"):
        m = mk[mk.measure == meas]
        j = m.merge(o, on=["code", "date"])
        if meas == "high":
            ok = j.hi <= np.floor(j.run_max + 0.5) - 1 - margin
        else:
            ok = j.lo >= np.floor(j.run_min + 0.5) + 1 + margin
        out.append(j[ok].groupby("ticker").ts_avail.min())
    return pd.concat(out).rename("tau")


def tape(margin):
    p = pd.read_parquet(OUT / "panel.parquet", columns=["ts", "ticker", "event", "code", "measure", "date", "y", "tyes",
                                                         "qty", "lo", "hi", "win", "close_ts", "settle_ts"])
    mk = p.drop_duplicates("ticker")[["ticker", "code", "measure", "date", "lo", "hi", "win"]]
    tau = dead_times(mk, margin)
    mk = mk.merge(tau, left_on="ticker", right_index=True)
    print(f"== tape, margin {margin}: dead markets {len(mk)}, of which won (mismatch losses): {int(mk.win.sum())}")
    p = p.merge(mk[["ticker", "tau"]], on="ticker")
    pre = p[p.ts < p.tau].sort_values("ts").groupby("ticker").tail(1)
    print("  last YES print before tau: share <=2c", round((pre.y <= .02).mean(), 3), " <=5c", round((pre.y <= .05).mean(), 3),
          " median", pre.y.median(), " (n with a pre-tau print", len(pre), ")")
    post = p[p.ts >= p.tau + 60]
    hit = post[~post.tyes & (post.y >= 0.02)]
    print("  after tau+60s: NO-taker prints at y>=2c:", len(hit), "contracts", int(hit.qty.sum()),
          "markets", hit.ticker.nunique(), "mean y", round(hit.y.mean(), 3))
    lifted = post[post.tyes & (post.y >= 0.02)]
    print("  after tau+60s: YES-taker (someone BOUGHT dead YES) at y>=2c:", len(lifted), "contracts", int(lifted.qty.sum()))
    lag = (hit.groupby("ticker").ts.max() - mk.set_index("ticker").tau).dropna() / 60
    print("  minutes from tau to last >=2c bid-hit print: median", round(lag.median(), 1), "p90", round(lag.quantile(.9), 1))
    # strategy (c) taker on bid-hit prints, share 50% / 25%
    res = []
    for ymin in (0.02, 0.04, 0.08):
        for share in (0.5, 0.25):
            h = post[~post.tyes & (post.y >= ymin)].copy()
            h["q"] = np.floor(h.qty * share)
            h = h[h.q >= 1]
            h["pnl"] = h.q * (h.y - h.win) - fee(h.q, h.y)
            for split, g in (("train", h[h.date < TEST_START]), ("test", h[h.date >= TEST_START])):
                res.append(dict(ymin=ymin, share=share, split=split, fills=len(g), contracts=g.q.sum(),
                                pnl=g.pnl.sum(), c_per=100 * g.pnl.sum() / max(g.q.sum(), 1),
                                notional=(g.q * (1 - g.y)).sum(), losses=int((g.win == 1).sum()),
                                events=g.ticker.nunique()))
    r = pd.DataFrame(res)
    days = {"train": (pd.Timestamp(TEST_START) - pd.Timestamp("2024-11-01")).days,
            "test": (pd.Timestamp("2026-02-11") - pd.Timestamp(TEST_START)).days}
    r["pnl_per_day"] = r.pnl / r.split.map(days)
    print(r.round(2).to_string(index=False))
    return mk, p


def candles(margin):
    f = pd.read_parquet(ROOT / "data" / "vendor" / "fin_E" / "kalshi_weather" / "markets.parquet")
    f = f[f.series.isin(SERIES)].copy()
    f["code"] = f.series.map(lambda s: SERIES[s][0]); f["measure"] = f.series.map(lambda s: SERIES[s][1])
    f["date"] = pd.to_datetime(f.event.str.split("-").str[1], format="%y%b%d").dt.date.astype(str)
    f["lo"] = np.where(f.strike_type == "between", f["floor"], np.where(f.strike_type == "greater", f["floor"] + 1, -np.inf))
    f["hi"] = np.where(f.strike_type == "between", f["cap"], np.where(f.strike_type == "less", f["cap"] - 1, np.inf))
    f["win"] = f.result.eq("yes").astype(int)
    tau = dead_times(f[["ticker", "code", "measure", "date", "lo", "hi"]], margin)
    f = f.merge(tau, left_on="ticker", right_index=True)
    rows = []
    for ev, g in f.groupby("event"):
        pth = ROOT / "data" / "vendor" / "fin_E" / "kalshi_weather" / "candles" / g.series.iloc[0] / f"{ev}.parquet"
        if not pth.exists():
            continue
        c = pd.read_parquet(pth)
        for col in ("bid_close", "ask_close", "volume"):
            c[col] = pd.to_numeric(c[col], errors="coerce")
        for r in g.itertuples():
            cc = c[c.ticker == r.ticker].sort_values("ts")
            if cc.empty:
                continue
            row = dict(ticker=r.ticker, win=r.win, tau=r.tau, date=r.date)
            for k in (-30, -5, 0, 1, 5, 15, 30, 60):
                # candle ts = minute END; state at tau + k min = last candle closing <= that time
                x = cc[cc.ts <= r.tau + 60 * k]
                row[f"bid_{k}"] = x.bid_close.iloc[-1] if len(x) else np.nan
            post = cc[cc.ts > r.tau + 60]
            row["vol_post"] = post.volume.sum()
            row["vol_post_bid_ge2"] = post[post.bid_close >= .02].volume.sum()
            rows.append(row)
    d = pd.DataFrame(rows)
    print(f"== candles 2026-08-15..10-12 (TWC era), margin {margin}: dead markets {len(d)}, won {int(d.win.sum())}")
    for k in (-30, -5, 0, 1, 5, 15, 30, 60):
        b = d[f"bid_{k}"]
        print(f"  tau{k:+d}m: YES bid median {b.median():.3f}  share bid>=2c {(b >= .02).mean():.3f}  >=5c {(b >= .05).mean():.3f}"
              f"  mean bid {b.mean():.4f}")
    # taker: hit YES bid at tau+1 min, size limited by post-tau volume share (no depth in candles)
    b = d["bid_1"].fillna(0)
    edge = b - fee(1, b) - d.win
    sel = b >= 0.02
    print("  taker at tau+1m (1 lot each, bid>=2c): n", int(sel.sum()), "mean c/contract", round(100 * edge[sel].mean(), 2),
          "losses", int(d.win[sel].sum()))
    d.to_parquet(OUT / f"converge_candles_m{margin}.parquet", index=False)
    return d


if __name__ == "__main__":
    for mg in (1, 0):
        if len(sys.argv) < 2 or sys.argv[1] != "candles":
            tape(mg)
        candles(mg)

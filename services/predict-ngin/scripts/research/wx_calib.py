"""wx: (1) market vs model calibration at fixed decision times, (2) time-varying favourite-longshot bias,
(3) YES/NO side normalisation spot check.  Uses data/vendor/wx/panel.parquet (causal features).

Market probability at decision time T = last print YES price in (T-2h, T]. Model = p_fc (forecast only) and
p_obs (forecast restricted by observed running max/min). Brier on markets with a print in the window.
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, STATIONS, TEST_START  # noqa: E402

pd.set_option("display.width", 220)


def snapshots(p):
    off = p.code.map(lambda c: STATIONS[c][2])
    d0 = (pd.to_datetime(p.date) - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s") - off * 3600  # D 00 LST
    p = p.assign(d0=d0)
    out = []
    for lbl, hrs in (("D-1 18h", -6), ("D 08h", 8), ("D 12h", 12), ("D 15h", 15), ("D 18h", 18), ("D 21h", 21)):
        T = p.d0 + hrs * 3600
        w = p[(p.ts <= T) & (p.ts > T - 7200)]
        last = w.sort_values("ts").groupby("ticker").tail(1).assign(T=lbl)
        out.append(last)
    return pd.concat(out)


def brier(x, col):
    return float(((x[col] - x.win) ** 2).mean())


def main():
    p = pd.read_parquet(OUT / "panel.parquet")
    print("== side check: 20 random prints (y, taker side, previous & next print on same ticker within 120s)")
    rs = p.sample(20, random_state=1).index
    for i in rs:
        r = p.loc[i]; prv = p.loc[i - 1] if i > 0 and p.loc[i - 1, "ticker"] == r.ticker else None
        prev = "" if prv is None else f"{prv.y:.2f}/{'Y' if prv.tyes else 'N'} dt={r.ts - prv.ts}s"
        print(f"  {r.ticker:28s} y={r.y:.2f} taker={'YES' if r.tyes else 'NO '} qty={r.qty:5d} "
              f"prev={prev} win={r.win}")
    # quantitative: within the same ticker, YES-taker prints (ask) should sit above NO-taker prints (bid) close in time
    q = p[["ticker", "ts", "y", "tyes"]].copy()
    q["py"] = q.groupby("ticker").y.shift(); q["pt"] = q.groupby("ticker").tyes.shift(); q["pts"] = q.groupby("ticker").ts.shift()
    z = q[(q.ts - q.pts <= 10) & (q.tyes != q.pt)]
    print("  ask-bid from adjacent opposite-side prints <=10s apart: median",
          float(np.median(np.where(z.tyes, z.y - z.py, z.py - z.y))), "share >=0:",
          float((np.where(z.tyes, z.y - z.py, z.py - z.y) >= 0).mean()))

    te = p[p.date >= TEST_START]
    s = snapshots(te)
    s = s[s.p_fc.notna()]
    rows = []
    for (T, meas), g in s.groupby(["T", "measure"]):
        rows.append(dict(T=T, measure=meas, n=len(g), events=g.event.nunique(), brier_mkt=brier(g, "y"),
                         brier_fc=brier(g, "p_fc"), brier_obs=brier(g, "p_obs"),
                         brier_blend=float((((0.5 * g.y + 0.5 * g.p_obs) - g.win) ** 2).mean())))
    print("== test-window Brier at decision times (lower is better)")
    print(pd.DataFrame(rows).round(4).to_string(index=False))

    print("== time-varying FLB on all KX prints (print-weighted), win - price, by hours to close and price bucket")
    p["hb"] = pd.cut(p.h_to_close, [0, 1, 3, 6, 12, 24, 48], right=False)
    p["pb"] = pd.cut(p.y, [0, .05, .15, .3, .5, .7, .85, .95, 1.0])
    for nm, g in (("train", p[p.date < TEST_START]), ("test", p[p.date >= TEST_START])):
        t = g.groupby(["hb", "pb"], observed=True).apply(lambda z: (z.win - z.y).mean() * 100).unstack().round(1)
        print(nm, "(cents; negative = YES overpriced)"); print(t.to_string())
    # contract-weighted, event-clustered CI for the favourite bucket (>=0.85) and last 12h
    for nm, g in (("train", p[p.date < TEST_START]), ("test", p[p.date >= TEST_START])):
        for lab, msk in (("fav>=0.85 & <12h", (g.y >= .85) & (g.h_to_close < 12)),
                         ("fav 0.7-0.85 & <12h", (g.y >= .7) & (g.y < .85) & (g.h_to_close < 12)),
                         ("longshot<=0.15 & 24-48h", (g.y <= .15) & (g.h_to_close >= 24)),
                         ("longshot<=0.15 & <12h", (g.y <= .15) & (g.h_to_close < 12))):
            z = g[msk]
            e = z.assign(d=(z.win - z.y) * z.qty).groupby("event").agg(d=("d", "sum"), q=("qty", "sum"))
            rng = np.random.default_rng(0)
            idx = rng.integers(0, len(e), (1000, len(e)))
            bs = e.d.to_numpy()[idx].sum(1) / e.q.to_numpy()[idx].sum(1) * 100
            print(f"  {nm} {lab:26s} contracts={int(z.qty.sum()):>10d} events={len(e):5d} "
                  f"win-price={e.d.sum() / e.q.sum() * 100:+.2f}c CI[{np.quantile(bs, .025):+.2f},{np.quantile(bs, .975):+.2f}]")


if __name__ == "__main__":
    main()

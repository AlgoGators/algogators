"""wx: settlement mechanics with ASOS 1-minute data, and convergence timing against the fast public feed.

1. Per station-day: CLI high/low vs max/min of (a) 1-minute temps, (b) 5-minute samples (minutes divisible by 5,
   which is what the free real-time 5-min ASOS feed carries), (c) METAR (T-groups + 6h groups).
2. TWC-era candles (2026-08-15..10-12): for each bracket made impossible by the 5-min running max/min
   (high: hi <= round(max5)-1; low: lo >= min5+1), tau5 = sample time + 3 min (feed lag). YES bid path around
   tau5 vs around the METAR tau (wx_converge). Did Kalshi move before the 5-min feed showed the crossing?
"""
from __future__ import annotations
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, ROOT, SERIES, STATIONS  # noqa: E402

FEED_LAG = 180


def load_1min():
    rows = []
    for f in glob.glob(str(OUT / "iem" / "asos1min" / "*.parquet")):
        c = Path(f).stem.split("_")[0]
        d = pd.read_parquet(f)
        if d.empty:
            continue
        d["valid"] = pd.to_datetime(d.valid)
        d["code"] = c
        rows.append(d)
    d = pd.concat(rows, ignore_index=True).drop_duplicates(["code", "valid"])
    d["tmpf"] = pd.to_numeric(d.tmpf, errors="coerce")
    d = d.dropna(subset=["tmpf"])
    off = d.code.map(lambda c: STATIONS[c][2])
    d["date"] = (d.valid + pd.to_timedelta(off, unit="h")).dt.date.astype(str)
    d["ts"] = d.valid.astype("datetime64[s]").astype("int64")
    d["is5"] = d.valid.dt.minute % 5 == 0
    return d.sort_values(["code", "ts"])


def mechanics(d):
    g1 = d.groupby(["code", "date"]).tmpf.agg(max1="max", min1="min", n1="size")
    g5 = d[d.is5].groupby(["code", "date"]).tmpf.agg(max5="max", min5="min")
    x = g1.join(g5).reset_index()
    x = x[x.n1 >= 1300]  # near-complete days only
    cli = pd.read_parquet(OUT / "cli.parquet")
    om = pd.read_parquet(OUT / "obs_daily.parquet")[["code", "date", "metar_max", "metar_min", "tmax_only"]]
    x = x.merge(cli[["code", "date", "high", "low"]], on=["code", "date"]).merge(om, on=["code", "date"], how="left")
    x["era"] = np.where(x.date >= "2026-08-15", "TWC 2026-08-15+", "NWS")
    out = {}
    for era, g in x.groupby("era"):
        out[era] = dict(
            n=len(g),
            high_eq_max1=(g.high == g.max1).mean(), high_minus_max1=g.assign(d=g.high - g.max1).d.value_counts(normalize=True).sort_index().round(4).to_dict(),
            high_eq_max5=(g.high == g.max5).mean(), high_minus_max5=g.assign(d=g.high - g.max5).d.value_counts(normalize=True).sort_index().round(4).to_dict(),
            high_eq_metar=(g.high == np.floor(g.metar_max + .5)).mean(),
            high_eq_hourlyT=(g.high == np.floor(g.tmax_only + .5)).mean(),
            low_eq_min1=(g.low == g.min1).mean(), low_eq_min5=(g.low == g.min5).mean(),
            low_minus_min5=g.assign(d=g.low - g.min5).d.value_counts(normalize=True).sort_index().round(4).to_dict())
        print(era, out[era])
    x.to_parquet(OUT / "onemin_daily.parquet", index=False)
    return x


def converge(d):
    f = pd.read_parquet(ROOT / "data" / "vendor" / "fin_E" / "kalshi_weather" / "markets.parquet")
    f = f[f.series.isin(SERIES)].copy()
    f["code"] = f.series.map(lambda s: SERIES[s][0]); f["measure"] = f.series.map(lambda s: SERIES[s][1])
    f["date"] = pd.to_datetime(f.event.str.split("-").str[1], format="%y%b%d").dt.date.astype(str)
    f["lo"] = np.where(f.strike_type == "between", f["floor"], np.where(f.strike_type == "greater", f["floor"] + 1, -np.inf))
    f["hi"] = np.where(f.strike_type == "between", f["cap"], np.where(f.strike_type == "less", f["cap"] - 1, np.inf))
    f["win"] = f.result.eq("yes").astype(int)
    d5 = d[d.is5].copy()
    d5["rmax"] = d5.groupby(["code", "date"]).tmpf.cummax(); d5["rmin"] = d5.groupby(["code", "date"]).tmpf.cummin()
    d1 = d.copy()
    d1["rmax"] = d1.groupby(["code", "date"]).tmpf.cummax(); d1["rmin"] = d1.groupby(["code", "date"]).tmpf.cummin()
    taus = {}
    for nm, src, lag in (("tau5", d5, FEED_LAG), ("tau1", d1, 0)):
        out = []
        for meas in ("high", "low"):
            j = f[f.measure == meas].merge(src[["code", "date", "ts", "rmax", "rmin"]], on=["code", "date"])
            ok = (j.hi <= j.rmax - 1) if meas == "high" else (j.lo >= j.rmin + 1)
            out.append(j[ok].groupby("ticker").ts.min() + lag)
        taus[nm] = pd.concat(out)
    f = f.merge(taus["tau5"].rename("tau5"), left_on="ticker", right_index=True, how="inner")
    f = f.merge(taus["tau1"].rename("tau1"), left_on="ticker", right_index=True, how="left")
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
            if cc.empty or cc.ts.min() > r.tau5 - 3600:
                continue
            row = dict(ticker=r.ticker, measure=r.measure, win=r.win, tau5=r.tau5, tau1=r.tau1)
            for k in (-60, -30, -15, -5, 0, 1, 2, 5, 15, 30):
                x = cc[cc.ts <= r.tau5 + 60 * k]
                row[f"bid_{k}"] = x.bid_close.iloc[-1] if len(x) else np.nan
            post = cc[(cc.ts > r.tau5) & (cc.ts <= r.tau5 + 1800)]
            row["vol_30m_post"] = post.volume.sum()
            rows.append(row)
    z = pd.DataFrame(rows)
    print("dead-by-5min-feed markets with candles:", len(z), " of which won (loss):", int(z.win.sum()))
    print("minutes tau5 - tau1 (feed vs truth): median", ((z.tau5 - z.tau1) / 60).median())
    for k in (-60, -30, -15, -5, 0, 1, 2, 5, 15, 30):
        b = z[f"bid_{k}"]
        print(f"  tau5{k:+d}m  YES bid mean {b.mean():.4f}  share>=2c {(b >= .02).mean():.3f}  share>=5c {(b >= .05).mean():.3f}")
    sel = z.bid_1 >= 0.02
    e = z.bid_1 - np.ceil(np.round(0.07 * z.bid_1 * (1 - z.bid_1) * 100, 6)) / 100 - z.win
    print("  taker hit YES bid at tau5+1m, bid>=2c: n", int(sel.sum()), "c/contract", round(100 * e[sel].mean(), 2),
          "losses", int(z.win[sel].sum()), " post-30m volume in those markets", z.vol_30m_post[sel].sum())
    z.to_parquet(OUT / "onemin_converge.parquet", index=False)


if __name__ == "__main__":
    dd = load_1min()
    mechanics(dd)
    converge(dd)

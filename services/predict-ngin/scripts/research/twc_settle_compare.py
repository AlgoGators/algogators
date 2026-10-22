"""twc task 2: Kalshi daily temperature settlement (TWC era) vs NWS CLI final, METAR, ASOS 5-min and 1-min.

Inputs (read-only): data/vendor/twc/daily_markets.parquet (twc_fetch.py daily; falls back to fin_E markets),
data/vendor/wx/cli.parquet, data/vendor/wx/obs_daily.parquet (METAR incl. 6-h groups), data/vendor/wx/iem/asos1min.
Writes data/vendor/twc/settle_compare.parquet and prints the tables used in docs/TWC_SETTLEMENT.md.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
from wx_common import SERIES, STATIONS  # noqa: E402  (read-only constants)

OUT = ROOT / "data" / "vendor" / "twc"
WX = ROOT / "data" / "vendor" / "wx"


def rnd(x):
    return np.floor(x + 0.5)


def station_of(rules: str):
    m = re.search(r"\(CLI([A-Z0-9]{3,4})\)", rules or "")
    return m.group(1) if m else None


def load_markets():
    p = OUT / "daily_markets.parquet"
    if p.exists():
        m = pd.read_parquet(p)
        m = m.rename(columns={"event_ticker": "event", "floor_strike": "floor", "cap_strike": "cap",
                              "rules_primary": "rules"})
    else:
        m = pd.read_parquet(ROOT / "data/vendor/fin_E/kalshi_weather/markets.parquet")
    m = m[m.result.isin(["yes", "no"])].copy()
    m["date"] = pd.to_datetime(m.event.str.split("-").str[1], format="%y%b%d", errors="coerce").dt.date.astype(str)
    m["src"] = np.where(m.rules.str.contains("Weather Company", na=False), "TWC",
                        np.where(m.rules.str.contains("National Weather|NWS|Climatological", na=False), "NWS", "?"))
    m["station"] = m.rules.map(station_of)
    m["measure"] = np.where(m.series.str.startswith("KXHIGH"), "high", "low")
    m["xv"] = pd.to_numeric(m.expiration_value.astype(str).str.extract(r"(-?\d+(?:\.\d+)?)")[0], errors="coerce")
    return m


def switch_dates(m):
    e = m.drop_duplicates("event")
    g = e.groupby(["series", "src"]).date.agg(["min", "max", "size"]).unstack("src")
    return g


def onemin_daily():
    import glob
    rows = []
    for f in glob.glob(str(WX / "iem" / "asos1min" / "*_2026*.parquet")):
        c = Path(f).stem.split("_")[0]
        d = pd.read_parquet(f)
        if d.empty:
            continue
        d["code"] = c
        rows.append(d)
    d = pd.concat(rows, ignore_index=True)
    d["valid"] = pd.to_datetime(d.valid)
    d["tmpf"] = pd.to_numeric(d.tmpf, errors="coerce")
    d = d.dropna(subset=["tmpf"]).drop_duplicates(["code", "valid"])
    off = d.code.map(lambda c: STATIONS[c][2])
    d["date"] = (d.valid + pd.to_timedelta(off, unit="h")).dt.date.astype(str)
    d5 = d[d.valid.dt.minute % 5 == 0]
    g1 = d.groupby(["code", "date"]).tmpf.agg(max1="max", min1="min", n1="size")
    g5 = d5.groupby(["code", "date"]).tmpf.agg(max5="max", min5="min")
    x = g1.join(g5).reset_index()
    return x[x.n1 >= 1300]


def main():
    m = load_markets()
    print("== switch dates (event date range by rules source)")
    print(switch_dates(m).to_string())
    print("stations in rules per series (TWC era):")
    st = m[m.src == "TWC"].groupby("series").station.agg(lambda s: sorted(set(s.dropna())))
    print(st[st.map(len) != 1].to_string() or "all single-station")
    w = m[m.result == "yes"].copy()
    w["lo"] = np.where(w.strike_type == "between", w["floor"], np.where(w.strike_type == "greater", w["floor"] + 1, -np.inf))
    w["hi"] = np.where(w.strike_type == "between", w["cap"], np.where(w.strike_type == "less", w["cap"] - 1, np.inf))
    ev = w.drop_duplicates("event")
    ev["code"] = ev.series.map(lambda s: SERIES.get(s, (None,))[0])
    ev["code"] = ev.code.fillna(ev.station)
    cli = pd.read_parquet(WX / "cli.parquet")[["code", "date", "high", "low", "prod_ts"]]
    ob = pd.read_parquet(WX / "obs_daily.parquet")[["code", "date", "metar_max", "metar_min", "tmax_only", "n"]]
    om = onemin_daily()
    x = ev.merge(cli, on=["code", "date"], how="left").merge(ob, on=["code", "date"], how="left").merge(
        om, on=["code", "date"], how="left")
    hi = x.measure == "high"
    x["cli"] = np.where(hi, x.high, x.low)
    x["metar"] = np.where(hi, rnd(x.metar_max), rnd(x.metar_min))
    x["metar_tonly"] = np.where(hi, rnd(x.tmax_only), np.nan)
    x["a5"] = np.where(hi, x.max5, x.min5)
    x["a1"] = np.where(hi, x.max1, x.min1)
    x["in_bracket_cli"] = (x.cli >= x.lo) & (x.cli <= x.hi)
    for c in ["cli", "metar", "metar_tonly", "a5", "a1"]:
        x[f"d_{c}"] = x.xv - x[c]
        x[f"br_{c}"] = np.where(x[c].isna(), np.nan, ((x[c] >= x.lo) & (x[c] <= x.hi)).astype(float))
    x.to_parquet(OUT / "settle_compare.parquet", index=False)
    t = x[x.src == "TWC"]
    print("\n== TWC era events:", len(t), "dates", t.date.min(), "->", t.date.max())

    def summ(g):
        r = {"n": len(g)}
        for c in ["cli", "metar", "metar_tonly", "a5", "a1"]:
            v = g[f"d_{c}"].dropna()
            b = g[f"br_{c}"].dropna()
            r[f"{c}_n"] = len(v)
            r[f"{c}_eq"] = round((v == 0).mean(), 4) if len(v) else np.nan
            r[f"{c}_brk_agree"] = round(b.mean(), 4) if len(b) else np.nan
        return pd.Series(r)

    print(t.groupby("measure").apply(summ).T.to_string())
    print("\n-- by station (TWC era): share xv == CLI, == round(METAR), bracket agreement with METAR")
    bys = t.groupby(["code", "measure"]).apply(summ)[["n", "cli_eq", "metar_eq", "metar_brk_agree", "a5_n", "a5_eq", "a1_eq"]]
    print(bys.to_string())
    print("\n-- xv - CLI distribution:", t.d_cli.value_counts().sort_index().to_dict())
    print("-- xv - round(METAR) distribution by measure:")
    print(t.groupby("measure").d_metar.value_counts().sort_index().to_string())
    bad = t[(t.d_cli != 0) & t.d_cli.notna()]
    print("\n-- TWC != CLI events:\n", bad[["event", "date", "code", "xv", "cli", "metar", "a5", "a1", "lo", "hi"]].to_string())
    nocli = t[t.cli.isna()]
    print("events without CLI match:", len(nocli), sorted(set(nocli.series))[:20])
    # pre-close predictability of METAR disagreement (what a METAR-watcher would get wrong)
    print("\n== predictability of xv != round(METAR) (features available before close)")
    q = t[t.metar.notna()].copy()
    q["dis"] = q.d_metar != 0
    v = np.where(q.measure == "high", q.metar_max, q.metar_min)
    q["frac_to_boundary"] = np.abs((v - np.floor(v)) - 0.5).round(2)
    q["near_bound"] = q.frac_to_boundary <= 0.1
    q["tgrp_lt_6h"] = (q.measure == "high") & (rnd(q.tmax_only) < q.metar)
    q["a5_gt_metar"] = (q.a5 - q.metar).where(q.a5.notna())
    for f in ["near_bound", "tgrp_lt_6h", "measure"]:
        print(q.groupby(f).dis.agg(["mean", "size"]).to_string())
    print(q.groupby("a5_gt_metar").agg(dis=("dis", "mean"), n=("dis", "size"),
                                        d_mean=("d_metar", "mean")).to_string())
    # old CLI-era: NWS events (pre-switch) vs CLI for reference
    o = x[x.src == "NWS"]
    if len(o):
        print("\n== pre-switch (NWS-rule) events in this pull:", len(o), "xv==CLI", (o.d_cli == 0).mean().round(4),
              "xv==METAR", (o.d_metar == 0).mean().round(4))


if __name__ == "__main__":
    main()

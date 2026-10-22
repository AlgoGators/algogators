"""wx: METAR observations -> causal running max/min per climate day, and METAR-vs-CLI agreement.

Temperature per report: METAR T-group (0.1 C) if present else IEM tmpf. Also the 6-hour max/min remark
groups (1snnn / 2snnn, 0.1 C, at synoptic hours) which capture between-report peaks.
Climate day = local STANDARD time midnight..midnight (NWS CLI convention).
Info time = report valid time + LAG_S (publication lag assumption, 5 min).

Outputs data/vendor/wx/obs.parquet   (code, ts_avail, date, tF, run_max, run_min) one row per report
        data/vendor/wx/obs_daily.parquet (code, date, metar_max, metar_min, cli_high, cli_low, d_high, d_low)
"""
from __future__ import annotations
import glob
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, STATIONS  # noqa: E402

LAG_S = 300
RT = re.compile(r"\bT([01])(\d{3})([01])(\d{3})\b")
R6X = re.compile(r"\s1([01])(\d{3})\s")
R6N = re.compile(r"\s2([01])(\d{3})\s")


def c2f(s, v):
    c = (-1 if s == "1" else 1) * int(v) / 10.0
    return c * 9 / 5 + 32


def parse(df):
    t = []; x6 = []; n6 = []
    for mt, tf in zip(df.metar.fillna("").to_numpy(), df.tmpf.to_numpy()):
        rmk = mt.split(" RMK ", 1)[1] + " " if " RMK " in mt else ""
        m = RT.search(mt)
        t.append(c2f(m.group(1), m.group(2)) if m else tf)
        a = R6X.search(" " + rmk); b = R6N.search(" " + rmk)
        x6.append(c2f(a.group(1), a.group(2)) if a else np.nan)
        n6.append(c2f(b.group(1), b.group(2)) if b else np.nan)
    df["tF"] = t; df["x6"] = x6; df["n6"] = n6
    return df


def build():
    rows = []
    for c, (icao, sid, off, _) in STATIONS.items():
        fs = sorted(glob.glob(str(OUT / "iem" / "asos" / f"{c}_*.parquet")))
        if not fs:
            continue
        d = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True).drop_duplicates(["valid", "metar"])
        d = parse(d)
        d["valid"] = pd.to_datetime(d.valid)
        d["ts"] = (d.valid - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")
        lst = d.valid + pd.Timedelta(hours=off)
        d["date"] = lst.dt.date.astype(str)
        # a 6h max group at hh:51 Z covers the 6 h ending then; credit it to the report's own climate day only
        # (conservative: a 00Z group in the eastern day straddles midnight LST -> skip groups whose window crosses
        # local-standard midnight)
        start_lst = lst - pd.Timedelta(hours=6)
        same = start_lst.dt.date == lst.dt.date
        d.loc[~same, ["x6", "n6"]] = np.nan
        d["hiF"] = d[["tF", "x6"]].max(axis=1); d["loF"] = d[["tF", "n6"]].min(axis=1)
        d = d.sort_values("ts")
        d["run_max"] = d.groupby("date").hiF.cummax(); d["run_min"] = d.groupby("date").loF.cummin()
        d["code"] = c; d["ts_avail"] = d.ts + LAG_S
        rows.append(d[["code", "ts", "ts_avail", "date", "tF", "x6", "n6", "run_max", "run_min"]])
        print(c, len(d), flush=True)
    o = pd.concat(rows, ignore_index=True)
    o.to_parquet(OUT / "obs.parquet", index=False)
    day = o.groupby(["code", "date"]).agg(metar_max=("run_max", "last"), metar_min=("run_min", "last"),
                                          tmax_only=("tF", "max"), n=("tF", "size")).reset_index()
    cli = pd.read_parquet(OUT / "cli.parquet")
    day = day.merge(cli[["code", "date", "high", "low"]], on=["code", "date"], how="inner")
    # NWS rounds F half-up from the ASOS value; use floor(x+0.5)
    day["d_high"] = day.high - np.floor(day.metar_max + 0.5)
    day["d_low"] = day.low - np.floor(day.metar_min + 0.5)
    day["d_high_tonly"] = day.high - np.floor(day.tmax_only + 0.5)
    day = day[day.n >= 20]
    day.to_parquet(OUT / "obs_daily.parquet", index=False)
    print("CLI high - round(METAR max incl 6h groups):", day.d_high.value_counts(normalize=True).sort_index().round(4).to_dict())
    print("CLI high - round(METAR hourly/special T only):", day.d_high_tonly.value_counts(normalize=True).sort_index().round(4).to_dict())
    print("CLI low - round(METAR min):", day.d_low.value_counts(normalize=True).sort_index().round(4).to_dict())
    print(day.groupby("code").d_high.agg(lambda x: (x == 0).mean()).round(3).to_dict())


if __name__ == "__main__":
    build()

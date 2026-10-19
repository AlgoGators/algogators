"""twc: what did The Weather Company report for the TWC-era hourly markets (2026-08-07 -> 09-10)?

Compares Kalshi expiration_value (TWC reading "at" hour S for station coordinates) with:
  (a) ASOS 1-minute temperature at S, S-1..S-10 (IEM archive), (b) the latest METAR/SPECI at or before S
  (whole-degF from the T-group), (c) the latest METAR before S-10 min.
and checks whether the market in its last minutes priced the value implied by the latest METAR (descriptive).
"""
from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "twc"
WX = ROOT / "data" / "vendor" / "wx" / "iem"
SER = {"KXTEMPAUSH": "AUS", "KXTEMPCHIH": "ORD", "KXTEMPDCH": "DCA", "KXTEMPLAXH": "LAX", "KXTEMPNYCH": "NYC"}


def tgroup_f(m):
    g = re.search(r" T([01])(\d{3})([01])(\d{3})", m or "")
    if not g:
        return np.nan
    c = int(g.group(2)) / 10 * (-1 if g.group(1) == "1" else 1)
    return c * 9 / 5 + 32


def load_metar(code):
    if code == "ORD":
        d = pd.read_csv(OUT / "iem" / "ORD_metar.csv")
    else:
        d = pd.concat([pd.read_parquet(f) for f in glob.glob(str(WX / "asos" / f"{code}_2026*.parquet"))])
    d["valid"] = pd.to_datetime(d.valid, utc=True)
    d["tmpf"] = pd.to_numeric(d.tmpf, errors="coerce")
    d["tg"] = d.metar.map(tgroup_f)
    return d.sort_values("valid")


def load_1min(code):
    if code == "ORD":
        d = pd.read_csv(OUT / "iem" / "ORD_1min.csv")
        d = d[d.station != "station"].rename(columns={"valid(UTC)": "valid"})
    else:
        d = pd.concat([pd.read_parquet(f) for f in glob.glob(str(WX / "asos1min" / f"{code}_2026*.parquet"))])
    d["valid"] = pd.to_datetime(d.valid, utc=True)
    d["tmpf"] = pd.to_numeric(d.tmpf, errors="coerce")
    return d.dropna(subset=["tmpf"]).drop_duplicates("valid").set_index("valid").tmpf.sort_index()


def main():
    h = pd.read_parquet(OUT / "hourly_markets.parquet")
    h = h[h.series.isin(SER) & h.rules_primary.str.contains("Weather Company", na=False) & h.result.isin(["yes", "no"])]
    h["S"] = pd.to_datetime(h.close_time, utc=True)
    h["xv"] = pd.to_numeric(h.expiration_value, errors="coerce")
    ev = h.drop_duplicates("event_ticker")[["series", "event_ticker", "S", "xv"]].copy()
    rows = []
    for s, code in SER.items():
        e = ev[ev.series == s]
        if e.empty:
            continue
        mt = load_metar(code)
        try:
            om = load_1min(code)
        except Exception:  # noqa: BLE001
            om = pd.Series(dtype=float)
        for r in e.itertuples():
            m0 = mt[mt.valid <= r.S]
            last = m0.iloc[-1] if len(m0) else None
            m10 = mt[mt.valid <= r.S - pd.Timedelta(minutes=10)]
            rec = dict(series=s, code=code, event=r.event_ticker, S=r.S, xv=r.xv,
                       metar_last=last.tmpf if last is not None else np.nan,
                       metar_last_tg=last.tg if last is not None else np.nan,
                       metar_age_min=(r.S - last.valid).total_seconds() / 60 if last is not None else np.nan,
                       metar_m10=m10.tmpf.iloc[-1] if len(m10) else np.nan)
            for k in (0, 1, 5, 10):
                rec[f"a1_m{k}"] = om.get(r.S - pd.Timedelta(minutes=k), np.nan)
            rows.append(rec)
    x = pd.DataFrame(rows)
    x["xv_int"] = (x.xv == x.xv.round()).astype(float)
    print("TWC-era hourly events:", len(x), "| integer settlement values:", x.xv_int.mean().round(4))
    for c in ["metar_last", "metar_m10", "a1_m0", "a1_m1", "a1_m5", "a1_m10"]:
        d = (x.xv - x[c]).dropna()
        print(f"xv - {c}: n={len(d)} eq={np.mean(d == 0):.3f} |d|<=1={np.mean(d.abs() <= 1):.3f} "
              f"mean={d.mean():+.2f} dist={d.round().clip(-3, 3).value_counts(normalize=True).sort_index().round(3).to_dict()}")
    print(x.groupby("code").apply(lambda g: pd.Series(dict(
        n=len(g), eq_metar=np.mean(g.xv == g.metar_last), eq_1min_S=np.mean(g.xv == g.a1_m0),
        age=g.metar_age_min.mean()))).round(3).to_string())
    x.to_parquet(OUT / "hourly_twcera_compare.parquet", index=False)


if __name__ == "__main__":
    main()

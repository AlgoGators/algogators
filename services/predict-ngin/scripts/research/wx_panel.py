"""wx: per-print panel for the local Kalshi tape (KX-era temperature markets) with strictly causal features.

For each print (ts, ticker, y = YES price, tyes = taker bought YES, qty):
  market: code, measure, date, lo, hi, win, close_ts, settle_ts
  forecast fair (Open-Meteo, wx_fv_om): latest lead whose avail_ts < ts  -> mu, s, nu, p_fc
  observations (wx_obs): latest METAR with ts_avail < ts within the climate day -> run_max/run_min, tF
  p_obs: forecast distribution restricted to the hard support implied by obs (V >= round(run_max)-1 for highs,
         V <= round(run_min)+1 for lows; margin 1F from the METAR-vs-CLI study), renormalised
  dead:  bracket impossible given obs with margin (high: hi <= round(run_max)-2; low: lo >= round(run_min)+2)
  hours_to_close, local hour (LST)
Output data/vendor/wx/panel.parquet
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, SERIES, STATIONS  # noqa: E402
import wx_fv_om as FV  # noqa: E402

MARGIN = 1
OBS_LATENCY = 60  # our decision loop after a METAR becomes public (ts_avail already = valid + 5 min)


def build():
    t = pd.read_parquet(OUT / "tape.parquet")
    t = t[t.series.str.startswith("KX")]
    m = pd.read_parquet(OUT / "markets.parquet")
    m = m[m.lo.notna()]
    t = t.merge(m[["ticker", "date", "lo", "hi", "win", "close_ts", "settle_ts", "open_ts"]], on="ticker", how="inner")
    t["code"] = t.series.map(lambda s: SERIES[s][0]); t["measure"] = t.series.map(lambda s: SERIES[s][1])
    t = t[(t.ts < t.close_ts) & t.settle_ts.notna()]
    t["key"] = t.code + "|" + t.measure + "|" + t.date
    # ---- forecast: latest lead available strictly before the print second
    f = pd.read_parquet(OUT / "fc_daily.parquet").dropna(subset=["f_g", "f_e", "f_n"])
    f["key"] = f.code + "|" + f.measure + "|" + f.date
    f = f.sort_values("avail_ts")
    t = t.sort_values("ts")
    t["ts_m1"] = t.ts - 1
    t = pd.merge_asof(t, f[["key", "avail_ts", "lead", "f_g", "f_e", "f_n", "day_start_ts", "day_end_ts"]],
                      left_on="ts_m1", right_on="avail_ts", by="key", direction="backward")
    fm = FV.load()
    ok = t.lead.notna()
    mu, s, nu = fm.musig(t.loc[ok, ["code", "measure", "lead", "f_g", "f_e", "f_n"]].astype({"lead": int}))
    t.loc[ok, "mu"] = mu; t.loc[ok, "s"] = s; t.loc[ok, "nu"] = nu
    t["p_fc"] = np.nan
    t.loc[ok, "p_fc"] = FV.OMFair.prob(mu, s, nu, t.loc[ok, "lo"].to_numpy(), t.loc[ok, "hi"].to_numpy())
    # ---- observations
    o = pd.read_parquet(OUT / "obs.parquet")[["code", "date", "ts_avail", "run_max", "run_min", "tF"]]
    o["key2"] = o.code + "|" + o.date
    o = o.sort_values("ts_avail")
    t["key2"] = t.code + "|" + t.date
    t["ts_obs"] = t.ts - OBS_LATENCY
    t = pd.merge_asof(t.sort_values("ts_obs"), o[["key2", "ts_avail", "run_max", "run_min", "tF"]], left_on="ts_obs",
                      right_on="ts_avail", by="key2", direction="backward")
    rmx = np.floor(t.run_max + 0.5); rmn = np.floor(t.run_min + 0.5)
    hi_m = t.measure.eq("high")
    t["dead"] = np.where(hi_m, t.hi <= rmx - 1 - MARGIN, t.lo >= rmn + 1 + MARGIN) & t.run_max.notna()
    floor_ = np.where(hi_m & t.run_max.notna(), rmx - MARGIN, -np.inf)
    cap_ = np.where(~hi_m & t.run_min.notna(), rmn + MARGIN, np.inf)
    t["p_obs"] = np.nan
    ok = t.mu.notna().to_numpy()
    t.loc[ok, "p_obs"] = FV.OMFair.prob(t.mu.to_numpy()[ok], t.s.to_numpy()[ok], t.nu.to_numpy()[ok],
                                        t.lo.to_numpy()[ok], t.hi.to_numpy()[ok], floor_[ok], cap_[ok])
    off = t.code.map(lambda c: STATIONS[c][2])
    t["lst_hour"] = ((t.ts + off * 3600) % 86400) / 3600
    t["h_to_close"] = (t.close_ts - t.ts) / 3600
    t["in_day"] = t.ts >= t.day_start_ts
    keep = ["ts", "series", "event", "ticker", "code", "measure", "date", "y", "tyes", "qty", "lo", "hi", "win",
            "close_ts", "settle_ts", "open_ts", "lead", "mu", "s", "nu", "p_fc", "run_max", "run_min", "tF", "dead",
            "p_obs", "lst_hour", "h_to_close", "in_day"]
    t = t[keep].sort_values(["ticker", "ts"], kind="mergesort").reset_index(drop=True)
    t.to_parquet(OUT / "panel.parquet", index=False)
    print(len(t), "prints;", t.p_fc.notna().mean().round(3), "with forecast;", t.dead.mean().round(4), "dead")
    print(t.groupby(t.date.str[:7]).size().to_dict())


if __name__ == "__main__":
    build()

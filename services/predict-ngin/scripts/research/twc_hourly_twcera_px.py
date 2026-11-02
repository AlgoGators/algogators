"""twc: TWC-era hourly markets (2026-08-07..09-10) - was the settling METAR priced before the close?

Settlement = the last METAR at or before S on 99.5% of events (twc_hourly_twcera.py). The routine METAR is observed at
:51-:56, i.e. 4-9 min before S. For each market, the side implied by that METAR (strike vs METAR whole degF) is
compared with its ask in the 1-minute candles ending at S-k (k = 1, 2, 3, 5), split by METAR obs time relative to tau.
If the implied side's ask is < 0.90 after the METAR obs time, a feed that delivers METARs within k minutes would
have had an edge. Descriptive (era is over: these series are no longer listed).
"""
from __future__ import annotations

import glob
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "twc"


def fee(p, c=10):
    return np.ceil(np.round(0.07 * c * p * (1 - p) * 100, 6)) / 100 / c


def main():
    x = pd.read_parquet(OUT / "hourly_twcera_compare.parquet")
    h = pd.read_parquet(OUT / "hourly_markets.parquet")
    h = h[h.event_ticker.isin(x.event) & h.result.isin(["yes", "no"])]
    h["K"] = pd.to_numeric(h.floor_strike, errors="coerce")
    h = h.merge(x[["event", "metar_last", "metar_age_min", "xv"]], left_on="event_ticker", right_on="event")
    h["imp_yes"] = h.metar_last > h.K
    h["win"] = (h.result == "yes")
    print("implied side == result:", (h.imp_yes == h.win).mean().round(4), len(h))
    c = pd.concat([pd.read_parquet(f) for f in glob.glob(str(OUT / "bcandles_twc" / "*.parquet"))], ignore_index=True)
    for col in ["bid_c", "ask_c"]:
        c[col] = pd.to_numeric(c[col], errors="coerce")
    c["Ss"] = c.S.map(lambda s: int(s.timestamp()))
    c["k"] = (c.Ss - c.ts) // 60
    m = h.merge(c[c.k.isin([1, 2, 3, 5, 8])][["ticker", "k", "bid_c", "ask_c"]], on="ticker")
    m["imp_ask"] = np.where(m.imp_yes, m.ask_c, 1 - m.bid_c)
    m = m[(m.imp_ask > 0) & (m.imp_ask < 1)]
    # contested markets only: strike within 2F of the METAR value
    m = m[(m.K - m.metar_last).abs() <= 2]
    m["obs_before_tau"] = m.metar_age_min > m.k
    m["pnl"] = np.where(m.imp_yes, m.win, ~m.win).astype(float) - m.imp_ask - fee(m.imp_ask)
    g = m.groupby(["k", "obs_before_tau"]).agg(n=("pnl", "size"), ev=("event_ticker", "nunique"),
                                              ask_med=("imp_ask", "median"), share_ask_lt90=("imp_ask", lambda s: (s < .9).mean()),
                                              c_per=("pnl", lambda s: s.mean() * 100))
    print(g.round(3).to_string())
    m.to_parquet(OUT / "twcera_implied_px.parquet", index=False)


if __name__ == "__main__":
    main()


def causal(lat_min=4):
    """Causal version: act at the first candle end >= METAR obs time + lat_min (aviationweather.gov latency
    measured ~3-5 min), buy the METAR-implied side at its ask if 0 < ask < 1."""
    x = pd.read_parquet(OUT / "hourly_twcera_compare.parquet")
    h = pd.read_parquet(OUT / "hourly_markets.parquet")
    h = h[h.event_ticker.isin(x.event) & h.result.isin(["yes", "no"])]
    h["K"] = pd.to_numeric(h.floor_strike, errors="coerce")
    h = h.merge(x[["event", "metar_last", "metar_age_min"]], left_on="event_ticker", right_on="event")
    h["imp_yes"] = h.metar_last > h.K
    h["win"] = h.result == "yes"
    c = pd.concat([pd.read_parquet(f) for f in glob.glob(str(OUT / "bcandles_twc" / "*.parquet"))], ignore_index=True)
    for col in ["bid_c", "ask_c"]:
        c[col] = pd.to_numeric(c[col], errors="coerce")
    c["k"] = (c.S.map(lambda s: int(s.timestamp())) - c.ts) // 60
    h["k_act"] = np.floor(h.metar_age_min - lat_min)          # minutes before S at which the METAR is in hand
    h = h[h.k_act >= 1]
    m = h.merge(c[["ticker", "k", "bid_c", "ask_c"]], left_on=["ticker", "k_act"], right_on=["ticker", "k"])
    m["imp_ask"] = np.where(m.imp_yes, m.ask_c, 1 - m.bid_c)
    tr = m[(m.imp_ask > 0) & (m.imp_ask < 1)].copy()
    tr["pnl"] = np.where(tr.imp_yes, tr.win, ~tr.win).astype(float) - tr.imp_ask - fee(tr.imp_ask)
    print(f"causal lat={lat_min}min: markets with METAR in hand >=1 min before close: {len(m)} "
          f"({m.event_ticker.nunique()} events); with a buyable implied side (ask<1): {len(tr)}")
    if len(tr):
        tr["day"] = tr.S.astype(str).str[:10] if "S" in tr else tr.event_ticker.str[-9:-2]
        print(tr.groupby(pd.cut(tr.imp_ask, [0, .5, .9, .97, .99, 1])).pnl.agg(["size", "mean"]).round(4).to_string())
        print("mean c/contract", round(tr.pnl.mean() * 100, 2), "sum $ at 10 lots", round(tr.pnl.sum() * 10, 1),
              "events", tr.event_ticker.nunique())
    return tr


if __name__ == "__main__":
    for L in (2, 4, 6):
        causal(L)

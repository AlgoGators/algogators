"""Frozen copies of the series parsers and settlement reader replaced in step 5.5 (2026-10-15),
verbatim as of commit f80c4e9. Do not edit; the golden tests compare against these."""
from __future__ import annotations

import numpy as np
import pandas as pd


def lip_pilot_series_of(ticker: str) -> str:            # src/live/lip_pilot/selection.py:33
    return ticker.split("-", 1)[0]


def kalshi_flb_series_from_event(event_ticker: str) -> str:   # src/strategies_legal/kalshi_flb.py:67
    return str(event_ticker).split("-")[0]


def market_feature_watch_series_of(event_ticker: str | None) -> str:   # scripts/live/market_feature_watch.py:144
    return (event_ticker or "").split("-")[0]


def capbook_series_of(event_ticker: str) -> str:        # scripts/research/capbook_common.py:62
    return str(event_ticker).split("-")[0]


def lpland_series_of(m: pd.Series) -> pd.Series:        # scripts/research/lpland_landscape.py:29
    return m.astype(str).str.split("-", n=1).str[0]


def settle_load_markets_frame(d: pd.DataFrame) -> pd.DataFrame:   # scripts/research/settle_common.py:23 (after read_parquet)
    d["close_ts"] = pd.to_datetime(d.close_time, utc=True, format="ISO8601").map(lambda x: x.value // 10**9)
    st = pd.to_datetime(d.settlement_ts, utc=True, format="ISO8601", errors="coerce")
    d["settle_ts"] = st.map(lambda x: x.value // 10**9 if pd.notna(x) else np.nan)
    d["win"] = (d.result == "yes").astype(float)
    return d[d.result.isin(["yes", "no"])].copy()

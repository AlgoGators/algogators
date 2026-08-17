"""Shared helper for aligning several named series into one panel DataFrame."""

from __future__ import annotations

import pandas as pd


def series_panel(data: dict[str, pd.Series]) -> pd.DataFrame:
    """Combine any named series (prices, equity curves, ...) into one aligned DataFrame."""
    series = {name: s for name, s in data.items() if s is not None and not s.empty}
    if not series:
        return pd.DataFrame()
    panel = pd.concat(series, axis=1)
    panel.columns = list(series.keys())
    return panel.dropna(how="all")


def close_panel(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Combine per-symbol OHLCV frames into one DataFrame of aligned close prices."""
    return series_panel({sym: df["close"] for sym, df in data.items() if not df.empty})

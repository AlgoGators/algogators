"""Spread and ratio analysis between two series."""

from __future__ import annotations

import pandas as pd

from algoterminal.analytics.panel import series_panel


def spread_series(
    data: dict[str, pd.Series], sym_a: str, sym_b: str, hedge_ratio: float = 1.0
) -> pd.Series:
    """`sym_a - hedge_ratio * sym_b`, e.g. for pairs-trading spread analysis."""
    panel = series_panel(data).dropna()
    spread = panel[sym_a] - hedge_ratio * panel[sym_b]
    spread.name = f"{sym_a} - {hedge_ratio:g}x{sym_b}"
    return spread


def ratio_series(data: dict[str, pd.Series], sym_a: str, sym_b: str) -> pd.Series:
    """`sym_a / sym_b`, useful for cross-asset relative value (e.g. gold/silver)."""
    panel = series_panel(data).dropna()
    ratio = panel[sym_a] / panel[sym_b]
    ratio.name = f"{sym_a}/{sym_b}"
    return ratio


def zscore_spread(
    data: dict[str, pd.Series], sym_a: str, sym_b: str, window: int = 20, hedge_ratio: float = 1.0
) -> pd.Series:
    """Rolling-normalized spread — how many std devs the spread sits from its own \
    rolling mean, the standard pairs-trading entry/exit signal."""
    spread = spread_series(data, sym_a, sym_b, hedge_ratio)
    rolling_mean = spread.rolling(window).mean()
    rolling_std = spread.rolling(window).std()
    z = (spread - rolling_mean) / rolling_std
    z.name = f"Z-score spread: {sym_a} - {hedge_ratio:g}x{sym_b} ({window}d)"
    return z.dropna()

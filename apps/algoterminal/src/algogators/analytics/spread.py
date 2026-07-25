"""Spread and ratio analysis between two series."""

from __future__ import annotations

import pandas as pd

from algogators.analytics.panel import series_panel


def spread_series(data: dict[str, pd.Series], sym_a: str, sym_b: str, hedge_ratio: float = 1.0) -> pd.Series:
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

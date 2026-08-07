"""Relative performance: rebased cumulative return series for cross-asset comparison."""

from __future__ import annotations

import pandas as pd

from algoterminal.analytics.panel import series_panel


def relative_performance(data: dict[str, pd.Series], base: int = 100) -> pd.DataFrame:
    """Rebase each series to a common starting index (default 100) so series
    with different scales (prices, equity curves, ...) can be compared on one chart.
    """
    panel = series_panel(data).dropna()
    if panel.empty:
        return panel
    return panel / panel.iloc[0] * base

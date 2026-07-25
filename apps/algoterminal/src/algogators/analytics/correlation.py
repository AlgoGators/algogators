"""Correlation tools: static matrix and rolling pairwise correlation."""

from __future__ import annotations

import pandas as pd

from algogators.analytics.panel import series_panel


def correlation_matrix(data: dict[str, pd.Series], method: str = "pearson") -> pd.DataFrame:
    """Correlation matrix of daily returns across any combination of series."""
    panel = series_panel(data)
    returns = panel.pct_change().dropna(how="all")
    return returns.corr(method=method)


def rolling_correlation(data: dict[str, pd.Series], sym_a: str, sym_b: str, window: int = 60) -> pd.Series:
    """Rolling correlation of daily returns between two series."""
    panel = series_panel(data)
    returns = panel.pct_change()
    corr = returns[sym_a].rolling(window).corr(returns[sym_b])
    corr.name = f"{sym_a} vs {sym_b} ({window}d)"
    return corr.dropna()

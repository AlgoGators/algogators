"""Rolling beta: how sensitive one series' returns are to another's."""

from __future__ import annotations

import pandas as pd

from algoterminal.analytics.panel import series_panel


def rolling_beta(data: dict[str, pd.Series], sym_a: str, sym_b: str, window: int = 60) -> pd.Series:
    """Rolling OLS beta of `sym_a`'s daily returns regressed on `sym_b`'s — cov(a, b) / var(b)."""
    panel = series_panel(data)
    returns = panel.pct_change()
    cov = returns[sym_a].rolling(window).cov(returns[sym_b])
    var = returns[sym_b].rolling(window).var()
    beta = cov / var
    beta.name = f"{sym_a} beta vs {sym_b} ({window}d)"
    return beta.dropna()

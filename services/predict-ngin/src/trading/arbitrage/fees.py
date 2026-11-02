"""
Per-fill trading fee models for Kalshi and Polymarket.

Shim: the code lives in ``predict_ngin.costs`` (``models`` for the Kalshi fill fee and
``FeeModel``, ``polymarket`` for the Polymarket rates). Every old name is re-exported unchanged.

Kalshi (general fee schedule):
    taker fee per fill = ceil_to_cent(0.07   * C * P * (1 - P))
    maker fee per fill = ceil_to_cent(0.0175 * C * P * (1 - P))   (fee-bearing markets only)
where C = contracts in the fill and P = fill price in dollars (0-1).
Fees are charged on every fill regardless of the eventual outcome; there is
no "profit fee". Some series use different coefficients, so they are
configurable.

Polymarket (taker fee on fee-enabled markets):
    fee = C * rate * p * (1 - p)
with rate by category: crypto 0.07, sports 0.05, politics/finance/tech 0.04,
economics/culture/weather/other 0.05, geopolitics 0. Makers pay 0.

All parameters live on ``FeeModel`` so backtests can run sensitivities
(e.g. ``FeeModel(poly_fees_enabled=False)`` for historical zero-fee periods).
"""

from __future__ import annotations

from predict_ngin.costs.kalshi import ARB_EPS
from predict_ngin.costs.models import (
    KALSHI_MAKER_COEF,
    KALSHI_TAKER_COEF,
    FeeModel,
    kalshi_fee,
)
from predict_ngin.costs.polymarket import (
    POLY_CATEGORY_RATES,
    POLY_DEFAULT_RATE,
    polymarket_fee,
    polymarket_fee_rate,
)
from predict_ngin.shared.units import ceil_cents

__all__ = [
    "KALSHI_TAKER_COEF",
    "KALSHI_MAKER_COEF",
    "POLY_CATEGORY_RATES",
    "POLY_DEFAULT_RATE",
    "kalshi_fee",
    "polymarket_fee_rate",
    "polymarket_fee",
    "FeeModel",
]


def _ceil_cents(x: float) -> float:
    """Round a dollar amount up to the next cent, tolerant of float noise."""
    if x <= 0:
        return 0.0
    return ceil_cents(x, eps=ARB_EPS)

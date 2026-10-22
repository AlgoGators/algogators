"""Polymarket fees: taker fee ``C x rate x P x (1 - P)`` on fee-enabled markets; makers pay 0.

Rates by category (docs.polymarket.com/trading/fees, checked 2026-10-14): crypto 0.07, sports 0.05,
politics/finance/tech 0.04, economics/culture/weather/other 0.05, geopolitics 0.
"""

from __future__ import annotations

from typing import Dict, Optional

__all__ = [
    "POLY_CATEGORY_RATES",
    "POLY_DEFAULT_RATE",
    "polymarket_fee_rate",
    "polymarket_fee",
    "polymarket_fee_per_share",
]

POLY_CATEGORY_RATES: Dict[str, float] = {
    "crypto": 0.07,
    "sports": 0.05,
    "politics": 0.04,
    "finance": 0.04,
    "tech": 0.04,
    "technology": 0.04,
    "economics": 0.05,
    "economy": 0.05,
    "econ": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "geopolitics": 0.0,
}
POLY_DEFAULT_RATE = 0.05


def polymarket_fee_rate(
    category: Optional[str],
    rates: Optional[Dict[str, float]] = None,
    default_rate: float = POLY_DEFAULT_RATE,
) -> float:
    """Taker fee rate for a category (case-insensitive; unknown -> ``default_rate``)."""
    table = POLY_CATEGORY_RATES if rates is None else rates
    key = (category or "other").strip().lower()
    return float(table.get(key, default_rate))


def polymarket_fee(
    contracts: float,
    price: float,
    category: Optional[str] = None,
    maker: bool = False,
    rates: Optional[Dict[str, float]] = None,
    default_rate: float = POLY_DEFAULT_RATE,
) -> float:
    """Polymarket fee in dollars for one fill (no rounding; makers pay nothing)."""
    if maker or contracts <= 0:
        return 0.0
    p = min(max(float(price), 0.0), 1.0)
    return contracts * polymarket_fee_rate(category, rates, default_rate) * p * (1.0 - p)


def polymarket_fee_per_share(price, rate: float = POLY_DEFAULT_RATE):
    """Taker fee per share, ``rate x P x (1 - P)``. Works on scalars, arrays and Series."""
    return rate * price * (1.0 - price)

"""``FeeModel``: one configurable object holding the Kalshi and Polymarket fee parameters.

Used by the arbitrage backtests for sensitivities, e.g. ``FeeModel(poly_fees_enabled=False)`` for
the historical zero-fee periods. Its Kalshi round-up uses the arbitrage noise guard
(``ceil(100 x - 1e-9)``) and clips the price to [0, 1].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from predict_ngin.costs.kalshi import ARB_EPS, MAKER_RATE, TAKER_RATE
from predict_ngin.costs.polymarket import POLY_CATEGORY_RATES, POLY_DEFAULT_RATE, polymarket_fee
from predict_ngin.shared.units import ceil_cents

__all__ = ["KALSHI_TAKER_COEF", "KALSHI_MAKER_COEF", "kalshi_fee", "FeeModel"]

KALSHI_TAKER_COEF = TAKER_RATE
KALSHI_MAKER_COEF = MAKER_RATE


def kalshi_fee(
    contracts: float,
    price: float,
    maker: bool = False,
    taker_coef: float = KALSHI_TAKER_COEF,
    maker_coef: float = KALSHI_MAKER_COEF,
) -> float:
    """Kalshi fee in dollars for one fill (price clipped to [0, 1], ``ceil(100 x - 1e-9)``)."""
    if contracts <= 0:
        return 0.0
    p = min(max(float(price), 0.0), 1.0)
    coef = maker_coef if maker else taker_coef
    x = coef * contracts * p * (1.0 - p)
    if x <= 0:
        return 0.0
    return ceil_cents(x, eps=ARB_EPS)


@dataclass
class FeeModel:
    """Configurable fee model for both venues."""

    kalshi_taker_coef: float = KALSHI_TAKER_COEF
    kalshi_maker_coef: float = KALSHI_MAKER_COEF
    poly_fees_enabled: bool = True
    poly_rates: Dict[str, float] = field(default_factory=lambda: dict(POLY_CATEGORY_RATES))
    poly_default_rate: float = POLY_DEFAULT_RATE

    def kalshi(self, contracts: float, price: float, maker: bool = False) -> float:
        return kalshi_fee(contracts, price, maker, self.kalshi_taker_coef, self.kalshi_maker_coef)

    def poly(
        self, contracts: float, price: float, category: Optional[str] = None, maker: bool = False
    ) -> float:
        if not self.poly_fees_enabled:
            return 0.0
        return polymarket_fee(
            contracts, price, category, maker, self.poly_rates, self.poly_default_rate
        )

    def kalshi_per_contract(self, price: float, maker: bool = False) -> float:
        """Un-rounded per-contract Kalshi fee (for screening; real fills round up)."""
        coef = self.kalshi_maker_coef if maker else self.kalshi_taker_coef
        return coef * price * (1.0 - price)

    def poly_per_contract(
        self, price: float, category: Optional[str] = None, maker: bool = False
    ) -> float:
        return self.poly(1.0, price, category, maker)

    def describe(self) -> Dict[str, object]:
        return {
            "kalshi_taker_coef": self.kalshi_taker_coef,
            "kalshi_maker_coef": self.kalshi_maker_coef,
            "poly_fees_enabled": self.poly_fees_enabled,
            "poly_rates": dict(self.poly_rates),
            "poly_default_rate": self.poly_default_rate,
        }

"""Kalshi perpetual-futures fee tiers (fraction of notional per fill).

Verified 2026-10-15 via the authenticated ``GET /margin/fee_tiers``:

* crypto perps (KXBTCPERP, KXSOLPERP, KXXRPPERP, ...): taker 0.12%, maker 0.02%;
* metals, US500 and USTECH100 perps: taker 0.0072%, maker 0.0012%.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PerpsFeeTier", "CRYPTO_PERPS", "METALS_INDEX_PERPS", "PERPS_FEE_TIERS", "perps_fee"]


@dataclass(frozen=True)
class PerpsFeeTier:
    taker: float
    maker: float

    def rate(self, maker: bool = False) -> float:
        return self.maker if maker else self.taker


CRYPTO_PERPS = PerpsFeeTier(taker=0.0012, maker=0.0002)
METALS_INDEX_PERPS = PerpsFeeTier(taker=0.000072, maker=0.000012)
PERPS_FEE_TIERS = {"crypto": CRYPTO_PERPS, "metals_index": METALS_INDEX_PERPS}


def perps_fee(notional: float, tier: PerpsFeeTier = CRYPTO_PERPS, maker: bool = False) -> float:
    """Fee in dollars for a fill of ``notional`` dollars (no rounding)."""
    return notional * tier.rate(maker)

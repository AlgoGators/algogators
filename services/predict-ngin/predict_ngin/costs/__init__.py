"""Point-in-time fee schedules, fill models and impact models.

* :mod:`.kalshi` -- Kalshi rates, per-order cent round-up, maker-fee rule;
* :mod:`.kalshi_schedule` -- the point-in-time series fee schedule (fee type and multiplier at ts);
* :mod:`.polymarket` -- Polymarket category taker rates;
* :mod:`.perps` -- Kalshi perpetual-futures fee tiers;
* :mod:`.models` -- ``FeeModel``, the configurable two-venue model used by the arbitrage backtests.
"""

from predict_ngin.costs.kalshi import (
    DIRECT_PRECISION_TS,
    MAKER_FEE_START,
    MAKER_FEE_START_TS,
    MAKER_RATE,
    TAKER_RATE,
    ceil_cents,
    kalshi_fill_fee,
    kalshi_order_fee,
    maker_fee_applies,
)
from predict_ngin.costs.models import FeeModel
from predict_ngin.costs.perps import CRYPTO_PERPS, METALS_INDEX_PERPS, PerpsFeeTier, perps_fee
from predict_ngin.costs.polymarket import (
    POLY_CATEGORY_RATES,
    POLY_DEFAULT_RATE,
    polymarket_fee,
    polymarket_fee_per_share,
    polymarket_fee_rate,
)

__all__ = [
    "TAKER_RATE",
    "MAKER_RATE",
    "MAKER_FEE_START",
    "MAKER_FEE_START_TS",
    "DIRECT_PRECISION_TS",
    "ceil_cents",
    "kalshi_order_fee",
    "kalshi_fill_fee",
    "maker_fee_applies",
    "FeeModel",
    "PerpsFeeTier",
    "CRYPTO_PERPS",
    "METALS_INDEX_PERPS",
    "perps_fee",
    "POLY_CATEGORY_RATES",
    "POLY_DEFAULT_RATE",
    "polymarket_fee",
    "polymarket_fee_per_share",
    "polymarket_fee_rate",
]

"""
Cost model compatibility shim.

The core cost model lives in trading.data_modules.costs. This module
re-exports it so imports like `from trading.costs import CostModel` work.
"""

from .data_modules.costs import (  # noqa: F401
    CostAssumptions,
    CostModel,
    COST_ASSUMPTIONS,
    DEFAULT_COST_MODEL,
    POLYMARKET_ZERO_COST_MODEL,
)

__all__ = [
    "CostAssumptions",
    "CostModel",
    "COST_ASSUMPTIONS",
    "DEFAULT_COST_MODEL",
    "POLYMARKET_ZERO_COST_MODEL",
]

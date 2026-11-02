"""Max-loss accounting with collateral-return netting, and cap enforcement.

For one binary market, with gross fills since start Y yes-contracts (cost Cy) and N no-contracts (cost Cn),
and resting buy orders r_y @ p_y (YES) and r_n @ p_n (NO):

  loss if YES settles = Cy + Cn - Y + (worst fills of resting orders: NO fills fully, YES fills none)
                      = Cy + Cn - Y + r_n * p_n
  loss if NO settles  = Cy + Cn - N + r_y * p_y
  max loss            = max(0, both of the above) + maker fees on everything that can still fill.

Netting (YES + NO held together pay $1 at once) changes cash timing, not this bound. A fill on BOTH sides
can only lower the loss because p_y + p_n < 1 for bids behind the touch. Fees on gas series are 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class MarketExposure:
    yes_filled: float = 0.0
    yes_cost: float = 0.0
    no_filled: float = 0.0
    no_cost: float = 0.0
    yes_resting: float = 0.0
    yes_px: float = 0.0
    no_resting: float = 0.0
    no_px: float = 0.0
    fees_paid: float = 0.0

    def max_loss(self, fee_per_contract: float = 0.0) -> float:
        base = self.yes_cost + self.no_cost + self.fees_paid
        fees_pending = fee_per_contract * (self.yes_resting + self.no_resting)
        loss_yes = base - self.yes_filled + self.no_resting * self.no_px
        loss_no = base - self.no_filled + self.yes_resting * self.yes_px
        return max(0.0, loss_yes, loss_no) + fees_pending


def total_max_loss(exposures: Iterable[MarketExposure], fee_per_contract: float = 0.0) -> float:
    """Upper bound across markets: assumes every market can lose its worst case together (conservative;
    strikes of one state are nested, not independent)."""
    return sum(e.max_loss(fee_per_contract) for e in exposures)


def max_side_size(exp: MarketExposure, outcome: str, px: float, per_market_cap: float,
                  room_total: float, fee_per_contract: float = 0.0, upper: int = 100000) -> int:
    """Largest resting size n for ``outcome`` at ``px`` (replacing that side's current resting order) such
    that the market's max loss <= ``per_market_cap`` and <= ``room_total`` (the total cap minus every other
    market's max loss). Exact: max loss is monotone non-decreasing in n, so binary-search it."""
    if px <= 0:
        return 0
    limit = min(per_market_cap, room_total)

    def loss(n: int) -> float:
        e = MarketExposure(**vars(exp))
        if outcome == "yes":
            e.yes_resting, e.yes_px = float(n), px
        else:
            e.no_resting, e.no_px = float(n), px
        return e.max_loss(fee_per_contract)

    if loss(0) > limit + 1e-9:
        return 0
    lo, hi = 0, upper
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if loss(mid) <= limit + 1e-9:
            lo = mid
        else:
            hi = mid - 1
    return lo

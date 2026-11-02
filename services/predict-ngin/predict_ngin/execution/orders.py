"""Venue-neutral order types. Prices are dollars per contract of the stated outcome (0 < p < 1)."""

from __future__ import annotations

import itertools
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

Outcome = Literal["yes", "no"]
Action = Literal["buy", "sell"]


@dataclass(frozen=True)
class OrderIntent:
    venue: str  # "kalshi" | "polymarket_us"
    market: str  # Kalshi ticker or Polymarket US market slug
    outcome: Outcome
    price: float  # limit price of `outcome`, dollars
    quantity: float  # contracts
    action: Action = "buy"
    post_only: bool = True
    client_order_id: str = ""
    expiration_ts: Optional[int] = None  # unix seconds; None = good till cancelled
    # Kalshi: good_till_canceled | immediate_or_cancel | fill_or_kill
    time_in_force: str = "good_till_canceled"

    def notional(self) -> float:
        """Dollars at risk if filled (cost of a buy; for a sell, the other outcome's cost)."""
        px = self.price if self.action == "buy" else 1.0 - self.price
        return float(self.quantity) * px

    def validate(self) -> None:
        if self.outcome not in ("yes", "no") or self.action not in ("buy", "sell"):
            raise ValueError("outcome must be yes|no and action buy|sell")
        if not (0.0 < self.price < 1.0):
            raise ValueError(f"price {self.price} outside (0, 1)")
        if not self.quantity > 0:
            raise ValueError("quantity must be positive")
        if not self.market:
            raise ValueError("market is required")


@dataclass
class OrderAck:
    venue: str
    market: str
    client_order_id: str
    order_id: Optional[str]
    # "dry_run" | "paper" | "resting" | "filled" | "partially_filled" | "rejected" | "cancelled"
    status: str
    filled: float = 0.0
    remaining: float = 0.0
    latency_ms: Optional[float] = None
    raw: dict = field(default_factory=dict)


_seq = itertools.count(1)
_run = uuid.uuid4().hex[:8]


def new_client_order_id(prefix: str = "pn") -> str:
    """Unique per process and intent; a resend after a network error lets the venue dedupe."""
    return f"{prefix}-{_run}-{int(time.time())}-{next(_seq):05d}"

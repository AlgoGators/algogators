"""Hard risk limits, checked before every order (paper and live alike).

Defaults are deliberately tiny; raise them in ``config/live.local.yaml`` under ``limits:``. A
breach raises :class:`LimitBreached` and the order is never sent.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, fields
from typing import Dict, List, Optional

from .orders import OrderIntent


class LimitBreached(RuntimeError):
    pass


@dataclass
class RiskLimits:
    max_order_usd: float = 5.0  # notional of one order
    max_daily_usd: float = (
        25.0  # total notional placed per UTC day (sum of orders sent, filled or not)
    )
    max_market_usd: float = 10.0  # resting + filled notional per market
    max_open_orders: int = 5
    max_contracts: float = 100.0
    min_price: float = 0.01
    max_price: float = 0.99
    post_only_only: bool = True  # refuse orders that may take liquidity
    allow_sell: bool = False  # selling = shorting an outcome; off until a strategy needs it
    markets: List[str] = field(default_factory=list)  # optional allowlist (empty = any market)

    @classmethod
    def from_config(cls, cfg: Optional[dict]) -> "RiskLimits":
        cfg = cfg or {}
        known = {f.name for f in fields(cls)}
        unknown = set(cfg) - known
        if unknown:
            raise ValueError(f"unknown risk limit keys {sorted(unknown)}")
        return cls(**cfg)


@dataclass
class LimitState:
    """What the limits are checked against; brokers keep it current."""

    day: str = ""
    placed_today_usd: float = 0.0
    open_orders: int = 0
    market_usd: Dict[str, float] = field(default_factory=dict)

    def roll(self) -> None:
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.placed_today_usd = today, 0.0


def check(intent: OrderIntent, limits: RiskLimits, state: LimitState) -> None:
    """Raise :class:`LimitBreached` naming every limit the order would break."""
    intent.validate()
    state.roll()
    n = intent.notional()
    bad = []
    if limits.post_only_only and not intent.post_only:
        bad.append("post_only_only: taker orders are disabled")
    if intent.action == "sell" and not limits.allow_sell:
        bad.append("allow_sell is false")
    if not (limits.min_price <= intent.price <= limits.max_price):
        bad.append(f"price {intent.price} outside [{limits.min_price}, {limits.max_price}]")
    if intent.quantity > limits.max_contracts:
        bad.append(f"quantity {intent.quantity} > max_contracts {limits.max_contracts}")
    if n > limits.max_order_usd:
        bad.append(f"order ${n:.2f} > max_order_usd {limits.max_order_usd}")
    if state.placed_today_usd + n > limits.max_daily_usd:
        bad.append(
            f"daily ${state.placed_today_usd + n:.2f} > max_daily_usd {limits.max_daily_usd}"
        )
    if state.market_usd.get(intent.market, 0.0) + n > limits.max_market_usd:
        bad.append(f"market ${state.market_usd.get(intent.market, 0.0) + n:.2f} > max_market_usd")
    if state.open_orders + 1 > limits.max_open_orders:
        bad.append(f"open orders would exceed max_open_orders {limits.max_open_orders}")
    if limits.markets and intent.market not in limits.markets:
        bad.append(f"market {intent.market} not in the allowlist")
    if bad:
        raise LimitBreached("; ".join(bad))


def record_placed(intent: OrderIntent, state: LimitState) -> None:
    n = intent.notional()
    state.placed_today_usd += n
    state.open_orders += 1
    state.market_usd[intent.market] = state.market_usd.get(intent.market, 0.0) + n


def record_closed(intent: OrderIntent, state: LimitState, unfilled_qty: float) -> None:
    """An order left the book: release its open-order slot and its unfilled market notional."""
    state.open_orders = max(0, state.open_orders - 1)
    px = intent.price if intent.action == "buy" else 1.0 - intent.price
    state.market_usd[intent.market] = max(
        0.0, state.market_usd.get(intent.market, 0.0) - unfilled_qty * px
    )

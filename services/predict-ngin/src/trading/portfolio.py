"""
Portfolio sizing and risk controls for backtests and paper trading.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Set

import pandas as pd


@dataclass
class PortfolioConstraints:
    """Risk constraints and market filters for portfolio management."""

    max_concurrent_positions: Optional[int] = None
    max_total_capital: Optional[float] = None
    max_capital_per_market: Optional[float] = None
    max_exposure_per_category: Optional[float] = None
    min_liquidity: float = 0.0
    min_volume: float = 0.0
    allowed_categories: Optional[Set[str]] = None
    blocked_categories: Optional[Set[str]] = None

    def market_allowed(self, category: str, liquidity: float, volume: float) -> bool:
        """Return True if a market passes liquidity/volume/category filters."""
        if liquidity < self.min_liquidity:
            return False
        if volume < self.min_volume:
            return False
        if self.allowed_categories and category not in self.allowed_categories:
            return False
        if self.blocked_categories and category in self.blocked_categories:
            return False
        return True


@dataclass
class PositionSizer:
    """Position sizing rules for a single trade."""

    base_position_size: float = 100.0
    min_position_size: float = 1.0
    max_position_size: Optional[float] = None
    max_pct_liquidity: Optional[float] = None

    def size_for_market(
        self,
        liquidity: float,
        capital_available: Optional[float] = None,
        cap_per_market: Optional[float] = None,
    ) -> float:
        """Return the position size after applying all caps."""
        size = float(self.base_position_size)

        if self.max_pct_liquidity is not None and liquidity > 0:
            size = min(size, liquidity * self.max_pct_liquidity)

        if self.max_position_size is not None:
            size = min(size, self.max_position_size)

        if cap_per_market is not None:
            size = min(size, cap_per_market)

        if capital_available is not None:
            size = min(size, capital_available)

        if size < self.min_position_size:
            return 0.0

        return size


@dataclass
class OpenPosition:
    """Tracks a single open position for portfolio accounting."""

    contract_id: str
    category: str
    size: float
    exit_time: pd.Timestamp


class PortfolioState:
    """Tracks open positions and enforces portfolio constraints."""

    def __init__(
        self,
        constraints: PortfolioConstraints,
        total_capital: Optional[float] = None,
    ):
        self.constraints = constraints
        self.total_capital = total_capital
        self.open_positions: Dict[str, OpenPosition] = {}
        self.capital_in_use = 0.0
        self.exposure_by_category: Dict[str, float] = {}

    def close_positions_through(self, as_of: pd.Timestamp) -> None:
        """Close all positions with exit_time <= as_of."""
        if not isinstance(as_of, pd.Timestamp):
            as_of = pd.to_datetime(as_of)

        to_close = [
            contract_id
            for contract_id, pos in self.open_positions.items()
            if pos.exit_time <= as_of
        ]

        for contract_id in to_close:
            pos = self.open_positions.pop(contract_id)
            self.capital_in_use -= pos.size

            if pos.category:
                current = self.exposure_by_category.get(pos.category, 0.0)
                new_value = max(current - pos.size, 0.0)
                if new_value == 0.0:
                    self.exposure_by_category.pop(pos.category, None)
                else:
                    self.exposure_by_category[pos.category] = new_value

    def available_capital(self) -> Optional[float]:
        """Return available capital after accounting for open positions."""
        limit = self.constraints.max_total_capital
        if limit is None:
            limit = self.total_capital
        if limit is None:
            return None
        return max(limit - self.capital_in_use, 0.0)

    def can_open(self, category: str, size: float) -> bool:
        """Check if a new position can be opened within constraints."""
        if self.constraints.max_concurrent_positions is not None:
            if len(self.open_positions) >= self.constraints.max_concurrent_positions:
                return False

        if self.constraints.max_capital_per_market is not None:
            if size > self.constraints.max_capital_per_market:
                return False

        capital_available = self.available_capital()
        if capital_available is not None and size > capital_available:
            return False

        if self.constraints.max_exposure_per_category is not None:
            current = self.exposure_by_category.get(category, 0.0)
            if current + size > self.constraints.max_exposure_per_category:
                return False

        return True

    def open_position(
        self,
        contract_id: str,
        category: str,
        size: float,
        exit_time: pd.Timestamp,
    ) -> None:
        """Register a new open position."""
        if contract_id in self.open_positions:
            return

        if not isinstance(exit_time, pd.Timestamp):
            exit_time = pd.to_datetime(exit_time)

        self.open_positions[contract_id] = OpenPosition(
            contract_id=contract_id,
            category=category,
            size=size,
            exit_time=exit_time,
        )
        self.capital_in_use += size

        if category:
            self.exposure_by_category[category] = (
                self.exposure_by_category.get(category, 0.0) + size
            )

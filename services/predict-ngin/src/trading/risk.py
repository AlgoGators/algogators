"""
Risk management layer.

Provides position sizing, exposure limits, drawdown controls,
and risk checks for trading strategies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Iterable, Type, Union
from enum import Enum
import numpy as np


class RiskAction(Enum):
    """Actions the risk manager can take."""

    ALLOW = "allow"
    REDUCE_SIZE = "reduce_size"
    REJECT = "reject"
    CLOSE_POSITION = "close_position"
    HALT_TRADING = "halt_trading"


@dataclass
class RiskCheckResult:
    """Result of a risk check."""

    action: RiskAction
    reason: str = ""
    adjusted_size: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.action in (RiskAction.ALLOW, RiskAction.REDUCE_SIZE)


@dataclass
class RiskLimits:
    """Risk limit configuration."""

    # Position limits
    max_position_size: float = 1000.0
    min_position_size: float = 10.0
    max_positions_per_market: int = 1
    max_total_positions: int = 100
    max_positions_per_category: int = 20

    # Exposure limits
    max_total_exposure: float = 50000.0
    max_exposure_per_market: float = 5000.0
    max_exposure_per_category: float = 15000.0
    max_exposure_pct_of_capital: float = 0.8  # 80% max

    # Concentration limits
    max_single_position_pct: float = 0.10  # 10% of capital
    max_category_pct: float = 0.30  # 30% per category
    max_correlated_exposure: float = 0.50  # 50% in correlated positions

    # Drawdown limits
    max_daily_drawdown_pct: float = 0.05  # 5% daily
    max_weekly_drawdown_pct: float = 0.10  # 10% weekly
    max_total_drawdown_pct: float = 0.20  # 20% total
    drawdown_halt_pct: float = 0.25  # Halt at 25%

    # Time limits
    max_holding_days: int = 90
    min_time_between_trades_seconds: int = 60

    # Market quality filters
    min_liquidity: float = 500.0
    min_volume_24h: float = 100.0
    max_spread_pct: float = 0.10  # 10% max spread

    # Volatility limits
    max_position_volatility: float = 0.30  # 30% daily vol
    vol_scaling_enabled: bool = True
    target_portfolio_volatility: float = 0.15  # 15% target


@dataclass
class PositionState:
    """Current state of a position."""

    market_id: str
    category: str
    outcome: str
    side: str  # BUY or SELL
    entry_time: datetime
    entry_price: float
    size: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    high_water_mark: float = 0.0
    max_drawdown: float = 0.0

    def update_price(self, price: float):
        """Update current price and track drawdown."""
        self.current_price = price
        direction = 1 if self.side == "BUY" else -1
        self.unrealized_pnl = direction * (price - self.entry_price) * self.size

        if self.unrealized_pnl > self.high_water_mark:
            self.high_water_mark = self.unrealized_pnl

        current_dd = self.high_water_mark - self.unrealized_pnl
        if current_dd > self.max_drawdown:
            self.max_drawdown = current_dd


@dataclass
class PortfolioRiskState:
    """Current risk state of the portfolio."""

    capital: float
    cash: float
    total_exposure: float = 0.0
    positions: Dict[str, PositionState] = field(default_factory=dict)
    exposure_by_category: Dict[str, float] = field(default_factory=dict)
    exposure_by_market: Dict[str, float] = field(default_factory=dict)

    # P&L tracking
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    high_water_mark: float = 0.0
    max_drawdown: float = 0.0

    # Daily tracking
    daily_pnl: float = 0.0
    daily_high: float = 0.0
    daily_drawdown: float = 0.0
    last_reset_date: Optional[datetime] = None

    # Trade tracking
    last_trade_time: Optional[datetime] = None
    trades_today: int = 0
    halted: bool = False
    halt_reason: str = ""

    def reset_daily(self, date: datetime):
        """Reset daily tracking."""
        self.daily_pnl = 0.0
        self.daily_high = 0.0
        self.daily_drawdown = 0.0
        self.last_reset_date = date
        self.trades_today = 0

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def equity(self) -> float:
        return self.capital + self.total_pnl

    @property
    def drawdown_pct(self) -> float:
        if self.high_water_mark <= 0:
            return 0.0
        return self.max_drawdown / self.high_water_mark


class RiskModule(ABC):
    """Abstract base class for risk modules."""

    @abstractmethod
    def check(
        self,
        signal: Dict[str, Any],
        state: PortfolioRiskState,
        limits: RiskLimits,
    ) -> RiskCheckResult:
        """Check if a trade passes this risk module."""

    @abstractmethod
    def name(self) -> str:
        """Module name for logging."""


class PositionLimitModule(RiskModule):
    """Check position count and size limits."""

    def name(self) -> str:
        return "position_limits"

    def check(
        self,
        signal: Dict[str, Any],
        state: PortfolioRiskState,
        limits: RiskLimits,
    ) -> RiskCheckResult:
        market_id = signal.get("market_id")
        category = signal.get("category", "unknown")
        size = signal.get("size", limits.max_position_size)

        # Check total positions
        if len(state.positions) >= limits.max_total_positions:
            return RiskCheckResult(
                action=RiskAction.REJECT,
                reason=f"Max positions reached ({limits.max_total_positions})",
            )

        # Check positions per market
        market_positions = sum(
            1 for p in state.positions.values()
            if p.market_id == market_id
        )
        if market_positions >= limits.max_positions_per_market:
            return RiskCheckResult(
                action=RiskAction.REJECT,
                reason=f"Max positions per market reached ({limits.max_positions_per_market})",
            )

        # Check positions per category
        category_positions = sum(
            1 for p in state.positions.values()
            if p.category == category
        )
        if category_positions >= limits.max_positions_per_category:
            return RiskCheckResult(
                action=RiskAction.REJECT,
                reason=f"Max positions in {category} reached ({limits.max_positions_per_category})",
            )

        # Check and adjust size
        if size > limits.max_position_size:
            return RiskCheckResult(
                action=RiskAction.REDUCE_SIZE,
                reason=f"Size reduced to max ({limits.max_position_size})",
                adjusted_size=limits.max_position_size,
            )

        if size < limits.min_position_size:
            return RiskCheckResult(
                action=RiskAction.REJECT,
                reason=f"Size below minimum ({limits.min_position_size})",
            )

        return RiskCheckResult(action=RiskAction.ALLOW)


class ExposureLimitModule(RiskModule):
    """Check exposure limits."""

    def name(self) -> str:
        return "exposure_limits"

    def check(
        self,
        signal: Dict[str, Any],
        state: PortfolioRiskState,
        limits: RiskLimits,
    ) -> RiskCheckResult:
        market_id = signal.get("market_id")
        category = signal.get("category", "unknown")
        size = signal.get("size", limits.max_position_size)

        # Check total exposure
        new_total = state.total_exposure + size
        if new_total > limits.max_total_exposure:
            available = limits.max_total_exposure - state.total_exposure
            if available < limits.min_position_size:
                return RiskCheckResult(
                    action=RiskAction.REJECT,
                    reason=f"Max total exposure reached (${limits.max_total_exposure:,.0f})",
                )
            return RiskCheckResult(
                action=RiskAction.REDUCE_SIZE,
                reason="Size reduced to fit exposure limit",
                adjusted_size=available,
            )

        # Check exposure as % of capital
        max_by_capital = state.capital * limits.max_exposure_pct_of_capital
        if new_total > max_by_capital:
            available = max_by_capital - state.total_exposure
            if available < limits.min_position_size:
                return RiskCheckResult(
                    action=RiskAction.REJECT,
                    reason=(
                        "Max exposure % reached "
                        f"({limits.max_exposure_pct_of_capital*100:.0f}%)"
                    ),
                )
            return RiskCheckResult(
                action=RiskAction.REDUCE_SIZE,
                adjusted_size=available,
            )

        # Check per-market exposure
        market_exposure = state.exposure_by_market.get(market_id, 0)
        if market_exposure + size > limits.max_exposure_per_market:
            available = limits.max_exposure_per_market - market_exposure
            if available < limits.min_position_size:
                return RiskCheckResult(
                    action=RiskAction.REJECT,
                    reason="Max exposure per market reached",
                )
            return RiskCheckResult(
                action=RiskAction.REDUCE_SIZE,
                adjusted_size=available,
            )

        # Check per-category exposure
        cat_exposure = state.exposure_by_category.get(category, 0)
        if cat_exposure + size > limits.max_exposure_per_category:
            available = limits.max_exposure_per_category - cat_exposure
            if available < limits.min_position_size:
                return RiskCheckResult(
                    action=RiskAction.REJECT,
                    reason=f"Max exposure in {category} reached",
                )
            return RiskCheckResult(
                action=RiskAction.REDUCE_SIZE,
                adjusted_size=available,
            )

        return RiskCheckResult(action=RiskAction.ALLOW)


class DrawdownModule(RiskModule):
    """Check drawdown limits."""

    def name(self) -> str:
        return "drawdown"

    def check(
        self,
        signal: Dict[str, Any],
        state: PortfolioRiskState,
        limits: RiskLimits,
    ) -> RiskCheckResult:
        # Check if trading is halted
        if state.halted:
            return RiskCheckResult(
                action=RiskAction.HALT_TRADING,
                reason=state.halt_reason,
            )

        # Check total drawdown
        if state.drawdown_pct >= limits.drawdown_halt_pct:
            return RiskCheckResult(
                action=RiskAction.HALT_TRADING,
                reason=f"Drawdown halt triggered ({state.drawdown_pct*100:.1f}%)",
            )

        if state.drawdown_pct >= limits.max_total_drawdown_pct:
            return RiskCheckResult(
                action=RiskAction.REJECT,
                reason=f"Max drawdown reached ({state.drawdown_pct*100:.1f}%)",
            )

        # Check daily drawdown
        if state.daily_drawdown > 0:
            daily_dd_pct = state.daily_drawdown / state.capital
            if daily_dd_pct >= limits.max_daily_drawdown_pct:
                return RiskCheckResult(
                    action=RiskAction.REJECT,
                    reason=f"Daily drawdown limit reached ({daily_dd_pct*100:.1f}%)",
                )

        return RiskCheckResult(action=RiskAction.ALLOW)


class MarketQualityModule(RiskModule):
    """Check market quality filters."""

    def name(self) -> str:
        return "market_quality"

    def check(
        self,
        signal: Dict[str, Any],
        state: PortfolioRiskState,
        limits: RiskLimits,
    ) -> RiskCheckResult:
        liquidity = signal.get("liquidity", 0)
        volume_24h = signal.get("volume_24h", 0)
        spread = signal.get("spread", 0)

        if liquidity < limits.min_liquidity:
            return RiskCheckResult(
                action=RiskAction.REJECT,
                reason=(
                    f"Liquidity too low (${liquidity:,.0f} < "
                    f"${limits.min_liquidity:,.0f})"
                ),
            )

        if volume_24h < limits.min_volume_24h:
            return RiskCheckResult(
                action=RiskAction.REJECT,
                reason=f"Volume too low (${volume_24h:,.0f})",
            )

        if spread > limits.max_spread_pct:
            return RiskCheckResult(
                action=RiskAction.REJECT,
                reason=f"Spread too wide ({spread*100:.1f}%)",
            )

        return RiskCheckResult(action=RiskAction.ALLOW)


class VolatilitySizingModule(RiskModule):
    """Adjust position size based on volatility."""

    def name(self) -> str:
        return "volatility_sizing"

    def check(
        self,
        signal: Dict[str, Any],
        state: PortfolioRiskState,
        limits: RiskLimits,
    ) -> RiskCheckResult:
        if not limits.vol_scaling_enabled:
            return RiskCheckResult(action=RiskAction.ALLOW)

        volatility = signal.get("volatility", 0.20)  # Default 20%
        size = signal.get("size", limits.max_position_size)

        # Reject if volatility too high
        if volatility > limits.max_position_volatility:
            return RiskCheckResult(
                action=RiskAction.REJECT,
                reason=f"Volatility too high ({volatility*100:.1f}%)",
            )

        # Scale size inversely with volatility
        # Target: position_vol * size = target_portfolio_vol * capital / sqrt(n_positions)
        target_vol = limits.target_portfolio_volatility
        n_positions = max(len(state.positions), 1)

        # Vol-adjusted size
        if volatility > 0:
            vol_adjusted = (target_vol * state.capital) / (volatility * np.sqrt(n_positions))
            vol_adjusted = min(vol_adjusted, limits.max_position_size)

            if vol_adjusted < size:
                return RiskCheckResult(
                    action=RiskAction.REDUCE_SIZE,
                    reason=f"Vol-adjusted sizing ({volatility*100:.0f}% vol)",
                    adjusted_size=vol_adjusted,
                )

        return RiskCheckResult(action=RiskAction.ALLOW)


DEFAULT_RISK_MODULE_ORDER = (
    "drawdown",
    "position_limits",
    "exposure_limits",
    "market_quality",
    "volatility_sizing",
)

RISK_MODULE_REGISTRY: Dict[str, Type[RiskModule]] = {
    "drawdown": DrawdownModule,
    "position_limits": PositionLimitModule,
    "exposure_limits": ExposureLimitModule,
    "market_quality": MarketQualityModule,
    "volatility_sizing": VolatilitySizingModule,
}


def list_risk_modules() -> List[str]:
    """Return available risk module names in default order."""
    return list(DEFAULT_RISK_MODULE_ORDER)


def load_risk_modules(
    modules: Optional[Iterable[Union[str, RiskModule, Type[RiskModule]]]] = None,
) -> List[RiskModule]:
    """
    Instantiate risk modules by name, class, or instance.

    Args:
        modules: Optional list of module names, classes, or instances.

    Returns:
        List of RiskModule instances.
    """
    if modules is None:
        return [RISK_MODULE_REGISTRY[name]() for name in DEFAULT_RISK_MODULE_ORDER]

    loaded: List[RiskModule] = []
    for module in modules:
        if isinstance(module, RiskModule):
            loaded.append(module)
            continue
        if isinstance(module, str):
            cls = RISK_MODULE_REGISTRY.get(module)
            if cls is None:
                raise ValueError(f"Unknown risk module: {module}")
            loaded.append(cls())
            continue
        if isinstance(module, type) and issubclass(module, RiskModule):
            loaded.append(module())
            continue
        raise ValueError(f"Unsupported risk module: {module}")

    return loaded


class RiskManager:
    """
    Central risk management system.

    Runs all risk modules and aggregates results.
    """

    def __init__(
        self,
        limits: Optional[RiskLimits] = None,
        modules: Optional[List[RiskModule]] = None,
    ):
        self.limits = limits or RiskLimits()
        self.modules = modules or load_risk_modules()
        self.state = PortfolioRiskState(
            capital=0,
            cash=0,
        )

    def initialize(self, capital: float):
        """Initialize with starting capital."""
        self.state = PortfolioRiskState(
            capital=capital,
            cash=capital,
            high_water_mark=capital,
        )

    def check_signal(self, signal: Dict[str, Any]) -> RiskCheckResult:
        """
        Run all risk checks on a signal.

        Returns the most restrictive result.
        """
        if self.state.halted:
            return RiskCheckResult(
                action=RiskAction.HALT_TRADING,
                reason=self.state.halt_reason,
            )

        adjusted_size = signal.get("size", self.limits.max_position_size)

        for module in self.modules:
            result = module.check(signal, self.state, self.limits)

            if result.action == RiskAction.HALT_TRADING:
                self.state.halted = True
                self.state.halt_reason = result.reason
                return result

            if result.action == RiskAction.REJECT:
                return result

            if result.action == RiskAction.REDUCE_SIZE:
                if result.adjusted_size is not None:
                    adjusted_size = min(adjusted_size, result.adjusted_size)

        # Return with final adjusted size
        if adjusted_size != signal.get("size"):
            return RiskCheckResult(
                action=RiskAction.REDUCE_SIZE,
                adjusted_size=adjusted_size,
            )

        return RiskCheckResult(action=RiskAction.ALLOW)

    def record_trade(
        self,
        market_id: str,
        category: str,
        outcome: str,
        side: str,
        size: float,
        price: float,
        timestamp: datetime,
    ):
        """Record a new position."""
        position_id = f"{market_id}_{outcome}_{timestamp.timestamp()}"

        self.state.positions[position_id] = PositionState(
            market_id=market_id,
            category=category,
            outcome=outcome,
            side=side,
            entry_time=timestamp,
            entry_price=price,
            size=size,
            current_price=price,
        )

        # Update exposure
        self.state.total_exposure += size
        self.state.exposure_by_market[market_id] = (
            self.state.exposure_by_market.get(market_id, 0) + size
        )
        self.state.exposure_by_category[category] = (
            self.state.exposure_by_category.get(category, 0) + size
        )

        self.state.cash -= size
        self.state.last_trade_time = timestamp
        self.state.trades_today += 1

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        timestamp: datetime,
    ) -> float:
        """Close a position and return realized P&L."""
        if position_id not in self.state.positions:
            return 0.0

        pos = self.state.positions.pop(position_id)

        # Calculate P&L
        direction = 1 if pos.side == "BUY" else -1
        pnl = direction * (exit_price - pos.entry_price) * pos.size

        # Update state
        self.state.realized_pnl += pnl
        self.state.total_exposure -= pos.size
        self.state.exposure_by_market[pos.market_id] -= pos.size
        self.state.exposure_by_category[pos.category] -= pos.size
        self.state.cash += pos.size + pnl

        # Update drawdown tracking
        self._update_drawdown()

        return pnl

    def update_prices(self, prices: Dict[str, float]):
        """Update position prices and recalculate unrealized P&L."""
        total_unrealized = 0.0

        for pos in self.state.positions.values():
            key = f"{pos.market_id}_{pos.outcome}"
            if key in prices:
                pos.update_price(prices[key])
            total_unrealized += pos.unrealized_pnl

        self.state.unrealized_pnl = total_unrealized
        self._update_drawdown()

    def _update_drawdown(self):
        """Update drawdown tracking."""
        equity = self.state.equity

        if equity > self.state.high_water_mark:
            self.state.high_water_mark = equity

        self.state.max_drawdown = self.state.high_water_mark - equity

        # Daily tracking
        if equity > self.state.daily_high:
            self.state.daily_high = equity
        self.state.daily_drawdown = self.state.daily_high - equity

    def reset_daily(self, date: datetime):
        """Reset daily tracking (call at start of each day)."""
        self.state.reset_daily(date)

    def get_risk_report(self) -> Dict[str, Any]:
        """Get current risk metrics."""
        return {
            "capital": self.state.capital,
            "equity": self.state.equity,
            "cash": self.state.cash,
            "total_exposure": self.state.total_exposure,
            "exposure_pct": (
                self.state.total_exposure / self.state.capital
                if self.state.capital > 0
                else 0
            ),
            "positions": len(self.state.positions),
            "realized_pnl": self.state.realized_pnl,
            "unrealized_pnl": self.state.unrealized_pnl,
            "total_pnl": self.state.total_pnl,
            "drawdown": self.state.max_drawdown,
            "drawdown_pct": self.state.drawdown_pct,
            "daily_drawdown": self.state.daily_drawdown,
            "halted": self.state.halted,
            "halt_reason": self.state.halt_reason,
        }

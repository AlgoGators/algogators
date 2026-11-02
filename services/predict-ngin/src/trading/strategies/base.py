"""
Base strategy types for the trading system.
"""

from __future__ import annotations

from ..strategy import (
    Strategy,
    StrategyConfig,
    StrategyManager,
    Signal,
    SignalType,
)


class BaseStrategy(Strategy):
    """Base class for trading strategies."""


__all__ = [
    "BaseStrategy",
    "Strategy",
    "StrategyConfig",
    "StrategyManager",
    "Signal",
    "SignalType",
]

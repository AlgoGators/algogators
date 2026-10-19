"""
Backtest engine for strategy evaluation.

Re-exports from existing modules for backward compatibility.
"""

# Re-export from existing trading module
# TradingEngine is the main backtest class in trading.engine
from trading.engine import TradingEngine, BacktestResult as TradingBacktestResult

# Alias for backward compatibility
BacktestEngine = TradingEngine

# Re-export from whale_strategy
from whale_strategy.backtest import run_backtest, print_result

# Use TradingBacktestResult as the main BacktestResult
BacktestResult = TradingBacktestResult

__all__ = [
    "BacktestEngine",
    "TradingEngine",
    "BacktestResult",
    "run_backtest",
    "print_result",
]

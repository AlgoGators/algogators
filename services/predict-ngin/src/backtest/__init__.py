"""
Backtest module for strategy evaluation.

Provides backtesting engines and validation tools.
"""

from .engine import BacktestEngine, TradingEngine, BacktestResult
from .costs import CostModel, COST_ASSUMPTIONS
from .walk_forward import WalkForwardValidator, WalkForwardResult, WalkForwardWindow
from .storage import (
    generate_run_id,
    BacktestMetadata,
    save_backtest_result,
    load_backtest_result,
)
from .catalog import BacktestCatalog, BacktestRecord
from .comparison import ComparisonReport, compare_backtests
from .simulation import (
    MonteCarloSimulator,
    MonteCarloResult,
    SimulationResult,
    StressTester,
    StressTestResult,
    DrawdownSimulator,
    DrawdownSimulationResult,
    CorrelationBreakdownTester,
    CorrelationBreakdownResult,
)

# Re-export portfolio for convenience
from trading.portfolio import (
    PortfolioConstraints,
    PortfolioState,
    PositionSizer,
)

__all__ = [
    "BacktestEngine",
    "TradingEngine",
    "BacktestResult",
    "CostModel",
    "COST_ASSUMPTIONS",
    "WalkForwardValidator",
    "WalkForwardResult",
    "WalkForwardWindow",
    "generate_run_id",
    "BacktestMetadata",
    "save_backtest_result",
    "load_backtest_result",
    "BacktestCatalog",
    "BacktestRecord",
    "ComparisonReport",
    "compare_backtests",
    "MonteCarloSimulator",
    "MonteCarloResult",
    "SimulationResult",
    "StressTester",
    "StressTestResult",
    "DrawdownSimulator",
    "DrawdownSimulationResult",
    "CorrelationBreakdownTester",
    "CorrelationBreakdownResult",
    "PortfolioConstraints",
    "PortfolioState",
    "PositionSizer",
]

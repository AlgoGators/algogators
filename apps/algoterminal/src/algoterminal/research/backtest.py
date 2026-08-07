"""The "Backtest" stage: a small, dependency-free vectorized backtest engine.

Runs the user's scaffolded strategy (generate_signals -> size_positions ->
apply_risk_rules) against pulled price data and produces an equity curve,
drawdown series, and a stats table.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from types import ModuleType

import pandas as pd

from algoterminal.analytics.stats import PerformanceStats, drawdown_series, performance_stats
from algoterminal.research.storage import ResearchRecord

BacktestStats = PerformanceStats


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    drawdown: pd.Series
    positions: pd.Series
    returns: pd.Series
    stats: BacktestStats


def run_backtest(strategy: ModuleType, prices: pd.Series, initial_capital: float = 100_000.0) -> BacktestResult:
    prices = prices.dropna()

    signal = strategy.generate_signals(prices)
    sized = strategy.size_positions(signal, prices)
    positions = strategy.apply_risk_rules(sized, prices)
    positions = positions.reindex(prices.index).fillna(0.0)

    asset_returns = prices.pct_change().fillna(0.0)
    strategy_returns = positions.shift(1).fillna(0.0) * asset_returns

    equity = (1 + strategy_returns).cumprod() * initial_capital
    drawdown = drawdown_series(equity)
    stats = performance_stats(strategy_returns, equity, positions)

    return BacktestResult(
        equity_curve=equity,
        drawdown=drawdown,
        positions=positions,
        returns=strategy_returns,
        stats=stats,
    )


def save_backtest_result(record: ResearchRecord, result: BacktestResult) -> None:
    with open(record.backtest_results_path, "w", encoding="utf-8") as f:
        json.dump(asdict(result.stats), f, indent=2)

    frame = pd.DataFrame(
        {
            "equity": result.equity_curve,
            "drawdown": result.drawdown,
            "positions": result.positions,
            "returns": result.returns,
        }
    )
    frame.to_parquet(record.equity_curve_path)


def load_backtest_result(record: ResearchRecord) -> BacktestResult:
    """Reload a previously saved backtest run without re-running it."""
    with open(record.backtest_results_path, encoding="utf-8") as f:
        stats = BacktestStats(**json.load(f))

    frame = pd.read_parquet(record.equity_curve_path)
    return BacktestResult(
        equity_curve=frame["equity"],
        drawdown=frame["drawdown"],
        positions=frame["positions"],
        returns=frame["returns"],
        stats=stats,
    )


def has_backtest_result(record: ResearchRecord) -> bool:
    return record.backtest_results_path.exists() and record.equity_curve_path.exists()

"""
Example script demonstrating simulation framework usage.

Run simulations on existing backtest results to assess robustness.
"""

import argparse
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
import pandas as pd
import logging

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))  # src.backtest.engine imports `trading`

from src.backtest.simulation import (
    MonteCarloSimulator,
    StressTester,
    DrawdownSimulator,
    CorrelationBreakdownTester,
)
from src.backtest.storage import load_backtest_result
from src.trading.reporting import compute_run_metrics, compute_daily_returns


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_simple_backtest_runner(trades_df: pd.DataFrame):
    """Create a simple backtest runner that just returns the trades DataFrame."""
    class SimpleResult:
        def __init__(self, trades_df):
            self.trades_df = trades_df
            self.daily_returns = compute_daily_returns(trades_df, starting_capital=10000)
            self.metrics = compute_run_metrics(
                trades_df,
                starting_capital=10000,
                position_size=100,
                daily_returns=self.daily_returns,
            )
    
    return SimpleResult(trades_df)


def run_monte_carlo_simulation(
    trades_df: pd.DataFrame,
    n_simulations: int = 100,
    starting_capital: float = 10000,
    position_size: float = 100,
) -> None:
    """Run Monte Carlo simulation."""
    logger.info("=" * 60)
    logger.info("MONTE CARLO SIMULATION")
    logger.info("=" * 60)
    
    simulator = MonteCarloSimulator(n_simulations=n_simulations, random_seed=42)
    
    def backtest_runner(df):
        return create_simple_backtest_runner(df)
    
    result = simulator.simulate(
        base_trades_df=trades_df,
        backtest_runner=backtest_runner,
        starting_capital=starting_capital,
        position_size=position_size,
        noise_level=0.05,
    )
    
    print("\n" + result.summary())
    print("\nConfidence Intervals (95%):")
    for metric, (lower, upper) in result.confidence_intervals.items():
        if metric in ["sharpe_ratio", "roi_pct", "max_drawdown"]:
            print(f"  {metric}: [{lower:.4f}, {upper:.4f}]")
    
    return result


def run_stress_tests(
    trades_df: pd.DataFrame,
    starting_capital: float = 10000,
    position_size: float = 100,
) -> None:
    """Run stress tests."""
    logger.info("=" * 60)
    logger.info("STRESS TESTING")
    logger.info("=" * 60)
    
    tester = StressTester()
    
    def backtest_runner(df):
        return create_simple_backtest_runner(df)
    
    results = tester.test_extreme_scenarios(
        base_trades_df=trades_df,
        backtest_runner=backtest_runner,
        starting_capital=starting_capital,
        position_size=position_size,
    )
    
    for result in results:
        print("\n" + result.summary())
    
    return results


def run_drawdown_simulation(
    trades_df: pd.DataFrame,
    starting_capital: float = 10000,
    position_size: float = 100,
    n_simulations: int = 1000,
) -> None:
    """Run drawdown simulation."""
    logger.info("=" * 60)
    logger.info("DRAWDOWN SIMULATION")
    logger.info("=" * 60)
    
    simulator = DrawdownSimulator()
    
    result = simulator.simulate_drawdowns(
        trades_df=trades_df,
        starting_capital=starting_capital,
        position_size=position_size,
        n_simulations=n_simulations,
    )
    
    print("\n" + result.summary())
    print("\nDrawdown Percentiles:")
    for percentile, value in result.drawdown_percentiles.items():
        print(f"  {percentile}th: {value:.2f}")
    
    return result


def run_correlation_breakdown_test(
    trades_df: pd.DataFrame,
    starting_capital: float = 10000,
    position_size: float = 100,
    breakdown_factor: float = 0.5,
) -> None:
    """Run correlation breakdown test."""
    logger.info("=" * 60)
    logger.info("CORRELATION BREAKDOWN TEST")
    logger.info("=" * 60)
    
    tester = CorrelationBreakdownTester()
    
    def backtest_runner(df):
        return create_simple_backtest_runner(df)
    
    result = tester.test_correlation_breakdown(
        base_trades_df=trades_df,
        backtest_runner=backtest_runner,
        starting_capital=starting_capital,
        position_size=position_size,
        breakdown_factor=breakdown_factor,
    )
    
    print("\n" + result.summary())
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Run simulation framework tests")
    parser.add_argument(
        "--run-id",
        type=str,
        help="Backtest run ID to load (optional)",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        help="Strategy name (if loading from run-id)",
    )
    parser.add_argument(
        "--trades-csv",
        type=str,
        help="Path to trades CSV file (alternative to run-id)",
    )
    parser.add_argument(
        "--monte-carlo",
        action="store_true",
        help="Run Monte Carlo simulation",
    )
    parser.add_argument(
        "--stress-test",
        action="store_true",
        help="Run stress tests",
    )
    parser.add_argument(
        "--drawdown",
        action="store_true",
        help="Run drawdown simulation",
    )
    parser.add_argument(
        "--correlation",
        action="store_true",
        help="Run correlation breakdown test",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all simulations",
    )
    parser.add_argument(
        "--n-simulations",
        type=int,
        default=100,
        help="Number of Monte Carlo simulations (default: 100)",
    )
    parser.add_argument(
        "--starting-capital",
        type=float,
        default=10000,
        help="Starting capital (default: 10000)",
    )
    parser.add_argument(
        "--position-size",
        type=float,
        default=100,
        help="Position size (default: 100)",
    )
    
    args = parser.parse_args()
    
    # Load trades data
    if args.run_id:
        logger.info(f"Loading backtest result: {args.run_id}")
        data = load_backtest_result(args.run_id, strategy_name=args.strategy)
        trades_df = data["trades_df"]
        if trades_df is None:
            logger.error("No trades DataFrame found in backtest result")
            return 1
    elif args.trades_csv:
        logger.info(f"Loading trades from CSV: {args.trades_csv}")
        trades_df = pd.read_csv(args.trades_csv)
    else:
        logger.error("Must provide either --run-id or --trades-csv")
        return 1
    
    if trades_df.empty:
        logger.error("Trades DataFrame is empty")
        return 1
    
    logger.info(f"Loaded {len(trades_df)} trades")
    
    # Run requested simulations
    run_all = args.all or (not args.monte_carlo and not args.stress_test and not args.drawdown and not args.correlation)
    
    if run_all or args.monte_carlo:
        run_monte_carlo_simulation(
            trades_df,
            n_simulations=args.n_simulations,
            starting_capital=args.starting_capital,
            position_size=args.position_size,
        )
    
    if run_all or args.stress_test:
        run_stress_tests(
            trades_df,
            starting_capital=args.starting_capital,
            position_size=args.position_size,
        )
    
    if run_all or args.drawdown:
        run_drawdown_simulation(
            trades_df,
            starting_capital=args.starting_capital,
            position_size=args.position_size,
            n_simulations=args.n_simulations,
        )
    
    if run_all or args.correlation:
        run_correlation_breakdown_test(
            trades_df,
            starting_capital=args.starting_capital,
            position_size=args.position_size,
        )
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

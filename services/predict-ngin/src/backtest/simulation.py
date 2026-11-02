"""
Simulation framework for strategy evaluation.

Provides:
- Monte Carlo simulation for strategies
- Stress testing with extreme scenarios
- Drawdown simulation
- Correlation breakdown scenarios
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from pathlib import Path
import logging

from ..trading.reporting import RunMetrics, compute_run_metrics, compute_daily_returns


logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """Results from a single simulation run."""
    
    run_id: int
    metrics: RunMetrics
    trades_df: pd.DataFrame
    daily_returns: pd.Series
    scenario_params: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for aggregation."""
        return {
            "run_id": self.run_id,
            **{k: v for k, v in self.metrics.__dict__.items()},
            "scenario_params": self.scenario_params,
        }


@dataclass
class MonteCarloResult:
    """Results from Monte Carlo simulation."""
    
    n_simulations: int
    base_metrics: RunMetrics
    simulation_results: List[SimulationResult]
    aggregated_metrics: Dict[str, Dict[str, float]]
    confidence_intervals: Dict[str, Tuple[float, float]]
    percentiles: Dict[str, Dict[str, float]]
    
    def summary(self) -> str:
        """Generate summary string."""
        lines = [
            f"Monte Carlo Simulation Results",
            f"  Simulations: {self.n_simulations}",
            f"  Base Sharpe Ratio: {self.base_metrics.sharpe_ratio:.4f}",
            f"  Simulated Sharpe (mean): {self.aggregated_metrics.get('sharpe_ratio', {}).get('mean', 0):.4f}",
            f"  Simulated Sharpe (std): {self.aggregated_metrics.get('sharpe_ratio', {}).get('std', 0):.4f}",
            f"  Base Max Drawdown: {self.base_metrics.max_drawdown:.2f}",
            f"  Simulated Max DD (mean): {self.aggregated_metrics.get('max_drawdown', {}).get('mean', 0):.2f}",
            f"  95% CI ROI: [{self.confidence_intervals.get('roi_pct', (0, 0))[0]:.2f}%, {self.confidence_intervals.get('roi_pct', (0, 0))[1]:.2f}%]",
        ]
        return "\n".join(lines)
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert simulation results to DataFrame."""
        rows = []
        for sim in self.simulation_results:
            rows.append(sim.to_dict())
        return pd.DataFrame(rows)


@dataclass
class StressTestResult:
    """Results from stress testing."""
    
    scenario_name: str
    base_metrics: RunMetrics
    stress_metrics: RunMetrics
    stress_params: Dict[str, Any]
    degradation: Dict[str, float]
    
    def summary(self) -> str:
        """Generate summary string."""
        lines = [
            f"Stress Test: {self.scenario_name}",
            f"  Base Sharpe: {self.base_metrics.sharpe_ratio:.4f}",
            f"  Stress Sharpe: {self.stress_metrics.sharpe_ratio:.4f}",
            f"  Sharpe Degradation: {self.degradation.get('sharpe_ratio', 0):.2%}",
            f"  Base Max DD: {self.base_metrics.max_drawdown:.2f}",
            f"  Stress Max DD: {self.stress_metrics.max_drawdown:.2f}",
            f"  Max DD Increase: {self.degradation.get('max_drawdown', 0):.2%}",
        ]
        return "\n".join(lines)


@dataclass
class DrawdownSimulationResult:
    """Results from drawdown simulation."""
    
    base_metrics: RunMetrics
    simulated_drawdowns: List[float]
    max_simulated_drawdown: float
    drawdown_percentiles: Dict[str, float]
    recovery_times: List[float]
    avg_recovery_time: float
    
    def summary(self) -> str:
        """Generate summary string."""
        lines = [
            f"Drawdown Simulation Results",
            f"  Base Max Drawdown: {self.base_metrics.max_drawdown:.2f}",
            f"  Simulated Max Drawdown: {self.max_simulated_drawdown:.2f}",
            f"  95th Percentile Drawdown: {self.drawdown_percentiles.get('95', 0):.2f}",
            f"  Average Recovery Time: {self.avg_recovery_time:.1f} days",
        ]
        return "\n".join(lines)


@dataclass
class CorrelationBreakdownResult:
    """Results from correlation breakdown scenario."""
    
    base_metrics: RunMetrics
    breakdown_metrics: RunMetrics
    correlation_changes: Dict[str, float]
    degradation: Dict[str, float]
    
    def summary(self) -> str:
        """Generate summary string."""
        lines = [
            f"Correlation Breakdown Scenario",
            f"  Base Sharpe: {self.base_metrics.sharpe_ratio:.4f}",
            f"  Breakdown Sharpe: {self.breakdown_metrics.sharpe_ratio:.4f}",
            f"  Sharpe Degradation: {self.degradation.get('sharpe_ratio', 0):.2%}",
        ]
        return "\n".join(lines)


class MonteCarloSimulator:
    """
    Monte Carlo simulator for strategy evaluation.
    
    Runs multiple simulations with random variations to assess robustness.
    """
    
    def __init__(
        self,
        n_simulations: int = 1000,
        random_seed: Optional[int] = None,
    ):
        """
        Initialize Monte Carlo simulator.
        
        Args:
            n_simulations: Number of simulations to run
            random_seed: Random seed for reproducibility
        """
        self.n_simulations = n_simulations
        self.random_seed = random_seed
        if random_seed is not None:
            np.random.seed(random_seed)
    
    def simulate(
        self,
        base_trades_df: pd.DataFrame,
        backtest_runner: Callable[[pd.DataFrame], Any],
        starting_capital: Optional[float] = None,
        position_size: Optional[float] = None,
        noise_level: float = 0.05,
        vary_entry_timing: bool = True,
        vary_exit_timing: bool = True,
        vary_slippage: bool = True,
        vary_fees: bool = True,
    ) -> MonteCarloResult:
        """
        Run Monte Carlo simulation.
        
        Args:
            base_trades_df: Base trades DataFrame
            backtest_runner: Function that takes trades_df and returns BacktestResult-like object
            starting_capital: Starting capital for metrics
            position_size: Position size for metrics
            noise_level: Level of random variation (0.0 to 1.0)
            vary_entry_timing: Whether to vary entry timing
            vary_exit_timing: Whether to vary exit timing
            vary_slippage: Whether to add random slippage
            vary_fees: Whether to vary fees
        
        Returns:
            MonteCarloResult with aggregated statistics
        """
        logger.info(f"Running Monte Carlo simulation with {self.n_simulations} iterations")
        
        # Compute base metrics
        base_daily_returns = compute_daily_returns(base_trades_df, starting_capital)
        base_metrics = compute_run_metrics(
            base_trades_df,
            starting_capital=starting_capital,
            position_size=position_size,
            daily_returns=base_daily_returns,
        )
        
        simulation_results = []
        
        for i in range(self.n_simulations):
            # Create perturbed trades DataFrame
            perturbed_df = self._perturb_trades(
                base_trades_df.copy(),
                noise_level=noise_level,
                vary_entry_timing=vary_entry_timing,
                vary_exit_timing=vary_exit_timing,
                vary_slippage=vary_slippage,
                vary_fees=vary_fees,
            )
            
            # Run backtest
            try:
                result = backtest_runner(perturbed_df)
                
                # Extract metrics
                if hasattr(result, "trades_df"):
                    trades_df = result.trades_df
                else:
                    trades_df = perturbed_df
                
                if hasattr(result, "daily_returns"):
                    daily_returns = result.daily_returns
                else:
                    daily_returns = compute_daily_returns(trades_df, starting_capital)
                
                if hasattr(result, "metrics"):
                    metrics = result.metrics
                else:
                    metrics = compute_run_metrics(
                        trades_df,
                        starting_capital=starting_capital,
                        position_size=position_size,
                        daily_returns=daily_returns,
                    )
                
                simulation_results.append(SimulationResult(
                    run_id=i,
                    metrics=metrics,
                    trades_df=trades_df,
                    daily_returns=daily_returns,
                    scenario_params={"noise_level": noise_level},
                ))
            except Exception as e:
                logger.warning(f"Simulation {i} failed: {e}")
                continue
        
        if not simulation_results:
            raise ValueError("All simulations failed")
        
        # Aggregate results
        aggregated_metrics = self._aggregate_metrics(simulation_results)
        confidence_intervals = self._compute_confidence_intervals(simulation_results)
        percentiles = self._compute_percentiles(simulation_results)
        
        return MonteCarloResult(
            n_simulations=len(simulation_results),
            base_metrics=base_metrics,
            simulation_results=simulation_results,
            aggregated_metrics=aggregated_metrics,
            confidence_intervals=confidence_intervals,
            percentiles=percentiles,
        )
    
    def _perturb_trades(
        self,
        trades_df: pd.DataFrame,
        noise_level: float,
        vary_entry_timing: bool,
        vary_exit_timing: bool,
        vary_slippage: bool,
        vary_fees: bool,
    ) -> pd.DataFrame:
        """Apply random perturbations to trades."""
        df = trades_df.copy()
        
        # Vary entry timing (add small random delays)
        if vary_entry_timing and "entry_time" in df.columns:
            entry_times = pd.to_datetime(df["entry_time"], errors="coerce")
            if entry_times.notna().any():
                delays = np.random.normal(0, noise_level * 3600, len(df))  # seconds
                df["entry_time"] = (entry_times + pd.to_timedelta(delays, unit="s")).astype(str)
        
        # Vary exit timing
        if vary_exit_timing and "exit_time" in df.columns:
            exit_times = pd.to_datetime(df["exit_time"], errors="coerce")
            if exit_times.notna().any():
                delays = np.random.normal(0, noise_level * 3600, len(df))
                df["exit_time"] = (exit_times + pd.to_timedelta(delays, unit="s")).astype(str)
        
        # Add slippage to entry/exit prices
        if vary_slippage:
            if "entry_price" in df.columns:
                slippage = np.random.normal(0, noise_level * 0.01, len(df))
                df["entry_price"] = np.clip(df["entry_price"] + slippage, 0, 1)
            if "exit_price" in df.columns:
                slippage = np.random.normal(0, noise_level * 0.01, len(df))
                df["exit_price"] = np.clip(df["exit_price"] + slippage, 0, 1)
        
        # Vary fees
        if vary_fees and "net_pnl" in df.columns and "gross_pnl" in df.columns:
            fee_multiplier = 1 + np.random.normal(0, noise_level * 0.2, len(df))
            fee_multiplier = np.clip(fee_multiplier, 0.5, 2.0)
            gross_pnl = df["gross_pnl"].fillna(0)
            fees = (gross_pnl - df["net_pnl"].fillna(0)) * fee_multiplier
            df["net_pnl"] = gross_pnl - fees
        
        # Recalculate cumulative PnL if present
        if "cumulative_pnl" in df.columns and "net_pnl" in df.columns:
            df["cumulative_pnl"] = df["net_pnl"].cumsum()
        
        return df
    
    def _aggregate_metrics(self, results: List[SimulationResult]) -> Dict[str, Dict[str, float]]:
        """Aggregate metrics across simulations."""
        metrics_dict = {}
        
        # Get all metric names from first result
        metric_names = [k for k in results[0].metrics.__dict__.keys() if isinstance(getattr(results[0].metrics, k), (int, float))]
        
        for metric_name in metric_names:
            values = [getattr(r.metrics, metric_name) for r in results]
            metrics_dict[metric_name] = {
                "mean": float(np.mean(values)),
                "std": float(np.std(values)),
                "min": float(np.min(values)),
                "max": float(np.max(values)),
            }
        
        return metrics_dict
    
    def _compute_confidence_intervals(
        self,
        results: List[SimulationResult],
        confidence: float = 0.95,
    ) -> Dict[str, Tuple[float, float]]:
        """Compute confidence intervals for metrics."""
        intervals = {}
        alpha = 1 - confidence
        
        metric_names = [k for k in results[0].metrics.__dict__.keys() if isinstance(getattr(results[0].metrics, k), (int, float))]
        
        for metric_name in metric_names:
            values = [getattr(r.metrics, metric_name) for r in results]
            lower = float(np.percentile(values, 100 * alpha / 2))
            upper = float(np.percentile(values, 100 * (1 - alpha / 2)))
            intervals[metric_name] = (lower, upper)
        
        return intervals
    
    def _compute_percentiles(
        self,
        results: List[SimulationResult],
        percentiles: List[float] = [5, 25, 50, 75, 95],
    ) -> Dict[str, Dict[str, float]]:
        """Compute percentiles for metrics."""
        percentile_dict = {}
        
        metric_names = [k for k in results[0].metrics.__dict__.keys() if isinstance(getattr(results[0].metrics, k), (int, float))]
        
        for metric_name in metric_names:
            values = [getattr(r.metrics, metric_name) for r in results]
            percentile_dict[metric_name] = {
                str(p): float(np.percentile(values, p)) for p in percentiles
            }
        
        return percentile_dict


class StressTester:
    """
    Stress testing framework for extreme scenarios.
    """
    
    def __init__(self):
        """Initialize stress tester."""
        pass
    
    def test_extreme_scenarios(
        self,
        base_trades_df: pd.DataFrame,
        backtest_runner: Callable[[pd.DataFrame], Any],
        starting_capital: Optional[float] = None,
        position_size: Optional[float] = None,
    ) -> List[StressTestResult]:
        """
        Run stress tests with extreme scenarios.
        
        Args:
            base_trades_df: Base trades DataFrame
            backtest_runner: Function that takes trades_df and returns BacktestResult-like object
            starting_capital: Starting capital for metrics
            position_size: Position size for metrics
        
        Returns:
            List of StressTestResult objects
        """
        logger.info("Running stress tests with extreme scenarios")
        
        # Compute base metrics
        base_daily_returns = compute_daily_returns(base_trades_df, starting_capital)
        base_metrics = compute_run_metrics(
            base_trades_df,
            starting_capital=starting_capital,
            position_size=position_size,
            daily_returns=base_daily_returns,
        )
        
        results = []
        
        # Scenario 1: Market crash (all positions lose)
        crash_result = self._test_market_crash(
            base_trades_df,
            backtest_runner,
            starting_capital,
            position_size,
            base_metrics,
        )
        if crash_result:
            results.append(crash_result)
        
        # Scenario 2: Increased fees
        fees_result = self._test_increased_fees(
            base_trades_df,
            backtest_runner,
            starting_capital,
            position_size,
            base_metrics,
        )
        if fees_result:
            results.append(fees_result)
        
        # Scenario 3: Slippage increase
        slippage_result = self._test_slippage(
            base_trades_df,
            backtest_runner,
            starting_capital,
            position_size,
            base_metrics,
        )
        if slippage_result:
            results.append(slippage_result)
        
        # Scenario 4: Liquidity crisis
        liquidity_result = self._test_liquidity_crisis(
            base_trades_df,
            backtest_runner,
            starting_capital,
            position_size,
            base_metrics,
        )
        if liquidity_result:
            results.append(liquidity_result)
        
        return results
    
    def _test_market_crash(
        self,
        base_trades_df: pd.DataFrame,
        backtest_runner: Callable,
        starting_capital: Optional[float],
        position_size: Optional[float],
        base_metrics: RunMetrics,
    ) -> Optional[StressTestResult]:
        """Test scenario where market crashes (all positions lose)."""
        df = base_trades_df.copy()
        
        # Make all losing trades lose more, winning trades become losers
        if "net_pnl" in df.columns:
            # Invert PnL with amplification
            df["net_pnl"] = df["net_pnl"] * -1.5
            if "gross_pnl" in df.columns:
                df["gross_pnl"] = df["gross_pnl"] * -1.5
        
        try:
            result = backtest_runner(df)
            stress_metrics = self._extract_metrics(result, df, starting_capital, position_size)
            degradation = self._compute_degradation(base_metrics, stress_metrics)
            
            return StressTestResult(
                scenario_name="Market Crash",
                base_metrics=base_metrics,
                stress_metrics=stress_metrics,
                stress_params={"pnl_multiplier": -1.5},
                degradation=degradation,
            )
        except Exception as e:
            logger.warning(f"Market crash stress test failed: {e}")
            return None
    
    def _test_increased_fees(
        self,
        base_trades_df: pd.DataFrame,
        backtest_runner: Callable,
        starting_capital: Optional[float],
        position_size: Optional[float],
        base_metrics: RunMetrics,
    ) -> Optional[StressTestResult]:
        """Test scenario with increased fees."""
        df = base_trades_df.copy()
        
        # Increase fees by 3x
        if "net_pnl" in df.columns and "gross_pnl" in df.columns:
            fees = (df["gross_pnl"] - df["net_pnl"]).fillna(0) * 3
            df["net_pnl"] = df["gross_pnl"] - fees
        
        try:
            result = backtest_runner(df)
            stress_metrics = self._extract_metrics(result, df, starting_capital, position_size)
            degradation = self._compute_degradation(base_metrics, stress_metrics)
            
            return StressTestResult(
                scenario_name="Increased Fees (3x)",
                base_metrics=base_metrics,
                stress_metrics=stress_metrics,
                stress_params={"fee_multiplier": 3.0},
                degradation=degradation,
            )
        except Exception as e:
            logger.warning(f"Increased fees stress test failed: {e}")
            return None
    
    def _test_slippage(
        self,
        base_trades_df: pd.DataFrame,
        backtest_runner: Callable,
        starting_capital: Optional[float],
        position_size: Optional[float],
        base_metrics: RunMetrics,
    ) -> Optional[StressTestResult]:
        """Test scenario with increased slippage."""
        df = base_trades_df.copy()
        
        # Add 2% slippage to all trades
        if "entry_price" in df.columns:
            df["entry_price"] = np.clip(df["entry_price"] + 0.02, 0, 1)
        if "exit_price" in df.columns:
            df["exit_price"] = np.clip(df["exit_price"] - 0.02, 0, 1)
        
        # Recalculate PnL if possible
        if "entry_price" in df.columns and "exit_price" in df.columns and "net_pnl" in df.columns:
            # Simplified: assume YES positions
            new_pnl = (df["exit_price"] - df["entry_price"]) * df.get("position_size", 100)
            if "gross_pnl" in df.columns:
                fees = df["gross_pnl"] - df["net_pnl"]
                df["net_pnl"] = new_pnl - fees
            else:
                df["net_pnl"] = new_pnl
        
        try:
            result = backtest_runner(df)
            stress_metrics = self._extract_metrics(result, df, starting_capital, position_size)
            degradation = self._compute_degradation(base_metrics, stress_metrics)
            
            return StressTestResult(
                scenario_name="Increased Slippage (2%)",
                base_metrics=base_metrics,
                stress_metrics=stress_metrics,
                stress_params={"slippage_pct": 0.02},
                degradation=degradation,
            )
        except Exception as e:
            logger.warning(f"Slippage stress test failed: {e}")
            return None
    
    def _test_liquidity_crisis(
        self,
        base_trades_df: pd.DataFrame,
        backtest_runner: Callable,
        starting_capital: Optional[float],
        position_size: Optional[float],
        base_metrics: RunMetrics,
    ) -> Optional[StressTestResult]:
        """Test scenario with liquidity crisis (can't exit positions)."""
        df = base_trades_df.copy()
        
        # Simulate inability to exit: delay exits by 30 days
        if "exit_time" in df.columns:
            exit_times = pd.to_datetime(df["exit_time"], errors="coerce")
            if exit_times.notna().any():
                df["exit_time"] = (exit_times + pd.Timedelta(days=30)).astype(str)
        
        # Reduce position sizes due to liquidity constraints
        if "position_size" in df.columns:
            df["position_size"] = df["position_size"] * 0.5
        
        try:
            result = backtest_runner(df)
            stress_metrics = self._extract_metrics(result, df, starting_capital, position_size)
            degradation = self._compute_degradation(base_metrics, stress_metrics)
            
            return StressTestResult(
                scenario_name="Liquidity Crisis",
                base_metrics=base_metrics,
                stress_metrics=stress_metrics,
                stress_params={"exit_delay_days": 30, "position_size_multiplier": 0.5},
                degradation=degradation,
            )
        except Exception as e:
            logger.warning(f"Liquidity crisis stress test failed: {e}")
            return None
    
    def _extract_metrics(
        self,
        result: Any,
        trades_df: pd.DataFrame,
        starting_capital: Optional[float],
        position_size: Optional[float],
    ) -> RunMetrics:
        """Extract metrics from result object."""
        if hasattr(result, "trades_df"):
            trades_df = result.trades_df
        
        if hasattr(result, "daily_returns"):
            daily_returns = result.daily_returns
        else:
            daily_returns = compute_daily_returns(trades_df, starting_capital)
        
        if hasattr(result, "metrics"):
            return result.metrics
        else:
            return compute_run_metrics(
                trades_df,
                starting_capital=starting_capital,
                position_size=position_size,
                daily_returns=daily_returns,
            )
    
    def _compute_degradation(
        self,
        base: RunMetrics,
        stress: RunMetrics,
    ) -> Dict[str, float]:
        """Compute degradation metrics."""
        degradation = {}
        
        # Sharpe ratio degradation
        if base.sharpe_ratio != 0:
            degradation["sharpe_ratio"] = (stress.sharpe_ratio - base.sharpe_ratio) / abs(base.sharpe_ratio)
        else:
            degradation["sharpe_ratio"] = 0.0
        
        # Max drawdown increase
        if base.max_drawdown != 0:
            degradation["max_drawdown"] = (stress.max_drawdown - base.max_drawdown) / abs(base.max_drawdown)
        else:
            degradation["max_drawdown"] = 0.0
        
        # ROI degradation
        if base.roi_pct != 0:
            degradation["roi_pct"] = (stress.roi_pct - base.roi_pct) / abs(base.roi_pct)
        else:
            degradation["roi_pct"] = 0.0
        
        return degradation


class DrawdownSimulator:
    """
    Simulator for drawdown scenarios.
    """
    
    def simulate_drawdowns(
        self,
        trades_df: pd.DataFrame,
        starting_capital: Optional[float] = None,
        position_size: Optional[float] = None,
        n_simulations: int = 1000,
    ) -> DrawdownSimulationResult:
        """
        Simulate drawdown scenarios.
        
        Args:
            trades_df: Trades DataFrame
            starting_capital: Starting capital
            position_size: Position size
            n_simulations: Number of simulations
        
        Returns:
            DrawdownSimulationResult
        """
        logger.info(f"Simulating drawdowns with {n_simulations} iterations")
        
        base_daily_returns = compute_daily_returns(trades_df, starting_capital)
        base_metrics = compute_run_metrics(
            trades_df,
            starting_capital=starting_capital,
            position_size=position_size,
            daily_returns=base_daily_returns,
        )
        
        simulated_drawdowns = []
        recovery_times = []
        
        # Bootstrap resample trades to simulate different sequences
        for i in range(n_simulations):
            # Resample trades with replacement
            resampled = trades_df.sample(n=len(trades_df), replace=True).reset_index(drop=True)
            
            # Recalculate cumulative PnL
            if "net_pnl" in resampled.columns:
                cumulative = resampled["net_pnl"].cumsum()
                running_max = cumulative.cummax()
                drawdown = running_max - cumulative
                max_dd = drawdown.max()
                simulated_drawdowns.append(max_dd)
                
                # Calculate recovery time (time from max drawdown to recovery)
                if len(drawdown) > 1:
                    max_dd_idx = drawdown.idxmax()
                    recovery_idx = None
                    for j in range(max_dd_idx + 1, len(cumulative)):
                        if cumulative.iloc[j] >= running_max.iloc[max_dd_idx]:
                            recovery_idx = j
                            break
                    if recovery_idx:
                        recovery_time = recovery_idx - max_dd_idx
                        recovery_times.append(recovery_time)
        
        if not simulated_drawdowns:
            simulated_drawdowns = [base_metrics.max_drawdown]
        
        max_simulated_drawdown = max(simulated_drawdowns)
        drawdown_percentiles = {
            "5": float(np.percentile(simulated_drawdowns, 5)),
            "25": float(np.percentile(simulated_drawdowns, 25)),
            "50": float(np.percentile(simulated_drawdowns, 50)),
            "75": float(np.percentile(simulated_drawdowns, 75)),
            "95": float(np.percentile(simulated_drawdowns, 95)),
        }
        
        avg_recovery_time = float(np.mean(recovery_times)) if recovery_times else 0.0
        
        return DrawdownSimulationResult(
            base_metrics=base_metrics,
            simulated_drawdowns=simulated_drawdowns,
            max_simulated_drawdown=max_simulated_drawdown,
            drawdown_percentiles=drawdown_percentiles,
            recovery_times=recovery_times,
            avg_recovery_time=avg_recovery_time,
        )


class CorrelationBreakdownTester:
    """
    Tester for correlation breakdown scenarios.
    """
    
    def test_correlation_breakdown(
        self,
        base_trades_df: pd.DataFrame,
        backtest_runner: Callable[[pd.DataFrame], Any],
        starting_capital: Optional[float] = None,
        position_size: Optional[float] = None,
        breakdown_factor: float = 0.5,
    ) -> CorrelationBreakdownResult:
        """
        Test scenario where correlations break down.
        
        Args:
            base_trades_df: Base trades DataFrame
            backtest_runner: Function that takes trades_df and returns BacktestResult-like object
            starting_capital: Starting capital for metrics
            position_size: Position size for metrics
            breakdown_factor: Factor by which to reduce correlation (0.0 to 1.0)
        
        Returns:
            CorrelationBreakdownResult
        """
        logger.info(f"Testing correlation breakdown (factor: {breakdown_factor})")
        
        # Compute base metrics
        base_daily_returns = compute_daily_returns(base_trades_df, starting_capital)
        base_metrics = compute_run_metrics(
            base_trades_df,
            starting_capital=starting_capital,
            position_size=position_size,
            daily_returns=base_daily_returns,
        )
        
        # Simulate correlation breakdown by randomizing trade outcomes
        # This simulates a scenario where previously correlated markets become independent
        df = base_trades_df.copy()
        
        # Shuffle trade outcomes to break correlations
        if "net_pnl" in df.columns:
            # Partially randomize: mix original with random
            original_pnl = df["net_pnl"].values
            random_pnl = np.random.permutation(original_pnl)
            
            # Blend based on breakdown_factor
            breakdown_pnl = (
                original_pnl * (1 - breakdown_factor) +
                random_pnl * breakdown_factor
            )
            df["net_pnl"] = breakdown_pnl
            
            # Adjust gross_pnl accordingly
            if "gross_pnl" in df.columns:
                fees = df["gross_pnl"] - original_pnl
                df["gross_pnl"] = breakdown_pnl + fees
        
        try:
            result = backtest_runner(df)
            breakdown_metrics = self._extract_metrics(result, df, starting_capital, position_size)
            degradation = self._compute_degradation(base_metrics, breakdown_metrics)
            
            return CorrelationBreakdownResult(
                base_metrics=base_metrics,
                breakdown_metrics=breakdown_metrics,
                correlation_changes={"breakdown_factor": breakdown_factor},
                degradation=degradation,
            )
        except Exception as e:
            logger.error(f"Correlation breakdown test failed: {e}")
            # Return result with base metrics if test fails
            return CorrelationBreakdownResult(
                base_metrics=base_metrics,
                breakdown_metrics=base_metrics,
                correlation_changes={"breakdown_factor": breakdown_factor, "error": str(e)},
                degradation={"sharpe_ratio": 0.0, "max_drawdown": 0.0, "roi_pct": 0.0},
            )
    
    def _extract_metrics(
        self,
        result: Any,
        trades_df: pd.DataFrame,
        starting_capital: Optional[float],
        position_size: Optional[float],
    ) -> RunMetrics:
        """Extract metrics from result object."""
        if hasattr(result, "trades_df"):
            trades_df = result.trades_df
        
        if hasattr(result, "daily_returns"):
            daily_returns = result.daily_returns
        else:
            daily_returns = compute_daily_returns(trades_df, starting_capital)
        
        if hasattr(result, "metrics"):
            return result.metrics
        else:
            return compute_run_metrics(
                trades_df,
                starting_capital=starting_capital,
                position_size=position_size,
                daily_returns=daily_returns,
            )
    
    def _compute_degradation(
        self,
        base: RunMetrics,
        breakdown: RunMetrics,
    ) -> Dict[str, float]:
        """Compute degradation metrics."""
        degradation = {}
        
        if base.sharpe_ratio != 0:
            degradation["sharpe_ratio"] = (breakdown.sharpe_ratio - base.sharpe_ratio) / abs(base.sharpe_ratio)
        else:
            degradation["sharpe_ratio"] = 0.0
        
        if base.max_drawdown != 0:
            degradation["max_drawdown"] = (breakdown.max_drawdown - base.max_drawdown) / abs(base.max_drawdown)
        else:
            degradation["max_drawdown"] = 0.0
        
        if base.roi_pct != 0:
            degradation["roi_pct"] = (breakdown.roi_pct - base.roi_pct) / abs(base.roi_pct)
        else:
            degradation["roi_pct"] = 0.0
        
        return degradation


__all__ = [
    "MonteCarloSimulator",
    "MonteCarloResult",
    "SimulationResult",
    "StressTester",
    "StressTestResult",
    "DrawdownSimulator",
    "DrawdownSimulationResult",
    "CorrelationBreakdownTester",
    "CorrelationBreakdownResult",
]

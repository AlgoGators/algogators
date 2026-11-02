"""
Walk-forward validation for strategy robustness testing.

Implements time-series cross-validation with embargo periods
to avoid look-ahead bias and detect overfitting.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
import logging

import numpy as np
import pandas as pd

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    plt = None
    MATPLOTLIB_AVAILABLE = False


@dataclass
class WalkForwardWindow:
    """
    A single train/test window in walk-forward validation.

    Attributes:
        window_id: Window identifier (0-indexed)
        train_start: Training period start date
        train_end: Training period end date
        test_start: Test period start date
        test_end: Test period end date
        train_records: Number of training records
        test_records: Number of test records
        metrics: Metrics computed on this window
    """

    window_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_records: int = 0
    test_records: int = 0
    metrics: Dict[str, float] = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    """
    Results from walk-forward validation.

    Attributes:
        windows: List of individual window results
        metrics_by_window: DataFrame with metrics for each window
        aggregate_metrics: Aggregated metrics across all windows
        stability_score: Measure of metric stability (0-1, higher is better)
        overfitting_score: Train vs test gap (0-1, lower is better)
    """

    windows: List[WalkForwardWindow]
    metrics_by_window: pd.DataFrame
    aggregate_metrics: Dict[str, float]
    stability_score: float
    overfitting_score: float

    def summary(self) -> str:
        """Generate summary string."""
        return (
            f"Walk-Forward Validation Results\n"
            f"  Windows: {len(self.windows)}\n"
            f"  Stability Score: {self.stability_score:.3f}\n"
            f"  Overfitting Score: {self.overfitting_score:.3f}\n"
            f"  Aggregate Metrics:\n" +
            "\n".join(f"    {k}: {v:.4f}" for k, v in self.aggregate_metrics.items())
        )


class WalkForwardValidator:
    """
    Walk-forward validation for strategy testing.

    Divides data into expanding or rolling windows, trains on each
    window, and tests on the subsequent period with an embargo gap.

    Example:
        validator = WalkForwardValidator(n_splits=5, train_ratio=0.7)

        # Generate splits
        for train_df, test_df in validator.generate_splits(trades_df):
            result = run_backtest(train_df, test_df)
            # ...

        # Or use validate() for full automation
        result = validator.validate(
            trades_df,
            backtest_runner=run_my_backtest,
        )
        print(result.summary())
    """

    def __init__(
        self,
        n_splits: int = 5,
        train_ratio: float = 0.7,
        embargo_days: int = 7,
        min_train_records: int = 100,
        min_test_records: int = 50,
        expanding: bool = False,
        datetime_col: str = "datetime",
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize validator.

        Args:
            n_splits: Number of validation windows
            train_ratio: Ratio of each window for training
            embargo_days: Gap between train and test to avoid leakage
            min_train_records: Minimum training records per window
            min_test_records: Minimum test records per window
            expanding: If True, use expanding window; if False, use rolling
            datetime_col: Column containing timestamps
            logger: Logger instance
        """
        self.n_splits = n_splits
        self.train_ratio = train_ratio
        self.embargo_days = embargo_days
        self.min_train_records = min_train_records
        self.min_test_records = min_test_records
        self.expanding = expanding
        self.datetime_col = datetime_col
        self.logger = logger or logging.getLogger("backtest.walk_forward")

    def generate_splits(
        self,
        df: pd.DataFrame,
    ) -> Iterator[Tuple[pd.DataFrame, pd.DataFrame]]:
        """
        Generate train/test splits for walk-forward validation.

        Args:
            df: DataFrame with datetime column

        Yields:
            Tuples of (train_df, test_df)
        """
        if df.empty:
            return

        if self.datetime_col not in df.columns:
            raise ValueError(f"Column '{self.datetime_col}' not found")

        # Sort by datetime
        df = df.sort_values(self.datetime_col).reset_index(drop=True)

        # Convert to datetime if needed
        df[self.datetime_col] = pd.to_datetime(df[self.datetime_col])

        min_date = df[self.datetime_col].min()
        max_date = df[self.datetime_col].max()
        total_days = (max_date - min_date).days

        # Calculate window size
        window_days = total_days // self.n_splits
        train_days = int(window_days * self.train_ratio)
        test_days = window_days - train_days - self.embargo_days

        if test_days <= 0:
            self.logger.warning("Window too small for embargo. Reducing embargo.")
            test_days = window_days - train_days

        for i in range(self.n_splits):
            if self.expanding:
                # Expanding window: train from start
                train_start = min_date
                train_end = min_date + timedelta(days=(i + 1) * window_days * self.train_ratio)
            else:
                # Rolling window
                train_start = min_date + timedelta(days=i * window_days)
                train_end = train_start + timedelta(days=train_days)

            # Apply embargo
            test_start = train_end + timedelta(days=self.embargo_days)
            test_end = test_start + timedelta(days=test_days)

            # Don't go beyond data
            if test_end > max_date:
                test_end = max_date

            if test_start >= max_date:
                break

            # Create masks
            train_mask = (
                (df[self.datetime_col] >= train_start) &
                (df[self.datetime_col] < train_end)
            )
            test_mask = (
                (df[self.datetime_col] >= test_start) &
                (df[self.datetime_col] <= test_end)
            )

            train_df = df[train_mask].copy()
            test_df = df[test_mask].copy()

            # Check minimum records
            if len(train_df) < self.min_train_records:
                self.logger.warning(f"Window {i}: insufficient training data ({len(train_df)})")
                continue

            if len(test_df) < self.min_test_records:
                self.logger.warning(f"Window {i}: insufficient test data ({len(test_df)})")
                continue

            yield train_df, test_df

    def validate(
        self,
        df: pd.DataFrame,
        backtest_runner: Callable[[pd.DataFrame, pd.DataFrame], Dict[str, float]],
        metrics_of_interest: Optional[List[str]] = None,
    ) -> WalkForwardResult:
        """
        Run full walk-forward validation.

        Args:
            df: Full dataset
            backtest_runner: Function that takes (train_df, test_df) and returns metrics dict
            metrics_of_interest: Metrics to track (default: all from first run)

        Returns:
            WalkForwardResult with all window results and aggregates
        """
        windows: List[WalkForwardWindow] = []
        all_metrics: List[Dict[str, float]] = []

        for i, (train_df, test_df) in enumerate(self.generate_splits(df)):
            self.logger.info(f"Running window {i + 1}/{self.n_splits}")

            train_start = train_df[self.datetime_col].min()
            train_end = train_df[self.datetime_col].max()
            test_start = test_df[self.datetime_col].min()
            test_end = test_df[self.datetime_col].max()

            try:
                metrics = backtest_runner(train_df, test_df)
            except Exception as e:
                self.logger.error(f"Window {i} failed: {e}")
                metrics = {}

            window = WalkForwardWindow(
                window_id=i,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                train_records=len(train_df),
                test_records=len(test_df),
                metrics=metrics,
            )
            windows.append(window)
            all_metrics.append(metrics)

        if not windows:
            return WalkForwardResult(
                windows=[],
                metrics_by_window=pd.DataFrame(),
                aggregate_metrics={},
                stability_score=0.0,
                overfitting_score=1.0,
            )

        # Build metrics DataFrame
        metrics_df = pd.DataFrame(all_metrics)
        metrics_df["window_id"] = [w.window_id for w in windows]
        metrics_df["train_start"] = [w.train_start for w in windows]
        metrics_df["test_start"] = [w.test_start for w in windows]

        # Calculate aggregate metrics
        aggregate = {}
        for col in metrics_df.select_dtypes(include=[np.number]).columns:
            if col == "window_id":
                continue
            aggregate[f"{col}_mean"] = metrics_df[col].mean()
            aggregate[f"{col}_std"] = metrics_df[col].std()
            aggregate[f"{col}_min"] = metrics_df[col].min()
            aggregate[f"{col}_max"] = metrics_df[col].max()

        # Calculate stability score (inverse of CV across windows)
        stability = self._calculate_stability(metrics_df)

        # Calculate overfitting score (would need train metrics)
        overfitting = self._calculate_overfitting(windows)

        return WalkForwardResult(
            windows=windows,
            metrics_by_window=metrics_df,
            aggregate_metrics=aggregate,
            stability_score=stability,
            overfitting_score=overfitting,
        )

    def _calculate_stability(self, metrics_df: pd.DataFrame) -> float:
        """
        Calculate stability score based on metric consistency.

        Returns value between 0 and 1, where 1 is perfectly stable.
        """
        if metrics_df.empty:
            return 0.0

        # Focus on key metrics if available
        key_metrics = ["sharpe", "sharpe_ratio", "win_rate", "total_return"]
        available = [m for m in key_metrics if m in metrics_df.columns]

        if not available:
            # Use all numeric columns
            available = metrics_df.select_dtypes(include=[np.number]).columns.tolist()
            available = [c for c in available if c != "window_id"]

        if not available:
            return 0.0

        # Calculate coefficient of variation for each metric
        cvs = []
        for col in available:
            values = metrics_df[col].dropna()
            if len(values) > 1 and values.std() != 0:
                cv = values.std() / abs(values.mean()) if values.mean() != 0 else 1.0
                cvs.append(min(cv, 2.0))  # Cap at 2.0

        if not cvs:
            return 0.0

        # Convert to stability (lower CV = higher stability)
        avg_cv = np.mean(cvs)
        stability = max(0, 1 - avg_cv / 2)  # Normalize

        return float(stability)

    def _calculate_overfitting(self, windows: List[WalkForwardWindow]) -> float:
        """
        Calculate overfitting score based on train vs test performance gap.

        Without train metrics, we estimate based on performance degradation over time.
        Returns value between 0 and 1, where 0 is no overfitting.
        """
        if len(windows) < 3:
            return 0.5  # Not enough data

        # Check if performance degrades over time (sign of overfitting)
        sharpe_values = [w.metrics.get("sharpe", w.metrics.get("sharpe_ratio", 0)) for w in windows]
        if not any(sharpe_values):
            return 0.5

        # Calculate trend
        x = np.arange(len(sharpe_values))
        y = np.array(sharpe_values)

        # Handle NaN/inf
        mask = np.isfinite(y)
        if mask.sum() < 2:
            return 0.5

        slope = np.polyfit(x[mask], y[mask], 1)[0]

        # Negative slope suggests overfitting (earlier windows do better)
        if slope < 0:
            # Normalize based on magnitude
            overfitting = min(1.0, abs(slope) / 0.5)
        else:
            overfitting = 0.0

        return float(overfitting)

    def plot_stability(
        self,
        result: WalkForwardResult,
        metric: str = "sharpe",
    ) -> Any:
        """
        Plot metric stability across windows.

        Args:
            result: WalkForwardResult from validate()
            metric: Metric to plot

        Returns:
            matplotlib Figure
        """
        if not MATPLOTLIB_AVAILABLE:
            raise ImportError("matplotlib required for plotting")

        if metric not in result.metrics_by_window.columns:
            # Try variations
            for alt in [f"{metric}_ratio", f"total_{metric}"]:
                if alt in result.metrics_by_window.columns:
                    metric = alt
                    break
            else:
                raise ValueError(f"Metric '{metric}' not found in results")

        fig, axes = plt.subplots(2, 1, figsize=(12, 8))

        # Plot metric over windows
        ax1 = axes[0]
        values = result.metrics_by_window[metric]
        ax1.bar(range(len(values)), values, alpha=0.7)
        ax1.axhline(y=values.mean(), color="red", linestyle="--", label=f"Mean: {values.mean():.3f}")
        ax1.fill_between(
            range(len(values)),
            values.mean() - values.std(),
            values.mean() + values.std(),
            alpha=0.2, color="red", label=f"±1 std: {values.std():.3f}"
        )
        ax1.set_xlabel("Window")
        ax1.set_ylabel(metric.replace("_", " ").title())
        ax1.set_title(f"{metric.replace('_', ' ').title()} by Window")
        ax1.legend()

        # Plot cumulative performance
        ax2 = axes[1]
        if "total_pnl" in result.metrics_by_window.columns:
            cumulative = result.metrics_by_window["total_pnl"].cumsum()
            ax2.plot(range(len(cumulative)), cumulative, marker="o")
            ax2.set_ylabel("Cumulative P&L ($)")
        elif "return" in result.metrics_by_window.columns:
            cumulative = (1 + result.metrics_by_window["return"]).cumprod() - 1
            ax2.plot(range(len(cumulative)), cumulative * 100, marker="o")
            ax2.set_ylabel("Cumulative Return (%)")
        else:
            cumulative = values.cumsum()
            ax2.plot(range(len(cumulative)), cumulative, marker="o")
            ax2.set_ylabel(f"Cumulative {metric}")

        ax2.set_xlabel("Window")
        ax2.set_title("Cumulative Performance")
        ax2.axhline(y=0, color="black", linestyle="--", alpha=0.5)

        # Add scores annotation
        scores_text = (
            f"Stability: {result.stability_score:.3f}\n"
            f"Overfitting: {result.overfitting_score:.3f}"
        )
        fig.text(0.02, 0.98, scores_text, fontsize=10, verticalalignment="top",
                 bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

        plt.tight_layout()
        return fig


__all__ = ["WalkForwardValidator", "WalkForwardResult", "WalkForwardWindow"]

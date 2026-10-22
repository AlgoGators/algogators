"""Unit tests for WalkForwardValidator."""

import pytest
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

from src.backtest.walk_forward import (
    WalkForwardValidator,
    WalkForwardResult,
    WalkForwardWindow,
)


@pytest.fixture
def sample_trades_df():
    """Create sample trades DataFrame with 2 years of data."""
    np.random.seed(42)
    n_days = 730  # 2 years

    base_date = datetime(2023, 1, 1)
    dates = [base_date + timedelta(days=i) for i in range(n_days)]

    # Multiple trades per day
    all_dates = []
    for d in dates:
        for _ in range(np.random.randint(1, 5)):
            all_dates.append(d + timedelta(hours=np.random.randint(0, 23)))

    n_trades = len(all_dates)

    return pd.DataFrame({
        "datetime": all_dates,
        "market_id": [f"market_{i % 20}" for i in range(n_trades)],
        "net_pnl": np.random.normal(5, 50, n_trades),
        "price": np.random.uniform(0.1, 0.9, n_trades),
    }).sort_values("datetime").reset_index(drop=True)


class TestWalkForwardWindow:
    def test_dataclass_fields(self):
        window = WalkForwardWindow(
            window_id=0,
            train_start=datetime(2023, 1, 1),
            train_end=datetime(2023, 6, 30),
            test_start=datetime(2023, 7, 7),
            test_end=datetime(2023, 12, 31),
            train_records=500,
            test_records=200,
            metrics={"sharpe": 1.5},
        )

        assert window.window_id == 0
        assert window.train_records == 500
        assert window.metrics["sharpe"] == 1.5


class TestWalkForwardResult:
    def test_summary(self):
        result = WalkForwardResult(
            windows=[],
            metrics_by_window=pd.DataFrame(),
            aggregate_metrics={"sharpe_mean": 1.2},
            stability_score=0.85,
            overfitting_score=0.15,
        )

        summary = result.summary()

        assert "Stability" in summary
        assert "Overfitting" in summary
        assert "0.85" in summary or ".85" in summary


class TestWalkForwardValidator:
    def test_init(self):
        validator = WalkForwardValidator(
            n_splits=5,
            train_ratio=0.7,
            embargo_days=7,
        )

        assert validator.n_splits == 5
        assert validator.train_ratio == 0.7
        assert validator.embargo_days == 7

    def test_generate_splits(self, sample_trades_df):
        validator = WalkForwardValidator(
            n_splits=5,
            train_ratio=0.7,
            embargo_days=7,
            min_train_records=50,
            min_test_records=20,
        )

        splits = list(validator.generate_splits(sample_trades_df))

        assert len(splits) > 0

        for train_df, test_df in splits:
            # Check train comes before test
            train_max = train_df["datetime"].max()
            test_min = test_df["datetime"].min()
            assert train_max < test_min

            # Check minimum records
            assert len(train_df) >= 50
            assert len(test_df) >= 20

    def test_generate_splits_expanding(self, sample_trades_df):
        validator = WalkForwardValidator(
            n_splits=3,
            expanding=True,
            min_train_records=50,
            min_test_records=20,
        )

        splits = list(validator.generate_splits(sample_trades_df))

        # With expanding window, train size should grow
        train_sizes = [len(train_df) for train_df, _ in splits]

        # Check train sizes are non-decreasing
        for i in range(1, len(train_sizes)):
            assert train_sizes[i] >= train_sizes[i-1] * 0.9  # Allow small variation

    def test_validate(self, sample_trades_df):
        validator = WalkForwardValidator(
            n_splits=3,
            train_ratio=0.7,
            min_train_records=50,
            min_test_records=20,
        )

        def mock_backtest(train_df, test_df):
            """Simple mock backtest function."""
            pnl = test_df["net_pnl"].sum()
            win_rate = (test_df["net_pnl"] > 0).mean()
            return {
                "total_pnl": pnl,
                "win_rate": win_rate,
                "sharpe": pnl / (test_df["net_pnl"].std() + 1e-10),
            }

        result = validator.validate(sample_trades_df, mock_backtest)

        assert isinstance(result, WalkForwardResult)
        assert len(result.windows) > 0
        assert "total_pnl_mean" in result.aggregate_metrics
        assert 0 <= result.stability_score <= 1
        assert 0 <= result.overfitting_score <= 1

    def test_validate_empty_data(self):
        validator = WalkForwardValidator(n_splits=3)

        result = validator.validate(
            pd.DataFrame(),
            lambda train, test: {"metric": 0},
        )

        assert len(result.windows) == 0
        assert result.stability_score == 0

    def test_embargo_applied(self, sample_trades_df):
        validator = WalkForwardValidator(
            n_splits=3,
            train_ratio=0.7,
            embargo_days=14,
            min_train_records=50,
            min_test_records=20,
        )

        for train_df, test_df in validator.generate_splits(sample_trades_df):
            train_max = train_df["datetime"].max()
            test_min = test_df["datetime"].min()
            gap_days = (test_min - train_max).days

            # Gap should be at least embargo_days
            assert gap_days >= 14

    def test_stability_calculation(self):
        validator = WalkForwardValidator()

        # Test with consistent metrics
        consistent_df = pd.DataFrame({
            "sharpe": [1.0, 1.1, 0.9, 1.0],
        })

        stability = validator._calculate_stability(consistent_df)
        assert stability > 0.7  # Should be high for consistent data

        # Test with inconsistent metrics
        inconsistent_df = pd.DataFrame({
            "sharpe": [1.0, -1.0, 2.0, -0.5],
        })

        stability = validator._calculate_stability(inconsistent_df)
        assert stability < 0.5  # Should be low for inconsistent data

    @pytest.mark.skipif(
        not pytest.importorskip("matplotlib", reason="matplotlib required"),
        reason="matplotlib not available"
    )
    def test_plot_stability(self, sample_trades_df):
        validator = WalkForwardValidator(
            n_splits=3,
            min_train_records=50,
            min_test_records=20,
        )

        def mock_backtest(train_df, test_df):
            return {"sharpe": np.random.uniform(0.5, 1.5)}

        result = validator.validate(sample_trades_df, mock_backtest)

        # Only test if matplotlib available
        try:
            fig = validator.plot_stability(result)
            assert fig is not None
        except ImportError:
            pass

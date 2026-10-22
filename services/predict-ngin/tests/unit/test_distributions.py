"""Unit tests for DistributionPlotter."""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

# Skip tests if matplotlib not available
pytest.importorskip("matplotlib")

from src.analysis.distributions import DistributionPlotter


@pytest.fixture
def temp_output_dir():
    """Create a temporary directory for plots."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def sample_trades_df():
    """Create sample trades DataFrame."""
    np.random.seed(42)
    n_trades = 100

    base_date = datetime(2025, 1, 1)
    dates = [base_date + timedelta(hours=i*12) for i in range(n_trades)]

    return pd.DataFrame({
        "datetime": dates,
        "market_id": [f"market_{i % 10}" for i in range(n_trades)],
        "category": np.random.choice(["crypto", "politics", "sports"], n_trades),
        "net_pnl": np.random.normal(5, 50, n_trades),
        "usd_amount": np.random.lognormal(5, 1, n_trades),
        "price": np.random.uniform(0.1, 0.9, n_trades),
    })


@pytest.fixture
def plotter(temp_output_dir):
    """Create a plotter with temporary directory."""
    return DistributionPlotter(output_dir=temp_output_dir)


class TestDistributionPlotter:
    def test_init(self, temp_output_dir):
        plotter = DistributionPlotter(output_dir=temp_output_dir)

        assert plotter.output_dir.exists()

    def test_plot_pnl_distribution(self, plotter, sample_trades_df):
        fig = plotter.plot_pnl_distribution(sample_trades_df)

        assert fig is not None
        # Check figure has axes
        assert len(fig.axes) > 0

    def test_plot_pnl_distribution_by_category(self, plotter, sample_trades_df):
        fig = plotter.plot_pnl_distribution(sample_trades_df, by="category")

        assert fig is not None

    def test_plot_trade_size_distribution(self, plotter, sample_trades_df):
        fig = plotter.plot_trade_size_distribution(sample_trades_df)

        assert fig is not None

    def test_plot_trade_size_linear_scale(self, plotter, sample_trades_df):
        fig = plotter.plot_trade_size_distribution(sample_trades_df, log_scale=False)

        assert fig is not None

    def test_plot_win_rate_by_dimension(self, plotter, sample_trades_df):
        fig = plotter.plot_win_rate_by_dimension(sample_trades_df, "category")

        assert fig is not None
        # Should have 2 subplots
        assert len(fig.axes) == 2

    def test_plot_temporal_patterns(self, plotter, sample_trades_df):
        fig = plotter.plot_temporal_patterns(sample_trades_df)

        assert fig is not None
        # Should have 4 subplots
        assert len(fig.axes) == 4

    def test_plot_cumulative_pnl(self, plotter, sample_trades_df):
        fig = plotter.plot_cumulative_pnl(sample_trades_df)

        assert fig is not None

    def test_generate_analysis_report(self, plotter, sample_trades_df, temp_output_dir):
        report_dir = plotter.generate_analysis_report(
            sample_trades_df,
            dimensions=["category"],
        )

        assert Path(report_dir).exists()
        assert (Path(report_dir) / "pnl_distribution.png").exists()
        assert (Path(report_dir) / "temporal_patterns.png").exists()

    def test_missing_column_error(self, plotter, sample_trades_df):
        with pytest.raises(ValueError, match="not found"):
            plotter.plot_pnl_distribution(sample_trades_df, pnl_col="nonexistent")

    def test_custom_title(self, plotter, sample_trades_df):
        fig = plotter.plot_pnl_distribution(
            sample_trades_df,
            title="Custom P&L Distribution",
        )

        assert fig is not None
        # Check title is set
        assert fig.axes[0].get_title() == "Custom P&L Distribution"

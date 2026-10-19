"""
Tests for performance attribution analysis.
"""

from datetime import datetime, timedelta
import pandas as pd
import pytest
import numpy as np
import sys
from pathlib import Path

# Add src to path for direct imports
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Import directly from module file to avoid package import issues
import importlib.util
attribution_path = SRC / "trading" / "attribution.py"
spec = importlib.util.spec_from_file_location("attribution", attribution_path)
attribution_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attribution_module)

# Import functions from the module
AttributionBreakdown = attribution_module.AttributionBreakdown
AttributionReport = attribution_module.AttributionReport
analyze_strategy_attribution = attribution_module.analyze_strategy_attribution
analyze_category_attribution = attribution_module.analyze_category_attribution
analyze_time_period_attribution = attribution_module.analyze_time_period_attribution
analyze_volume_tier_attribution = attribution_module.analyze_volume_tier_attribution
analyze_expiry_window_attribution = attribution_module.analyze_expiry_window_attribution
analyze_trade_size_attribution = attribution_module.analyze_trade_size_attribution
generate_attribution_report = attribution_module.generate_attribution_report
attribution_report_to_dataframe = attribution_module.attribution_report_to_dataframe
calculate_sharpe = attribution_module.calculate_sharpe
calculate_max_drawdown = attribution_module.calculate_max_drawdown
calculate_profit_factor = attribution_module.calculate_profit_factor


@pytest.fixture
def sample_trades_with_strategy() -> pd.DataFrame:
    """Sample trades DataFrame with strategy column."""
    base_time = datetime(2026, 1, 15, 12, 0, 0)
    trades = []
    
    strategies = ["momentum", "mean_reversion", "whale"]
    
    for i in range(30):
        strategy = strategies[i % 3]
        # Make momentum profitable, mean_reversion break-even, whale losing
        if strategy == "momentum":
            pnl = 50.0 + np.random.normal(0, 10)
        elif strategy == "mean_reversion":
            pnl = np.random.normal(0, 5)
        else:  # whale
            pnl = -30.0 + np.random.normal(0, 10)
        
        trades.append({
            "trade_id": f"trade_{i:03d}",
            "market_id": f"market_{i % 5:03d}",
            "strategy": strategy,
            "entry_time": base_time + timedelta(days=i),
            "exit_time": base_time + timedelta(days=i + 1),
            "net_pnl": pnl,
            "gross_pnl": pnl + 5.0,
            "size": 100.0 + i * 10,
            "usd_amount": 1000.0 + i * 100,
        })
    
    return pd.DataFrame(trades)


@pytest.fixture
def sample_trades_with_categories() -> pd.DataFrame:
    """Sample trades DataFrame with category information."""
    base_time = datetime(2026, 1, 15, 12, 0, 0)
    trades = []
    
    categories = ["Politics", "Sports", "Economics"]
    
    for i in range(20):
        category = categories[i % 3]
        # Make Politics profitable, Sports break-even, Economics losing
        if category == "Politics":
            pnl = 40.0 + np.random.normal(0, 8)
        elif category == "Sports":
            pnl = np.random.normal(0, 5)
        else:  # Economics
            pnl = -25.0 + np.random.normal(0, 8)
        
        trades.append({
            "trade_id": f"trade_{i:03d}",
            "market_id": f"market_{i % 5:03d}",
            "entry_time": base_time + timedelta(days=i),
            "exit_time": base_time + timedelta(days=i + 1),
            "net_pnl": pnl,
            "gross_pnl": pnl + 5.0,
            "size": 100.0,
        })
    
    df = pd.DataFrame(trades)
    df["category"] = df["market_id"].map({
        "market_000": "Politics",
        "market_001": "Sports",
        "market_002": "Economics",
        "market_003": "Politics",
        "market_004": "Sports",
    })
    return df


def test_calculate_sharpe():
    """Test Sharpe ratio calculation."""
    # Positive returns
    returns = pd.Series([0.01, 0.02, 0.01, 0.015, 0.01])
    sharpe = calculate_sharpe(returns)
    assert sharpe > 0
    
    # Negative returns
    returns = pd.Series([-0.01, -0.02, -0.01, -0.015, -0.01])
    sharpe = calculate_sharpe(returns)
    assert sharpe < 0
    
    # Zero variance
    returns = pd.Series([0.01, 0.01, 0.01, 0.01, 0.01])
    sharpe = calculate_sharpe(returns)
    assert sharpe == 0
    
    # Empty series
    returns = pd.Series(dtype=float)
    sharpe = calculate_sharpe(returns)
    assert sharpe == 0


def test_calculate_max_drawdown():
    """Test max drawdown calculation."""
    # Simple drawdown
    cumulative = pd.Series([100, 110, 105, 120, 100])
    max_dd = calculate_max_drawdown(cumulative)
    assert max_dd > 0
    
    # No drawdown (always increasing)
    cumulative = pd.Series([100, 110, 120, 130, 140])
    max_dd = calculate_max_drawdown(cumulative)
    assert max_dd == 0
    
    # Empty series
    cumulative = pd.Series(dtype=float)
    max_dd = calculate_max_drawdown(cumulative)
    assert max_dd == 0


def test_calculate_profit_factor():
    """Test profit factor calculation."""
    winners = pd.Series([10, 20, 15])
    losers = pd.Series([-5, -10])
    pf = calculate_profit_factor(winners, losers)
    assert pf > 1  # Should be profitable
    
    # Only winners
    winners = pd.Series([10, 20])
    losers = pd.Series(dtype=float)
    pf = calculate_profit_factor(winners, losers)
    assert pf == float('inf')
    
    # Only losers
    winners = pd.Series(dtype=float)
    losers = pd.Series([-10, -20])
    pf = calculate_profit_factor(winners, losers)
    assert pf == 0


def test_analyze_strategy_attribution(sample_trades_with_strategy):
    """Test strategy attribution analysis."""
    breakdowns = analyze_strategy_attribution(
        sample_trades_with_strategy,
        pnl_column="net_pnl",
        strategy_column="strategy",
    )
    
    assert len(breakdowns) == 3  # Three strategies
    assert "momentum" in breakdowns
    assert "mean_reversion" in breakdowns
    assert "whale" in breakdowns
    
    # Momentum should be most profitable
    momentum_pnl = breakdowns["momentum"].total_pnl
    whale_pnl = breakdowns["whale"].total_pnl
    assert momentum_pnl > whale_pnl
    
    # Check all breakdowns have required fields
    for breakdown in breakdowns.values():
        assert breakdown.dimension == "strategy"
        assert breakdown.trade_count > 0
        assert 0 <= breakdown.win_rate <= 1


def test_analyze_category_attribution(sample_trades_with_categories):
    """Test category attribution analysis."""
    market_categories = {
        f"market_{i:03d}": sample_trades_with_categories[
            sample_trades_with_categories["market_id"] == f"market_{i:03d}"
        ]["category"].iloc[0]
        for i in range(5)
    }
    
    breakdowns = analyze_category_attribution(
        sample_trades_with_categories,
        market_categories=market_categories,
        pnl_column="net_pnl",
    )
    
    assert len(breakdowns) > 0
    
    # Check all breakdowns have required fields
    for breakdown in breakdowns.values():
        assert breakdown.dimension == "category"
        assert breakdown.trade_count > 0


def test_analyze_time_period_attribution(sample_trades_with_strategy):
    """Test time period attribution analysis."""
    breakdowns = analyze_time_period_attribution(
        sample_trades_with_strategy,
        pnl_column="net_pnl",
        time_column="entry_time",
        period="month",
    )
    
    assert len(breakdowns) > 0
    
    # Check all breakdowns have required fields
    for breakdown in breakdowns.values():
        assert breakdown.dimension.startswith("time_")
        assert breakdown.trade_count > 0


def test_analyze_volume_tier_attribution(sample_trades_with_strategy):
    """Test volume tier attribution analysis."""
    # Add market volume column
    sample_trades_with_strategy["market_volume"] = (
        sample_trades_with_strategy.groupby("market_id")["usd_amount"].transform("sum")
    )
    
    breakdowns = analyze_volume_tier_attribution(
        sample_trades_with_strategy,
        pnl_column="net_pnl",
        volume_column="usd_amount",
    )
    
    assert len(breakdowns) > 0
    
    # Check all breakdowns have required fields
    for breakdown in breakdowns.values():
        assert breakdown.dimension == "volume_tier"
        assert breakdown.trade_count > 0


def test_analyze_expiry_window_attribution(sample_trades_with_strategy):
    """Test expiry window attribution analysis."""
    # Add expiry information
    base_time = datetime(2026, 1, 15, 12, 0, 0)
    sample_trades_with_strategy["expiry_date"] = (
        sample_trades_with_strategy["entry_time"] + timedelta(days=np.random.randint(1, 30))
    )
    
    breakdowns = analyze_expiry_window_attribution(
        sample_trades_with_strategy,
        pnl_column="net_pnl",
        entry_time_column="entry_time",
        expiry_column="expiry_date",
    )
    
    assert len(breakdowns) > 0
    
    # Check all breakdowns have required fields
    for breakdown in breakdowns.values():
        assert breakdown.dimension == "expiry_window"
        assert breakdown.trade_count > 0


def test_analyze_trade_size_attribution(sample_trades_with_strategy):
    """Test trade size attribution analysis."""
    breakdowns = analyze_trade_size_attribution(
        sample_trades_with_strategy,
        pnl_column="net_pnl",
        size_column="size",
    )
    
    assert len(breakdowns) > 0
    
    # Check all breakdowns have required fields
    for breakdown in breakdowns.values():
        assert breakdown.dimension == "trade_size"
        assert breakdown.trade_count > 0


def test_generate_attribution_report(sample_trades_with_strategy):
    """Test full attribution report generation."""
    report = generate_attribution_report(
        sample_trades_with_strategy,
        pnl_column="net_pnl",
        strategy_column="strategy",
        time_period="month",
    )
    
    assert isinstance(report, AttributionReport)
    assert report.overall is not None
    assert len(report.by_strategy) > 0
    assert report.overall.trade_count == len(sample_trades_with_strategy)
    
    # Check overall metrics
    assert report.overall.dimension == "overall"
    assert report.overall.trade_count > 0


def test_attribution_report_to_dataframe(sample_trades_with_strategy):
    """Test conversion of attribution report to DataFrame."""
    report = generate_attribution_report(
        sample_trades_with_strategy,
        pnl_column="net_pnl",
        strategy_column="strategy",
    )
    
    df = attribution_report_to_dataframe(report)
    
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert "dimension" in df.columns
    assert "category" in df.columns
    assert "trade_count" in df.columns
    assert "total_pnl" in df.columns
    assert "win_rate" in df.columns
    
    # Check that overall row exists
    overall_rows = df[df["dimension"] == "overall"]
    assert len(overall_rows) == 1


def test_attribution_with_empty_dataframe():
    """Test attribution with empty DataFrame."""
    empty_df = pd.DataFrame()
    
    report = generate_attribution_report(
        empty_df,
        pnl_column="net_pnl",
    )
    
    assert report.overall.trade_count == 0
    assert len(report.by_strategy) == 0


def test_attribution_with_missing_columns():
    """Test attribution handles missing columns gracefully."""
    df = pd.DataFrame([
        {"trade_id": "1", "net_pnl": 10.0},
        {"trade_id": "2", "net_pnl": -5.0},
    ])
    
    # Should not raise error, but return empty breakdowns for missing columns
    breakdowns = analyze_strategy_attribution(df, pnl_column="net_pnl")
    assert len(breakdowns) == 0  # No strategy column
    
    breakdowns = analyze_category_attribution(df, pnl_column="net_pnl")
    assert len(breakdowns) == 0  # No market_id column

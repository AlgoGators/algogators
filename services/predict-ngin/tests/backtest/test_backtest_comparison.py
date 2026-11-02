"""
Tests for src.backtest.comparison module.
"""

import tempfile
from pathlib import Path

import pytest
import pandas as pd

from src.backtest.comparison import ComparisonReport, compare_backtests
from src.backtest.catalog import BacktestRecord


def test_comparison_report_save_html(tmp_path):
    """Test ComparisonReport.save_html()."""
    df = pd.DataFrame({
        "run_id": ["run1", "run2"],
        "strategy_name": ["momentum", "mean_reversion"],
        "sharpe": [1.5, 1.2],
    })
    
    report = ComparisonReport(
        run_ids=["run1", "run2"],
        comparison_df=df,
        metrics_summary={},
    )
    
    output_path = tmp_path / "comparison.html"
    report.save_html(str(output_path))
    
    assert output_path.exists()
    content = output_path.read_text()
    assert "Backtest Comparison" in content
    assert "run1" in content
    assert "run2" in content


def test_comparison_report_save_csv(tmp_path):
    """Test ComparisonReport.save_csv()."""
    df = pd.DataFrame({
        "run_id": ["run1", "run2"],
        "strategy_name": ["momentum", "mean_reversion"],
        "sharpe": [1.5, 1.2],
    })
    
    report = ComparisonReport(
        run_ids=["run1", "run2"],
        comparison_df=df,
        metrics_summary={},
    )
    
    output_path = tmp_path / "comparison.csv"
    report.save_csv(str(output_path))
    
    assert output_path.exists()
    loaded_df = pd.read_csv(output_path)
    assert len(loaded_df) == 2
    assert "run_id" in loaded_df.columns


def test_compare_backtests_empty():
    """Test compare_backtests with empty records."""
    report = compare_backtests([])
    
    assert report.run_ids == []
    assert report.comparison_df.empty
    assert report.metrics_summary == {}


def test_compare_backtests_with_dicts():
    """Test compare_backtests with dictionary records."""
    records = [
        {
            "run_id": "run1",
            "strategy_name": "momentum",
            "timestamp": "2026-01-01T00:00:00",
            "git_commit": "abc123def456",
            "parameters": {"threshold": 0.05},
            "metrics": {"sharpe": 1.5, "return": 0.10},
        },
        {
            "run_id": "run2",
            "strategy_name": "mean_reversion",
            "timestamp": "2026-01-02T00:00:00",
            "git_commit": "def456ghi789",
            "parameters": {"threshold": 0.03},
            "metrics": {"sharpe": 1.2, "return": 0.08},
        },
    ]
    
    report = compare_backtests(records)
    
    assert len(report.run_ids) == 2
    assert len(report.comparison_df) == 2
    assert "run1" in report.run_ids
    assert "run2" in report.run_ids
    assert "sharpe" in report.comparison_df.columns
    assert "return" in report.comparison_df.columns
    assert "param_threshold" in report.comparison_df.columns


def test_compare_backtests_with_backtest_records():
    """Test compare_backtests with BacktestRecord objects."""
    records = [
        BacktestRecord(
            run_id="run1",
            strategy_name="momentum",
            timestamp="2026-01-01T00:00:00",
            git_commit="abc123",
            parameters={"threshold": 0.05},
            metrics={"sharpe": 1.5, "return": 0.10},
            tags=[],
            run_dir="backtests/momentum/run1",
        ),
        BacktestRecord(
            run_id="run2",
            strategy_name="mean_reversion",
            timestamp="2026-01-02T00:00:00",
            git_commit="def456",
            parameters={"threshold": 0.03},
            metrics={"sharpe": 1.2, "return": 0.08},
            tags=[],
            run_dir="backtests/mean_reversion/run2",
        ),
    ]
    
    report = compare_backtests(records)
    
    assert len(report.run_ids) == 2
    assert len(report.comparison_df) == 2
    assert report.comparison_df.iloc[0]["strategy_name"] == "momentum"
    assert report.comparison_df.iloc[1]["strategy_name"] == "mean_reversion"


def test_compare_backtests_with_specific_metrics():
    """Test compare_backtests with specific metrics filter."""
    records = [
        {
            "run_id": "run1",
            "strategy_name": "momentum",
            "timestamp": "2026-01-01T00:00:00",
            "git_commit": "abc123",
            "parameters": {},
            "metrics": {"sharpe": 1.5, "return": 0.10, "max_drawdown": -0.05},
        },
        {
            "run_id": "run2",
            "strategy_name": "mean_reversion",
            "timestamp": "2026-01-02T00:00:00",
            "git_commit": "def456",
            "parameters": {},
            "metrics": {"sharpe": 1.2, "return": 0.08, "max_drawdown": -0.03},
        },
    ]
    
    report = compare_backtests(records, metrics=["sharpe", "return"])
    
    assert "sharpe" in report.comparison_df.columns
    assert "return" in report.comparison_df.columns
    assert "max_drawdown" not in report.comparison_df.columns


def test_compare_backtests_metrics_summary():
    """Test that metrics_summary is calculated correctly."""
    records = [
        {
            "run_id": "run1",
            "strategy_name": "momentum",
            "timestamp": "2026-01-01T00:00:00",
            "git_commit": "abc123",
            "parameters": {},
            "metrics": {"sharpe": 1.5, "return": 0.10},
        },
        {
            "run_id": "run2",
            "strategy_name": "mean_reversion",
            "timestamp": "2026-01-02T00:00:00",
            "git_commit": "def456",
            "parameters": {},
            "metrics": {"sharpe": 1.2, "return": 0.08},
        },
        {
            "run_id": "run3",
            "strategy_name": "breakout",
            "timestamp": "2026-01-03T00:00:00",
            "git_commit": "ghi789",
            "parameters": {},
            "metrics": {"sharpe": 1.8, "return": 0.12},
        },
    ]
    
    report = compare_backtests(records)
    
    assert "sharpe" in report.metrics_summary
    assert "return" in report.metrics_summary
    
    sharpe_summary = report.metrics_summary["sharpe"]
    assert "mean" in sharpe_summary
    assert "std" in sharpe_summary
    assert "min" in sharpe_summary
    assert "max" in sharpe_summary
    assert sharpe_summary["min"] == 1.2
    assert sharpe_summary["max"] == 1.8


def test_compare_backtests_invalid_record_type():
    """Test compare_backtests raises error for invalid record type."""
    records = [123]  # Invalid type
    
    with pytest.raises(ValueError, match="Invalid record type"):
        compare_backtests(records)


def test_compare_backtests_git_commit_truncation():
    """Test that git commits are truncated to 12 characters."""
    records = [
        {
            "run_id": "run1",
            "strategy_name": "momentum",
            "timestamp": "2026-01-01T00:00:00",
            "git_commit": "abcdefghijklmnopqrstuvwxyz",
            "parameters": {},
            "metrics": {},
        },
    ]
    
    report = compare_backtests(records)
    
    assert len(report.comparison_df.iloc[0]["git_commit"]) == 12
    assert report.comparison_df.iloc[0]["git_commit"] == "abcdefghijkl"


def test_compare_backtests_column_ordering():
    """Test that columns are ordered correctly."""
    records = [
        {
            "run_id": "run1",
            "strategy_name": "momentum",
            "timestamp": "2026-01-01T00:00:00",
            "git_commit": "abc123",
            "parameters": {"threshold": 0.05, "window": 20},
            "metrics": {"sharpe": 1.5, "return": 0.10},
        },
    ]
    
    report = compare_backtests(records)
    
    cols = list(report.comparison_df.columns)
    # First columns should be: run_id, strategy_name, timestamp, git_commit
    assert cols[0] == "run_id"
    assert cols[1] == "strategy_name"
    assert cols[2] == "timestamp"
    assert cols[3] == "git_commit"
    # Then param columns (sorted)
    assert "param_threshold" in cols
    assert "param_window" in cols
    # Then metric columns (sorted)
    assert "return" in cols
    assert "sharpe" in cols

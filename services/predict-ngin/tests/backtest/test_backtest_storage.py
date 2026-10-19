"""
Tests for src.backtest.storage module.
"""

import json
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest
import pandas as pd
import sys
from pathlib import Path as PathLib

# Add src to path
ROOT = PathLib(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.backtest.storage import (
    generate_run_id,
    BacktestMetadata,
    _get_git_info,
    save_backtest_result,
    load_backtest_result,
)


def test_generate_run_id():
    """Test run ID generation."""
    run_id = generate_run_id("Momentum Strategy")
    
    assert run_id.startswith("momentum_strategy_")
    assert len(run_id.split("_")) >= 3  # strategy_timestamp_hash
    
    # Should sanitize special characters
    run_id2 = generate_run_id("Test/Strategy")
    assert "/" not in run_id2
    assert "test_strategy" in run_id2.lower()


def test_generate_run_id_uniqueness():
    """Test that generated run IDs are unique."""
    ids = {generate_run_id("test") for _ in range(100)}
    # Should have high uniqueness (allowing for rare collisions)
    assert len(ids) >= 95


def test_backtest_metadata_defaults():
    """Test BacktestMetadata with defaults."""
    metadata = BacktestMetadata(
        run_id="test_123",
        strategy_name="momentum",
        timestamp="2026-01-01T00:00:00"
    )
    
    assert metadata.parameters == {}
    assert metadata.config_snapshot == {}
    assert metadata.environment == {}
    assert metadata.tags == []
    assert metadata.notes == ""


def test_backtest_metadata_to_dict():
    """Test BacktestMetadata.to_dict()."""
    metadata = BacktestMetadata(
        run_id="test_123",
        strategy_name="momentum",
        timestamp="2026-01-01T00:00:00",
        parameters={"threshold": 0.05},
        tags=["test"]
    )
    
    result = metadata.to_dict()
    assert result["run_id"] == "test_123"
    assert result["parameters"] == {"threshold": 0.05}
    assert result["tags"] == ["test"]


def test_backtest_metadata_from_dict():
    """Test BacktestMetadata.from_dict()."""
    data = {
        "run_id": "test_123",
        "strategy_name": "momentum",
        "timestamp": "2026-01-01T00:00:00",
        "parameters": {"threshold": 0.05},
        "tags": ["test"]
    }
    
    metadata = BacktestMetadata.from_dict(data)
    assert metadata.run_id == "test_123"
    assert metadata.parameters == {"threshold": 0.05}


@patch('subprocess.run')
def test_get_git_info_success(mock_run):
    """Test _get_git_info() with successful git commands."""
    # Mock git rev-parse HEAD
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="abc123def456\n"),
        MagicMock(returncode=0, stdout="main\n"),
    ]
    
    info = _get_git_info()
    assert info["git_commit"] == "abc123def456"
    assert info["git_branch"] == "main"


@patch('subprocess.run')
def test_get_git_info_failure(mock_run):
    """Test _get_git_info() when git commands fail."""
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    
    info = _get_git_info()
    assert info["git_commit"] is None
    assert info["git_branch"] is None


def test_save_backtest_result(tmp_path):
    """Test saving a backtest result."""
    from src.trading.reporting import RunSummary, RunMetadata, RunMetrics, RunDiagnostics
    
    strategy_name = "momentum"
    base_dir = tmp_path
    
    trades_df = pd.DataFrame([
        {"datetime": "2026-01-01", "market_id": "market1", "action": "buy", "price": 0.50},
        {"datetime": "2026-01-02", "market_id": "market1", "action": "sell", "price": 0.55},
    ])
    
    # Create a mock result object with proper dataclass values
    metadata = RunMetadata(
        strategy_name=strategy_name,
        run_type="backtest",
        as_of=pd.Timestamp("2026-01-01"),
        start_date=pd.Timestamp("2026-01-01"),
        end_date=pd.Timestamp("2026-01-02"),
        position_size=100.0,  # Add explicit values to avoid MagicMock issues
        starting_capital=10000.0,
    )
    metrics = RunMetrics(
        roi_pct=10.0,
        sharpe_ratio=1.5,
        total_trades=2,
    )
    diagnostics = RunDiagnostics()
    summary = RunSummary(metadata=metadata, metrics=metrics, diagnostics=diagnostics)
    
    # Create a mock result object
    class MockResult:
        def __init__(self):
            self.summary = summary
            self.trades_df = trades_df
    
    result = MockResult()
    
    with patch('src.backtest.storage._get_git_info', return_value={"git_commit": "abc123", "git_branch": "main"}):
        run_id = save_backtest_result(
            strategy_name=strategy_name,
            result=result,
            base_dir=str(base_dir)
        )
    
    # Verify files were created
    run_dir = base_dir / strategy_name / run_id
    assert run_dir.exists()
    assert (run_dir / "metadata.json").exists()
    assert (run_dir / "results" / "summary.json").exists()
    assert (run_dir / "results" / "trades.csv").exists()


def test_load_backtest_result(tmp_path):
    """Test loading a backtest result."""
    run_id = "test_123"
    strategy_name = "momentum"
    base_dir = tmp_path
    
    # Create the result structure
    run_dir = base_dir / strategy_name / run_id
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True)
    
    metadata = {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "timestamp": "2026-01-01T00:00:00"
    }
    
    summary = {
        "metadata": {"strategy_name": strategy_name},
        "metrics": {"roi_pct": 10.0, "sharpe_ratio": 1.5},
        "diagnostics": {},
    }
    
    (run_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (results_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    
    # Create a simple trades CSV
    trades_df = pd.DataFrame([
        {"datetime": "2026-01-01", "market_id": "market1", "action": "buy", "price": 0.50},
    ])
    trades_df.to_csv(results_dir / "trades.csv", index=False)
    
    # Load it
    result = load_backtest_result(run_id, strategy_name, str(base_dir))
    
    assert result["metadata"]["run_id"] == run_id
    assert result["summary"] is not None
    assert result["summary"]["metrics"]["roi_pct"] == 10.0
    assert "trades_df" in result
    assert result["trades_df"] is not None
    assert len(result["trades_df"]) == 1


def test_load_backtest_result_not_found(tmp_path):
    """Test loading a non-existent backtest result."""
    with pytest.raises(FileNotFoundError):
        load_backtest_result("nonexistent", "momentum", str(tmp_path))


def test_save_backtest_result_with_daily_returns(tmp_path):
    """Test saving backtest result with daily returns."""
    from src.trading.reporting import RunSummary, RunMetadata, RunMetrics, RunDiagnostics
    
    strategy_name = "momentum"
    base_dir = tmp_path
    
    trades_df = pd.DataFrame([
        {"datetime": "2026-01-01", "market_id": "market1", "action": "buy", "price": 0.50},
    ])
    
    daily_returns = pd.Series([0.01, 0.02, -0.01], index=pd.date_range("2026-01-01", periods=3))
    
    metadata = RunMetadata(
        strategy_name=strategy_name,
        run_type="backtest",
        as_of=pd.Timestamp("2026-01-01"),
        start_date=pd.Timestamp("2026-01-01"),
        end_date=pd.Timestamp("2026-01-03"),
    )
    metrics = RunMetrics(roi_pct=10.0, sharpe_ratio=1.5, total_trades=1)
    diagnostics = RunDiagnostics()
    summary = RunSummary(metadata=metadata, metrics=metrics, diagnostics=diagnostics)
    
    class MockResult:
        def __init__(self):
            self.summary = summary
            self.trades_df = trades_df
            self.daily_returns = daily_returns
    
    result = MockResult()
    
    with patch('src.backtest.storage._get_git_info', return_value={"git_commit": "abc123", "git_branch": "main"}):
        run_id = save_backtest_result(
            strategy_name=strategy_name,
            result=result,
            base_dir=str(base_dir)
        )
    
    run_dir = base_dir / strategy_name / run_id
    equity_path = run_dir / "results" / "equity_curve.csv"
    assert equity_path.exists()
    
    equity_df = pd.read_csv(equity_path)
    assert "date" in equity_df.columns
    assert "return" in equity_df.columns
    assert "cumulative" in equity_df.columns


def test_save_backtest_result_with_config(tmp_path):
    """Test saving backtest result with config snapshot."""
    from src.trading.reporting import RunSummary, RunMetadata, RunMetrics, RunDiagnostics
    
    strategy_name = "momentum"
    base_dir = tmp_path
    
    trades_df = pd.DataFrame([
        {"datetime": "2026-01-01", "market_id": "market1", "action": "buy", "price": 0.50},
    ])
    
    metadata = RunMetadata(
        strategy_name=strategy_name,
        run_type="backtest",
        as_of=pd.Timestamp("2026-01-01"),
        start_date=pd.Timestamp("2026-01-01"),
        end_date=pd.Timestamp("2026-01-02"),
    )
    metrics = RunMetrics(roi_pct=10.0, sharpe_ratio=1.5, total_trades=1)
    diagnostics = RunDiagnostics()
    summary = RunSummary(metadata=metadata, metrics=metrics, diagnostics=diagnostics)
    
    class MockResult:
        def __init__(self):
            self.summary = summary
            self.trades_df = trades_df
    
    result = MockResult()
    config = {"threshold": 0.05, "window": 20}
    
    with patch('src.backtest.storage._get_git_info', return_value={"git_commit": "abc123", "git_branch": "main"}):
        run_id = save_backtest_result(
            strategy_name=strategy_name,
            result=result,
            config=config,
            base_dir=str(base_dir)
        )
    
    run_dir = base_dir / strategy_name / run_id
    # Check if config file exists (could be .yaml or .json depending on yaml availability)
    config_yaml = run_dir / "config.yaml"
    config_json = run_dir / "config.json"
    assert config_yaml.exists() or config_json.exists()


def test_save_backtest_result_with_signals(tmp_path):
    """Test saving backtest result with signals DataFrame."""
    from src.trading.reporting import RunSummary, RunMetadata, RunMetrics, RunDiagnostics
    
    strategy_name = "momentum"
    base_dir = tmp_path
    
    trades_df = pd.DataFrame([
        {"datetime": "2026-01-01", "market_id": "market1", "action": "buy", "price": 0.50},
    ])
    
    signals_df = pd.DataFrame([
        {"datetime": "2026-01-01", "market_id": "market1", "signal": "buy", "strength": 0.8},
    ])
    
    metadata = RunMetadata(
        strategy_name=strategy_name,
        run_type="backtest",
        as_of=pd.Timestamp("2026-01-01"),
        start_date=pd.Timestamp("2026-01-01"),
        end_date=pd.Timestamp("2026-01-02"),
    )
    metrics = RunMetrics(roi_pct=10.0, sharpe_ratio=1.5, total_trades=1)
    diagnostics = RunDiagnostics()
    summary = RunSummary(metadata=metadata, metrics=metrics, diagnostics=diagnostics)
    
    class MockResult:
        def __init__(self):
            self.summary = summary
            self.trades_df = trades_df
    
    result = MockResult()
    
    with patch('src.backtest.storage._get_git_info', return_value={"git_commit": "abc123", "git_branch": "main"}):
        run_id = save_backtest_result(
            strategy_name=strategy_name,
            result=result,
            signals_df=signals_df,
            base_dir=str(base_dir)
        )
    
    run_dir = base_dir / strategy_name / run_id
    signals_path = run_dir / "signals" / "signals.csv"
    assert signals_path.exists()


def test_load_backtest_result_without_strategy_name(tmp_path):
    """Test loading backtest result by searching all strategies."""
    run_id = "test_123"
    strategy_name = "momentum"
    base_dir = tmp_path
    
    # Create the result structure
    run_dir = base_dir / strategy_name / run_id
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True)
    
    metadata = {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "timestamp": "2026-01-01T00:00:00"
    }
    
    summary = {
        "metadata": {"strategy_name": strategy_name},
        "metrics": {"roi_pct": 10.0},
        "diagnostics": {},
    }
    
    (run_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (results_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    
    # Load without strategy name
    result = load_backtest_result(run_id, strategy_name=None, base_dir=str(base_dir))
    
    assert result["metadata"]["run_id"] == run_id


def test_extract_parameters_from_result():
    """Test _extract_parameters function."""
    from src.backtest.storage import _extract_parameters
    
    # Test with direct attributes
    class ResultWithAttrs:
        def __init__(self):
            self.position_size = 100.0
            self.starting_capital = 10000.0
            self.threshold = 0.05
    
    result = ResultWithAttrs()
    params = _extract_parameters(result)
    assert params["position_size"] == 100.0
    assert params["starting_capital"] == 10000.0
    assert params["threshold"] == 0.05
    
    # Test with summary.metadata
    from src.trading.reporting import RunSummary, RunMetadata, RunMetrics, RunDiagnostics
    
    metadata = RunMetadata(
        strategy_name="test",
        position_size=200.0,
        starting_capital=20000.0,
    )
    summary = RunSummary(
        metadata=metadata,
        metrics=RunMetrics(),
        diagnostics=RunDiagnostics(),
    )
    
    class ResultWithSummary:
        def __init__(self):
            self.summary = summary
    
    result2 = ResultWithSummary()
    params2 = _extract_parameters(result2)
    assert params2["position_size"] == 200.0
    assert params2["starting_capital"] == 20000.0


def test_get_environment_info():
    """Test _get_environment_info function."""
    from src.backtest.storage import _get_environment_info
    
    info = _get_environment_info()
    
    assert "python_version" in info
    assert "platform" in info
    assert "python_executable" in info
    assert isinstance(info["python_version"], str)

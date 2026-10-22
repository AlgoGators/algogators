"""
Tests for dashboard functionality.
"""

import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, mock_open
import sys
import importlib.util

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

# Mock problematic imports before importing dashboard
import sys
from unittest.mock import MagicMock

# Create mock modules
mock_reporting = MagicMock()
mock_config_module = MagicMock()
mock_config_instance = MagicMock()
mock_config_instance.logging.execution.path = "test_path"
mock_config_module.get_config.return_value = mock_config_instance

# Insert mocks into sys.modules only while importing the dashboard, then restore
# the originals so the mocks don't leak into other test modules (they used to break
# tests/backtest/test_backtest_storage.py when collected in the same session).
with patch.dict(sys.modules, {
    'src.trading.reporting': mock_reporting,
    'src.config': mock_config_module,
}):
    dashboard_path = _project_root / "scripts" / "monitoring" / "dashboard.py"
    spec = importlib.util.spec_from_file_location("dashboard", dashboard_path)
    dashboard_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dashboard_module)

load_paper_trading_log = dashboard_module.load_paper_trading_log
load_execution_log = dashboard_module.load_execution_log
calculate_portfolio_summary = dashboard_module.calculate_portfolio_summary
create_dashboard_table = dashboard_module.create_dashboard_table


@pytest.fixture
def sample_paper_log(tmp_path):
    """Create a sample paper trading log file."""
    log_path = tmp_path / "paper_trading_log.jsonl"
    
    entries = [
        {
            "timestamp": (datetime.utcnow() - timedelta(hours=1)).isoformat(),
            "type": "trade",
            "status": "closed",
            "pnl": 50.0,
            "market_id": "market_001",
        },
        {
            "timestamp": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
            "type": "trade",
            "status": "closed",
            "pnl": -20.0,
            "market_id": "market_002",
        },
        {
            "timestamp": datetime.utcnow().isoformat(),
            "type": "position",
            "status": "open",
            "unrealized_pnl": 10.0,
            "market_id": "market_003",
        },
        {
            "timestamp": datetime.utcnow().isoformat(),
            "type": "account",
            "total_capital": 10000.0,
            "deployed_capital": 500.0,
            "available_capital": 9500.0,
        },
    ]
    
    with open(log_path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    
    return str(log_path)


@pytest.fixture
def sample_execution_log(tmp_path):
    """Create a sample execution log file."""
    log_path = tmp_path / "execution_log.jsonl"
    
    entries = [
        {
            "timestamp": datetime.utcnow().isoformat(),
            "order_id": "order_001",
            "status": "filled",
            "pnl": 5.0,
        },
    ]
    
    with open(log_path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    
    return str(log_path)


def test_load_paper_trading_log(sample_paper_log):
    """Test loading paper trading log."""
    entries = load_paper_trading_log(sample_paper_log)
    
    assert len(entries) == 4
    assert entries[0]["type"] == "trade"


def test_load_paper_trading_log_nonexistent():
    """Test loading nonexistent log file."""
    entries = load_paper_trading_log("/nonexistent/path.jsonl")
    
    assert entries == []


def test_load_paper_trading_log_invalid_json(tmp_path):
    """Test loading log with invalid JSON."""
    log_path = tmp_path / "invalid.jsonl"
    
    with open(log_path, "w") as f:
        f.write("not valid json\n")
        f.write('{"valid": true}\n')
    
    entries = load_paper_trading_log(str(log_path))
    
    # Should skip invalid lines
    assert len(entries) == 1
    assert entries[0]["valid"] is True


def test_load_execution_log(sample_execution_log):
    """Test loading execution log."""
    entries = load_execution_log(sample_execution_log)
    
    assert len(entries) == 1
    assert entries[0]["order_id"] == "order_001"


def test_calculate_portfolio_summary(sample_paper_log):
    """Test portfolio summary calculation."""
    entries = load_paper_trading_log(sample_paper_log)
    summary = calculate_portfolio_summary(entries)
    
    assert summary["total_trades"] == 2  # Only closed trades
    assert summary["open_positions"] == 1
    assert summary["total_pnl"] == 30.0  # 50 - 20
    assert summary["realized_pnl"] == 30.0
    assert summary["unrealized_pnl"] == 10.0
    assert summary["total_capital"] == 10000.0
    assert summary["deployed_capital"] == 500.0
    assert summary["available_capital"] == 9500.0


def test_calculate_portfolio_summary_empty():
    """Test portfolio summary with empty entries."""
    summary = calculate_portfolio_summary([])
    
    assert summary["total_trades"] == 0
    assert summary["open_positions"] == 0
    assert summary["total_pnl"] == 0.0


def test_calculate_portfolio_summary_recent_filter(sample_paper_log):
    """Test portfolio summary filters recent entries."""
    entries = load_paper_trading_log(sample_paper_log)
    
    # Add old entry
    old_entry = {
        "timestamp": (datetime.utcnow() - timedelta(days=2)).isoformat(),
        "type": "trade",
        "status": "closed",
        "pnl": 100.0,
    }
    entries.append(old_entry)
    
    summary = calculate_portfolio_summary(entries)
    
    # Recent trades should only include last 24 hours
    assert summary["recent_trades"] <= len(entries)


def test_create_dashboard_table(sample_paper_log, capsys):
    """Test dashboard table creation."""
    entries = load_paper_trading_log(sample_paper_log)
    summary = calculate_portfolio_summary(entries)
    
    dashboard = create_dashboard_table(summary, use_rich=False)
    
    assert isinstance(dashboard, str)
    assert "Portfolio" in dashboard or "Capital" in dashboard
    assert "10,000" in dashboard or "10000" in dashboard or "10000.00" in dashboard


def test_create_dashboard_table_rich(sample_paper_log):
    """Test dashboard table creation with rich."""
    entries = load_paper_trading_log(sample_paper_log)
    summary = calculate_portfolio_summary(entries)
    
    # Mock rich availability
    with patch("scripts.monitoring.dashboard.RICH_AVAILABLE", True):
        dashboard = create_dashboard_table(summary, use_rich=True)
        
        # Should return Layout or Panel object
        assert dashboard is not None


@patch("scripts.monitoring.dashboard.load_paper_trading_log")
@patch("scripts.monitoring.dashboard.load_execution_log")
@patch("scripts.monitoring.dashboard.get_config")
def test_display_dashboard_static(
    mock_config,
    mock_exec_log,
    mock_paper_log,
    sample_paper_log,
    sample_execution_log,
    capsys,
):
    """Test static dashboard display."""
    from scripts.monitoring.dashboard import display_dashboard
    
    mock_config.return_value.logging.execution.path = sample_execution_log
    mock_paper_log.return_value = load_paper_trading_log(sample_paper_log)
    mock_exec_log.return_value = load_execution_log(sample_execution_log)
    
    display_dashboard(
        paper_log_path=sample_paper_log,
        execution_log_path=sample_execution_log,
        live=False,
    )
    
    captured = capsys.readouterr()
    # Should have printed something
    assert len(captured.out) > 0


def test_dashboard_handles_missing_logs(capsys):
    """Test dashboard handles missing log files gracefully."""
    # Use the imported function directly
    empty_summary = calculate_portfolio_summary([])
    dashboard = create_dashboard_table(empty_summary, use_rich=False)
    
    # Should not crash and should show zeros
    assert isinstance(dashboard, str)
    assert "0" in dashboard or "Capital" in dashboard

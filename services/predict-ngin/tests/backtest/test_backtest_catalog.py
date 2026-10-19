"""
Tests for src.backtest.catalog module.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

import pytest

from src.backtest.catalog import BacktestCatalog, BacktestRecord


def test_backtest_record_to_dict():
    """Test BacktestRecord.to_dict()."""
    record = BacktestRecord(
        run_id="test_123",
        strategy_name="momentum",
        timestamp="2026-01-01T00:00:00",
        git_commit="abc123",
        parameters={"threshold": 0.05},
        metrics={"sharpe": 1.5, "return": 0.10},
        tags=["test", "baseline"],
        run_dir="backtests/momentum/test_123"
    )
    
    result = record.to_dict()
    assert result["run_id"] == "test_123"
    assert result["strategy_name"] == "momentum"
    assert isinstance(result["parameters"], str)  # JSON string
    assert json.loads(result["parameters"]) == {"threshold": 0.05}


def test_backtest_record_from_row():
    """Test BacktestRecord.from_row()."""
    row = {
        "run_id": "test_123",
        "strategy_name": "momentum",
        "timestamp": "2026-01-01T00:00:00",
        "git_commit": "abc123",
        "parameters": json.dumps({"threshold": 0.05}),
        "metrics": json.dumps({"sharpe": 1.5}),
        "tags": json.dumps(["test"]),
        "run_dir": "backtests/momentum/test_123"
    }
    
    record = BacktestRecord.from_row(row)
    assert record.run_id == "test_123"
    assert record.parameters == {"threshold": 0.05}
    assert record.metrics == {"sharpe": 1.5}
    assert record.tags == ["test"]


def test_backtest_record_from_row_empty():
    """Test BacktestRecord.from_row() with empty JSON fields."""
    row = {
        "run_id": "test_123",
        "strategy_name": "momentum",
        "timestamp": "2026-01-01T00:00:00",
        "git_commit": None,
        "parameters": None,
        "metrics": None,
        "tags": None,
        "run_dir": "backtests/momentum/test_123"
    }
    
    record = BacktestRecord.from_row(row)
    assert record.git_commit is None
    assert record.parameters == {}
    assert record.metrics == {}
    assert record.tags == []


def test_backtest_catalog_init(tmp_path):
    """Test BacktestCatalog initialization."""
    catalog = BacktestCatalog(base_dir=str(tmp_path))
    assert catalog.base_dir == tmp_path
    assert catalog.catalog_db == tmp_path / "catalog.db"
    assert catalog.catalog_db.exists()


def test_backtest_catalog_init_custom_db(tmp_path):
    """Test BacktestCatalog with custom catalog_db path."""
    custom_db = tmp_path / "custom_catalog.db"
    catalog = BacktestCatalog(base_dir=str(tmp_path), catalog_db=str(custom_db))
    assert catalog.catalog_db == custom_db
    assert custom_db.exists()


def test_backtest_catalog_index_run(tmp_path):
    """Test indexing a backtest run."""
    catalog = BacktestCatalog(base_dir=str(tmp_path))
    
    metadata = {
        "run_id": "test_123",
        "strategy_name": "momentum",
        "timestamp": "2026-01-01T00:00:00",
        "git_commit": "abc123",
        "parameters": {"threshold": 0.05},
    }
    
    summary = {
        "total_return": 0.10,
        "sharpe_ratio": 1.5,
    }
    
    run_dir = tmp_path / "momentum" / "test_123"
    run_dir.mkdir(parents=True)
    
    catalog.index_run(
        run_id="test_123",
        strategy_name="momentum",
        metadata=metadata,
        summary=summary,
        run_dir=run_dir
    )
    
    # Verify it was indexed
    records = catalog.search(strategy_name="momentum")
    assert len(records) == 1
    assert records[0].run_id == "test_123"


def test_backtest_catalog_search_by_strategy(tmp_path):
    """Test searching by strategy name."""
    catalog = BacktestCatalog(base_dir=str(tmp_path))
    
    # Index multiple runs
    for i in range(3):
        catalog.index_run(
            run_id=f"test_{i}",
            strategy_name="momentum",
            metadata={
                "run_id": f"test_{i}",
                "strategy_name": "momentum",
                "timestamp": f"2026-01-0{i+1}T00:00:00",
            },
            run_dir=tmp_path / "momentum" / f"test_{i}"
        )
    
    catalog.index_run(
        run_id="other_1",
        strategy_name="mean_reversion",
        metadata={
            "run_id": "other_1",
            "strategy_name": "mean_reversion",
            "timestamp": "2026-01-01T00:00:00",
        },
        run_dir=tmp_path / "mean_reversion" / "other_1"
    )
    
    # Search for momentum only
    records = catalog.search(strategy_name="momentum")
    assert len(records) == 3
    assert all(r.strategy_name == "momentum" for r in records)


def test_backtest_catalog_search_by_git_commit(tmp_path):
    """Test searching by git commit."""
    catalog = BacktestCatalog(base_dir=str(tmp_path))
    
    catalog.index_run(
        run_id="test_1",
        strategy_name="momentum",
        metadata={
            "run_id": "test_1",
            "strategy_name": "momentum",
            "timestamp": "2026-01-01T00:00:00",
            "git_commit": "abc123",
        },
        run_dir=tmp_path / "momentum" / "test_1"
    )
    
    catalog.index_run(
        run_id="test_2",
        strategy_name="momentum",
        metadata={
            "run_id": "test_2",
            "strategy_name": "momentum",
            "timestamp": "2026-01-02T00:00:00",
            "git_commit": "def456",
        },
        run_dir=tmp_path / "momentum" / "test_2"
    )
    
    records = catalog.search(git_commit="abc123")
    assert len(records) == 1
    assert records[0].run_id == "test_1"


def test_backtest_catalog_get_run(tmp_path):
    """Test getting a specific run by ID."""
    catalog = BacktestCatalog(base_dir=str(tmp_path))
    
    catalog.index_run(
        run_id="test_123",
        strategy_name="momentum",
        metadata={
            "run_id": "test_123",
            "strategy_name": "momentum",
            "timestamp": "2026-01-01T00:00:00",
        },
        run_dir=tmp_path / "momentum" / "test_123"
    )
    
    record = catalog.get_run("test_123")
    assert record is not None
    assert record.run_id == "test_123"
    
    # Non-existent run
    assert catalog.get_run("nonexistent") is None


def test_backtest_catalog_get_best(tmp_path):
    """Test getting best run by metric."""
    catalog = BacktestCatalog(base_dir=str(tmp_path))
    
    # Index runs with different sharpe ratios
    for i, sharpe in enumerate([1.0, 2.0, 1.5]):
        catalog.index_run(
            run_id=f"test_{i}",
            strategy_name="momentum",
            metadata={
                "run_id": f"test_{i}",
                "strategy_name": "momentum",
                "timestamp": f"2026-01-0{i+1}T00:00:00",
            },
            summary={"metrics": {"sharpe_ratio": sharpe}},
            run_dir=tmp_path / "momentum" / f"test_{i}"
        )
    
    best = catalog.get_best("momentum", metric="sharpe_ratio")
    assert best is not None
    assert best.metrics.get("sharpe_ratio") == 2.0

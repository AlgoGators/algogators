"""
Backtest catalog for searchable indexing of all backtest runs.

Provides fast search and comparison capabilities.
"""

import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import logging

import pandas as pd


@dataclass
class BacktestRecord:
    """Record of a backtest run in the catalog."""
    
    run_id: str
    strategy_name: str
    timestamp: str
    git_commit: Optional[str]
    parameters: Dict[str, Any]
    metrics: Dict[str, float]
    tags: List[str]
    run_dir: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "run_id": self.run_id,
            "strategy_name": self.strategy_name,
            "timestamp": self.timestamp,
            "git_commit": self.git_commit,
            "parameters": json.dumps(self.parameters),
            "metrics": json.dumps(self.metrics),
            "tags": json.dumps(self.tags),
            "run_dir": self.run_dir,
        }
    
    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "BacktestRecord":
        """Create from database row."""
        return cls(
            run_id=row["run_id"],
            strategy_name=row["strategy_name"],
            timestamp=row["timestamp"],
            git_commit=row.get("git_commit"),
            parameters=json.loads(row.get("parameters") or "{}"),
            metrics=json.loads(row.get("metrics") or "{}"),
            tags=json.loads(row.get("tags") or "[]"),
            run_dir=row["run_dir"],
        )


class BacktestCatalog:
    """
    Searchable catalog of all backtest runs.
    
    Indexes backtest results in SQLite for fast search and comparison.
    """
    
    def __init__(
        self,
        base_dir: Union[str, Path] = "backtests",
        catalog_db: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize catalog.
        
        Args:
            base_dir: Base directory containing backtests
            catalog_db: Path to catalog database (default: base_dir/catalog.db)
            logger: Optional logger
        """
        self.base_dir = Path(base_dir)
        self.catalog_db = Path(catalog_db) if catalog_db else self.base_dir / "catalog.db"
        self.logger = logger or logging.getLogger("backtest.catalog")
        
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize catalog database schema."""
        conn = sqlite3.connect(str(self.catalog_db))
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backtests (
                run_id TEXT PRIMARY KEY,
                strategy_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                git_commit TEXT,
                parameters TEXT,
                metrics TEXT,
                tags TEXT,
                run_dir TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            )
        """)
        
        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_strategy ON backtests(strategy_name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON backtests(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_git_commit ON backtests(git_commit)
        """)
        
        conn.commit()
        conn.close()
    
    def index_run(
        self,
        run_id: str,
        strategy_name: str,
        metadata: Dict[str, Any],
        summary: Optional[Dict[str, Any]] = None,
        run_dir: Optional[Path] = None,
    ) -> None:
        """
        Index a backtest run in the catalog.
        
        Args:
            run_id: Run ID
            strategy_name: Strategy name
            metadata: Metadata dictionary
            summary: Optional summary dictionary with metrics
            run_dir: Optional run directory path
        """
        if run_dir is None:
            run_dir = self.base_dir / strategy_name / run_id
        
        # Extract metrics from summary
        metrics = {}
        if summary and "metrics" in summary:
            metrics = summary["metrics"]
        elif summary and isinstance(summary, dict):
            # Try to find metrics directly
            metrics = summary.get("metrics", {})
        
        # Convert metrics to float where possible
        metrics_clean = {}
        for k, v in metrics.items():
            try:
                if isinstance(v, (int, float)):
                    metrics_clean[k] = float(v)
                elif isinstance(v, str) and v.replace(".", "").replace("-", "").isdigit():
                    metrics_clean[k] = float(v)
            except (ValueError, TypeError):
                pass
        
        record = BacktestRecord(
            run_id=run_id,
            strategy_name=strategy_name,
            timestamp=metadata.get("timestamp", datetime.utcnow().isoformat()),
            git_commit=metadata.get("git_commit"),
            parameters=metadata.get("parameters", {}),
            metrics=metrics_clean,
            tags=metadata.get("tags", []),
            run_dir=str(run_dir),
        )
        
        conn = sqlite3.connect(str(self.catalog_db))
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO backtests
            (run_id, strategy_name, timestamp, git_commit, parameters,
             metrics, tags, run_dir, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.run_id,
            record.strategy_name,
            record.timestamp,
            record.git_commit,
            json.dumps(record.parameters),
            json.dumps(record.metrics),
            json.dumps(record.tags),
            record.run_dir,
            datetime.utcnow().isoformat(),
        ))
        
        conn.commit()
        conn.close()
        
        self.logger.debug(f"Indexed run: {run_id}")
    
    def search(
        self,
        strategy_name: Optional[str] = None,
        min_sharpe: Optional[float] = None,
        min_win_rate: Optional[float] = None,
        min_roi: Optional[float] = None,
        tags: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        git_commit: Optional[str] = None,
        limit: int = 100,
    ) -> List[BacktestRecord]:
        """
        Search backtests by criteria.
        
        Args:
            strategy_name: Filter by strategy name
            min_sharpe: Minimum Sharpe ratio
            min_win_rate: Minimum win rate (0-1)
            min_roi: Minimum ROI percentage
            tags: Filter by tags (any match)
            start_date: Filter by start date (YYYY-MM-DD)
            end_date: Filter by end date
            git_commit: Filter by git commit hash
            limit: Maximum results to return
            
        Returns:
            List of matching BacktestRecord objects
        """
        conn = sqlite3.connect(str(self.catalog_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM backtests WHERE 1=1"
        params: List[Any] = []
        
        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)
        
        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date)
        
        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date)
        
        if git_commit:
            query += " AND git_commit = ?"
            params.append(git_commit)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        records = [BacktestRecord.from_row(dict(row)) for row in rows]
        
        # Filter by metrics (in Python since metrics are JSON)
        if min_sharpe is not None:
            records = [
                r for r in records
                if r.metrics.get("sharpe_ratio", r.metrics.get("sharpe", 0)) >= min_sharpe
            ]
        
        if min_win_rate is not None:
            records = [
                r for r in records
                if r.metrics.get("win_rate", 0) >= min_win_rate
            ]
        
        if min_roi is not None:
            records = [
                r for r in records
                if r.metrics.get("roi_pct", 0) >= min_roi
            ]
        
        # Filter by tags
        if tags:
            records = [
                r for r in records
                if any(t in r.tags for t in tags)
            ]
        
        return records
    
    def get_run(self, run_id: str) -> Optional[BacktestRecord]:
        """
        Get a specific run by ID.
        
        Args:
            run_id: Run identifier
            
        Returns:
            BacktestRecord or None if not found
        """
        conn = sqlite3.connect(str(self.catalog_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM backtests WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return BacktestRecord.from_row(dict(row))
        return None
    
    def get_best(
        self,
        strategy_name: str,
        metric: str = "sharpe_ratio",
        minimize: bool = False,
    ) -> Optional[BacktestRecord]:
        """
        Get the best run by a specific metric.
        
        Args:
            strategy_name: Strategy name
            metric: Metric to optimize (e.g., "sharpe_ratio", "win_rate")
            minimize: If True, find minimum instead of maximum
            
        Returns:
            Best BacktestRecord or None
        """
        runs = self.search(strategy_name=strategy_name, limit=1000)
        
        if not runs:
            return None
        
        # Filter to runs with the metric
        runs_with_metric = [
            r for r in runs
            if metric in r.metrics or metric.replace("_ratio", "") in r.metrics
        ]
        
        if not runs_with_metric:
            return None
        
        def get_metric_value(r: BacktestRecord) -> float:
            if metric in r.metrics:
                return r.metrics[metric]
            # Try without _ratio suffix
            alt = metric.replace("_ratio", "")
            return r.metrics.get(alt, 0.0)
        
        if minimize:
            return min(runs_with_metric, key=get_metric_value)
        return max(runs_with_metric, key=get_metric_value)
    
    def reindex_all(self) -> int:
        """
        Reindex all backtests in the base directory.
        
        Returns:
            Number of runs indexed
        """
        try:
            from .storage import load_backtest_result
        except ImportError:
            # Fallback if circular import
            from src.backtest.storage import load_backtest_result
        
        count = 0
        
        for strategy_dir in self.base_dir.iterdir():
            if not strategy_dir.is_dir() or strategy_dir.name.startswith("."):
                continue
            
            strategy_name = strategy_dir.name
            
            for run_dir in strategy_dir.iterdir():
                if not run_dir.is_dir() or run_dir.name == "latest":
                    continue
                
                run_id = run_dir.name
                
                try:
                    result = load_backtest_result(run_id, strategy_name, self.base_dir)
                    self.index_run(
                        run_id=run_id,
                        strategy_name=strategy_name,
                        metadata=result["metadata"],
                        summary=result.get("summary"),
                        run_dir=run_dir,
                    )
                    count += 1
                except Exception as e:
                    self.logger.warning(f"Failed to index {run_id}: {e}")
        
        self.logger.info(f"Reindexed {count} runs")
        return count


__all__ = ["BacktestCatalog", "BacktestRecord"]

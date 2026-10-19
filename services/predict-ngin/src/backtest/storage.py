"""
Backtest result storage utilities.

Provides organized storage of backtest results with metadata,
config snapshots, and artifact management.
"""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union
import logging

import pandas as pd

try:
    import yaml
except ImportError:
    yaml = None

from ..trading.reporting import RunSummary, save_trades_csv, save_summary_csv


def generate_run_id(strategy_name: str) -> str:
    """
    Generate unique run ID: {strategy}_{timestamp}_{hash}
    
    Args:
        strategy_name: Strategy name (will be sanitized)
        
    Returns:
        Unique run ID string
    """
    # Sanitize strategy name
    safe_name = strategy_name.lower().replace(" ", "_").replace("/", "_")
    
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    unique = uuid.uuid4().hex[:6]
    return f"{safe_name}_{timestamp}_{unique}"


@dataclass
class BacktestMetadata:
    """Metadata for a backtest run."""
    
    run_id: str
    strategy_name: str
    timestamp: str
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    code_version: Optional[str] = None
    parameters: Dict[str, Any] = None
    config_snapshot: Dict[str, Any] = None
    environment: Dict[str, Any] = None
    tags: list = None
    notes: str = ""
    
    def __post_init__(self):
        if self.parameters is None:
            self.parameters = {}
        if self.config_snapshot is None:
            self.config_snapshot = {}
        if self.environment is None:
            self.environment = {}
        if self.tags is None:
            self.tags = []
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BacktestMetadata":
        """Create from dictionary."""
        return cls(**data)


def _get_git_info() -> Dict[str, Optional[str]]:
    """Get git commit and branch info."""
    import subprocess
    
    info = {"git_commit": None, "git_branch": None}
    
    try:
        # Get commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            info["git_commit"] = result.stdout.strip()[:12]
        
        # Get branch name
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            info["git_branch"] = result.stdout.strip()
    except Exception:
        pass
    
    return info


def _get_environment_info() -> Dict[str, Any]:
    """Get environment information."""
    import sys
    import platform
    
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "python_executable": sys.executable,
    }


def save_backtest_result(
    strategy_name: str,
    result: Any,
    config: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    base_dir: Union[str, Path] = "backtests",
    git_info: bool = True,
    environment_info: bool = True,
    tags: Optional[list] = None,
    notes: str = "",
    logger: Optional[logging.Logger] = None,
    signals_df: Optional[pd.DataFrame] = None,
    quantstats_html_path: Optional[Union[str, Path]] = None,
    auto_index: bool = False,
) -> str:
    """
    Save backtest result to organized structure.
    
    Args:
        strategy_name: Name of the strategy
        result: BacktestResult or RunSummary object
        config: Configuration dictionary snapshot
        run_id: Optional run ID (will be generated if not provided)
        base_dir: Base directory for backtests
        git_info: Whether to capture git commit/branch
        environment_info: Whether to capture environment info
        tags: Optional tags for categorization
        notes: Optional notes about the run
        logger: Optional logger
        signals_df: Optional DataFrame of input signals to save
        quantstats_html_path: Optional path to quantstats HTML report to copy
        auto_index: If True, automatically index this run in the catalog
        
    Returns:
        Run ID string
    """
    log = logger or logging.getLogger("backtest.storage")
    
    # Generate run ID if not provided
    if run_id is None:
        run_id = generate_run_id(strategy_name)
    
    # Create directory structure
    base_path = Path(base_dir)
    strategy_dir = base_path / strategy_name
    run_dir = strategy_dir / run_id
    results_dir = run_dir / "results"
    signals_dir = run_dir / "signals"
    logs_dir = run_dir / "logs"
    
    for d in [run_dir, results_dir, signals_dir, logs_dir]:
        d.mkdir(parents=True, exist_ok=True)
    
    log.info(f"Saving backtest result: {run_id}")
    
    # Extract metrics and data
    if isinstance(result, dict):
        # Whale backtest and similar dict-based results
        trades_df = result.get("trades_df")
        daily_returns = result.get("daily_returns")
        summary = None
        if trades_df is not None and not trades_df.empty:
            try:
                from ..trading.reporting import (
                    RunMetadata,
                    RunMetrics,
                    RunDiagnostics,
                    RunSummary,
                )
                metrics = RunMetrics(
                    total_trades=result.get("total_trades", 0),
                    win_rate=result.get("win_rate", 0.0),
                    total_net_pnl=result.get("total_net_pnl", 0.0),
                    roi_pct=result.get("roi_pct", 0.0),
                )
                summary = RunSummary(
                    metadata=RunMetadata(strategy_name=strategy_name),
                    metrics=metrics,
                    diagnostics=RunDiagnostics(),
                )
            except Exception:
                summary = None
    elif hasattr(result, "summary"):
        # PolymarketBacktestResult or similar
        summary = result.summary
        trades_df = result.trades_df
        daily_returns = getattr(result, "daily_returns", None)
    elif hasattr(result, "metrics"):
        # RunSummary
        summary = result
        trades_df = getattr(result, "trades_df", None)
        daily_returns = None
    else:
        # Try to extract directly
        summary = None
        trades_df = getattr(result, "trades_df", None)
        daily_returns = getattr(result, "daily_returns", None)
    
    # Build metadata
    git_data = _get_git_info() if git_info else {}
    env_data = _get_environment_info() if environment_info else {}
    
    metadata = BacktestMetadata(
        run_id=run_id,
        strategy_name=strategy_name,
        timestamp=datetime.utcnow().isoformat(),
        git_commit=git_data.get("git_commit"),
        git_branch=git_data.get("git_branch"),
        parameters={**_extract_parameters(result), **(config or {})},
        config_snapshot=config or {},
        environment=env_data,
        tags=tags or [],
        notes=notes,
    )
    
    # Save metadata
    metadata_path = run_dir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata.to_dict(), f, indent=2)
    
    # Save config snapshot
    if config:
        config_path = run_dir / "config.yaml"
        if yaml:
            with open(config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False)
        else:
            # Fallback to JSON if yaml not available
            config_path = run_dir / "config.json"
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
    
    # Save results
    if summary:
        # Save summary as JSON
        summary_dict = {
            "metadata": asdict(summary.metadata),
            "metrics": asdict(summary.metrics),
            "diagnostics": asdict(summary.diagnostics),
        }
        summary_path = results_dir / "summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary_dict, f, indent=2, default=str)
        
        # Save diagnostics separately as JSON (per recommended structure)
        diagnostics_dict = asdict(summary.diagnostics)
        diagnostics_path = results_dir / "diagnostics.json"
        with open(diagnostics_path, "w") as f:
            json.dump(diagnostics_dict, f, indent=2, default=str)
    
    # Save trades CSV
    if trades_df is not None and not trades_df.empty:
        trades_path = results_dir / "trades.csv"
        save_trades_csv(trades_df, str(trades_path))
    
    # Save equity curve (daily returns)
    if daily_returns is not None and len(daily_returns) > 0:
        equity_df = pd.DataFrame({
            "date": daily_returns.index,
            "return": daily_returns.values,
            "cumulative": (1 + daily_returns).cumprod() - 1,
        })
        equity_path = results_dir / "equity_curve.csv"
        equity_df.to_csv(equity_path, index=False)
    
    # Save quantstats HTML report if provided
    if quantstats_html_path:
        quantstats_path = Path(quantstats_html_path)
        if quantstats_path.exists():
            target_path = results_dir / "quantstats.html"
            import shutil
            shutil.copy2(quantstats_path, target_path)
            log.debug(f"Copied quantstats report to: {target_path}")
    
    # Save signals CSV if provided
    if signals_df is not None and not signals_df.empty:
        signals_path = signals_dir / "signals.csv"
        signals_df.to_csv(signals_path, index=False)
        log.debug(f"Saved signals to: {signals_path}")
    
    # Create symlink to latest
    latest_link = strategy_dir / "latest"
    if latest_link.exists() or latest_link.is_symlink():
        latest_link.unlink()
    try:
        latest_link.symlink_to(run_id)
    except OSError:
        # Windows may not support symlinks, create a text file instead
        with open(strategy_dir / "latest.txt", "w") as f:
            f.write(run_id)
    
    # Auto-index in catalog if requested
    if auto_index:
        try:
            from .catalog import BacktestCatalog
            catalog = BacktestCatalog(base_dir=base_path)
            # Build a flat metrics dict for the catalog from the result
            if isinstance(result, dict):
                direct_metrics = {
                    "total_trades":    result.get("total_trades", 0),
                    "win_rate":        result.get("win_rate", 0.0),
                    "total_net_pnl":   result.get("total_net_pnl", 0.0),
                    "roi_pct":         result.get("roi_pct", 0.0),
                    "sharpe_ratio":    result.get("sharpe_ratio", 0.0),
                    "max_drawdown_pct": result.get("max_drawdown_pct", 0.0),
                    "profit_factor":   result.get("profit_factor", 0.0),
                    "avg_win":         result.get("avg_win", 0.0),
                    "avg_loss":        result.get("avg_loss", 0.0),
                    "whales_followed": result.get("whales_followed", 0),
                }
                index_summary = {"metrics": direct_metrics}
            else:
                index_summary = summary_dict if summary else None
            catalog.index_run(
                run_id=run_id,
                strategy_name=strategy_name,
                metadata=metadata.to_dict(),
                summary=index_summary,
                run_dir=run_dir,
            )
            log.debug(f"Auto-indexed run in catalog: {run_id}")
        except Exception as e:
            log.warning(f"Failed to auto-index run: {e}")
    
    log.info(f"Backtest saved to: {run_dir}")
    return run_id


def _extract_parameters(result: Any) -> Dict[str, Any]:
    """Extract parameters from result object."""
    params = {}

    # Dict-based result (e.g. whale backtest)
    if isinstance(result, dict):
        for attr in ["capital", "min_usd", "train_ratio", "whales_followed", "categories"]:
            if attr in result and result[attr] is not None:
                params[attr] = result[attr]
        return params

    # Try common attributes
    for attr in ["position_size", "starting_capital", "threshold", "config"]:
        if hasattr(result, attr):
            value = getattr(result, attr)
            if value is not None:
                params[attr] = value

    # Try summary.metadata
    if hasattr(result, "summary") and hasattr(result.summary, "metadata"):
        meta = result.summary.metadata
        for attr in ["position_size", "starting_capital", "cost_model"]:
            if hasattr(meta, attr):
                value = getattr(meta, attr)
                if value is not None:
                    params[attr] = value

    return params


def load_backtest_result(
    run_id: str,
    strategy_name: Optional[str] = None,
    base_dir: Union[str, Path] = "backtests",
) -> Dict[str, Any]:
    """
    Load a backtest result from storage.
    
    Args:
        run_id: Run ID to load
        strategy_name: Optional strategy name (will search if not provided)
        base_dir: Base directory for backtests
        
    Returns:
        Dictionary with metadata, summary, trades_df, etc.
    """
    base_path = Path(base_dir)
    
    # Find the run directory
    if strategy_name:
        run_dir = base_path / strategy_name / run_id
    else:
        # Search all strategies
        run_dir = None
        for strategy_dir in base_path.iterdir():
            if strategy_dir.is_dir():
                candidate = strategy_dir / run_id
                if candidate.exists():
                    run_dir = candidate
                    break
        
        if run_dir is None:
            raise FileNotFoundError(f"Run {run_id} not found")
    
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")
    
    results_dir = run_dir / "results"
    
    # Load metadata
    metadata_path = run_dir / "metadata.json"
    if metadata_path.exists():
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
    else:
        metadata = {}
    
    # Load summary
    summary_path = results_dir / "summary.json"
    summary = None
    if summary_path.exists():
        with open(summary_path, "r") as f:
            summary = json.load(f)
    
    # Load trades
    trades_path = results_dir / "trades.csv"
    trades_df = None
    if trades_path.exists():
        trades_df = pd.read_csv(trades_path)
    
    # Load equity curve
    equity_path = results_dir / "equity_curve.csv"
    daily_returns = None
    if equity_path.exists():
        equity_df = pd.read_csv(equity_path)
        if "return" in equity_df.columns:
            daily_returns = pd.Series(
                equity_df["return"].values,
                index=pd.to_datetime(equity_df["date"]),
            )
    
    return {
        "run_id": run_id,
        "metadata": metadata,
        "summary": summary,
        "trades_df": trades_df,
        "daily_returns": daily_returns,
        "run_dir": run_dir,
    }


__all__ = [
    "generate_run_id",
    "BacktestMetadata",
    "save_backtest_result",
    "load_backtest_result",
]

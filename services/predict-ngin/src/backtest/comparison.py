"""
Backtest comparison utilities.

Provides side-by-side comparison of multiple backtest runs.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

import pandas as pd


@dataclass
class ComparisonReport:
    """Report comparing multiple backtest runs."""
    
    run_ids: List[str]
    comparison_df: pd.DataFrame
    metrics_summary: Dict[str, Dict[str, float]]
    
    def save_html(self, output_path: str) -> None:
        """Save comparison as HTML table."""
        html = self.comparison_df.to_html(classes="table table-striped", escape=False)
        
        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Backtest Comparison</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #4CAF50; color: white; }}
                tr:nth-child(even) {{ background-color: #f2f2f2; }}
            </style>
        </head>
        <body>
            <h1>Backtest Comparison</h1>
            <p>Comparing {len(self.run_ids)} runs</p>
            {html}
        </body>
        </html>
        """
        
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(full_html)
    
    def save_csv(self, output_path: str) -> None:
        """Save comparison as CSV."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self.comparison_df.to_csv(output_path, index=False)


def compare_backtests(
    records: List[Any],
    metrics: Optional[List[str]] = None,
) -> ComparisonReport:
    """
    Compare multiple backtest runs.
    
    Args:
        records: List of BacktestRecord objects or run dictionaries
        metrics: Specific metrics to compare (default: all available)
        
    Returns:
        ComparisonReport with side-by-side comparison
    """
    if not records:
        return ComparisonReport(
            run_ids=[],
            comparison_df=pd.DataFrame(),
            metrics_summary={},
        )
    
    # Normalize records
    normalized = []
    for r in records:
        if hasattr(r, "run_id"):
            # BacktestRecord
            normalized.append({
                "run_id": r.run_id,
                "strategy_name": r.strategy_name,
                "timestamp": r.timestamp,
                "git_commit": r.git_commit,
                "metrics": r.metrics,
                "parameters": r.parameters,
            })
        elif isinstance(r, dict):
            normalized.append(r)
        else:
            raise ValueError(f"Invalid record type: {type(r)}")
    
    # Build comparison DataFrame
    rows = []
    for rec in normalized:
        row = {
            "run_id": rec["run_id"],
            "strategy_name": rec.get("strategy_name", ""),
            "timestamp": rec.get("timestamp", ""),
            "git_commit": rec.get("git_commit", "")[:12] if rec.get("git_commit") else "",
        }
        
        # Add parameters
        params = rec.get("parameters", {})
        for key, value in params.items():
            row[f"param_{key}"] = value
        
        # Add metrics
        metrics_dict = rec.get("metrics", {})
        if metrics is None:
            # Include all metrics
            for key, value in metrics_dict.items():
                row[key] = value
        else:
            # Include only specified metrics
            for key in metrics:
                row[key] = metrics_dict.get(key, None)
        
        rows.append(row)
    
    df = pd.DataFrame(rows)
    
    # Reorder columns
    first_cols = ["run_id", "strategy_name", "timestamp", "git_commit"]
    param_cols = [c for c in df.columns if c.startswith("param_")]
    metric_cols = [c for c in df.columns if c not in first_cols + param_cols]
    
    ordered_cols = first_cols + sorted(param_cols) + sorted(metric_cols)
    df = df[[c for c in ordered_cols if c in df.columns]]
    
    # Calculate metrics summary
    metrics_summary = {}
    for col in metric_cols:
        if col in df.columns and df[col].dtype in ["float64", "int64"]:
            metrics_summary[col] = {
                "mean": float(df[col].mean()),
                "std": float(df[col].std()),
                "min": float(df[col].min()),
                "max": float(df[col].max()),
            }
    
    return ComparisonReport(
        run_ids=[r["run_id"] for r in normalized],
        comparison_df=df,
        metrics_summary=metrics_summary,
    )


__all__ = ["ComparisonReport", "compare_backtests"]

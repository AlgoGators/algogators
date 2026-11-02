#!/usr/bin/env python3
"""
List and search backtest runs.

Usage:
    python scripts/list_backtests.py
    python scripts/list_backtests.py --strategy momentum --min-sharpe 2.0
    python scripts/list_backtests.py --strategy momentum --limit 10
"""

import argparse
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

# Add project root and src for imports
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

from src.backtest.catalog import BacktestCatalog


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List and search backtest runs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Filter by strategy name",
    )
    parser.add_argument(
        "--min-sharpe",
        type=float,
        default=None,
        help="Minimum Sharpe ratio",
    )
    parser.add_argument(
        "--min-win-rate",
        type=float,
        default=None,
        help="Minimum win rate (0-1)",
    )
    parser.add_argument(
        "--min-roi",
        type=float,
        default=None,
        help="Minimum ROI percentage",
    )
    parser.add_argument(
        "--tags",
        nargs="+",
        default=None,
        help="Filter by tags",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Start date filter (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="End date filter (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum results to show",
    )
    parser.add_argument(
        "--backtests-dir",
        default=str(_project_root / "backtests"),
        help="Backtests directory",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json", "csv"),
        default="table",
        help="Output format",
    )
    args = parser.parse_args()

    catalog = BacktestCatalog(base_dir=args.backtests_dir)
    
    # Search runs
    runs = catalog.search(
        strategy_name=args.strategy,
        min_sharpe=args.min_sharpe,
        min_win_rate=args.min_win_rate,
        min_roi=args.min_roi,
        tags=args.tags,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
    )
    
    if not runs:
        print("No backtests found matching criteria.")
        return 0
    
    # Format output
    if args.format == "json":
        import json
        output = [
            {
                "run_id": r.run_id,
                "strategy_name": r.strategy_name,
                "timestamp": r.timestamp,
                "metrics": r.metrics,
                "parameters": r.parameters,
            }
            for r in runs
        ]
        print(json.dumps(output, indent=2))
    elif args.format == "csv":
        import pandas as pd
        
        rows = []
        for r in runs:
            row = {
                "run_id": r.run_id,
                "strategy_name": r.strategy_name,
                "timestamp": r.timestamp,
                "sharpe_ratio": r.metrics.get("sharpe_ratio", ""),
                "win_rate": r.metrics.get("win_rate", ""),
                "roi_pct": r.metrics.get("roi_pct", ""),
                "total_net_pnl": r.metrics.get("total_net_pnl", ""),
            }
            rows.append(row)
        
        df = pd.DataFrame(rows)
        print(df.to_csv(index=False))
    else:
        # Table format
        print(f"\nFound {len(runs)} backtest(s):\n")
        print(f"{'Run ID':<30} {'Strategy':<15} {'Timestamp':<20} {'Sharpe':<8} {'Win Rate':<10} {'ROI %':<10}")
        print("-" * 100)
        
        for r in runs:
            sharpe = r.metrics.get("sharpe_ratio", r.metrics.get("sharpe", ""))
            win_rate = r.metrics.get("win_rate", "")
            roi = r.metrics.get("roi_pct", "")
            
            sharpe_str = f"{sharpe:.2f}" if isinstance(sharpe, (int, float)) else str(sharpe)
            win_rate_str = f"{win_rate:.1%}" if isinstance(win_rate, (int, float)) else str(win_rate)
            roi_str = f"{roi:.1f}%" if isinstance(roi, (int, float)) else str(roi)
            
            timestamp_short = r.timestamp[:19] if len(r.timestamp) > 19 else r.timestamp
            
            print(
                f"{r.run_id:<30} {r.strategy_name:<15} {timestamp_short:<20} "
                f"{sharpe_str:<8} {win_rate_str:<10} {roi_str:<10}"
            )
        
        print("\nUse --format json or --format csv for machine-readable output.")
        print("Use 'python scripts/compare_backtests.py <run_id1> <run_id2> ...' to compare runs.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

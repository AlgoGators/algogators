#!/usr/bin/env python3
"""
Compare multiple backtest runs side-by-side.

Usage:
    python scripts/compare_backtests.py run_id1 run_id2
    python scripts/compare_backtests.py run_id1 run_id2 run_id3 --output comparison.html
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
from src.backtest.comparison import compare_backtests


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare multiple backtest runs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "run_ids",
        nargs="+",
        help="Run IDs to compare",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (HTML or CSV)",
    )
    parser.add_argument(
        "--backtests-dir",
        default=str(_project_root / "backtests"),
        help="Backtests directory",
    )
    parser.add_argument(
        "--metrics",
        nargs="+",
        default=None,
        help="Specific metrics to compare (default: all)",
    )
    args = parser.parse_args()

    if len(args.run_ids) < 2:
        print("Error: Need at least 2 run IDs to compare.")
        return 1

    catalog = BacktestCatalog(base_dir=args.backtests_dir)
    
    # Load runs
    records = []
    for run_id in args.run_ids:
        record = catalog.get_run(run_id)
        if record is None:
            print(f"Warning: Run {run_id} not found in catalog.")
            continue
        records.append(record)
    
    if len(records) < 2:
        print("Error: Need at least 2 valid runs to compare.")
        return 1
    
    # Compare
    comparison = compare_backtests(records, metrics=args.metrics)
    
    # Output
    if args.output:
        output_path = Path(args.output)
        if output_path.suffix == ".html":
            comparison.save_html(str(output_path))
            print(f"Comparison saved to: {output_path}")
        elif output_path.suffix == ".csv":
            comparison.save_csv(str(output_path))
            print(f"Comparison saved to: {output_path}")
        else:
            # Default to HTML
            comparison.save_html(str(output_path.with_suffix(".html")))
            print(f"Comparison saved to: {output_path.with_suffix('.html')}")
    else:
        # Print to console
        print("\n" + "=" * 100)
        print("BACKTEST COMPARISON")
        print("=" * 100)
        print()
        print(comparison.comparison_df.to_string(index=False))
        print()
        
        if comparison.metrics_summary:
            print("Metrics Summary:")
            for metric, stats in comparison.metrics_summary.items():
                print(f"  {metric}:")
                print(f"    Mean: {stats['mean']:.4f}")
                print(f"    Std:  {stats['std']:.4f}")
                print(f"    Min:  {stats['min']:.4f}")
                print(f"    Max:  {stats['max']:.4f}")
                print()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

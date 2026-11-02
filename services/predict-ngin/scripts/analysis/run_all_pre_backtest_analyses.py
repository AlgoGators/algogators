#!/usr/bin/env python3
"""
Re-run all pre-backtest analysis and capture results in one place.

Uses parquet only by default (no database). Data paths: data/polymarket/markets.parquet, data/polymarket/trades/.

Runs:
  1. Volume distribution → data/research/ (histogram, pareto, buckets, top markets, 4-panel, power law)
  2. Holistic market research → data/research/holistic_research/ (category, liquidity, lifecycle, dashboard, report, CSVs)

Usage:
  python scripts/analysis/run_all_pre_backtest_analyses.py
  python scripts/analysis/run_all_pre_backtest_analyses.py --no-holistic   # volume only
  python scripts/analysis/run_all_pre_backtest_analyses.py --no-viz        # skip volume charts
  python scripts/analysis/run_all_pre_backtest_analyses.py --use-db        # use database instead of parquet
"""

import sys
import subprocess
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run all pre-backtest analyses and capture results")
    parser.add_argument("--output-dir", default="data/research", help="Base output directory")
    parser.add_argument("--no-holistic", action="store_true", help="Skip holistic market research")
    parser.add_argument("--no-viz", action="store_true", help="Skip volume visualizations (volume metrics only)")
    parser.add_argument("--use-trades", action="store_true", help="Prefer volume from parquet trades if no parquet markets")
    parser.add_argument("--parquet-markets-dir", default="data/polymarket", help="Parquet markets directory (default)")
    parser.add_argument("--db-path", default="data/prediction_markets.db", help="Database path (only with --use-db)")
    parser.add_argument("--use-db", action="store_true", help="Use database instead of parquet")
    args = parser.parse_args()

    base = Path(args.output_dir)
    base.mkdir(parents=True, exist_ok=True)
    results_manifest = []

    # 1. Volume distribution
    print("=" * 60)
    print("1. Volume distribution")
    print("=" * 60)
    cmd = [
        sys.executable,
        str(_project_root / "scripts/analysis/volume_distribution.py"),
        "--output-dir", str(base),
        "--parquet-markets-dir", args.parquet_markets_dir,
    ]
    if args.no_viz:
        cmd.append("--no-viz")
    if args.use_trades:
        cmd.append("--use-trades")
    if args.use_db:
        cmd.extend(["--use-db", "--db-path", args.db_path])
    r = subprocess.run(cmd, cwd=str(_project_root))
    if r.returncode != 0:
        print(f"Volume distribution exited with code {r.returncode}")
    else:
        for name in [
            "volume_distribution_histogram.png",
            "volume_pareto_chart.png",
            "volume_buckets.png",
            "top_markets_volume.png",
            "volume_distribution.png",
            "volume_power_law.png",
        ]:
            p = base / name
            if p.exists():
                results_manifest.append(str(p))

    if not args.no_holistic:
        print("\n" + "=" * 60)
        print("2. Holistic market research")
        print("=" * 60)
        holistic_dir = base / "holistic_research"
        holistic_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(_project_root / "scripts/analysis/holistic_market_research.py"),
            "--output-dir", str(holistic_dir),
            "--parquet-markets-dir", args.parquet_markets_dir,
        ]
        if args.use_db:
            cmd.extend(["--use-db", "--db-path", args.db_path])
        r = subprocess.run(cmd, cwd=str(_project_root))
        if r.returncode != 0:
            print(f"Holistic research exited with code {r.returncode}")
        else:
            for name in [
                "volume_by_category.png",
                "liquidity_analysis.png",
                "market_lifecycle.png",
                "correlation_matrix.png",
                "comprehensive_dashboard.png",
                "holistic_research_report.md",
                "markets_data.csv",
                "category_stats.csv",
            ]:
                p = holistic_dir / name
                if p.exists():
                    results_manifest.append(str(p))

    # Write manifest
    manifest_path = base / "pre_backtest_results_manifest.txt"
    with open(manifest_path, "w") as f:
        f.write("\n".join(results_manifest))
    print("\n" + "=" * 60)
    print(f"Results manifest: {manifest_path}")
    print("=" * 60)
    for p in results_manifest:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

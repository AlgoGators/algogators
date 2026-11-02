#!/usr/bin/env python3
"""
Parallel parameter sweep for the whale-following backtest.

Runs the whale backtest across a grid of parameters using ProcessPoolExecutor,
then prints a ranked table and saves results to CSV.

Usage:
    # Full grid (~216 combinations) with 35 workers
    python scripts/backtest/run_whale_sweep.py --workers 35

    # Quick grid (8 combinations) for a smoke test
    python scripts/backtest/run_whale_sweep.py --quick

    # Restrict to specific categories
    python scripts/backtest/run_whale_sweep.py --categories Tech,Politics --workers 35

    # Custom output
    python scripts/backtest/run_whale_sweep.py --output data/output/my_sweep.csv
"""

import argparse
import itertools
import os
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
_scripts_backtest = Path(__file__).resolve().parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_scripts_backtest))  # so workers can import run_whale_category_backtest

import pandas as pd

# ---------------------------------------------------------------------------
# Parameter grids
# ---------------------------------------------------------------------------

FULL_GRID = {
    "min_surprise":              [0.0, 0.02, 0.05],
    "volume_percentile":         [85.0, 90.0, 95.0],
    "train_ratio":               [0.3, 0.5, 0.7],
    "require_positive_surprise": [True, False],
}

QUICK_GRID = {
    "min_surprise":              [0.0, 0.05],
    "volume_percentile":         [90.0, 95.0],
    "train_ratio":               [0.3],
    "require_positive_surprise": [True],
}


# ---------------------------------------------------------------------------
# Worker (top-level for pickling)
# ---------------------------------------------------------------------------

def _sweep_worker(args):
    """Run one whale backtest configuration. Returns (params_dict, metrics_dict, error_str)."""
    (project_root_str, research_dir_str, capital, min_usd, position_size,
     start_date, end_date, categories_list,
     min_surprise, volume_percentile,
     train_ratio, require_positive_surprise) = args

    # Ensure paths available in subprocess (needed on Windows spawn)
    import sys
    from pathlib import Path
    root = Path(project_root_str)
    for p in [str(root), str(root / "src"), str(root / "scripts" / "backtest")]:
        if p not in sys.path:
            sys.path.insert(0, p)

    from src.whale_strategy.whale_config import WhaleConfig
    from run_whale_category_backtest import run_whale_category_backtest

    params = {
        "min_surprise": min_surprise,
        "volume_percentile": volume_percentile,
        "train_ratio": train_ratio,
        "require_positive_surprise": require_positive_surprise,
    }

    whale_config = WhaleConfig(
        mode="volume_only",
        min_surprise=min_surprise,
        volume_percentile=volume_percentile,
        require_positive_surprise=require_positive_surprise,
    )

    try:
        result = run_whale_category_backtest(
            research_dir=Path(research_dir_str),
            capital=capital,
            min_usd=min_usd,
            position_size=position_size,
            train_ratio=train_ratio,
            start_date=start_date,
            end_date=end_date,
            categories=categories_list if categories_list else None,
            whale_config=whale_config,
            volume_only=True,
            rebalance_freq="1M",
            n_workers=1,
        )

        if "error" in result:
            return params, None, result["error"]

        sharpe = 0.0
        if "daily_returns" in result and len(result["daily_returns"]) >= 2:
            dr = result["daily_returns"]
            sharpe = float((dr.mean() / dr.std() * (252 ** 0.5)) if dr.std() > 0 else 0)

        metrics = {
            "win_rate":     round(result.get("win_rate", 0) * 100, 2),
            "roi_pct":      round(result.get("roi_pct", 0), 2),
            "total_pnl":    round(result.get("total_net_pnl", 0), 0),
            "total_trades": result.get("total_trades", 0),
            "whales":       result.get("whales_followed", 0),
            "sharpe":       round(sharpe, 4),
        }
        return params, metrics, None

    except Exception as exc:
        return params, None, str(exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parallel parameter sweep for the whale-following strategy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--research-dir", default=str(_project_root / "data" / "research"),
                        help="data/research directory")
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--min-usd", type=float, default=100.0)
    parser.add_argument("--position-size", type=float, default=25_000.0)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1,
                        help="Parallel workers (default: all CPUs)")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--categories", default=None,
                        help="Comma-separated category list (default: all)")
    parser.add_argument("--output", default=str(_project_root / "data" / "output" / "whale_sweep_results.csv"),
                        help="Output CSV path")
    parser.add_argument("--quick", action="store_true",
                        help="Use small grid (8 combos) for a fast smoke test")
    parser.add_argument("--top-n", type=int, default=20,
                        help="Number of top configs to print (by Sharpe)")
    args = parser.parse_args()

    grid = QUICK_GRID if args.quick else FULL_GRID
    keys = list(grid.keys())
    combos = list(itertools.product(*grid.values()))
    categories_list = [c.strip() for c in args.categories.split(",")] if args.categories else []

    print("=" * 65)
    print("WHALE STRATEGY PARAMETER SWEEP")
    print("=" * 65)
    print(f"  Combinations : {len(combos)}")
    print(f"  Workers      : {min(args.workers, len(combos))}")
    print(f"  Research dir : {args.research_dir}")
    print(f"  Capital      : ${args.capital:,.0f}")
    print(f"  Categories   : {args.categories or 'all'}")
    print()
    for k, v in grid.items():
        print(f"  {k}: {v}")
    print()

    worker_args = [
        (str(_project_root), args.research_dir, args.capital, args.min_usd, args.position_size,
         args.start_date, args.end_date, categories_list,
         *[combo[i] for i in range(len(keys))])
        for combo in combos
    ]

    results = []
    n_errors = 0
    t0 = time.time()
    n_workers = min(args.workers, len(combos))

    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_sweep_worker, wa): i for i, wa in enumerate(worker_args)}
        for n_done, future in enumerate(as_completed(futures), start=1):
            elapsed = time.time() - t0
            eta = (len(combos) - n_done) / (n_done / elapsed) if elapsed > 0 else 0
            params, metrics, error = future.result()
            label = "  ".join(f"{k}={v}" for k, v in params.items())
            if error:
                n_errors += 1
                print(f"  [{n_done:>3}/{len(combos)}] ERROR  {label}  →  {error}")
            else:
                results.append({**params, **metrics})
                print(f"  [{n_done:>3}/{len(combos)}] "
                      f"Sharpe={metrics['sharpe']:>7.3f}  ROI={metrics['roi_pct']:>7.2f}%  "
                      f"Win={metrics['win_rate']:>5.1f}%  "
                      f"Trades={metrics['total_trades']:>5}  "
                      f"ETA={eta:.0f}s  {label}")

    if not results:
        print("No successful runs.")
        return 1

    df = pd.DataFrame(results).sort_values("sharpe", ascending=False).reset_index(drop=True)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)

    print()
    print("=" * 65)
    print(f"TOP {min(args.top_n, len(df))} CONFIGURATIONS  (ranked by Sharpe)")
    print("=" * 65)
    display_cols = ["sharpe", "roi_pct", "win_rate", "total_trades", "whales"] + keys
    print(df[display_cols].head(args.top_n).to_string(index=True))
    print()
    print(f"Errors  : {n_errors} / {len(combos)}")
    print(f"Results : {args.output}")
    print(f"Runtime : {(time.time() - t0) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())

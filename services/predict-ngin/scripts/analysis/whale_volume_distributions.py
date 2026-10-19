#!/usr/bin/env python3
"""
Compute surprise win rate, Sharpe, and returns for volume-only whales and plot distributions.

Whales = 95th percentile volume in market (rolling, no look-ahead).
Requires resolutions for metric calculation.

Usage:
    python scripts/analysis/whale_volume_distributions.py
    python scripts/analysis/whale_volume_distributions.py --category Tech --min-trades 5
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

from src.whale_strategy.research_data_loader import (
    load_research_trades,
    load_resolution_winners,
    get_research_categories,
)
from src.whale_strategy.whale_surprise import (
    identify_whales_rolling,
    calculate_performance_score_with_surprise,
)


def compute_whale_metrics(
    trades_df: pd.DataFrame,
    resolution_winners: dict,
    volume_only: bool = True,
    min_trades: int = 5,
    unfavored_only: bool = False,
    unfavored_max_price: float = 0.40,
) -> pd.DataFrame:
    """Compute surprise_win_rate, sharpe, avg_roi for each volume whale with enough resolved trades."""
    print("Identifying volume whales (95th pct, rolling)...")
    trades_with_whale = identify_whales_rolling(
        trades_df, trader_col="maker", volume_only=volume_only
    )
    whale_addrs = trades_with_whale[trades_with_whale["is_whale"]]["maker"].unique()

    rows = []
    for addr in whale_addrs:
        result = calculate_performance_score_with_surprise(
            addr, trades_df, resolution_winners,
            direction_col="maker_direction",
            min_trades=min_trades,
            unfavored_only=unfavored_only,
            unfavored_max_price=unfavored_max_price,
        )
        if result is None or result["sample_size"] < min_trades:
            continue
        rows.append({
            "whale": addr,
            "surprise_win_rate": result["surprise_win_rate"],
            "sharpe": result["sharpe"],
            "avg_roi": result["avg_roi"],
            "expected_win_rate": result["expected_win_rate"],
            "actual_win_rate": result["actual_win_rate"],
            "sample_size": result["sample_size"],
        })

    return pd.DataFrame(rows)


def plot_distributions(df: pd.DataFrame, output_dir: Path, title_suffix: str = "") -> None:
    """Plot histograms for surprise_win_rate, sharpe, avg_roi."""
    output_dir.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    fig.suptitle(f"Volume Whale Distributions (95th pct volume){title_suffix}", fontsize=14)

    # Surprise win rate
    ax = axes[0]
    vals = df["surprise_win_rate"].dropna()
    vals = vals[(vals >= -1) & (vals <= 1)]
    ax.hist(vals, bins=50, edgecolor="black", alpha=0.7, color="steelblue")
    ax.axvline(0, color="red", linestyle="--", label="Zero")
    ax.axvline(vals.median(), color="green", linestyle=":", label=f"Median: {vals.median():.2f}")
    ax.set_xlabel("Surprise Win Rate (actual - expected)")
    ax.set_ylabel("Count")
    ax.set_title("Surprise Win Rate")
    ax.legend()

    # Sharpe (clip extreme outliers for display)
    ax = axes[1]
    vals = df["sharpe"].dropna()
    vals = vals[(vals >= -5) & (vals <= 5)]
    ax.hist(vals, bins=50, edgecolor="black", alpha=0.7, color="steelblue")
    ax.axvline(0, color="red", linestyle="--", label="Zero")
    ax.axvline(vals.median(), color="green", linestyle=":", label=f"Median: {vals.median():.2f}")
    ax.set_xlabel("Sharpe Ratio")
    ax.set_ylabel("Count")
    ax.set_title("Sharpe Ratio")
    ax.legend()

    # Avg ROI (returns) - clip to ±500% for viz
    ax = axes[2]
    vals = df["avg_roi"].dropna()
    vals = vals[(vals >= -2) & (vals <= 5)]
    ax.hist(vals, bins=50, edgecolor="black", alpha=0.7, color="steelblue")
    ax.axvline(0, color="red", linestyle="--", label="Zero")
    ax.axvline(vals.median(), color="green", linestyle=":", label=f"Median: {vals.median():.2%}")
    ax.set_xlabel("Avg ROI per trade")
    ax.set_ylabel("Count")
    ax.set_title("Average ROI")
    ax.legend()

    plt.tight_layout()
    out_path = output_dir / "whale_volume_distributions.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def plot_comparison(df_all: pd.DataFrame, df_unfavored: pd.DataFrame, output_dir: Path) -> None:
    """Side-by-side comparison: all whales vs unfavored-only."""
    output_dir.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 3, figsize=(14, 9))
    fig.suptitle("Hypothesis Test: All Whales vs Unfavored-Only (<=40c)", fontsize=14)

    metrics = ["surprise_win_rate", "sharpe", "avg_roi"]
    titles = ["Surprise Win Rate", "Sharpe Ratio", "Avg ROI"]
    xlims = [(-1, 1), (-5, 5), (-2, 5)]

    for col, (metric, title) in enumerate(zip(metrics, titles)):
        ax_all = axes[0, col]
        ax_unf = axes[1, col]
        xmin, xmax = xlims[col]

        for ax, df, label in [(ax_all, df_all, "All trades"), (ax_unf, df_unfavored, "Unfavored only")]:
            vals = df[metric].dropna()
            vals = vals[(vals >= xmin) & (vals <= xmax)]
            ax.hist(vals, bins=40, edgecolor="black", alpha=0.7, color="steelblue")
            ax.axvline(0, color="red", linestyle="--", label="Zero")
            ax.axvline(vals.median(), color="green", linestyle=":", label=f"Median: {vals.median():.3f}" if metric != "avg_roi" else f"Median: {vals.median():.2%}")
            ax.set_xlabel(title)
            ax.set_ylabel("Count")
            ax.set_title(label)
            ax.legend()

    axes[0, 0].set_ylabel("All trades")
    axes[1, 0].set_ylabel("Unfavored only (≤40¢)")
    plt.tight_layout()
    out_path = output_dir / "whale_unfavored_hypothesis.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Volume whale metric distributions (surprise, sharpe, returns)"
    )
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_project_root / "data" / "output" / "whale_research",
    )
    parser.add_argument("--category", default=None)
    parser.add_argument("--categories", default=None, help="Comma-separated")
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--max-trades", type=int, default=None)
    parser.add_argument(
        "--unfavored-only",
        action="store_true",
        help="Only include underdog trades (BUY ≤40¢, SELL ≥60¢)",
    )
    parser.add_argument(
        "--unfavored-max-price",
        type=float,
        default=0.40,
        help="Max price for unfavored BUY (default 0.40)",
    )
    parser.add_argument(
        "--hypothesis-test",
        action="store_true",
        help="Run both all and unfavored-only, compare distributions",
    )
    args = parser.parse_args()

    categories = [args.category] if args.category else None
    if args.categories:
        categories = [c.strip() for c in args.categories.split(",")]
    categories = categories or get_research_categories(args.research_dir)

    if not categories:
        print("No categories found.")
        return 1

    print("Loading data...")
    trades_df = load_research_trades(
        args.research_dir, categories=categories, min_usd=10
    )
    if args.max_trades:
        trades_df = trades_df.head(args.max_trades)
        print(f"  Limited to {args.max_trades:,} trades")
    resolution_winners = load_resolution_winners(args.research_dir)

    if not resolution_winners:
        print("No resolution data. Run extract_resolutions_from_markets.py first.")
        return 1

    print(f"  Trades: {len(trades_df):,}")
    print(f"  Resolutions: {len(resolution_winners):,}")

    unfavored_max = args.unfavored_max_price
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.hypothesis_test:
        print("\n" + "=" * 60)
        print("HYPOTHESIS TEST: All vs Unfavored-only (<=40c)")
        print("=" * 60)
        df_all = compute_whale_metrics(
            trades_df, resolution_winners, volume_only=True, min_trades=args.min_trades,
            unfavored_only=False,
        )
        df_unfavored = compute_whale_metrics(
            trades_df, resolution_winners, volume_only=True, min_trades=args.min_trades,
            unfavored_only=True, unfavored_max_price=unfavored_max,
        )
        print(f"\nALL trades: {len(df_all)} whales")
        print(f"  Surprise WR: median={df_all['surprise_win_rate'].median():.3f}, mean={df_all['surprise_win_rate'].mean():.3f}")
        print(f"  Sharpe:      median={df_all['sharpe'].median():.3f}")
        print(f"  Avg ROI:     median={df_all['avg_roi'].median():.2%}")
        print(f"\nUNFAVORED only (<=40c): {len(df_unfavored)} whales")
        print(f"  Surprise WR: median={df_unfavored['surprise_win_rate'].median():.3f}, mean={df_unfavored['surprise_win_rate'].mean():.3f}")
        print(f"  Sharpe:      median={df_unfavored['sharpe'].median():.3f}")
        print(f"  Avg ROI:     median={df_unfavored['avg_roi'].median():.2%}")
        df_all.to_csv(args.output_dir / "whale_volume_metrics.csv", index=False)
        df_unfavored.to_csv(args.output_dir / "whale_volume_metrics_unfavored.csv", index=False)
        plot_comparison(df_all, df_unfavored, args.output_dir)
    else:
        df = compute_whale_metrics(
            trades_df, resolution_winners, volume_only=True, min_trades=args.min_trades,
            unfavored_only=args.unfavored_only, unfavored_max_price=unfavored_max,
        )
        if df.empty:
            print("No whales with enough resolved trades.")
            return 1
        suffix = " (unfavored <=40c)" if args.unfavored_only else ""
        print(f"\nWhales with >= {args.min_trades} resolved trades{suffix}: {len(df)}")
        print(f"  Surprise WR: median={df['surprise_win_rate'].median():.3f}, mean={df['surprise_win_rate'].mean():.3f}")
        print(f"  Sharpe:      median={df['sharpe'].median():.3f}, mean={df['sharpe'].mean():.3f}")
        print(f"  Avg ROI:     median={df['avg_roi'].median():.2%}, mean={df['avg_roi'].mean():.2%}")
        csv_name = "whale_volume_metrics_unfavored.csv" if args.unfavored_only else "whale_volume_metrics.csv"
        df.to_csv(args.output_dir / csv_name, index=False)
        print(f"\nSaved: {args.output_dir / csv_name}")
        plot_distributions(df, args.output_dir, title_suffix=suffix)

    return 0


if __name__ == "__main__":
    sys.exit(main())

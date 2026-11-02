#!/usr/bin/env python3
"""
Whale surprise research: expected vs actual win rate by category.

Whales = traders with capital >= $50k OR 95th percentile volume in market (rolling, no look-ahead).
Surprise win rate = actual WR - expected WR (expected = market-implied from entry price).

Usage:
    python scripts/analysis/whale_surprise_research.py
    python scripts/analysis/whale_surprise_research.py --category Tech
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import numpy as np

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

from src.whale_strategy.research_data_loader import (
    load_research_trades,
    load_research_markets,
    load_resolution_winners,
    get_research_categories,
)
from src.whale_strategy.whale_surprise import (
    identify_whales_rolling,
    calculate_surprise_metrics,
    calculate_performance_score_with_surprise,
)


def run_surprise_research(
    trades_df: pd.DataFrame,
    resolution_winners: dict,
    categories: list,
) -> dict:
    """Compute surprise metrics per category and per whale."""
    # Rolling whale identification
    print("Identifying whales (rolling, no look-ahead)...")
    trades_with_whale = identify_whales_rolling(trades_df, trader_col="maker")
    whale_trades = trades_with_whale[trades_with_whale["is_whale"]].copy()

    if whale_trades.empty:
        return {"error": "No whale trades identified"}

    # Join to resolutions (normalize market_id for matching)
    whale_trades["market_id_str"] = whale_trades["market_id"].astype(str).str.strip().str.replace(".0", "", regex=False)
    whale_trades["winner"] = whale_trades["market_id_str"].map(resolution_winners)
    resolved = whale_trades.dropna(subset=["winner"])
    if resolved.empty:
        return {"error": "No resolved whale trades"}

    # Per-category surprise metrics
    category_results = []
    for cat in categories:
        cat_trades = resolved[resolved["category"] == cat]
        if len(cat_trades) < 10:
            continue
        metrics = calculate_surprise_metrics(
            cat_trades, resolution_winners, direction_col="maker_direction"
        )
        category_results.append({
            "category": cat,
            "whale_trades": len(cat_trades),
            "whale_count": cat_trades["maker"].nunique(),
            "expected_win_rate": metrics["expected_win_rate"],
            "actual_win_rate": metrics["actual_win_rate"],
            "surprise_win_rate": metrics["surprise_win_rate"],
        })

    # Per-whale surprise (for whales with >= 10 resolved trades)
    whale_scores = []
    for whale in resolved["maker"].unique():
        w_trades = resolved[resolved["maker"] == whale]
        if len(w_trades) < 10:
            continue
        score = calculate_performance_score_with_surprise(
            whale, trades_df, resolution_winners, direction_col="maker_direction"
        )
        if score:
            score["whale"] = whale
            score["category"] = w_trades["category"].mode().iloc[0] if len(w_trades["category"].unique()) == 1 else "mixed"
            whale_scores.append(score)

    return {
        "category_results": pd.DataFrame(category_results),
        "whale_scores": pd.DataFrame(whale_scores) if whale_scores else pd.DataFrame(),
        "total_whale_trades": len(whale_trades),
        "total_whales": whale_trades["maker"].nunique(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Whale surprise research (expected vs actual WR)")
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research")
    parser.add_argument("--output-dir", type=Path, default=_project_root / "data" / "research" / "whale_research")
    parser.add_argument("--category", default=None)
    parser.add_argument("--categories", default=None, help="Comma-separated")
    parser.add_argument("--min-usd", type=float, default=10)
    parser.add_argument("--max-trades", type=int, default=None, help="Limit trades for faster run (default: all)")
    args = parser.parse_args()

    categories = [args.category] if args.category else None
    if args.categories:
        categories = [c.strip() for c in args.categories.split(",")]
    categories = categories or get_research_categories(args.research_dir)

    if not categories:
        print("No categories found.")
        return 1

    print("Loading data...")
    trades_df = load_research_trades(args.research_dir, categories=categories, min_usd=args.min_usd)
    if args.max_trades:
        trades_df = trades_df.head(args.max_trades)
        print(f"  Limited to {args.max_trades:,} trades")
    resolution_winners = load_resolution_winners(args.research_dir)

    if not resolution_winners:
        print("No resolution data. Run extract_resolutions_from_markets.py first.")
        return 1

    print(f"  Trades: {len(trades_df):,}")
    print(f"  Resolutions: {len(resolution_winners):,}")

    result = run_surprise_research(trades_df, resolution_winners, categories)

    if "error" in result:
        print(f"\nError: {result['error']}")
        for k in ("whale_trades_count", "whale_markets", "resolution_markets", "overlap_markets"):
            if k in result:
                print(f"  {k}: {result[k]}")
        return 1

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Category surprise table
    cat_df = result["category_results"]
    cat_df.to_csv(output_dir / "surprise_by_category.csv", index=False)

    print("\n" + "=" * 70)
    print("SURPRISE WIN RATE BY CATEGORY (Whales: 50k capital OR 95th pct volume)")
    print("=" * 70)
    print(f"{'Category':<20} {'Trades':>8} {'Whales':>7} {'Expected%':>10} {'Actual%':>10} {'Surprise%':>10}")
    print("-" * 70)
    for _, row in cat_df.iterrows():
        print(f"{row['category']:<20} {row['whale_trades']:>8,.0f} {row['whale_count']:>7,.0f} "
              f"{row['expected_win_rate']*100:>9.1f}% {row['actual_win_rate']*100:>9.1f}% "
              f"{row['surprise_win_rate']*100:>+9.1f}%")

    # Per-whale scores
    whale_df = result["whale_scores"]
    if not whale_df.empty:
        whale_df = whale_df.sort_values("surprise_win_rate", ascending=False)
        whale_df.to_csv(output_dir / "whale_surprise_scores.csv", index=False)

        print("\n" + "=" * 70)
        print("TOP 15 WHALES BY SURPRISE WIN RATE")
        print("=" * 70)
        top = whale_df.head(15)
        for _, row in top.iterrows():
            addr = str(row["whale"])[:10] + "..." + str(row["whale"])[-6:]
            print(f"  {addr}  Exp:{row['expected_win_rate']*100:.1f}%  Act:{row['actual_win_rate']*100:.1f}%  "
                  f"Surprise:{row['surprise_win_rate']*100:+.1f}%  Score:{row['score']:.2f}  n={row['sample_size']:.0f}")

    # Summary JSON
    summary = {
        "total_whale_trades": int(result["total_whale_trades"]),
        "total_whales": int(result["total_whales"]),
        "categories": list(cat_df["category"]),
        "output_dir": str(output_dir),
    }
    with open(output_dir / "surprise_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nOutput: {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

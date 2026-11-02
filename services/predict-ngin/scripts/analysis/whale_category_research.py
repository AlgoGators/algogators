#!/usr/bin/env python3
"""
Whale strategy research at category level.

Analyzes:
- Whale performance by category (win rate, ROI, volume)
- Category-level exposure and concentration
- Whale quality score distribution by category
- Top whales per category

Outputs to data/research/whale_research/

Usage:
    python scripts/analysis/whale_category_research.py
    python scripts/analysis/whale_category_research.py --research-dir data/research --output-dir data/research/whale_research
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

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
from src.whale_strategy.polymarket_whales import (
    identify_polymarket_whales,
    build_price_snapshot,
    calculate_trader_stats,
)
from src.whale_strategy.whale_scoring import (
    calculate_whale_score,
    calculate_performance_score,
    WHALE_CRITERIA,
)


def analyze_whales_by_category(
    trades_df: pd.DataFrame,
    resolution_winners: Dict[str, str],
    markets_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute per-category whale stats: count, volume, win rate proxy."""
    if "category" not in trades_df.columns:
        return pd.DataFrame()

    results = []
    for cat in trades_df["category"].unique():
        cat_trades = trades_df[trades_df["category"] == cat]
        if cat_trades.empty:
            continue

        # Whale identification (simplified: top by volume)
        stats = cat_trades.groupby("maker").agg(
            total_volume=("usd_amount", "sum"),
            trade_count=("usd_amount", "count"),
            avg_trade=("usd_amount", "mean"),
        ).reset_index()
        stats = stats[stats["trade_count"] >= 5]
        whales = set(stats["maker"])

        whale_trades = cat_trades[cat_trades["maker"].isin(whales)]
        total_vol = whale_trades["usd_amount"].sum()
        total_trades = len(whale_trades)

        # Win rate proxy from resolution (if available)
        if resolution_winners:
            cat_markets = set(cat_trades["market_id"].astype(str))
            resolved = cat_trades[
                cat_trades["market_id"].astype(str).isin(resolution_winners.keys())
            ]
            if len(resolved) >= 10:
                resolved["winner"] = resolved["market_id"].astype(str).map(resolution_winners)
                resolved = resolved.dropna(subset=["winner"])
                dir_col = "maker_direction"
                resolved["correct"] = (
                    ((resolved[dir_col].str.lower() == "buy") & (resolved["winner"] == "YES")) |
                    ((resolved[dir_col].str.lower() == "sell") & (resolved["winner"] == "NO"))
                )
                win_rate = resolved["correct"].mean()
            else:
                win_rate = np.nan
        else:
            win_rate = np.nan

        results.append({
            "category": cat,
            "whale_count": len(whales),
            "total_volume": total_vol,
            "total_trades": total_trades,
            "win_rate_proxy": win_rate,
            "markets_traded": cat_trades["market_id"].nunique(),
        })

    return pd.DataFrame(results)


def top_whales_per_category(
    trades_df: pd.DataFrame,
    resolution_winners: Dict[str, str],
    top_n: int = 10,
) -> Dict[str, pd.DataFrame]:
    """Top N whales by volume per category."""
    if "category" not in trades_df.columns:
        return {}

    out = {}
    for cat in trades_df["category"].unique():
        cat_trades = trades_df[trades_df["category"] == cat]
        stats = cat_trades.groupby("maker").agg(
            total_volume=("usd_amount", "sum"),
            trade_count=("usd_amount", "count"),
            avg_trade=("usd_amount", "mean"),
        ).reset_index()
        stats = stats[stats["trade_count"] >= 5]
        stats = stats.nlargest(top_n, "total_volume")
        stats["category"] = cat
        out[cat] = stats

    return out


def whale_score_distribution(
    trades_df: pd.DataFrame,
    resolution_winners: Dict[str, str],
    markets_df: pd.DataFrame,
    sample_size: int = 500,
) -> pd.DataFrame:
    """Sample trades and compute whale scores for distribution analysis."""
    if not resolution_winners or markets_df.empty:
        return pd.DataFrame()

    # Build market meta
    market_meta = {}
    for _, row in markets_df.iterrows():
        mid = str(row.get("market_id", "")).strip()
        market_meta[mid] = row.to_dict()

    # Sample large trades
    large = trades_df[trades_df["usd_amount"] >= 1000]
    if len(large) > sample_size:
        large = large.sample(n=sample_size, random_state=42)

    scores = []
    for _, row in large.iterrows():
        score = calculate_whale_score(
            row, trades_df, resolution_winners,
            market_meta.get(str(row["market_id"]).strip(), {}),
            role="maker",
        )
        scores.append({
            "market_id": row["market_id"],
            "category": row.get("category", "Unknown"),
            "whale": row["maker"],
            "usd_amount": row["usd_amount"],
            "whale_score": score,
        })

    return pd.DataFrame(scores)


def main() -> int:
    parser = argparse.ArgumentParser(description="Whale category research")
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research")
    parser.add_argument("--output-dir", type=Path, default=_project_root / "data" / "research" / "whale_research")
    parser.add_argument("--category", default=None, help="Single category (e.g. Tech). Overrides --categories.")
    parser.add_argument("--categories", default=None, help="Comma-separated (default: all)")
    parser.add_argument("--min-usd", type=float, default=10)
    args = parser.parse_args()

    research_dir = args.research_dir
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    categories = None
    if args.category:
        categories = [args.category]
    elif args.categories:
        categories = [c.strip() for c in args.categories.split(",")]

    categories = categories or get_research_categories(research_dir)
    if not categories:
        print("No categories found. Run fetch_research_trades_and_prices first.")
        return 1

    print("Loading research data...")
    trades_df = load_research_trades(research_dir, categories=categories, min_usd=args.min_usd)
    if trades_df.empty:
        print("No trades loaded.")
        return 1

    markets_df = load_research_markets(research_dir, categories=categories)
    resolution_winners = load_resolution_winners(research_dir)

    print(f"  Trades: {len(trades_df):,}")
    print(f"  Markets: {len(markets_df):,}")
    print(f"  Resolutions: {len(resolution_winners):,}")

    # 1. Category-level whale stats
    print("\nAnalyzing whales by category...")
    cat_stats = analyze_whales_by_category(trades_df, resolution_winners, markets_df)
    if not cat_stats.empty:
        cat_stats.to_csv(output_dir / "category_whale_stats.csv", index=False)
        print(cat_stats.to_string(index=False))

    # 2. Top whales per category
    print("\nTop whales per category...")
    top_whales = top_whales_per_category(trades_df, resolution_winners, top_n=10)
    for cat, df in top_whales.items():
        path = output_dir / f"top_whales_{cat.replace(' ', '_')}.csv"
        df.to_csv(path, index=False)

    # 3. Whale score distribution (if resolutions available)
    if resolution_winners:
        print("\nWhale score distribution...")
        score_df = whale_score_distribution(trades_df, resolution_winners, markets_df)
        if not score_df.empty:
            score_df.to_csv(output_dir / "whale_scores_sample.csv", index=False)
            print(f"  Score mean: {score_df['whale_score'].mean():.2f}")
            print(f"  Score >= 6: {(score_df['whale_score'] >= 6).sum()} / {len(score_df)}")

    # 4. Summary JSON
    summary = {
        "categories": categories,
        "total_trades": len(trades_df),
        "total_volume_usd": float(trades_df["usd_amount"].sum()),
        "resolution_coverage": len(resolution_winners),
        "output_dir": str(output_dir),
    }
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nOutput written to {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

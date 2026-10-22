#!/usr/bin/env python3
"""
Analyze market categories and data volume per category.

Queries markets from database/parquet, categorizes them, and reports:
- Number of markets per category
- Price data points per category
- Trade count per category
- Volume/liquidity per category
"""

import sys
from pathlib import Path
from collections import defaultdict

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

import pandas as pd

try:
    from trading.data_modules.database import PredictionMarketDB
    from trading.data_modules.categories import categorize_market, CATEGORIES
    from trading.data_modules.parquet_store import PriceStore, TradeStore
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running from the project root with src in path")
    sys.exit(1)


def analyze_markets_from_db(db_path: str = "data/prediction_markets.db") -> pd.DataFrame:
    """Load and categorize markets from SQLite database."""
    db = PredictionMarketDB(db_path)
    try:
        markets_df = db.query("SELECT id, question, slug, volume, volume_24hr, liquidity FROM polymarket_markets")
        if markets_df.empty:
            return pd.DataFrame()
        
        # Categorize
        markets_df["category"] = markets_df["question"].apply(
            lambda q: categorize_market(str(q) if pd.notna(q) else "")
        )
        return markets_df
    finally:
        db.close()


def analyze_markets_from_parquet(parquet_dir: str = "data/polymarket") -> pd.DataFrame:
    """Load and categorize markets from parquet files.
    Supports: data/polymarket/markets.parquet (single) or data/parquet/markets/markets_*.parquet (sharded).
    """
    markets_dir = Path(parquet_dir)
    if not markets_dir.exists():
        # Fallback to legacy parquet location
        markets_dir = Path("data/parquet/markets")
        if not markets_dir.exists():
            return pd.DataFrame()

    files = []
    single = markets_dir / "markets.parquet"
    if single.exists():
        files = [single]
    else:
        files = sorted(markets_dir.glob("markets_*.parquet"))

    if not files:
        return pd.DataFrame()

    dfs = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            dfs.append(df)
        except Exception:
            continue

    if not dfs:
        return pd.DataFrame()

    markets_df = pd.concat(dfs, ignore_index=True)
    
    # Extract question from various possible columns
    question_col = None
    for col in ["question", "Question", "text", "name"]:
        if col in markets_df.columns:
            question_col = col
            break
    
    if question_col is None:
        print("Warning: No question column found in markets parquet")
        markets_df["question"] = ""
    else:
        markets_df["question"] = markets_df[question_col]
    
    # Ensure id column exists
    if "id" not in markets_df.columns:
        for col in ["market_id", "marketId", "slug"]:
            if col in markets_df.columns:
                markets_df["id"] = markets_df[col]
                break
    
    # Categorize
    markets_df["category"] = markets_df["question"].apply(
        lambda q: categorize_market(str(q) if pd.notna(q) else "")
    )
    
    return markets_df


def analyze_prices_by_category(
    markets_df: pd.DataFrame,
    parquet_dir: str = "data/polymarket/prices",
    sample_limit: int = None,
) -> dict:
    """Count price data points per category."""
    if markets_df.empty:
        return {}
    
    price_store = PriceStore(parquet_dir)
    if not price_store.available():
        return {}
    
    category_counts = defaultdict(int)
    category_markets = defaultdict(set)
    
    # Sample markets if limit provided
    markets_to_check = markets_df.head(sample_limit) if sample_limit else markets_df
    
    print(f"Analyzing prices for {len(markets_to_check)} markets...")
    for idx, row in markets_to_check.iterrows():
        market_id = str(row["id"])
        category = row["category"]
        
        try:
            prices = price_store.get_price_history(market_id, "YES")
            if not prices.empty:
                count = len(prices)
                category_counts[category] += count
                category_markets[category].add(market_id)
        except Exception:
            continue
        
        if (idx + 1) % 100 == 0:
            print(f"  Processed {idx + 1}/{len(markets_to_check)} markets...")
    
    return {
        "price_counts": dict(category_counts),
        "markets_with_prices": {k: len(v) for k, v in category_markets.items()},
    }


def analyze_trades_by_category(
    markets_df: pd.DataFrame,
    parquet_dir: str = "data/polymarket/trades",
    sample_limit: int = None,
) -> dict:
    """Count trades per category."""
    if markets_df.empty:
        return {}
    
    trade_store = TradeStore(parquet_dir)
    if not trade_store.available():
        return {}
    
    # Create market_id -> category mapping
    market_to_category = dict(zip(markets_df["id"].astype(str), markets_df["category"]))
    
    # Load trades (sample if limit provided)
    print("Loading trades...")
    try:
        trades_df = trade_store.load_trades(limit=sample_limit)
        if trades_df.empty:
            return {}
    except Exception as e:
        print(f"Error loading trades: {e}")
        return {}
    
    # Map trades to categories
    if "market_id" in trades_df.columns:
        trades_df["category"] = trades_df["market_id"].astype(str).map(market_to_category)
        trades_df["category"] = trades_df["category"].fillna("unknown")
        
        if "usd_amount" in trades_df.columns:
            category_stats = trades_df.groupby("category").agg({
                "market_id": "nunique",
                "usd_amount": ["count", "sum"],
            })
            return {
                "trade_counts": {cat: int(row[("market_id", "nunique")]) for cat, row in category_stats.iterrows()},
                "trade_volume": {cat: float(row[("usd_amount", "sum")]) for cat, row in category_stats.iterrows()},
                "markets_traded": {cat: int(row[("market_id", "nunique")]) for cat, row in category_stats.iterrows()},
            }
        else:
            category_stats = trades_df.groupby("category").agg({
                "market_id": "nunique",
            })
            return {
                "trade_counts": {cat: len(trades_df[trades_df["category"] == cat]) for cat in category_stats.index},
                "markets_traded": {cat: int(row["market_id"]) for cat, row in category_stats.iterrows()},
            }
    
    return {}


def print_category_report(
    markets_df: pd.DataFrame,
    price_stats: dict,
    trade_stats: dict,
) -> None:
    """Print formatted category analysis report."""
    print("\n" + "="*80)
    print("MARKET CATEGORY ANALYSIS")
    print("="*80)
    
    # Market counts
    market_counts = markets_df["category"].value_counts().sort_values(ascending=False)
    
    print("\nMARKETS BY CATEGORY")
    print("-" * 80)
    print(f"{'Category':<20} {'Markets':>10} {'% of Total':>12} {'Avg Volume':>15} {'Avg Liquidity':>15}")
    print("-" * 80)
    
    total_markets = len(markets_df)
    for category in sorted(market_counts.index):
        count = market_counts[category]
        pct = (count / total_markets * 100) if total_markets > 0 else 0
        
        cat_df = markets_df[markets_df["category"] == category]
        if "volume" in cat_df.columns:
            avg_volume = pd.to_numeric(cat_df["volume"], errors="coerce").mean()
        else:
            avg_volume = 0
        if "liquidity" in cat_df.columns:
            avg_liquidity = pd.to_numeric(cat_df["liquidity"], errors="coerce").mean()
        else:
            avg_liquidity = 0
        
        print(f"{category:<20} {count:>10,} {pct:>11.1f}% ${avg_volume:>14,.0f} ${avg_liquidity:>14,.0f}")
    
    print("-" * 80)
    print(f"{'TOTAL':<20} {total_markets:>10,} {'100.0%':>12}")
    
    # Price data
    if price_stats.get("price_counts"):
        print("\nPRICE DATA POINTS BY CATEGORY")
        print("-" * 80)
        print(f"{'Category':<20} {'Price Points':>15} {'Markets w/ Data':>18} {'Avg per Market':>18}")
        print("-" * 80)
        
        price_counts = price_stats["price_counts"]
        markets_with_prices = price_stats.get("markets_with_prices", {})
        
        for category in sorted(price_counts.keys(), key=lambda x: price_counts[x], reverse=True):
            points = price_counts[category]
            markets = markets_with_prices.get(category, 0)
            avg = points / markets if markets > 0 else 0
            print(f"{category:<20} {points:>15,} {markets:>18,} {avg:>17,.0f}")
        
        total_points = sum(price_counts.values())
        total_markets_with_prices = sum(markets_with_prices.values())
        print("-" * 80)
        print(f"{'TOTAL':<20} {total_points:>15,} {total_markets_with_prices:>18,}")
    
    # Trade data
    if trade_stats.get("trade_counts"):
        print("\nTRADES BY CATEGORY")
        print("-" * 80)
        print(f"{'Category':<20} {'Markets Traded':>18} {'Trade Count':>15} {'Volume (USD)':>18}")
        print("-" * 80)
        
        trade_counts = trade_stats.get("trade_counts", {})
        trade_volume = trade_stats.get("trade_volume", {})
        markets_traded = trade_stats.get("markets_traded", {})
        
        for category in sorted(trade_counts.keys(), key=lambda x: trade_counts[x], reverse=True):
            markets = markets_traded.get(category, 0)
            count = trade_counts.get(category, 0)
            volume = trade_volume.get(category, 0)
            print(f"{category:<20} {markets:>18,} {count:>15,} ${volume:>17,.0f}")
    
    # Category definitions
    print("\nCATEGORY DEFINITIONS")
    print("-" * 80)
    for category, keywords in sorted(CATEGORIES.items()):
        print(f"\n{category.upper()}:")
        print(f"  Keywords: {', '.join(keywords[:10])}{'...' if len(keywords) > 10 else ''}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Analyze market categories and data volume")
    parser.add_argument("--db-path", default="data/prediction_markets.db", help="SQLite database path")
    parser.add_argument("--parquet-prices", default="data/polymarket/prices", help="Parquet prices directory")
    parser.add_argument("--parquet-trades", default="data/polymarket/trades", help="Parquet trades directory")
    parser.add_argument("--sample-markets", type=int, default=None, help="Limit market analysis (for speed)")
    parser.add_argument("--sample-trades", type=int, default=None, help="Limit trade analysis (for speed)")
    parser.add_argument("--skip-prices", action="store_true", help="Skip price analysis")
    parser.add_argument("--skip-trades", action="store_true", help="Skip trade analysis")
    
    args = parser.parse_args()
    
    print("Loading markets from database...")
    markets_df = analyze_markets_from_db(args.db_path)
    
    if markets_df.empty:
        print("No markets found in database, trying parquet...")
        markets_df = analyze_markets_from_parquet("data/polymarket")
    
    if markets_df.empty:
        print("No markets found in database or parquet.")
        return 1
    
    print(f"Loaded {len(markets_df):,} markets")
    
    price_stats = {}
    if not args.skip_prices:
        price_stats = analyze_prices_by_category(
            markets_df,
            parquet_dir=args.parquet_prices,
            sample_limit=args.sample_markets,
        )
    
    trade_stats = {}
    if not args.skip_trades:
        trade_stats = analyze_trades_by_category(
            markets_df,
            parquet_dir=args.parquet_trades,
            sample_limit=args.sample_trades,
        )
    
    print_category_report(markets_df, price_stats, trade_stats)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

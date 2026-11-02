#!/usr/bin/env python3
"""
Segment prediction market data by category for individual analysis.

Creates copies of markets, trades, and prices subdivided into market categories:
- crypto, politics, sports, ai_tech, finance, geopolitics, entertainment, science, other

Processes in batches and flushes to disk frequently for low memory use (~16GB).

Output structure:
    data/by_category/
    ├── crypto/
    │   ├── markets.parquet
    │   ├── trades.parquet   (or trades_0000.parquet, trades_0001.parquet if large)
    │   └── prices.parquet
    ├── politics/
    └── ...

Usage:
    python scripts/data/segment_data_by_category.py
    python scripts/data/segment_data_by_category.py --flush-rows 25000  # Tighter memory
    python scripts/data/segment_data_by_category.py --categories tech  # Only tech
"""

import argparse
import gc
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

import pandas as pd

try:
    from trading.data_modules.database import PredictionMarketDB
    from trading.data_modules.parquet_store import PriceStore, TradeStore
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

# Use taxonomy for richer categorization (includes entertainment, science)
try:
    from taxonomy.markets import MarketTaxonomy
    TAXONOMY_AVAILABLE = True
except ImportError:
    from trading.data_modules.categories import categorize_market, CATEGORIES
    TAXONOMY_AVAILABLE = False


def load_and_categorize_markets(
    db_path: Optional[str] = None,
    parquet_dir: Optional[str] = None,
    taxonomy_path: Optional[str] = None,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    """Load markets from DB or parquet and add category column."""
    markets_df = pd.DataFrame()

    # Try database first
    if db_path and Path(db_path).exists():
        db = PredictionMarketDB(db_path)
        try:
            limit_sql = f" LIMIT {int(limit)}" if limit else ""
            markets_df = db.query(
                "SELECT id, slug, question, volume, volume_24hr, liquidity, end_date "
                f"FROM polymarket_markets{limit_sql}"
            )
            if not markets_df.empty:
                # Ensure id column
                if "id" not in markets_df.columns:
                    markets_df["id"] = markets_df.get("market_id", markets_df.index.astype(str))
                markets_df["id"] = markets_df["id"].astype(str).str.replace(r"\.0$", "", regex=True)
        finally:
            db.close()

    # Fallback to parquet
    if markets_df.empty and parquet_dir:
        parquet_path = Path(parquet_dir)
        single = parquet_path / "markets.parquet"
        files = [single] if single.exists() else sorted(parquet_path.glob("markets_*.parquet"))

        if files:
            dfs = []
            for f in files:
                try:
                    dfs.append(pd.read_parquet(f))
                except Exception:
                    continue
            if dfs:
                markets_df = pd.concat(dfs, ignore_index=True)
                if limit:
                    markets_df = markets_df.head(limit)

        # Normalize columns
        if not markets_df.empty:
            for col in ["question", "Question", "text", "name"]:
                if col in markets_df.columns and "question" not in markets_df.columns:
                    markets_df["question"] = markets_df[col]
                    break
            if "question" not in markets_df.columns:
                markets_df["question"] = ""
            if "id" not in markets_df.columns:
                for c in ["market_id", "marketId", "slug"]:
                    if c in markets_df.columns:
                        markets_df["id"] = markets_df[c]
                        break
            markets_df["id"] = markets_df["id"].astype(str).str.replace(r"\.0$", "", regex=True)

    if markets_df.empty:
        return markets_df

    # Classify markets
    if TAXONOMY_AVAILABLE:
        taxonomy = MarketTaxonomy(taxonomy_path or str(_project_root / "config" / "taxonomy.yaml"))
        markets_df["category"] = markets_df["question"].apply(
            lambda q: taxonomy.classify(str(q) if pd.notna(q) else "").primary_category
        )
    else:
        markets_df["category"] = markets_df["question"].apply(
            lambda q: categorize_market(str(q) if pd.notna(q) else "")
        )

    return markets_df


def export_markets_by_category(
    markets_df: pd.DataFrame,
    output_base: Path,
    formats: List[str] = ["parquet", "csv"],
    categories_filter: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Export markets to per-category subdirectories. Writes each category immediately to limit memory."""
    counts = {}
    for category, group in markets_df.groupby("category"):
        if categories_filter:
            # tech filter uses ai_tech data; when filtering for tech only, output to tech/
            if "tech" in categories_filter and category == "ai_tech":
                out_category = "tech"
            elif category in categories_filter:
                out_category = category
            else:
                continue
        else:
            out_category = category

        cat_dir = output_base / out_category
        cat_dir.mkdir(parents=True, exist_ok=True)
        counts[out_category] = len(group)

        for fmt in formats:
            out_path = cat_dir / f"markets.{fmt}"
            if fmt == "parquet":
                group.to_parquet(out_path, index=False)
            else:
                group.to_csv(out_path, index=False)

        # When not filtering, also create tech alias for ai_tech
        if not categories_filter and category == "ai_tech":
            tech_dir = output_base / "tech"
            tech_dir.mkdir(parents=True, exist_ok=True)
            for fmt in formats:
                out_path = tech_dir / f"markets.{fmt}"
                if fmt == "parquet":
                    group.to_parquet(out_path, index=False)
                else:
                    group.to_csv(out_path, index=False)
        del group
        gc.collect()

    return counts


def export_trades_by_category(
    market_to_category: Dict[str, str],
    output_base: Path,
    db_path: Optional[str] = None,
    parquet_trades_dir: Optional[str] = None,
    formats: List[str] = ["parquet", "csv"],
    categories_filter: Optional[List[str]] = None,
    batch_size: int = 500,
    flush_rows: int = 50_000,
) -> Dict[str, int]:
    """Export trades filtered by category. Processes in batches and flushes frequently for low memory."""
    category_market_ids: Dict[str, Set[str]] = {}
    for mid, cat in market_to_category.items():
        category_market_ids.setdefault(cat, set()).add(mid)
    if "ai_tech" in category_market_ids:
        category_market_ids["tech"] = category_market_ids["ai_tech"]

    cats_to_export = list(category_market_ids.keys())
    if categories_filter:
        cats_to_export = [
            c for c in cats_to_export
            if (c in categories_filter and c != "tech") or ("tech" in categories_filter and c == "ai_tech")
        ]

    counts = {c: 0 for c in cats_to_export}
    all_mids = list(market_to_category.keys())
    if not all_mids:
        return counts

    # Accumulators per category (flush when flush_rows reached)
    accum: Dict[str, List[pd.DataFrame]] = {c: [] for c in cats_to_export}
    total_rows = 0

    shard_idx: Dict[str, int] = {c: 0 for c in cats_to_export}

    def flush_accumulators() -> None:
        nonlocal total_rows
        for out_cat in cats_to_export:
            if not accum[out_cat]:
                continue
            combined = pd.concat(accum[out_cat], ignore_index=True)
            accum[out_cat] = []
            counts[out_cat] += len(combined)
            cat_dir = output_base / out_cat
            cat_dir.mkdir(parents=True, exist_ok=True)
            idx = shard_idx[out_cat]
            shard_idx[out_cat] += 1
            for fmt in formats:
                out_path = cat_dir / f"trades_{idx:04d}.{fmt}"
                if fmt == "parquet":
                    combined.to_parquet(out_path, index=False)
                else:
                    combined.to_csv(out_path, index=False)
            del combined
        total_rows = 0
        gc.collect()

    # DB: batch by market_ids (SQLite limit ~999)
    if db_path and Path(db_path).exists():
        db = PredictionMarketDB(db_path)
        try:
            for i in range(0, len(all_mids), batch_size):
                batch_mids = all_mids[i : i + batch_size]
                placeholders = ",".join("?" * len(batch_mids))
                batch_df = db.query(
                    f"SELECT * FROM polymarket_trades WHERE market_id IN ({placeholders})",
                    tuple(batch_mids),
                )
                if batch_df.empty:
                    continue
                batch_df["market_id"] = batch_df["market_id"].astype(str).str.replace(r"\.0$", "", regex=True)
                batch_df["category"] = batch_df["market_id"].map(market_to_category)
                for out_cat in cats_to_export:
                    mids = category_market_ids.get("ai_tech", set()) if out_cat == "tech" else category_market_ids.get(out_cat, set())
                    cat_df = batch_df[batch_df["market_id"].isin(mids)].drop(columns=["category"], errors="ignore")
                    if not cat_df.empty:
                        accum[out_cat].append(cat_df)
                total_rows += len(batch_df)
                del batch_df
                if total_rows >= flush_rows:
                    flush_accumulators()
            flush_accumulators()  # remaining
        finally:
            db.close()

    # Parquet: iterate files one at a time
    elif parquet_trades_dir:
        store = TradeStore(parquet_trades_dir)
        if store.available():
            files = sorted(Path(store._effective_dir()).glob("trades_*.parquet"))
            for f in files:
                batch_df = pd.read_parquet(f)
                if batch_df.empty or "market_id" not in batch_df.columns:
                    continue
                batch_df["market_id"] = batch_df["market_id"].astype(str).str.replace(r"\.0$", "", regex=True)
                batch_df["category"] = batch_df["market_id"].map(market_to_category)
                for out_cat in cats_to_export:
                    mids = category_market_ids.get("ai_tech", set()) if out_cat == "tech" else category_market_ids.get(out_cat, set())
                    cat_df = batch_df[batch_df["market_id"].isin(mids)].drop(columns=["category"], errors="ignore")
                    if not cat_df.empty:
                        accum[out_cat].append(cat_df)
                total_rows += len(batch_df)
                del batch_df
                if total_rows >= flush_rows:
                    flush_accumulators()
                gc.collect()
            flush_accumulators()

    # Rename single shard to trades.parquet for consistency
    for out_cat in cats_to_export:
        cat_dir = output_base / out_cat
        shards = sorted(cat_dir.glob("trades_*.parquet"))
        if len(shards) == 1:
            shards[0].rename(cat_dir / "trades.parquet")

    return counts


def export_prices_by_category(
    market_to_category: Dict[str, str],
    output_base: Path,
    db_path: Optional[str] = None,
    parquet_prices_dir: Optional[str] = None,
    formats: List[str] = ["parquet", "csv"],
    categories_filter: Optional[List[str]] = None,
    batch_size: int = 500,
    flush_rows: int = 100_000,
) -> Dict[str, int]:
    """Export prices filtered by category. Processes in batches and flushes frequently for low memory."""
    category_market_ids: Dict[str, Set[str]] = {}
    for mid, cat in market_to_category.items():
        category_market_ids.setdefault(cat, set()).add(mid)
    if "ai_tech" in category_market_ids:
        category_market_ids["tech"] = category_market_ids["ai_tech"]

    cats_to_export = list(category_market_ids.keys())
    if categories_filter:
        cats_to_export = [
            c for c in cats_to_export
            if (c in categories_filter and c != "tech") or ("tech" in categories_filter and c == "ai_tech")
        ]

    counts = {c: 0 for c in cats_to_export}
    all_mids = list(market_to_category.keys())
    if not all_mids:
        return counts

    accum: Dict[str, List[pd.DataFrame]] = {c: [] for c in cats_to_export}
    total_rows = 0
    shard_idx: Dict[str, int] = {c: 0 for c in cats_to_export}

    def flush_accumulators() -> None:
        nonlocal total_rows
        for out_cat in cats_to_export:
            if not accum[out_cat]:
                continue
            combined = pd.concat(accum[out_cat], ignore_index=True)
            accum[out_cat] = []
            counts[out_cat] += len(combined)
            cat_dir = output_base / out_cat
            cat_dir.mkdir(parents=True, exist_ok=True)
            idx = shard_idx[out_cat]
            shard_idx[out_cat] += 1
            for fmt in formats:
                out_path = cat_dir / f"prices_{idx:04d}.{fmt}"
                if fmt == "parquet":
                    combined.to_parquet(out_path, index=False)
                else:
                    combined.to_csv(out_path, index=False)
            del combined
        total_rows = 0
        gc.collect()

    # DB: batch by market_ids (SQLite limit ~999)
    if db_path and Path(db_path).exists():
        db = PredictionMarketDB(db_path)
        try:
            for i in range(0, len(all_mids), batch_size):
                batch_mids = all_mids[i : i + batch_size]
                placeholders = ",".join("?" * len(batch_mids))
                batch_df = db.query(
                    f"SELECT * FROM polymarket_prices WHERE market_id IN ({placeholders})",
                    tuple(batch_mids),
                )
                if batch_df.empty:
                    continue
                batch_df["market_id"] = batch_df["market_id"].astype(str).str.replace(r"\.0$", "", regex=True)
                batch_df["category"] = batch_df["market_id"].map(market_to_category)
                for out_cat in cats_to_export:
                    mids = category_market_ids.get("ai_tech", set()) if out_cat == "tech" else category_market_ids.get(out_cat, set())
                    cat_df = batch_df[batch_df["market_id"].isin(mids)].drop(columns=["category"], errors="ignore")
                    if not cat_df.empty:
                        accum[out_cat].append(cat_df)
                total_rows += len(batch_df)
                del batch_df
                if total_rows >= flush_rows:
                    flush_accumulators()
            flush_accumulators()
        finally:
            db.close()

    # Parquet: batch by market_ids
    elif parquet_prices_dir:
        store = PriceStore(parquet_prices_dir)
        if store.available():
            for i in range(0, len(all_mids), batch_size):
                batch_mids = all_mids[i : i + batch_size]
                batch_df = store.load_prices_for_markets(batch_mids)
                if batch_df.empty:
                    continue
                batch_df["market_id"] = batch_df["market_id"].astype(str).str.replace(r"\.0$", "", regex=True)
                batch_df["category"] = batch_df["market_id"].map(market_to_category)
                for out_cat in cats_to_export:
                    mids = category_market_ids.get("ai_tech", set()) if out_cat == "tech" else category_market_ids.get(out_cat, set())
                    cat_df = batch_df[batch_df["market_id"].isin(mids)].drop(columns=["category"], errors="ignore")
                    if not cat_df.empty:
                        accum[out_cat].append(cat_df)
                total_rows += len(batch_df)
                del batch_df
                if total_rows >= flush_rows:
                    flush_accumulators()
            flush_accumulators()
        gc.collect()

    # Rename single shard to prices.parquet
    for out_cat in cats_to_export:
        cat_dir = output_base / out_cat
        shards = sorted(cat_dir.glob("prices_*.parquet"))
        if len(shards) == 1:
            shards[0].rename(cat_dir / "prices.parquet")

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Segment prediction market data by category for individual analysis"
    )
    parser.add_argument(
        "--db-path",
        default="data/prediction_markets.db",
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--parquet-dir",
        default="data/polymarket",
        help="Path to parquet data (markets, trades, prices subdirs)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/by_category",
        help="Output directory for category-segmented data",
    )
    parser.add_argument(
        "--taxonomy",
        default=None,
        help="Path to taxonomy YAML (optional)",
    )
    parser.add_argument(
        "--markets-only",
        action="store_true",
        help="Only export markets; skip trades and prices",
    )
    parser.add_argument(
        "--skip-prices",
        action="store_true",
        help="Skip price export (useful when prices table is very large)",
    )
    parser.add_argument(
        "--format",
        choices=["parquet", "csv", "both"],
        default="parquet",
        help="Output format (default: parquet)",
    )
    parser.add_argument(
        "--categories",
        type=str,
        default=None,
        metavar="CAT1,CAT2",
        help="Only export these categories (e.g. tech, politics). 'tech' maps to ai_tech markets.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of markets (for testing only; omit for full dataset)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Market IDs per batch for trades/prices (default: 500, SQLite limit ~999)",
    )
    parser.add_argument(
        "--flush-rows",
        type=int,
        default=50_000,
        help="Flush to disk after this many rows (default: 50000, lower for tighter memory)",
    )
    args = parser.parse_args()

    output_base = Path(args.output_dir)
    parquet_path = Path(args.parquet_dir)
    formats = ["parquet", "csv"] if args.format == "both" else [args.format]
    categories_filter = [c.strip() for c in args.categories.split(",")] if args.categories else None

    if categories_filter:
        print(f"Filtering for categories: {categories_filter}")

    print("=" * 70)
    print("SEGMENT DATA BY CATEGORY")
    print("=" * 70)
    print(f"Output: {output_base}")
    print(f"Sources: DB={args.db_path}, Parquet={parquet_path}")
    print()

    # Load and categorize markets
    print("Loading and categorizing markets...")
    markets_df = load_and_categorize_markets(
        db_path=args.db_path,
        parquet_dir=str(parquet_path) if parquet_path.exists() else None,
        taxonomy_path=args.taxonomy,
        limit=args.limit,
    )

    if markets_df.empty:
        print("No markets found. Ensure database or parquet data exists.")
        return 1

    if args.limit:
        print(f"Limited to {args.limit:,} markets")

    print(f"Loaded {len(markets_df):,} markets")
    market_counts = markets_df["category"].value_counts()
    for cat, n in market_counts.items():
        print(f"  {cat}: {n:,} markets")
    print()

    market_to_category = dict(zip(markets_df["id"].astype(str), markets_df["category"]))

    # Export markets
    print("Exporting markets by category...")
    export_markets_by_category(markets_df, output_base, formats, categories_filter)
    print(f"  -> {output_base}/{{category}}/markets.{{parquet,csv}}")
    del markets_df
    gc.collect()
    print()

    if not args.markets_only:
        parquet_trades = parquet_path / "trades" if parquet_path.exists() else None
        parquet_prices = parquet_path / "prices" if parquet_path.exists() else None

        # Export trades
        if Path(args.db_path).exists() or (parquet_trades and parquet_trades.exists()):
            print("Exporting trades by category...")
            trade_counts = export_trades_by_category(
                market_to_category,
                output_base,
                db_path=args.db_path,
                parquet_trades_dir=str(parquet_trades) if parquet_trades else None,
                formats=formats,
                categories_filter=categories_filter,
                batch_size=args.batch_size,
                flush_rows=args.flush_rows,
            )
            for cat, n in sorted(trade_counts.items(), key=lambda x: -x[1]):
                print(f"  {cat}: {n:,} trades")
            print()
        else:
            print("Skipping trades (no DB or parquet trades found)")
            print()

        # Export prices
        if not args.skip_prices and (Path(args.db_path).exists() or (parquet_prices and parquet_prices.exists())):
            print("Exporting prices by category...")
            price_counts = export_prices_by_category(
                market_to_category,
                output_base,
                db_path=args.db_path,
                parquet_prices_dir=str(parquet_prices) if parquet_prices else None,
                formats=formats,
                categories_filter=categories_filter,
                batch_size=args.batch_size,
                flush_rows=args.flush_rows,
            )
            for cat, n in sorted(price_counts.items(), key=lambda x: -x[1]):
                print(f"  {cat}: {n:,} price rows")
            print()
        else:
            print("Skipping prices (no DB or parquet prices found)")
            print()

    print("=" * 70)
    print("DONE. Category data saved to:", output_base)
    print("  Each category folder contains markets, trades, and prices for individual analysis.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())

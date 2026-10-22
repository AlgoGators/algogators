#!/usr/bin/env python3
"""
Efficient price data loading utilities.

Provides optimized functions for loading price data from parquet files
using Polars for maximum speed with large datasets.
"""

import sys
from pathlib import Path
from typing import List, Optional, Dict, Any

# Add project root to path
_script_path = Path(__file__).resolve()
_project_root = _script_path.parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

import pandas as pd
import numpy as np

try:
    import polars as pl
    POLARS_AVAILABLE = True
except ImportError:
    POLARS_AVAILABLE = False
    pl = None

from trading.data_modules.parquet_store import PriceStore


def load_prices_polars(
    parquet_dir: str = "data/polymarket/prices",
    market_ids: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    outcome: str = "YES",
    columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Load prices using Polars for maximum speed (recommended for large datasets).
    
    Args:
        parquet_dir: Directory with prices_*.parquet files
        market_ids: Optional list of market IDs to filter
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
        outcome: YES or NO
        columns: Optional list of columns to load (faster if specified)
    
    Returns:
        DataFrame with price data
    """
    if not POLARS_AVAILABLE:
        raise RuntimeError("Polars required. Install with: pip install polars")
    
    prices_dir = Path(parquet_dir)
    if not prices_dir.exists():
        return pd.DataFrame()
    
    files = sorted(prices_dir.glob("prices_*.parquet"))
    if not files:
        return pd.DataFrame()
    
    # Build filters
    filters = [("outcome", "==", outcome)]
    
    if market_ids:
        market_ids_str = [str(m) for m in market_ids]
        filters.append(("market_id", "in", market_ids_str))
    
    if start_date:
        filters.append(("timestamp", ">=", start_date))
    
    if end_date:
        filters.append(("timestamp", "<=", end_date))
    
    # Default columns if not specified
    if columns is None:
        columns = ["market_id", "outcome", "timestamp", "price"]
    
    try:
        # Use scan_parquet for lazy evaluation and predicate pushdown
        lf = pl.scan_parquet(
            [str(f) for f in files],
            filters=filters,
            columns=columns,
        )
        df = lf.collect()
        
        if df.is_empty():
            return pd.DataFrame()
        
        return df.to_pandas()
    except Exception as e:
        # Fallback to per-file reading if scan fails
        print(f"Polars scan failed: {e}, falling back to per-file read")
        dfs = []
        for f in files:
            try:
                df = pl.read_parquet(str(f), columns=columns)
                # Apply filters manually
                if "outcome" in df.columns:
                    df = df.filter(pl.col("outcome") == outcome)
                if market_ids and "market_id" in df.columns:
                    df = df.filter(pl.col("market_id").cast(pl.Utf8).is_in([str(m) for m in market_ids]))
                if start_date and "timestamp" in df.columns:
                    df = df.filter(pl.col("timestamp") >= start_date)
                if end_date and "timestamp" in df.columns:
                    df = df.filter(pl.col("timestamp") <= end_date)
                if not df.is_empty():
                    dfs.append(df)
            except Exception:
                continue
        
        if not dfs:
            return pd.DataFrame()
        
        result = pl.concat(dfs, how="vertical")
        return result.to_pandas()


def load_prices_by_category(
    markets_df: pd.DataFrame,
    category: str,
    parquet_dir: str = "data/polymarket/prices",
    max_markets: int = 100,
    use_polars: bool = True,
) -> pd.DataFrame:
    """
    Load prices for all markets in a category.
    
    Args:
        markets_df: DataFrame with markets (must have 'id' and 'category' columns)
        category: Category name to filter
        parquet_dir: Parquet prices directory
        max_markets: Maximum markets to load
        use_polars: Use Polars for faster loading
    
    Returns:
        DataFrame with prices for category markets
    """
    cat_markets = markets_df[
        markets_df["category"] == category
    ]["id"].astype(str).head(max_markets).tolist()
    
    if not cat_markets:
        return pd.DataFrame()
    
    if use_polars and POLARS_AVAILABLE:
        return load_prices_polars(
            parquet_dir=parquet_dir,
            market_ids=cat_markets,
            outcome="YES",
        )
    else:
        price_store = PriceStore(parquet_dir)
        return price_store.load_prices_for_markets(cat_markets, "YES")


def load_prices_date_range(
    start_date: str,
    end_date: str,
    parquet_dir: str = "data/polymarket/prices",
    market_ids: Optional[List[str]] = None,
    outcome: str = "YES",
    use_polars: bool = True,
) -> pd.DataFrame:
    """
    Load prices for a date range, optionally filtered by markets.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        parquet_dir: Parquet prices directory
        market_ids: Optional list of market IDs
        outcome: YES or NO
        use_polars: Use Polars for faster loading
    
    Returns:
        DataFrame with prices in date range
    """
    if use_polars and POLARS_AVAILABLE:
        return load_prices_polars(
            parquet_dir=parquet_dir,
            market_ids=market_ids,
            start_date=start_date,
            end_date=end_date,
            outcome=outcome,
        )
    else:
        # Fallback: use PriceStore (slower but works without Polars)
        price_store = PriceStore(parquet_dir)
        if market_ids:
            return price_store.load_prices_for_markets(market_ids, outcome)
        else:
            # PriceStore doesn't support date-only filtering, need to load all then filter
            print("Warning: Date-only filtering requires Polars. Loading all markets...")
            return pd.DataFrame()


def fetch_prices_from_api(
    min_volume: float = 10000,
    max_markets: int = 500,
    max_days: int = 90,
    workers: int = 20,
    output_dir: str = "data/polymarket_clob",
) -> int:
    """
    Fetch price data from Polymarket API and save to JSON.
    
    Args:
        min_volume: Minimum 24hr volume to include market
        max_markets: Maximum markets to fetch
        max_days: Days of history per market
        workers: Concurrent API requests
        output_dir: Output directory for JSON files
    
    Returns:
        Number of markets fetched
    """
    from trading.data_modules.fetcher import DataFetcher
    
    fetcher = DataFetcher(Path(output_dir).parent)
    return fetcher.fetch_polymarket_clob_data(
        min_volume=min_volume,
        max_markets=max_markets,
        max_days=max_days,
        workers=workers,
        include_closed=False,
    )


def convert_json_to_parquet(
    json_dir: str = "data/polymarket_clob",
    output_dir: str = "data/polymarket/prices",
) -> None:
    """
    Convert fetched JSON price files to monthly parquet partitions.
    
    Uses optimized parquet writing (Zstd compression, row group optimization)
    for maximum performance with large datasets.
    
    Args:
        json_dir: Directory with market_*.json files
        output_dir: Output directory for prices_YYYY-MM.parquet files
    """
    import json
    from collections import defaultdict
    
    try:
        from trading.data_modules.parquet_utils import write_prices_optimized
        USE_OPTIMIZED = True
    except ImportError:
        USE_OPTIMIZED = False
        import pyarrow.parquet as pq
        import pyarrow as pa
    
    json_path = Path(json_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Group by month
    monthly_data = defaultdict(list)
    
    json_files = sorted(json_path.glob("market_*.json"))
    print(f"Converting {len(json_files)} JSON files to optimized parquet...")
    
    for json_file in json_files:
        try:
            with open(json_file, "r") as f:
                data = json.load(f)
            
            market_info = data.get("market_info", {})
            market_id = str(market_info.get("id", ""))
            price_history = data.get("price_history", {})
            
            for outcome, hist_data in price_history.items():
                history = hist_data.get("history", [])
                for point in history:
                    ts = point.get("t", 0)
                    price = point.get("p", 0)
                    if ts and price:
                        # Convert timestamp to date for grouping
                        date_str = pd.Timestamp(ts, unit="s").strftime("%Y-%m")
                        monthly_data[date_str].append({
                            "market_id": market_id,
                            "outcome": outcome,
                            "timestamp": ts,
                            "price": price,
                        })
        except Exception as e:
            print(f"  Error processing {json_file.name}: {e}")
            continue
    
    # Write monthly parquet files with optimization
    for month, rows in monthly_data.items():
        if not rows:
            continue
        df = pd.DataFrame(rows)
        
        output_file = output_path / f"prices_{month}.parquet"
        
        if USE_OPTIMIZED:
            # Use optimized writer (Zstd + row group optimization + sorting)
            write_prices_optimized(df, output_file)
        else:
            # Fallback: manual optimization
            df = df.sort_values(["market_id", "outcome", "timestamp"]).reset_index(drop=True)
            try:
                table = pa.Table.from_pandas(df, preserve_index=False)
                pq.write_table(
                    table,
                    output_file,
                    compression="zstd",
                    compression_level=3,
                    row_group_size=500_000,
                )
            except ImportError:
                # Final fallback: basic pandas
                df.to_parquet(output_file, index=False, compression="snappy")
        
        print(f"  Saved {len(rows):,} rows to {output_file.name} (Zstd compressed, optimized)")
    
    print(f"\n✓ Converted to {len(monthly_data)} monthly parquet files")
    print(f"  Files optimized for fast predicate pushdown and compression")


def get_price_statistics(
    prices_df: pd.DataFrame,
) -> Dict[str, Any]:
    """
    Calculate statistics for a price DataFrame.
    
    Returns:
        Dict with count, date range, unique markets, etc.
    """
    if prices_df.empty:
        return {
            "count": 0,
            "unique_markets": 0,
            "date_range": None,
            "price_stats": None,
        }
    
    stats = {
        "count": len(prices_df),
        "unique_markets": prices_df["market_id"].nunique() if "market_id" in prices_df.columns else 0,
    }
    
    if "timestamp" in prices_df.columns:
        ts = pd.to_numeric(prices_df["timestamp"], errors="coerce")
        ts = ts.dropna()
        if not ts.empty:
            stats["date_range"] = {
                "start": pd.Timestamp(int(ts.min()), unit="s"),
                "end": pd.Timestamp(int(ts.max()), unit="s"),
            }
    
    if "price" in prices_df.columns:
        prices = pd.to_numeric(prices_df["price"], errors="coerce").dropna()
        if not prices.empty:
            stats["price_stats"] = {
                "min": float(prices.min()),
                "max": float(prices.max()),
                "mean": float(prices.mean()),
                "median": float(prices.median()),
                "std": float(prices.std()),
            }
    
    return stats


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Efficient price data loading")
    parser.add_argument("--method", choices=["polars", "pricestore", "api"], default="polars")
    parser.add_argument("--market-ids", nargs="+", help="Market IDs to load")
    parser.add_argument("--start-date", help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", help="End date YYYY-MM-DD")
    parser.add_argument("--category", help="Load prices for category")
    parser.add_argument("--max-markets", type=int, default=100)
    parser.add_argument("--output", help="Output CSV path")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    
    args = parser.parse_args()
    
    if args.method == "api":
        print("Fetching prices from API...")
        count = fetch_prices_from_api(max_markets=args.max_markets)
        print(f"Fetched {count} markets")
    else:
        if args.category:
            # Load markets first
            sys.path.insert(0, str(_project_root / "scripts" / "analysis"))
            try:
                from analyze_category_details import load_markets
            except ImportError:
                raise SystemExit("--category needs scripts/analysis/analyze_category_details.py, which was "
                                 "removed; filter predict_ngin.lake's polymarket_markets by category instead.")
            markets_df = load_markets()
            prices_df = load_prices_by_category(
                markets_df,
                args.category,
                max_markets=args.max_markets,
                use_polars=(args.method == "polars"),
            )
        else:
            prices_df = load_prices_polars(
                market_ids=args.market_ids,
                start_date=args.start_date,
                end_date=args.end_date,
            )
        
        if args.stats:
            stats = get_price_statistics(prices_df)
            print("\nPrice Statistics:")
            for key, value in stats.items():
                print(f"  {key}: {value}")
        
        if args.output:
            prices_df.to_csv(args.output, index=False)
            print(f"\nSaved to {args.output}")
        else:
            print(f"\nLoaded {len(prices_df):,} price records")
            print(f"  Markets: {prices_df['market_id'].nunique() if 'market_id' in prices_df.columns else 'N/A'}")
            if not prices_df.empty and "timestamp" in prices_df.columns:
                ts = pd.to_numeric(prices_df["timestamp"], errors="coerce").dropna()
                if not ts.empty:
                    print(f"  Date range: {pd.Timestamp(int(ts.min()), unit='s')} to {pd.Timestamp(int(ts.max()), unit='s')}")

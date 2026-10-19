#!/usr/bin/env python3
"""
Compute Price Correlation Matrix (Overnight Batch Processing)

Computes pairwise price correlations between all markets in batches with checkpointing.
Designed for low-resource overnight processing.

This complements the NLP similarity matrix by finding markets that move together
based on actual price movements rather than just text similarity.

Usage:
    # Basic run (processes all markets with price data)
    python scripts/research/compute_price_correlations.py

    # Limit markets for testing
    python scripts/research/compute_price_correlations.py --max-markets 500

    # Resume from checkpoint
    python scripts/research/compute_price_correlations.py --resume
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import argparse
import json
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Optional, Set, Tuple, Dict
from collections import defaultdict

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    class FakeTqdm:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def update(self, n=1):
            pass
        def set_postfix(self, **kwargs):
            pass
    tqdm = FakeTqdm

from src.trading.data_modules import PredictionMarketDB, DEFAULT_DB_PATH
from src.trading.data_modules.parquet_store import PriceStore


# Default paths
CHECKPOINT_DIR = Path("data/research/checkpoints")
OUTPUT_DIR = Path("data/research/correlations")
CHECKPOINT_FILE = CHECKPOINT_DIR / "correlation_checkpoint.json"
CORRELATION_FILE = OUTPUT_DIR / "price_correlations.parquet"


def load_price_series(
    db_path: Optional[str] = None,
    parquet_prices_dir: Optional[str] = None,
    max_markets: Optional[int] = None,
    min_data_points: int = 24,  # Minimum hours of data
    resample_freq: str = "1h",
) -> Dict[str, pd.Series]:
    """
    Load price series for all markets.
    
    Returns:
        Dict of market_id -> price Series (resampled hourly)
    """
    price_series = {}
    
    # Try parquet first
    if parquet_prices_dir:
        parquet_path = Path(parquet_prices_dir)
        if parquet_path.exists():
            print(f"Loading prices from parquet ({parquet_path})...")
            try:
                price_store = PriceStore(str(parquet_path))
                if price_store.available():
                    # Get all market IDs
                    markets = price_store.list_markets()
                    if max_markets:
                        markets = markets[:max_markets]
                    
                    print(f"  Found {len(markets):,} markets with price data")
                    print(f"  Loading price series (min {min_data_points} points)...")
                    
                    for i, market_id in enumerate(markets):
                        if (i + 1) % 100 == 0:
                            print(f"    Loaded {i+1}/{len(markets)} markets...")
                        
                        try:
                            prices_df = price_store.load_prices_for_markets([market_id], outcome="YES")
                            if prices_df.empty:
                                continue
                            
                            # Convert to datetime index
                            prices_df["datetime"] = pd.to_datetime(prices_df["datetime"])
                            prices_df = prices_df.sort_values("datetime")
                            
                            # Resample to hourly
                            series = prices_df.set_index("datetime")["price"].resample(resample_freq).last().dropna()
                            
                            if len(series) >= min_data_points:
                                price_series[market_id] = series
                        except Exception as e:
                            continue  # Skip markets with errors
                    
                    print(f"  Loaded {len(price_series):,} valid price series")
                    return price_series
            except Exception as e:
                print(f"  Warning: Could not load from parquet: {e}")
    
    # Fallback to database
    db_path = db_path or DEFAULT_DB_PATH
    print(f"Loading prices from database {db_path}...")
    try:
        db = PredictionMarketDB(db_path)
        
        # Get all markets with price data
        markets_query = """
            SELECT DISTINCT market_id 
            FROM polymarket_prices 
            GROUP BY market_id 
            HAVING COUNT(*) >= ?
        """
        markets_df = db.query(markets_query, params=(min_data_points,))
        
        if max_markets:
            markets_df = markets_df.head(max_markets)
        
        market_ids = markets_df["market_id"].astype(str).tolist()
        print(f"  Found {len(market_ids):,} markets with sufficient price data")
        
        for i, market_id in enumerate(market_ids):
            if (i + 1) % 100 == 0:
                print(f"    Loaded {i+1}/{len(market_ids)} markets...")
            
            try:
                prices_df = db.get_price_history(market_id, outcome="YES")
                if prices_df.empty:
                    continue
                
                # Convert to datetime index
                prices_df["datetime"] = pd.to_datetime(prices_df["datetime"])
                prices_df = prices_df.sort_values("datetime")
                
                # Resample to hourly
                series = prices_df.set_index("datetime")["price"].resample(resample_freq).last().dropna()
                
                if len(series) >= min_data_points:
                    price_series[market_id] = series
            except Exception as e:
                continue  # Skip markets with errors
        
        db.close()
        print(f"  Loaded {len(price_series):,} valid price series")
        return price_series
    except Exception as e:
        print(f"  ERROR: Could not load prices: {e}")
        return {}


def load_checkpoint() -> Tuple[Set[Tuple[str, str]], pd.DataFrame]:
    """Load checkpoint data."""
    if not CHECKPOINT_FILE.exists():
        return set(), pd.DataFrame()
    
    try:
        with open(CHECKPOINT_FILE, 'r') as f:
            checkpoint_data = json.load(f)
        
        processed_pairs = set()
        for pair in checkpoint_data.get("processed_pairs", []):
            processed_pairs.add(tuple(sorted(pair)))
        
        # Load existing correlations if file exists
        correlations_df = pd.DataFrame()
        if CORRELATION_FILE.exists():
            correlations_df = pd.read_parquet(CORRELATION_FILE)
        
        return processed_pairs, correlations_df
    except Exception as e:
        print(f"  Warning: Could not load checkpoint: {e}")
        return set(), pd.DataFrame()


def save_checkpoint(processed_pairs: Set[Tuple[str, str]], correlations_df: pd.DataFrame):
    """Save checkpoint data."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    
    checkpoint_data = {
        "timestamp": datetime.now().isoformat(),
        "processed_pairs": [list(pair) for pair in processed_pairs],
        "total_correlations": len(correlations_df),
    }
    
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(checkpoint_data, f, indent=2)
    
    # Save correlations incrementally
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    correlations_df.to_parquet(CORRELATION_FILE, index=False, compression='snappy')


def compute_correlations_batch(
    price_series: Dict[str, pd.Series],
    min_correlation: float = 0.3,
    min_overlap_hours: int = 24,
    resume: bool = False,
    save_interval: int = 500,
) -> pd.DataFrame:
    """
    Compute pairwise price correlations in batches with checkpointing.
    
    Args:
        price_series: Dict of market_id -> price Series
        min_correlation: Minimum correlation threshold to save
        min_overlap_hours: Minimum overlapping hours required
        resume: Whether to resume from checkpoint
        save_interval: Save checkpoint every N comparisons
    
    Returns:
        DataFrame with market_id1, market_id2, correlation, overlap_hours
    """
    market_ids = list(price_series.keys())
    n_markets = len(market_ids)
    
    # Load checkpoint if resuming
    processed_pairs = set()
    correlations_df = pd.DataFrame(columns=["market_id1", "market_id2", "correlation", "overlap_hours"])
    
    if resume:
        processed_pairs, existing_correlations = load_checkpoint()
        if not existing_correlations.empty:
            correlations_df = existing_correlations
            print(f"  Resuming: {len(processed_pairs):,} pairs already processed")
            print(f"  Existing correlations: {len(correlations_df):,}")
    
    # Compute correlations
    total_comparisons = n_markets * (n_markets - 1) // 2
    comparisons_done = len(processed_pairs)
    
    print(f"\nComputing correlations for {n_markets:,} markets")
    print(f"  Total comparisons: {total_comparisons:,}")
    print(f"  Comparisons done: {comparisons_done:,}")
    print(f"  Remaining: {total_comparisons - comparisons_done:,}")
    print(f"  Min correlation: {min_correlation}")
    print(f"  Min overlap: {min_overlap_hours} hours")
    print(f"  Save interval: {save_interval:,} comparisons\n")
    
    # Process in batches
    new_correlations = []
    comparisons_since_save = 0
    
    pbar = tqdm(total=total_comparisons, initial=comparisons_done, desc="Computing correlations")
    last_progress_print = 0
    
    try:
        for i in range(n_markets):
            market_id1 = market_ids[i]
            series1 = price_series[market_id1]
            
            for j in range(i + 1, n_markets):
                market_id2 = market_ids[j]
                pair_key = tuple(sorted([market_id1, market_id2]))
                
                # Skip if already processed
                if pair_key in processed_pairs:
                    pbar.update(1)
                    continue
                
                # Compute correlation
                series2 = price_series[market_id2]
                
                try:
                    # Align series
                    aligned = pd.concat([series1, series2], axis=1, join="inner")
                    aligned.columns = ["price1", "price2"]
                    aligned = aligned.dropna()
                    
                    overlap_hours = len(aligned)
                    
                    if overlap_hours < min_overlap_hours:
                        # Mark as processed but don't save
                        processed_pairs.add(pair_key)
                        comparisons_done += 1
                        pbar.update(1)
                        continue
                    
                    # Compute correlation
                    correlation = aligned["price1"].corr(aligned["price2"])
                    
                    if pd.isna(correlation):
                        correlation = 0.0
                    
                    # Save if above threshold
                    if abs(correlation) >= min_correlation:
                        new_correlations.append({
                            "market_id1": market_id1,
                            "market_id2": market_id2,
                            "correlation": correlation,
                            "overlap_hours": overlap_hours,
                        })
                    
                    # Mark as processed
                    processed_pairs.add(pair_key)
                    comparisons_done += 1
                    comparisons_since_save += 1
                    
                    # Save checkpoint periodically
                    if comparisons_since_save >= save_interval:
                        if new_correlations:
                            new_df = pd.DataFrame(new_correlations)
                            correlations_df = pd.concat([correlations_df, new_df], ignore_index=True)
                            new_correlations = []
                        
                        save_checkpoint(processed_pairs, correlations_df)
                        comparisons_since_save = 0
                        pbar.set_postfix({
                            "correlations": len(correlations_df),
                            "processed": len(processed_pairs)
                        })
                
                except Exception as e:
                    # Still mark as processed to avoid retrying
                    processed_pairs.add(pair_key)
                    comparisons_done += 1
                
                pbar.update(1)
                
                # Print progress periodically if no tqdm
                if not TQDM_AVAILABLE:
                    if comparisons_done % 10000 == 0:
                        print(f"  Progress: {comparisons_done:,} / {total_comparisons:,} ({100*comparisons_done/total_comparisons:.1f}%)")
    
    finally:
        if hasattr(pbar, '__exit__'):
            pbar.__exit__(None, None, None)
    
    # Save final checkpoint
    if new_correlations:
        new_df = pd.DataFrame(new_correlations)
        correlations_df = pd.concat([correlations_df, new_df], ignore_index=True)
    
    save_checkpoint(processed_pairs, correlations_df)
    
    return correlations_df


def main():
    parser = argparse.ArgumentParser(
        description="Compute Price Correlation Matrix (Overnight Batch Processing)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Process all markets (overnight task)
    python scripts/research/compute_price_correlations.py

    # Test with limited markets
    python scripts/research/compute_price_correlations.py --max-markets 500

    # Resume from checkpoint
    python scripts/research/compute_price_correlations.py --resume

    # Lower correlation threshold
    python scripts/research/compute_price_correlations.py --min-correlation 0.2
        """
    )
    
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Database path (default: data/prediction_markets.db)",
    )
    parser.add_argument(
        "--parquet-prices-dir",
        type=str,
        default="data/polymarket/prices",
        help="Directory with prices parquet files",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=None,
        help="Maximum number of markets to process (for testing)",
    )
    parser.add_argument(
        "--min-correlation",
        type=float,
        default=0.3,
        help="Minimum correlation threshold to save",
    )
    parser.add_argument(
        "--min-overlap-hours",
        type=int,
        default=24,
        help="Minimum overlapping hours required for correlation",
    )
    parser.add_argument(
        "--save-interval",
        type=int,
        default=500,
        help="Save checkpoint every N comparisons",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint",
    )
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("PRICE CORRELATION MATRIX COMPUTATION")
    print("=" * 70)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Load price series
    print("\n[1] Loading price series...")
    price_series = load_price_series(
        db_path=args.db_path,
        parquet_prices_dir=args.parquet_prices_dir,
        max_markets=args.max_markets,
        min_data_points=args.min_overlap_hours,
    )
    
    if not price_series:
        print("ERROR: No price series loaded")
        return 1
    
    print(f"  Price series loaded: {len(price_series):,}")
    
    # Compute correlations
    print(f"\n[2] Computing pairwise correlations...")
    resume_flag = args.resume
    
    correlations_df = compute_correlations_batch(
        price_series=price_series,
        min_correlation=args.min_correlation,
        min_overlap_hours=args.min_overlap_hours,
        resume=resume_flag,
        save_interval=args.save_interval,
    )
    
    # Final summary
    print("\n" + "=" * 70)
    print("COMPUTATION COMPLETE")
    print("=" * 70)
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\nResults:")
    print(f"  Total correlations found: {len(correlations_df):,}")
    print(f"  Output file: {CORRELATION_FILE}")
    print(f"  Checkpoint file: {CHECKPOINT_FILE}")
    
    if not correlations_df.empty:
        print(f"\nCorrelation statistics:")
        print(f"  Mean: {correlations_df['correlation'].mean():.3f}")
        print(f"  Median: {correlations_df['correlation'].median():.3f}")
        print(f"  Min: {correlations_df['correlation'].min():.3f}")
        print(f"  Max: {correlations_df['correlation'].max():.3f}")
        print(f"  Std: {correlations_df['correlation'].std():.3f}")
        
        # Top correlations
        top_correlations = correlations_df.nlargest(10, 'correlation')
        print(f"\nTop 10 most correlated market pairs:")
        for idx, row in top_correlations.iterrows():
            print(f"  {row['market_id1'][:20]}... <-> {row['market_id2'][:20]}... : {row['correlation']:.3f} (overlap: {row['overlap_hours']}h)")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

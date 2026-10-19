#!/usr/bin/env python3
"""
Compute Market Similarity Matrix (Overnight Batch Processing)

Computes pairwise NLP similarities between all markets in batches with checkpointing.
Designed for low-resource overnight processing.

This script:
- Loads all markets from database/parquet
- Computes pairwise similarities in small batches
- Saves progress incrementally (checkpointing)
- Uses minimal memory and CPU
- Produces valuable research data for market relationship analysis

Usage:
    # Basic run (processes all markets)
    python scripts/research/compute_market_similarities.py

    # Limit markets for testing
    python scripts/research/compute_market_similarities.py --max-markets 1000

    # Resume from checkpoint
    python scripts/research/compute_market_similarities.py --resume

    # Custom similarity threshold
    python scripts/research/compute_market_similarities.py --min-similarity 0.3
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
from typing import Optional, Set, Tuple

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    # Fallback if tqdm not available
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

from src.trading.strategies.nlp_correlation import NLPSimilarityEngine
from src.trading.data_modules import PredictionMarketDB, DEFAULT_DB_PATH


# Default paths
CHECKPOINT_DIR = Path("data/research/checkpoints")
OUTPUT_DIR = Path("data/research/similarities")
CHECKPOINT_FILE = CHECKPOINT_DIR / "similarity_checkpoint.json"
SIMILARITY_FILE = OUTPUT_DIR / "market_similarities.parquet"


def load_markets(
    db_path: Optional[str] = None,
    parquet_markets_dir: Optional[str] = None,
    max_markets: Optional[int] = None,
) -> pd.DataFrame:
    """Load markets from parquet (preferred) or database."""
    markets_df = pd.DataFrame()
    
    # Try parquet first
    if parquet_markets_dir:
        parquet_path = Path(parquet_markets_dir)
        if parquet_path.exists():
            single = parquet_path / "markets.parquet"
            markets_files = [single] if single.exists() else list(parquet_path.glob("markets_*.parquet"))
            if markets_files:
                print(f"Loading markets from parquet ({len(markets_files)} files)...")
                markets_list = []
                for f in markets_files:
                    df = pd.read_parquet(f)
                    markets_list.append(df)
                if markets_list:
                    markets_df = pd.concat(markets_list, ignore_index=True)
                    # Normalize column names
                    if "question" not in markets_df.columns and "Question" in markets_df.columns:
                        markets_df["question"] = markets_df["Question"]
                    if "id" not in markets_df.columns and "ID" in markets_df.columns:
                        markets_df["id"] = markets_df["ID"]
                    print(f"  Loaded {len(markets_df):,} markets from parquet")
    
    # Fallback to database
    if markets_df.empty:
        db_path = db_path or DEFAULT_DB_PATH
        print(f"Loading markets from database {db_path}...")
        try:
            db = PredictionMarketDB(db_path)
            markets_df = db.get_all_markets()
            db.close()
            print(f"  Loaded {len(markets_df):,} markets from database")
        except Exception as e:
            print(f"  ERROR: Could not load markets: {e}")
            return pd.DataFrame()
    
    if markets_df.empty:
        print("  ERROR: No markets found!")
        return pd.DataFrame()
    
    # Ensure required columns
    if "question" not in markets_df.columns:
        markets_df["question"] = ""
    if "id" not in markets_df.columns:
        print("  ERROR: No 'id' column found!")
        return pd.DataFrame()
    
    # Filter out markets without questions
    original_count = len(markets_df)
    markets_df = markets_df[markets_df["question"].notna() & (markets_df["question"] != "")].copy()
    if len(markets_df) < original_count:
        print(f"  Filtered out {original_count - len(markets_df):,} markets without questions")
    
    # Limit if requested
    if max_markets and len(markets_df) > max_markets:
        print(f"  Limiting to {max_markets:,} markets")
        markets_df = markets_df.head(max_markets)
    
    return markets_df


def load_checkpoint() -> Tuple[Set[str], pd.DataFrame]:
    """Load checkpoint data."""
    if not CHECKPOINT_FILE.exists():
        return set(), pd.DataFrame()
    
    try:
        with open(CHECKPOINT_FILE, 'r') as f:
            checkpoint_data = json.load(f)
        
        processed_pairs = set()
        for pair in checkpoint_data.get("processed_pairs", []):
            processed_pairs.add(tuple(sorted(pair)))
        
        # Load existing similarities if file exists
        similarities_df = pd.DataFrame()
        if SIMILARITY_FILE.exists():
            similarities_df = pd.read_parquet(SIMILARITY_FILE)
        
        return processed_pairs, similarities_df
    except Exception as e:
        print(f"  Warning: Could not load checkpoint: {e}")
        return set(), pd.DataFrame()


def save_checkpoint(processed_pairs: Set[Tuple[str, str]], similarities_df: pd.DataFrame):
    """Save checkpoint data."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    
    checkpoint_data = {
        "timestamp": datetime.now().isoformat(),
        "processed_pairs": [list(pair) for pair in processed_pairs],
        "total_similarities": len(similarities_df),
    }
    
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(checkpoint_data, f, indent=2)
    
    # Save similarities incrementally
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    similarities_df.to_parquet(SIMILARITY_FILE, index=False, compression='snappy')


def compute_similarities_batch(
    markets_df: pd.DataFrame,
    similarity_engine: NLPSimilarityEngine,
    batch_size: int = 50,
    min_similarity: float = 0.3,
    resume: bool = False,
    save_interval: int = 1000,
) -> pd.DataFrame:
    """
    Compute pairwise similarities in batches with checkpointing.
    
    Args:
        markets_df: DataFrame with market data
        similarity_engine: NLP similarity engine
        batch_size: Number of markets to process per batch
        min_similarity: Minimum similarity threshold to save
        resume: Whether to resume from checkpoint
        save_interval: Save checkpoint every N comparisons
    
    Returns:
        DataFrame with market_id1, market_id2, similarity_score
    """
    n_markets = len(markets_df)
    market_ids = markets_df["id"].astype(str).tolist()
    
    # Load checkpoint if resuming
    processed_pairs = set()
    similarities_df = pd.DataFrame(columns=["market_id1", "market_id2", "similarity_score", "method"])
    
    if resume:
        processed_pairs, existing_similarities = load_checkpoint()
        if not existing_similarities.empty:
            similarities_df = existing_similarities
            print(f"  Resuming: {len(processed_pairs):,} pairs already processed")
            print(f"  Existing similarities: {len(similarities_df):,}")
    
    # Compute similarities in batches
    total_comparisons = n_markets * (n_markets - 1) // 2
    comparisons_done = len(processed_pairs)
    
    print(f"\nComputing similarities for {n_markets:,} markets")
    print(f"  Total comparisons: {total_comparisons:,}")
    print(f"  Comparisons done: {comparisons_done:,}")
    print(f"  Remaining: {total_comparisons - comparisons_done:,}")
    print(f"  Batch size: {batch_size}")
    print(f"  Min similarity: {min_similarity}")
    print(f"  Save interval: {save_interval:,} comparisons\n")
    
    # Process in batches
    new_similarities = []
    comparisons_since_save = 0
    
    # Use tqdm for progress bar (or simple counter if not available)
    pbar = tqdm(total=total_comparisons, initial=comparisons_done, desc="Computing similarities")
    last_progress_print = 0
    
    try:
        for i_start in range(0, n_markets, batch_size):
            i_end = min(i_start + batch_size, n_markets)
            
            # Process batch i against all markets j > i
            for i in range(i_start, i_end):
                market_id1 = market_ids[i]
                question1 = str(markets_df.iloc[i]["question"])
                
                # Compare with all markets after i
                for j in range(i + 1, n_markets):
                    market_id2 = market_ids[j]
                    pair_key = tuple(sorted([market_id1, market_id2]))
                    
                    # Skip if already processed
                    if pair_key in processed_pairs:
                        pbar.update(1)
                        continue
                    
                    # Compute similarity
                    question2 = str(markets_df.iloc[j]["question"])
                    try:
                        similarity = similarity_engine.compute_similarity(question1, question2)
                        
                        # Save if above threshold
                        if similarity >= min_similarity:
                            new_similarities.append({
                                "market_id1": market_id1,
                                "market_id2": market_id2,
                                "similarity_score": similarity,
                                "method": similarity_engine.method,
                            })
                        
                        # Mark as processed
                        processed_pairs.add(pair_key)
                        comparisons_done += 1
                        comparisons_since_save += 1
                        
                        # Save checkpoint periodically
                        if comparisons_since_save >= save_interval:
                            if new_similarities:
                                new_df = pd.DataFrame(new_similarities)
                                similarities_df = pd.concat([similarities_df, new_df], ignore_index=True)
                                new_similarities = []
                            
                            save_checkpoint(processed_pairs, similarities_df)
                            comparisons_since_save = 0
                            pbar.set_postfix({
                                "similarities": len(similarities_df),
                                "processed": len(processed_pairs)
                            })
                    
                    except Exception as e:
                        print(f"\n  Error computing similarity for ({market_id1}, {market_id2}): {e}")
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
    if new_similarities:
        new_df = pd.DataFrame(new_similarities)
        similarities_df = pd.concat([similarities_df, new_df], ignore_index=True)
    
    save_checkpoint(processed_pairs, similarities_df)
    
    return similarities_df


def main():
    parser = argparse.ArgumentParser(
        description="Compute Market Similarity Matrix (Overnight Batch Processing)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Process all markets (overnight task)
    python scripts/research/compute_market_similarities.py

    # Test with limited markets
    python scripts/research/compute_market_similarities.py --max-markets 500

    # Resume from checkpoint
    python scripts/research/compute_market_similarities.py --resume

    # Lower similarity threshold (more results)
    python scripts/research/compute_market_similarities.py --min-similarity 0.2

    # Use TF-IDF method (faster, less memory)
    python scripts/research/compute_market_similarities.py --method tfidf
        """
    )
    
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Database path (default: data/prediction_markets.db)",
    )
    parser.add_argument(
        "--parquet-markets-dir",
        type=str,
        default="data/polymarket",
        help="Directory with markets parquet files",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=None,
        help="Maximum number of markets to process (for testing)",
    )
    parser.add_argument(
        "--method",
        type=str,
        default="tfidf",
        choices=["bag_of_words", "tfidf", "embedding", "hybrid"],
        help="Similarity method (tfidf is fastest and lowest memory)",
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.3,
        help="Minimum similarity threshold to save (0.0-1.0)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of markets to process per batch (lower = less memory)",
    )
    parser.add_argument(
        "--save-interval",
        type=int,
        default=1000,
        help="Save checkpoint every N comparisons",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh (ignore checkpoint)",
    )
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("MARKET SIMILARITY MATRIX COMPUTATION")
    print("=" * 70)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Load markets
    print("\n[1] Loading markets...")
    markets_df = load_markets(
        db_path=args.db_path,
        parquet_markets_dir=args.parquet_markets_dir,
        max_markets=args.max_markets,
    )
    
    if markets_df.empty:
        print("ERROR: No markets loaded")
        return 1
    
    print(f"  Markets loaded: {len(markets_df):,}")
    
    # Create similarity engine
    print(f"\n[2] Initializing similarity engine...")
    print(f"  Method: {args.method}")
    print(f"  Min similarity: {args.min_similarity}")
    
    similarity_engine = NLPSimilarityEngine(
        method=args.method,
        min_similarity=args.min_similarity,
    )
    
    # Compute similarities
    print(f"\n[3] Computing pairwise similarities...")
    resume_flag = args.resume and not args.no_resume
    
    similarities_df = compute_similarities_batch(
        markets_df=markets_df,
        similarity_engine=similarity_engine,
        batch_size=args.batch_size,
        min_similarity=args.min_similarity,
        resume=resume_flag,
        save_interval=args.save_interval,
    )
    
    # Final summary
    print("\n" + "=" * 70)
    print("COMPUTATION COMPLETE")
    print("=" * 70)
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\nResults:")
    print(f"  Total similarities found: {len(similarities_df):,}")
    print(f"  Output file: {SIMILARITY_FILE}")
    print(f"  Checkpoint file: {CHECKPOINT_FILE}")
    
    if not similarities_df.empty:
        print(f"\nSimilarity statistics:")
        print(f"  Mean: {similarities_df['similarity_score'].mean():.3f}")
        print(f"  Median: {similarities_df['similarity_score'].median():.3f}")
        print(f"  Min: {similarities_df['similarity_score'].min():.3f}")
        print(f"  Max: {similarities_df['similarity_score'].max():.3f}")
        print(f"  Std: {similarities_df['similarity_score'].std():.3f}")
        
        # Top similarities
        top_similarities = similarities_df.nlargest(10, 'similarity_score')
        print(f"\nTop 10 most similar market pairs:")
        for idx, row in top_similarities.iterrows():
            print(f"  {row['market_id1'][:20]}... <-> {row['market_id2'][:20]}... : {row['similarity_score']:.3f}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

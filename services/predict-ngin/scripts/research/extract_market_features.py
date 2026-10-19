#!/usr/bin/env python3
"""
Extract Market Features (Overnight Batch Processing)

Extracts comprehensive features for all markets from price and trade data.
Designed for low-resource overnight processing.

Features extracted:
- Price statistics (volatility, returns, trends)
- Volume patterns (daily volume, volume trends)
- Liquidity metrics (spread estimates, depth)
- Time-based patterns (trading hours, activity patterns)
- Market lifecycle (days to resolution, age)

Usage:
    # Extract features for all markets
    python scripts/research/extract_market_features.py

    # Limit markets for testing
    python scripts/research/extract_market_features.py --max-markets 500

    # Resume from checkpoint
    python scripts/research/extract_market_features.py --resume
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
from datetime import datetime, timedelta
from typing import Optional, Set, Dict, Any
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
from src.trading.data_modules.parquet_store import PriceStore, TradeStore


# Default paths
CHECKPOINT_DIR = Path("data/research/checkpoints")
OUTPUT_DIR = Path("data/research/features")
CHECKPOINT_FILE = CHECKPOINT_DIR / "features_checkpoint.json"
FEATURES_FILE = OUTPUT_DIR / "market_features.parquet"


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
    
    if "id" not in markets_df.columns:
        print("  ERROR: No 'id' column found!")
        return pd.DataFrame()
    
    if max_markets and len(markets_df) > max_markets:
        print(f"  Limiting to {max_markets:,} markets")
        markets_df = markets_df.head(max_markets)
    
    return markets_df


def extract_price_features(prices_df: pd.DataFrame) -> Dict[str, Any]:
    """Extract features from price history."""
    if prices_df.empty or len(prices_df) < 2:
        return {}
    
    prices_df = prices_df.sort_values("datetime")
    prices = prices_df["price"].values
    
    features = {}
    
    # Basic statistics
    features["price_mean"] = float(prices.mean())
    features["price_std"] = float(prices.std())
    features["price_min"] = float(prices.min())
    features["price_max"] = float(prices.max())
    features["price_range"] = features["price_max"] - features["price_min"]
    
    # Returns
    returns = np.diff(prices) / prices[:-1]
    if len(returns) > 0:
        features["return_mean"] = float(returns.mean())
        features["return_std"] = float(returns.std())
        features["return_skew"] = float(pd.Series(returns).skew()) if len(returns) > 2 else 0.0
        features["return_kurtosis"] = float(pd.Series(returns).kurtosis()) if len(returns) > 2 else 0.0
        
        # Volatility (annualized)
        if features["return_std"] > 0:
            features["volatility_annualized"] = float(features["return_std"] * np.sqrt(365 * 24))  # Assuming hourly data
        else:
            features["volatility_annualized"] = 0.0
    
    # Trends
    if len(prices) > 1:
        time_diff = (prices_df["datetime"].iloc[-1] - prices_df["datetime"].iloc[0]).total_seconds() / 3600
        if time_diff > 0:
            price_change = prices[-1] - prices[0]
            features["trend_slope"] = float(price_change / time_diff)
            features["total_return"] = float(price_change / prices[0])
        else:
            features["trend_slope"] = 0.0
            features["total_return"] = 0.0
    
    # Recent vs historical
    if len(prices) >= 24:
        recent_prices = prices[-24:]
        historical_prices = prices[:-24]
        features["recent_mean"] = float(recent_prices.mean())
        features["historical_mean"] = float(historical_prices.mean())
        features["recent_vs_historical"] = features["recent_mean"] - features["historical_mean"]
    
    # Price stability (time spent near current price)
    if len(prices) > 1:
        current_price = prices[-1]
        price_window = 0.05  # 5% window
        near_current = np.abs(prices - current_price) < (current_price * price_window)
        features["stability_ratio"] = float(near_current.sum() / len(prices))
    
    return features


def extract_volume_features(trades_df: pd.DataFrame) -> Dict[str, Any]:
    """Extract features from trade history."""
    if trades_df.empty:
        return {}
    
    features = {}
    
    # Volume metrics
    if "usd_amount" in trades_df.columns:
        features["total_volume"] = float(trades_df["usd_amount"].sum())
        features["avg_trade_size"] = float(trades_df["usd_amount"].mean())
        features["median_trade_size"] = float(trades_df["usd_amount"].median())
        features["max_trade_size"] = float(trades_df["usd_amount"].max())
        features["trade_count"] = len(trades_df)
    else:
        features["total_volume"] = 0.0
        features["avg_trade_size"] = 0.0
        features["median_trade_size"] = 0.0
        features["max_trade_size"] = 0.0
        features["trade_count"] = len(trades_df)
    
    # Volume over time
    if "timestamp" in trades_df.columns and "usd_amount" in trades_df.columns:
        trades_df["date"] = pd.to_datetime(trades_df["timestamp"]).dt.date
        daily_volume = trades_df.groupby("date")["usd_amount"].sum()
        if len(daily_volume) > 0:
            features["avg_daily_volume"] = float(daily_volume.mean())
            features["daily_volume_std"] = float(daily_volume.std())
            features["max_daily_volume"] = float(daily_volume.max())
            
            # Volume trend
            if len(daily_volume) > 1:
                volume_trend = np.polyfit(range(len(daily_volume)), daily_volume.values, 1)[0]
                features["volume_trend"] = float(volume_trend)
            else:
                features["volume_trend"] = 0.0
    
    # Trading frequency
    if "timestamp" in trades_df.columns:
        trades_df["datetime"] = pd.to_datetime(trades_df["timestamp"])
        time_span = (trades_df["datetime"].max() - trades_df["datetime"].min()).total_seconds() / 3600
        if time_span > 0:
            features["trades_per_hour"] = float(len(trades_df) / time_span)
        else:
            features["trades_per_hour"] = 0.0
    
    return features


def extract_market_features(
    market_id: str,
    market_info: Dict[str, Any],
    prices_df: Optional[pd.DataFrame],
    trades_df: Optional[pd.DataFrame],
) -> Dict[str, Any]:
    """Extract all features for a single market."""
    features = {
        "market_id": market_id,
        "extracted_at": datetime.now().isoformat(),
    }
    
    # Market metadata
    features["question"] = str(market_info.get("question", ""))[:200]  # Truncate
    features["volume"] = float(market_info.get("volume", 0) or 0)
    features["volume_24hr"] = float(market_info.get("volume24hr", 0) or 0)
    features["liquidity"] = float(market_info.get("liquidity", 0) or 0)
    
    # End date / resolution
    end_date = market_info.get("endDate") or market_info.get("end_date")
    if end_date:
        try:
            end_dt = pd.to_datetime(end_date)
            features["end_date"] = end_dt.isoformat()
            features["days_to_resolution"] = (end_dt - datetime.now()).total_seconds() / 86400
        except:
            features["end_date"] = None
            features["days_to_resolution"] = None
    else:
        features["end_date"] = None
        features["days_to_resolution"] = None
    
    # Price features
    if prices_df is not None and not prices_df.empty:
        price_features = extract_price_features(prices_df)
        features.update(price_features)
        features["has_price_data"] = True
        features["price_points"] = len(prices_df)
    else:
        features["has_price_data"] = False
        features["price_points"] = 0
    
    # Volume/trade features
    if trades_df is not None and not trades_df.empty:
        volume_features = extract_volume_features(trades_df)
        features.update(volume_features)
        features["has_trade_data"] = True
    else:
        features["has_trade_data"] = False
    
    return features


def load_checkpoint() -> Set[str]:
    """Load checkpoint data."""
    if not CHECKPOINT_FILE.exists():
        return set()
    
    try:
        with open(CHECKPOINT_FILE, 'r') as f:
            checkpoint_data = json.load(f)
        return set(checkpoint_data.get("processed_markets", []))
    except Exception as e:
        print(f"  Warning: Could not load checkpoint: {e}")
        return set()


def save_checkpoint(processed_markets: Set[str], features_df: pd.DataFrame):
    """Save checkpoint data."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    
    checkpoint_data = {
        "timestamp": datetime.now().isoformat(),
        "processed_markets": list(processed_markets),
        "total_features": len(features_df),
    }
    
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(checkpoint_data, f, indent=2)
    
    # Save features incrementally
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    features_df.to_parquet(FEATURES_FILE, index=False, compression='snappy')


def main():
    parser = argparse.ArgumentParser(
        description="Extract Market Features (Overnight Batch Processing)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Extract features for all markets
    python scripts/research/extract_market_features.py

    # Test with limited markets
    python scripts/research/extract_market_features.py --max-markets 500

    # Resume from checkpoint
    python scripts/research/extract_market_features.py --resume
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
        "--parquet-prices-dir",
        type=str,
        default="data/polymarket/prices",
        help="Directory with prices parquet files",
    )
    parser.add_argument(
        "--parquet-trades-dir",
        type=str,
        default="data/polymarket/trades",
        help="Directory with trades parquet files",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=None,
        help="Maximum number of markets to process (for testing)",
    )
    parser.add_argument(
        "--save-interval",
        type=int,
        default=100,
        help="Save checkpoint every N markets",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint",
    )
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("MARKET FEATURE EXTRACTION")
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
    
    # Initialize data stores
    print("\n[2] Initializing data stores...")
    price_store = None
    trade_store = None
    
    if args.parquet_prices_dir:
        price_store = PriceStore(args.parquet_prices_dir)
        if price_store.available():
            print("  Price store: available")
        else:
            print("  Price store: not available")
            price_store = None
    
    if args.parquet_trades_dir:
        trade_store = TradeStore(args.parquet_trades_dir)
        if trade_store.available():
            print("  Trade store: available")
        else:
            print("  Trade store: not available")
            trade_store = None
    
    # Load checkpoint
    processed_markets = set()
    features_df = pd.DataFrame()
    
    if args.resume:
        processed_markets = load_checkpoint()
        if FEATURES_FILE.exists():
            features_df = pd.read_parquet(FEATURES_FILE)
        print(f"  Resuming: {len(processed_markets):,} markets already processed")
    
    # Extract features
    print(f"\n[3] Extracting features...")
    print(f"  Markets to process: {len(markets_df) - len(processed_markets):,}")
    
    new_features = []
    markets_since_save = 0
    
    pbar = tqdm(total=len(markets_df), initial=len(processed_markets), desc="Extracting features")
    
    try:
        for idx, row in markets_df.iterrows():
            market_id = str(row["id"])
            
            if market_id in processed_markets:
                pbar.update(1)
                continue
            
            # Load price and trade data
            prices_df = None
            trades_df = None
            
            if price_store:
                try:
                    prices_df = price_store.load_prices_for_markets([market_id], outcome="YES")
                except:
                    pass
            
            if trade_store:
                try:
                    trades_df = trade_store.load_trades(market_ids=[market_id])
                except:
                    pass
            
            # Extract features
            market_info = row.to_dict()
            features = extract_market_features(market_id, market_info, prices_df, trades_df)
            new_features.append(features)
            
            processed_markets.add(market_id)
            markets_since_save += 1
            
            # Save checkpoint periodically
            if markets_since_save >= args.save_interval:
                if new_features:
                    new_df = pd.DataFrame(new_features)
                    features_df = pd.concat([features_df, new_df], ignore_index=True)
                    new_features = []
                
                save_checkpoint(processed_markets, features_df)
                markets_since_save = 0
                pbar.set_postfix({"features": len(features_df)})
            
            pbar.update(1)
    
    finally:
        if hasattr(pbar, '__exit__'):
            pbar.__exit__(None, None, None)
    
    # Save final checkpoint
    if new_features:
        new_df = pd.DataFrame(new_features)
        features_df = pd.concat([features_df, new_df], ignore_index=True)
    
    save_checkpoint(processed_markets, features_df)
    
    # Final summary
    print("\n" + "=" * 70)
    print("EXTRACTION COMPLETE")
    print("=" * 70)
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\nResults:")
    print(f"  Markets processed: {len(processed_markets):,}")
    print(f"  Features extracted: {len(features_df):,}")
    print(f"  Output file: {FEATURES_FILE}")
    print(f"  Checkpoint file: {CHECKPOINT_FILE}")
    
    if not features_df.empty:
        print(f"\nFeature statistics:")
        print(f"  Markets with price data: {features_df['has_price_data'].sum():,}")
        print(f"  Markets with trade data: {features_df['has_trade_data'].sum():,}")
        if "volatility_annualized" in features_df.columns:
            print(f"  Avg volatility: {features_df['volatility_annualized'].mean():.3f}")
        if "total_volume" in features_df.columns:
            print(f"  Total volume: ${features_df['total_volume'].sum():,.0f}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

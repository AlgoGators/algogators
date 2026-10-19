"""
PriceStore implementation that extracts prices from trade data.

Useful when you don't have separate price history files but have trades.
"""

from typing import Optional, Dict, Any, List
from collections import OrderedDict

import pandas as pd
import numpy as np

try:
    from src.data_quality.polymarket_tokens import ensure_yes_trades
except ImportError:  # src/ itself on sys.path
    from data_quality.polymarket_tokens import ensure_yes_trades

from .parquet_store import PriceStore
try:
    from src.timeconv import to_epoch, to_epoch_seconds
except ImportError:  # src/ itself on sys.path
    from timeconv import to_epoch, to_epoch_seconds


class TradeBasedPriceStore(PriceStore):
    """
    PriceStore that extracts prices from trade data.
    
    Useful when you don't have separate price history files but have trades.
    Prices are extracted from trades (either directly from 'price' column or
    calculated from usd_amount / token_amount).
    
    Note: Trade-based prices are sparse (only when trades occur), so this
    is best for strategies that don't need continuous price history.
    
    Example:
        from trading.data_modules.parquet_store import TradeStore
        from trading.data_modules.trade_price_store import TradeBasedPriceStore
        
        # Load trades
        trade_store = TradeStore()
        trades = trade_store.load_trades(min_usd=10)
        
        # Create price store from trades
        price_store = TradeBasedPriceStore(trades)
        
        # Use in backtest
        from trading.polymarket_backtest import run_polymarket_backtest
        result = run_polymarket_backtest(signals_df, price_store=price_store)
    """
    
    def __init__(self, trades_df: pd.DataFrame, cache_size: int = 100):
        """
        Initialize from trades DataFrame.
        
        Args:
            trades_df: DataFrame with columns:
                      - market_id, outcome, timestamp (required)
                      - price (preferred) OR usd_amount + token_amount (to calculate)
            cache_size: LRU cache size for price histories
        """
        # Don't call super().__init__() - we don't use file-based storage
        self.base_dir = None
        
        # Store only market volume summary for metadata (much smaller than full trades)
        # Calculate volume per market upfront to avoid storing full trades
        if not trades_df.empty:
            # pmfix: YES-normalise before the outcome="YES" inference below
            trades_df = ensure_yes_trades(trades_df, where="TradeBasedPriceStore")
            if "usd_amount" in trades_df.columns:
                self._market_volumes = trades_df.groupby("market_id")["usd_amount"].sum().to_dict()
            elif "price" in trades_df.columns and "token_amount" in trades_df.columns:
                volume_df = trades_df.copy()
                volume_df["volume"] = volume_df["price"] * volume_df["token_amount"]
                self._market_volumes = volume_df.groupby("market_id")["volume"].sum().to_dict()
            else:
                # Fallback: trade count
                self._market_volumes = trades_df.groupby("market_id").size().to_dict()
        else:
            self._market_volumes = {}
        
        if trades_df.empty:
            self._price_df = pd.DataFrame(
                columns=["market_id", "outcome", "timestamp", "price", "timestamp_unix"]
            )
        else:
            trades_df = trades_df.copy()
            
            # Calculate price if needed
            if "price" not in trades_df.columns:
                if "usd_amount" in trades_df.columns and "token_amount" in trades_df.columns:
                    trades_df["price"] = (
                        trades_df["usd_amount"] / trades_df["token_amount"]
                    )
                    # Remove invalid prices
                    trades_df = trades_df[
                        (trades_df["price"] > 0) & (trades_df["price"] < 1)
                    ]
                else:
                    raise ValueError(
                        "Trades must have 'price' column or both 'usd_amount' and 'token_amount'"
                    )
            
            # Add outcome column if missing (infer from maker_direction or taker_direction)
            if "outcome" not in trades_df.columns:
                if "maker_direction" in trades_df.columns:
                    trades_df["outcome"] = trades_df["maker_direction"].str.upper()
                    # Normalize: if it's buy/sell or other values, default to YES
                    trades_df.loc[~trades_df["outcome"].isin(["YES", "NO"]), "outcome"] = "YES"
                elif "taker_direction" in trades_df.columns:
                    trades_df["outcome"] = trades_df["taker_direction"].str.upper()
                    trades_df.loc[~trades_df["outcome"].isin(["YES", "NO"]), "outcome"] = "YES"
                else:
                    # No direction info, default to YES
                    trades_df["outcome"] = "YES"
            
            # Create price history from trades
            self._price_df = (
                trades_df[["market_id", "outcome", "timestamp", "price"]]
                .copy()
                .dropna(subset=["market_id", "outcome", "timestamp", "price"])
            )
            
            # Normalize outcome to uppercase
            if "outcome" in self._price_df.columns:
                self._price_df["outcome"] = self._price_df["outcome"].str.upper()
            
            # Convert timestamp to unix seconds if needed
            if self._price_df["timestamp"].dtype == "object":
                self._price_df["timestamp"] = pd.to_datetime(
                    self._price_df["timestamp"], errors="coerce"
                )
            
            # Ensure numeric timestamp (unix seconds)
            if pd.api.types.is_datetime64_any_dtype(self._price_df["timestamp"]):
                self._price_df["timestamp_unix"] = (
                    to_epoch_seconds(self._price_df["timestamp"])
                )
            elif self._price_df["timestamp"].dtype in ["int64", "int32", "float64"]:
                # Already numeric, assume unix seconds (or milliseconds)
                ts_values = self._price_df["timestamp"].values
                # If values > 1e12, assume milliseconds
                if ts_values.max() > 1_000_000_000_000:
                    self._price_df["timestamp_unix"] = (ts_values // 1000).astype("int64")
                else:
                    self._price_df["timestamp_unix"] = ts_values.astype("int64")
            else:
                # Try to convert
                self._price_df["timestamp_unix"] = pd.to_numeric(
                    self._price_df["timestamp"], errors="coerce"
                )
                # Handle milliseconds
                if self._price_df["timestamp_unix"].max() > 1_000_000_000_000:
                    self._price_df["timestamp_unix"] = (
                        self._price_df["timestamp_unix"] // 1000
                    ).astype("int64")
            
            # Remove invalid timestamps
            self._price_df = self._price_df[
                (self._price_df["timestamp_unix"] > 0) &
                (self._price_df["price"] > 0) &
                (self._price_df["price"] < 1)
            ]
            
            # Sort for efficient lookups
            self._price_df = self._price_df.sort_values(
                ["market_id", "outcome", "timestamp_unix"]
            ).reset_index(drop=True)
        
        # LRU cache
        self._cache: OrderedDict = OrderedDict()
        self._cache_size = cache_size
        self._metadata_cache: Dict[str, Dict[str, Any]] = {}
    
    def get_market_metadata(self, market_id: str) -> Dict[str, Any]:
        """
        Get market metadata from trades.
        
        Since we only have trades, we can calculate volume but not liquidity or end_date.
        Returns minimal metadata compatible with backtest expectations.
        
        Args:
            market_id: Market identifier
            
        Returns:
            Dict with liquidity, volume, volume_24hr, end_date, etc.
        """
        
        # Check cache
        if market_id in self._metadata_cache:
            return self._metadata_cache[market_id]
        
        # Get volume from pre-calculated market volumes (much faster and memory-efficient)
        volume = float(self._market_volumes.get(str(market_id), 0.0))
        
        
        # Return minimal metadata compatible with backtest
        meta = {
            "id": str(market_id),
            "liquidity": 0.0,  # Not available from trades
            "volume": volume,
            "volume_24hr": volume,  # Use total volume as proxy
            "end_date": None,  # Not available from trades
            "endDate": None,
            "closed_time": None,
            "resolution_outcome": None,
            "resolution_price_yes": None,
            "resolution_price_no": None,
        }
        
        # Cache it
        self._metadata_cache[market_id] = meta
        
        
        return meta
    
    def available(self) -> bool:
        """Always available if trades were provided."""
        return not self._price_df.empty
    
    def get_price_history(
        self,
        market_id: str,
        outcome: str = "YES",
    ) -> pd.DataFrame:
        """
        Get price history from trades.
        
        Returns sparse price history (only when trades occurred).
        """
        cache_key = (str(market_id), outcome.upper())
        
        # Check cache
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key].copy()
        
        # Filter trades for this market/outcome
        df = self._price_df[
            (self._price_df["market_id"] == str(market_id)) &
            (self._price_df["outcome"] == outcome.upper())
        ].copy()
        
        if df.empty:
            result = pd.DataFrame(
                columns=["market_id", "outcome", "timestamp", "price"]
            )
        else:
            # Convert timestamp_unix back to datetime for output
            result = df[["market_id", "outcome", "timestamp", "price"]].copy()
            if "timestamp_unix" in df.columns:
                # Use original timestamp if available, otherwise convert from unix
                if pd.api.types.is_datetime64_any_dtype(result["timestamp"]):
                    pass  # Already datetime
                else:
                    result["timestamp"] = pd.to_datetime(
                        df["timestamp_unix"], unit="s", errors="coerce"
                    )
            
            result = result.sort_values("timestamp").reset_index(drop=True)
        
        # Cache with LRU eviction
        if len(self._cache) >= self._cache_size:
            self._cache.popitem(last=False)
        
        self._cache[cache_key] = result
        return result.copy()
    
    def price_at_or_after(
        self,
        market_id: str,
        outcome: str,
        timestamp: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Get price at or after timestamp from trades.
        
        If no trade exists at or after timestamp, returns the last known price
        (forward-fill behavior).
        """
        df = self._price_df[
            (self._price_df["market_id"] == str(market_id)) &
            (self._price_df["outcome"] == outcome.upper())
        ]
        
        if df.empty:
            return None
        
        # Find trades at or after timestamp
        df_after = df[df["timestamp_unix"] >= timestamp]
        
        if df_after.empty:
            # No trades after timestamp - use last known price (forward-fill)
            row = df.iloc[-1]
        else:
            # Use first trade at or after timestamp
            row = df_after.iloc[0]
        
        return {
            "timestamp": int(row["timestamp_unix"]),
            "price": float(row["price"]),
        }
    
    def last_price(
        self,
        market_id: str,
        outcome: str,
    ) -> Optional[Dict[str, Any]]:
        """Get last price from trades."""
        df = self._price_df[
            (self._price_df["market_id"] == str(market_id)) &
            (self._price_df["outcome"] == outcome.upper())
        ]
        
        if df.empty:
            return None
        
        row = df.iloc[-1]
        return {
            "timestamp": int(row["timestamp_unix"]),
            "price": float(row["price"]),
        }
    
    def load_prices_for_markets(
        self,
        market_ids: List[str],
        outcome: str = "YES",
    ) -> pd.DataFrame:
        """
        Load prices for multiple markets at once from trades.
        
        Args:
            market_ids: List of market identifiers
            outcome: YES or NO
            
        Returns:
            DataFrame with price data for all specified markets
        """
        if not market_ids:
            return pd.DataFrame()
        
        market_ids_str = [str(m) for m in market_ids]
        
        df = self._price_df[
            (self._price_df["market_id"].isin(market_ids_str)) &
            (self._price_df["outcome"] == outcome.upper())
        ].copy()
        
        if df.empty:
            return pd.DataFrame()
        
        result = df[["market_id", "outcome", "timestamp", "price"]].copy()
        if "timestamp_unix" in df.columns:
            if not pd.api.types.is_datetime64_any_dtype(result["timestamp"]):
                result["timestamp"] = pd.to_datetime(
                    df["timestamp_unix"], unit="s", errors="coerce"
                )
        
        return result.sort_values(["market_id", "timestamp"]).reset_index(drop=True)


__all__ = ["TradeBasedPriceStore"]

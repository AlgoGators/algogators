"""
Parquet-based data store for large tables (prices, trades).

Provides pandas-compatible loading with date/market filtering.
Small metadata tables remain in SQLite.

Usage:
    from trading.data_modules.parquet_store import TradeStore, PriceStore

    trades = TradeStore().load_trades(min_usd=100, start_date="2024-01-01")
    prices = PriceStore().get_price_history("12345", "YES")
"""

from pathlib import Path
import os
from typing import List, Optional, Sequence
from collections import OrderedDict

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

try:
    from src.data_quality.polymarket_tokens import ensure_yes_trades
except ImportError:  # src/ itself on sys.path
    from data_quality.polymarket_tokens import ensure_yes_trades

try:
    from src.data_quality import legacy_pm_trades as _legacy_trades
except ImportError:  # src/ itself on sys.path
    from data_quality import legacy_pm_trades as _legacy_trades

try:
    import polars as pl
except ImportError:  # pragma: no cover - optional dependency
    pl = None

def _require_polars() -> bool:
    return os.getenv("PREDICT_NGIN_FORCE_POLARS", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


DEFAULT_PARQUET_DIR = "data/polymarket"


_PARQUET_TRADES_FALLBACK = "data/parquet/trades"
_PARQUET_PRICES_FALLBACK = "data/parquet/prices"


class TradeStore:
    """Load trade data from monthly partitioned parquet files."""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir or f"{DEFAULT_PARQUET_DIR}/trades")

    def _effective_dir(self) -> Path:
        """Use polymarket/trades; fallback to parquet/trades if empty."""
        if self.base_dir.exists() and any(self.base_dir.glob("trades_*.parquet")):
            return self.base_dir
        fallback = Path(_PARQUET_TRADES_FALLBACK)
        if fallback.exists() and any(fallback.glob("trades_*.parquet")):
            return fallback
        return self.base_dir

    def _has_monthly_files(self) -> bool:
        return self._effective_dir().exists() and any(self._effective_dir().glob("trades_*.parquet"))

    def _compat(self) -> bool:
        """Monthly files gone (deleted 2026-10-17 as a duplicate): serve them from trades.parquet instead."""
        return not self._has_monthly_files() and _legacy_trades.available(self.base_dir)

    def available(self) -> bool:
        """Check if parquet trade files exist (or the trades.parquet they were built from)."""
        return self._has_monthly_files() or self._compat()

    def list_months(self) -> List[str]:
        """List available months (YYYY-MM)."""
        if self._compat():
            return _legacy_trades.months(self.base_dir)
        files = sorted(self._effective_dir().glob("trades_*.parquet"))
        return [f.stem.replace("trades_", "") for f in files]

    def _load_trades_compat(self, min_usd, start_date, end_date, limit, columns) -> pd.DataFrame:
        from predict_ngin.shared.duck import connect

        sql = _legacy_trades.trades_sql(columns, start_date, end_date, min_usd, self.base_dir)
        sql += " ORDER BY timestamp" + (f" LIMIT {int(limit)}" if limit else "")
        return connect().sql(sql).df()

    def _select_files(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Path]:
        """Select parquet files based on date range."""
        files = sorted(self._effective_dir().glob("trades_*.parquet"))
        if not files:
            return []

        if start_date:
            start_month = start_date[:7]  # YYYY-MM
            files = [f for f in files if f.stem.replace("trades_", "") >= start_month]
        if end_date:
            end_month = end_date[:7]
            files = [f for f in files if f.stem.replace("trades_", "") <= end_month]

        return files

    def load_trades(
        self,
        min_usd: float = 10.0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        columns: Optional[Sequence[str]] = None,
        yes_normalize: bool = True,
    ) -> pd.DataFrame:
        """Load trades; by default every print is expressed in YES-token terms (pmfix).

        The monthly files store each print in units of whichever token traded (``nonusdc_side`` token1/token2),
        so a raw ``price`` series interleaves p and 1-p (STRATEGY_AUDIT D1). With ``yes_normalize`` (default)
        ``price`` is the YES price, maker/taker directions are in YES terms, ``price_raw`` keeps the stored
        value and rows with unknown token identity are dropped. Pass ``yes_normalize=False`` for raw rows.
        """
        cols = list(columns) if columns else None
        if yes_normalize and cols is not None and "price" in cols and "nonusdc_side" not in cols:
            cols = cols + ["nonusdc_side"]
        df = self._load_trades_raw(min_usd, start_date, end_date, limit, cols)
        if yes_normalize:
            df = ensure_yes_trades(df, where="TradeStore.load_trades")
        return df

    def _load_trades_raw(
        self,
        min_usd: float = 10.0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        columns: Optional[Sequence[str]] = None,
    ) -> pd.DataFrame:
        """
        Load trades with optional filtering (raw: token-mixed prices).

        Args:
            min_usd: Minimum USD trade size
            start_date: Filter trades after this date (YYYY-MM-DD)
            end_date: Filter trades before this date (YYYY-MM-DD)
            limit: Maximum rows to return

        Returns:
            DataFrame with trade data
        """
        if self._compat():
            return self._load_trades_compat(min_usd, start_date, end_date, limit, columns)
        if _require_polars() and pl is None:
            raise RuntimeError("Polars is required but not installed.")

        if pl is not None:
            try:
                return self._load_trades_polars(min_usd, start_date, end_date, limit, columns)
            except Exception:
                # Fallback to pandas path if polars hits a schema/type edge case
                if _require_polars():
                    raise

        files = self._select_files(start_date, end_date)
        if not files:
            return pd.DataFrame()

        dfs = []
        total_rows = 0

        for f in files:
            df = pd.read_parquet(f, columns=list(columns) if columns else None)

            # Apply USD filter
            if "usd_amount" in df.columns:
                df = df[df["usd_amount"] >= min_usd]

            # Apply date filters (more precise than file-level)
            if start_date and "timestamp" in df.columns:
                df = df[df["timestamp"] >= start_date]
            if end_date and "timestamp" in df.columns:
                df = df[df["timestamp"] <= end_date]

            if df.empty:
                continue

            dfs.append(df)
            total_rows += len(df)

            if limit and total_rows >= limit:
                break

        if not dfs:
            return pd.DataFrame()

        result = pd.concat(dfs, ignore_index=True)
        if "timestamp" in result.columns:
            result = result.sort_values("timestamp")

        if limit:
            result = result.head(limit)

        return result

    def _load_trades_polars(
        self,
        min_usd: float = 10.0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        columns: Optional[Sequence[str]] = None,
    ) -> pd.DataFrame:
        """Load trades with polars for faster parallel IO."""
        files = self._select_files(start_date, end_date)
        if not files:
            return pd.DataFrame()

        try:
            lf = pl.scan_parquet(
                [str(f) for f in files],
                columns=list(columns) if columns else None,
                missing_columns="insert",
            )
            schema = lf.schema

            if "usd_amount" in schema:
                lf = lf.filter(pl.col("usd_amount") >= min_usd)

            # NOTE: timestamp in parquet is stored as ISO-like strings.
            # We keep string comparisons to match the pandas fallback behavior.
            if start_date and "timestamp" in schema:
                lf = lf.filter(pl.col("timestamp").cast(pl.Utf8, strict=False) >= start_date)
            if end_date and "timestamp" in schema:
                lf = lf.filter(pl.col("timestamp").cast(pl.Utf8, strict=False) <= end_date)

            df = lf.collect()
            if df.is_empty():
                return pd.DataFrame()

            if "timestamp" in df.columns:
                df = df.sort("timestamp")

            if limit:
                df = df.head(limit)

            return df.to_pandas()
        except Exception:
            # Some files have mixed dtypes (e.g., market_id int vs float). Read per-file and cast.
            dfs = []
            total_rows = 0
            for f in files:
                df = pl.read_parquet(
                    str(f),
                    columns=list(columns) if columns else None,
                    missing_columns="insert",
                )

                # Normalize market_id dtype to avoid concat schema errors
                if "market_id" in df.columns:
                    df = df.with_columns(
                        pl.col("market_id").cast(pl.Float64, strict=False)
                    )

                # Apply USD filter
                if "usd_amount" in df.columns:
                    df = df.filter(pl.col("usd_amount") >= min_usd)

                # Apply date filters (more precise than file-level)
                if start_date and "timestamp" in df.columns:
                    df = df.filter(pl.col("timestamp").cast(pl.Utf8, strict=False) >= start_date)
                if end_date and "timestamp" in df.columns:
                    df = df.filter(pl.col("timestamp").cast(pl.Utf8, strict=False) <= end_date)

                if df.is_empty():
                    continue

                dfs.append(df)
                total_rows += df.height

                if limit and total_rows >= limit:
                    break

            if not dfs:
                return pd.DataFrame()

            df = pl.concat(dfs, how="vertical")
            if "timestamp" in df.columns:
                df = df.sort("timestamp")
            if limit:
                df = df.head(limit)
            return df.to_pandas()


def _is_parquet(path: Path) -> bool:
    """A complete parquet file ends with the magic bytes PAR1 (a truncated download does not)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(-4, os.SEEK_END)
            return fh.read(4) == b"PAR1"
    except OSError:
        return False


class PriceStore:
    """Load price data from monthly partitioned parquet files.
    
    Optimized for 100GB+ datasets using Polars lazy scanning and LRU caching.
    """

    def __init__(self, base_dir: Optional[str] = None, cache_size: int = 100):
        self.base_dir = Path(base_dir or f"{DEFAULT_PARQUET_DIR}/prices")
        # Use OrderedDict for LRU cache with size limit to prevent memory issues
        self._cache: OrderedDict = OrderedDict()
        self._cache_size = cache_size

    def _effective_dir(self) -> Path:
        """Use polymarket/prices; fallback to parquet/prices if empty."""
        if self.base_dir.exists() and any(self.base_dir.glob("prices_*.parquet")):
            return self.base_dir
        fallback = Path(_PARQUET_PRICES_FALLBACK)
        if fallback.exists() and any(fallback.glob("prices_*.parquet")):
            return fallback
        return self.base_dir

    def _price_files(self) -> List[Path]:
        """Monthly price files, skipping any that is not a complete parquet file (with a warning)."""
        out = []
        for f in sorted(self._effective_dir().glob("prices_*.parquet")):
            if _is_parquet(f):
                out.append(f)
            else:
                import warnings

                warnings.warn(f"skipping unreadable price file {f} (truncated or not parquet)", stacklevel=2)
        return out

    def available(self) -> bool:
        """Check if parquet price files exist."""
        return self._effective_dir().exists() and any(
            self._effective_dir().glob("prices_*.parquet")
        )

    def get_price_history(
        self,
        market_id: str,
        outcome: str = "YES",
    ) -> pd.DataFrame:
        """
        Load price history for a specific market and outcome.
        
        Optimized with Polars lazy scanning for 100GB+ datasets.
        Uses predicate pushdown and column projection for minimal I/O.

        Args:
            market_id: Market identifier
            outcome: YES or NO

        Returns:
            DataFrame with columns [market_id, outcome, timestamp, price]
            sorted by timestamp
        """
        cache_key = (str(market_id), outcome)
        
        # Check cache with LRU eviction
        if cache_key in self._cache:
            # Move to end (most recently used) - OrderedDict maintains order
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key].copy()

        # Use Polars lazy scanning if available (much faster for large datasets)
        if pl is not None:
            try:
                files = self._price_files()
                if not files:
                    result = pd.DataFrame(
                        columns=["market_id", "outcome", "timestamp", "price"]
                    )
                else:
                    # Lazy scan with predicate pushdown - only reads matching rows
                    # This is MUCH faster than scanning all files with pandas
                    lf = pl.concat([
                        pl.scan_parquet(str(f))
                        .filter(pl.col("market_id").cast(pl.Utf8, strict=False) == str(market_id))
                        .filter(pl.col("outcome").cast(pl.Utf8, strict=False) == outcome.upper())
                        for f in files
                    ])
                    
                    # Only select needed columns (column projection)
                    df = (
                        lf.select(["market_id", "outcome", "timestamp", "price"])
                        .sort("timestamp")
                        .collect()
                        .to_pandas()
                    )
                    
                    # Ensure numeric types
                    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
                    df["price"] = pd.to_numeric(df["price"], errors="coerce")
                    df = df.dropna(subset=["timestamp", "price"])
                    df = df.sort_values("timestamp").reset_index(drop=True)
                    
                    result = df
            except Exception as e:
                # Fallback to pandas if Polars fails (e.g., schema issues)
                result = self._get_price_history_pandas(market_id, outcome)
        else:
            # Fallback to pandas if Polars not available
            result = self._get_price_history_pandas(market_id, outcome)

        # Cache with LRU eviction
        if len(self._cache) >= self._cache_size:
            # Remove least recently used (first item in OrderedDict)
            self._cache.popitem(last=False)
        
        self._cache[cache_key] = result
        return result.copy()
    
    def _get_price_history_pandas(
        self,
        market_id: str,
        outcome: str,
    ) -> pd.DataFrame:
        """Fallback pandas implementation for get_price_history."""
        dfs = []
        for f in self._price_files():
            try:
                df = pd.read_parquet(
                    f,
                    filters=[
                        ("market_id", "==", str(market_id)),
                        ("outcome", "==", outcome),
                    ],
                )
                if not df.empty:
                    dfs.append(df)
            except Exception:
                # If predicate pushdown fails, read and filter manually
                df = pd.read_parquet(f)
                df = df[
                    (df["market_id"] == str(market_id))
                    & (df["outcome"] == outcome)
                ]
                if not df.empty:
                    dfs.append(df)

        if not dfs:
            return pd.DataFrame(
                columns=["market_id", "outcome", "timestamp", "price"]
            )
        
        result = pd.concat(dfs, ignore_index=True)
        result["timestamp"] = pd.to_numeric(result["timestamp"], errors="coerce")
        result["price"] = pd.to_numeric(result["price"], errors="coerce")
        result = result.dropna(subset=["timestamp", "price"])
        result = result.sort_values("timestamp").reset_index(drop=True)
        return result

    def load_prices_for_markets(
        self,
        market_ids: List[str],
        outcome: str = "YES",
    ) -> pd.DataFrame:
        """
        Load prices for multiple markets at once.
        
        Optimized with Polars lazy scanning for better performance.

        Args:
            market_ids: List of market identifiers
            outcome: YES or NO

        Returns:
            DataFrame with price data for all specified markets
        """
        if not market_ids:
            return pd.DataFrame()
        
        market_ids_str = [str(m) for m in market_ids]
        
        # Use Polars lazy scanning if available
        if pl is not None:
            try:
                files = self._price_files()
                if not files:
                    return pd.DataFrame()
                
                # Lazy scan with predicate pushdown for multiple markets
                lf = pl.concat([
                    pl.scan_parquet(str(f))
                    .filter(pl.col("market_id").cast(pl.Utf8, strict=False).is_in(market_ids_str))
                    .filter(pl.col("outcome").cast(pl.Utf8, strict=False) == outcome.upper())
                    for f in files
                ])
                
                df = (
                    lf.select(["market_id", "outcome", "timestamp", "price"])
                    .sort("timestamp")
                    .collect()
                    .to_pandas()
                )
                
                # Ensure numeric types
                df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
                df["price"] = pd.to_numeric(df["price"], errors="coerce")
                df = df.dropna(subset=["timestamp", "price"])
                return df.sort_values("timestamp").reset_index(drop=True)
            except Exception:
                # Fallback to pandas
                pass
        
        # Fallback to pandas implementation
        dfs = []
        for f in self._price_files():
            try:
                df = pd.read_parquet(
                    str(f),
                    filters=[
                        ("market_id", "in", market_ids_str),
                        ("outcome", "==", outcome),
                    ],
                )
            except Exception:
                try:
                    df = pd.read_parquet(str(f))
                    df = df[
                        (df["market_id"].astype(str).isin(market_ids_str))
                        & (df["outcome"].astype(str).str.upper() == outcome.upper())
                    ]
                except Exception:
                    continue  # Skip corrupted or invalid parquet files
            if not df.empty:
                dfs.append(df)

        if not dfs:
            return pd.DataFrame()

        result = pd.concat(dfs, ignore_index=True)
        result["timestamp"] = pd.to_numeric(result["timestamp"], errors="coerce")
        result["price"] = pd.to_numeric(result["price"], errors="coerce")
        return result.sort_values("timestamp").reset_index(drop=True)

    def price_at_or_after(
        self,
        market_id: str,
        outcome: str,
        timestamp: int,
    ) -> Optional[dict]:
        """
        Get the price at or after a given timestamp.

        Args:
            market_id: Market identifier
            outcome: YES or NO
            timestamp: Unix timestamp (seconds)

        Returns:
            Dict with timestamp and price, or None
        """
        df = self.get_price_history(market_id, outcome)
        if df.empty:
            return None

        timestamps = df["timestamp"].to_numpy()
        idx = int(np.searchsorted(timestamps, timestamp, side="left"))
        if idx >= len(df):
            return None

        row = df.iloc[idx]
        return {
            "timestamp": int(row["timestamp"]),
            "price": float(row["price"]),
        }

    def last_price(
        self,
        market_id: str,
        outcome: str,
    ) -> Optional[dict]:
        """
        Get the last known price for a market/outcome.

        Args:
            market_id: Market identifier
            outcome: YES or NO

        Returns:
            Dict with timestamp and price, or None
        """
        df = self.get_price_history(market_id, outcome)
        if df.empty:
            return None
        row = df.iloc[-1]
        return {
            "timestamp": int(row["timestamp"]),
            "price": float(row["price"]),
        }

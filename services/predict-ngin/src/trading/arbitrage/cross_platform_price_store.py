"""
Cross-platform price store for arbitrage backtesting.

Provides unified price access across Polymarket and Kalshi, normalizing
timestamps and price formats for spread computation.

Both platforms store prices as 0.0-1.0 probabilities in our parquet files
(Kalshi raw API returns cents but the fetcher converts to 0-1).

Usage:
    from trading.arbitrage.cross_platform_price_store import CrossPlatformPriceStore

    store = CrossPlatformPriceStore(
        polymarket_prices_dir="data/polymarket/prices",
        kalshi_prices_dir="data/kalshi/prices",
    )
    spread = store.get_spread("poly_id", "KALSHI-TICKER", timestamp)
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..data_modules.parquet_store import PriceStore

logger = logging.getLogger(__name__)

# Any epoch at or below this is a corrupted timestamp (e.g. the timestamp == 1
# rows produced by the old HF price derivation).
MIN_VALID_TS = 1


def _to_epoch_seconds(ts: pd.Series) -> pd.Series:
    """datetime64 (any unit, tz-aware or naive) -> int64 unix seconds."""
    ts = pd.to_datetime(ts, utc=True)
    return ((ts - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).astype("int64")


def resample_last_no_lookahead(
    series: pd.Series, freq: str, ffill_limit: Optional[int] = 3
) -> pd.Series:
    """Resample to the last observation per bucket WITHOUT look-ahead.

    ``resample().last()`` defaults to ``label='left', closed='left'``: the close
    of day D is stamped at 00:00 of day D, i.e. before it happened. Here each
    bucket (t-freq, t] is labelled at its END ``t`` so the value at a label only
    uses observations at or before that label. Forward-fill is bounded so a
    dead market is not carried indefinitely.
    """
    if series.empty:
        return series
    out = series.sort_index().resample(freq, label="right", closed="right").last()
    if ffill_limit is None or ffill_limit > 0:
        out = out.ffill(limit=ffill_limit)
    return out


# ---------------------------------------------------------------------------
# Kalshi price loader (reads kalshi/prices/ parquet files)
# ---------------------------------------------------------------------------

class KalshiPriceStore:
    """
    Load Kalshi candlestick prices from monthly parquet files.

    Kalshi parquet schema (written by fetcher):
        ticker, event_ticker, timestamp (unix seconds),
        open, high, low, close, mean, volume, open_interest

    This adapts to the PriceStore interface used elsewhere.
    """

    def __init__(self, base_dir: str = "data/kalshi/prices", cache_size: int = 200):
        self.base_dir = Path(base_dir)
        self._cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self._cache_size = cache_size
        self._all_prices: Optional[pd.DataFrame] = None
        self.dropped_bad_timestamp = 0
        self.empty_close_rows = 0

    def available(self) -> bool:
        return self.base_dir.exists() and any(self.base_dir.glob("prices_*.parquet"))

    def _load_all(self) -> pd.DataFrame:
        """Lazy-load all Kalshi prices into memory (indexed by ticker)."""
        if self._all_prices is not None:
            return self._all_prices

        files = sorted(self.base_dir.glob("prices_*.parquet"))
        if not files:
            self._all_prices = pd.DataFrame()
            return self._all_prices

        dfs = []
        for f in files:
            try:
                df = pd.read_parquet(f)
                dfs.append(df)
            except Exception:
                continue

        if not dfs:
            self._all_prices = pd.DataFrame()
            return self._all_prices

        self._all_prices = pd.concat(dfs, ignore_index=True)

        # Normalize columns
        if "timestamp" in self._all_prices.columns:
            self._all_prices["timestamp"] = pd.to_numeric(
                self._all_prices["timestamp"], errors="coerce"
            )
        if "close" in self._all_prices.columns:
            self._all_prices["close"] = pd.to_numeric(
                self._all_prices["close"], errors="coerce"
            )

        self._all_prices = self._all_prices.dropna(subset=["timestamp"])

        # Refuse corrupted timestamps (ts <= 1 came from a pandas>=2 epoch bug in
        # the HF price derivation: ~88% of data/kalshi/prices rows).
        bad = self._all_prices["timestamp"] <= MIN_VALID_TS
        self.dropped_bad_timestamp = int(bad.sum())
        if self.dropped_bad_timestamp:
            logger.warning(
                "KalshiPriceStore: dropped %d/%d rows with invalid timestamp <= %d in %s "
                "(regenerate with scripts/data/repair_kalshi_trades.py --then-derive-prices)",
                self.dropped_bad_timestamp, len(self._all_prices), MIN_VALID_TS, self.base_dir,
            )
            self._all_prices = self._all_prices[~bad]
        if "close" in self._all_prices.columns:
            # Empty candles (no trades) carry NaN close; legacy files stored 0.
            self.empty_close_rows = int(self._all_prices["close"].isna().sum())

        self._all_prices = self._all_prices.sort_values(
            ["ticker", "timestamp"]
        ).reset_index(drop=True)
        return self._all_prices

    def get_price_history(self, ticker: str) -> pd.DataFrame:
        """
        Get OHLCV price history for a Kalshi market ticker.

        Returns DataFrame with columns:
            ticker, timestamp (unix seconds), open, high, low, close,
            mean, volume, open_interest
        """
        cache_key = ticker
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key].copy()

        all_prices = self._load_all()
        if all_prices.empty:
            result = pd.DataFrame()
        else:
            result = all_prices[all_prices["ticker"] == ticker].copy()
            result = result.sort_values("timestamp").reset_index(drop=True)

        if len(self._cache) >= self._cache_size:
            self._cache.popitem(last=False)
        self._cache[cache_key] = result
        return result.copy()

    def _valid(self, ticker: str) -> pd.DataFrame:
        df = self.get_price_history(ticker)
        if df.empty or "close" not in df.columns:
            return df
        return df[df["close"].notna()].reset_index(drop=True)

    def volume_at_or_before(self, ticker: str, timestamp: int) -> Optional[float]:
        """Volume (contracts) of the latest candle ending at or before ``timestamp``."""
        df = self.get_price_history(ticker)
        if df.empty or "volume" not in df.columns:
            return None
        idx = int(np.searchsorted(df["timestamp"].to_numpy(), timestamp, side="right")) - 1
        if idx < 0:
            return None
        return float(df.iloc[idx]["volume"])

    def price_at_or_before(
        self, ticker: str, timestamp: int
    ) -> Optional[Dict[str, Any]]:
        """Get the Kalshi close price at or before a timestamp."""
        df = self._valid(ticker)
        if df.empty:
            return None

        ts_array = df["timestamp"].to_numpy()
        idx = int(np.searchsorted(ts_array, timestamp, side="right")) - 1
        if idx < 0:
            return None

        row = df.iloc[idx]
        return {
            "timestamp": int(row["timestamp"]),
            "price": float(row["close"]),
        }

    def price_at_or_after(
        self, ticker: str, timestamp: int
    ) -> Optional[Dict[str, Any]]:
        """Get the Kalshi close price at or after a timestamp."""
        df = self._valid(ticker)
        if df.empty:
            return None

        ts_array = df["timestamp"].to_numpy()
        idx = int(np.searchsorted(ts_array, timestamp, side="left"))
        if idx >= len(df):
            return None

        row = df.iloc[idx]
        return {
            "timestamp": int(row["timestamp"]),
            "price": float(row["close"]),
        }

    def last_price(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get the last known Kalshi price."""
        df = self._valid(ticker)
        if df.empty:
            return None
        row = df.iloc[-1]
        return {
            "timestamp": int(row["timestamp"]),
            "price": float(row["close"]),
        }


# ---------------------------------------------------------------------------
# Cross-platform unified store
# ---------------------------------------------------------------------------

class CrossPlatformPriceStore:
    """
    Unified price store spanning Polymarket and Kalshi.

    Normalizes both platforms to a common interface:
    - Prices are 0.0-1.0 (probability)
    - Timestamps are unix seconds
    - Outcome is YES (we track YES price; NO = 1 - YES)

    For spread computation, both platforms must have a price at or near
    the requested timestamp. We use the nearest available price within
    a configurable tolerance window.
    """

    def __init__(
        self,
        polymarket_prices_dir: str = "data/polymarket/prices",
        kalshi_prices_dir: str = "data/kalshi/prices",
        max_timestamp_gap: int = 86400,  # 24h tolerance for price alignment
        cache_size: int = 200,
        max_ffill_periods: int = 3,
    ):
        """
        Args:
            polymarket_prices_dir: Path to Polymarket monthly price parquet files.
            kalshi_prices_dir: Path to Kalshi monthly price parquet files.
            max_timestamp_gap: Maximum seconds between prices to consider aligned.
            cache_size: LRU cache size for each store.
        """
        self.poly_store = PriceStore(polymarket_prices_dir, cache_size=cache_size)
        self.kalshi_store = KalshiPriceStore(kalshi_prices_dir, cache_size=cache_size)
        self.max_timestamp_gap = max_timestamp_gap
        self.max_ffill_periods = max_ffill_periods

    def available(self) -> Tuple[bool, bool]:
        """Check availability of each platform's data."""
        return self.poly_store.available(), self.kalshi_store.available()

    # -------------------------------------------------------------------
    # Per-platform access
    # -------------------------------------------------------------------

    def get_polymarket_price(
        self, market_id: str, timestamp: int, outcome: str = "YES"
    ) -> Optional[Dict[str, Any]]:
        """Latest Polymarket price at or before ``timestamp`` (as-of; no look-ahead).

        Returns None if the latest price is older than ``max_timestamp_gap``.
        """
        hist = self.get_polymarket_history(market_id, outcome)
        if hist is None or hist.empty:
            return None
        ts = hist["timestamp"]
        if pd.api.types.is_datetime64_any_dtype(ts):
            ts = _to_epoch_seconds(ts)
        ts = ts.to_numpy()
        order = np.argsort(ts, kind="mergesort")
        ts = ts[order]
        idx = int(np.searchsorted(ts, timestamp, side="right")) - 1
        if idx < 0 or timestamp - ts[idx] > self.max_timestamp_gap:
            return None
        return {"timestamp": int(ts[idx]), "price": float(hist["price"].to_numpy()[order][idx])}

    def get_kalshi_price(
        self, ticker: str, timestamp: int
    ) -> Optional[Dict[str, Any]]:
        """Latest Kalshi YES close at or before ``timestamp`` (as-of; no look-ahead)."""
        p = self.kalshi_store.price_at_or_before(ticker, timestamp)
        if p is None or timestamp - p["timestamp"] > self.max_timestamp_gap:
            return None
        return p

    def get_kalshi_last_price(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Last valid Kalshi close (for marking open positions)."""
        return self.kalshi_store.last_price(ticker)

    def get_polymarket_last_price(self, market_id: str, outcome: str = "YES") -> Optional[Dict[str, Any]]:
        """Last Polymarket price (for marking open positions)."""
        return self._poly_last(market_id, outcome)

    def _poly_last(self, market_id: str, outcome: str) -> Optional[Dict[str, Any]]:
        hist = self.get_polymarket_history(market_id, outcome)
        if hist is None or hist.empty:
            return None
        ts = _to_epoch_seconds(hist["timestamp"]) if pd.api.types.is_datetime64_any_dtype(hist["timestamp"])             else hist["timestamp"].astype("int64")
        i = int(np.argmax(ts.to_numpy()))
        return {"timestamp": int(ts.iloc[i]), "price": float(hist["price"].iloc[i])}

    def get_kalshi_volume(self, ticker: str, timestamp: int) -> Optional[float]:
        """Contracts traded in the latest Kalshi candle ending at or before ``timestamp``."""
        return self.kalshi_store.volume_at_or_before(ticker, timestamp)

    def get_polymarket_history(
        self, market_id: str, outcome: str = "YES"
    ) -> pd.DataFrame:
        """Full Polymarket price history for a market."""
        return self.poly_store.get_price_history(market_id, outcome)

    def get_kalshi_history(self, ticker: str) -> pd.DataFrame:
        """Full Kalshi OHLCV history for a market."""
        return self.kalshi_store.get_price_history(ticker)

    # -------------------------------------------------------------------
    # Cross-platform spread
    # -------------------------------------------------------------------

    def get_spread(
        self,
        poly_id: str,
        kalshi_ticker: str,
        timestamp: int,
        outcome: str = "YES",
    ) -> Optional[Dict[str, Any]]:
        """
        Compute the price spread at a given timestamp.

        spread = polymarket_yes_price - kalshi_yes_price

        Positive spread means Polymarket is more expensive (buy Kalshi, sell Poly).
        Negative spread means Kalshi is more expensive (buy Poly, sell Kalshi).

        Returns:
            Dict with poly_price, kalshi_price, spread, abs_spread, timestamp,
            or None if prices unavailable within tolerance.
        """
        poly_point = self.get_polymarket_price(poly_id, timestamp, outcome)
        kalshi_point = self.get_kalshi_price(kalshi_ticker, timestamp)

        if poly_point is None or kalshi_point is None:
            return None

        # Check time alignment
        gap = abs(poly_point["timestamp"] - kalshi_point["timestamp"])
        if gap > self.max_timestamp_gap:
            return None

        poly_price = float(poly_point["price"])
        kalshi_price = float(kalshi_point["price"])
        spread = poly_price - kalshi_price

        return {
            "poly_price": poly_price,
            "kalshi_price": kalshi_price,
            "spread": spread,
            "abs_spread": abs(spread),
            "poly_timestamp": poly_point["timestamp"],
            "kalshi_timestamp": kalshi_point["timestamp"],
            "timestamp": int(timestamp),
            "gap_seconds": gap,
        }

    def build_spread_series(
        self,
        poly_id: str,
        kalshi_ticker: str,
        outcome: str = "YES",
        resample_freq: str = "1D",
    ) -> pd.DataFrame:
        """
        Build a time-aligned spread series for a matched pair.

        Resamples both price series to a common frequency and computes
        the spread at each point.

        Args:
            poly_id: Polymarket market ID
            kalshi_ticker: Kalshi market ticker
            outcome: YES or NO
            resample_freq: Pandas frequency string (default: daily)

        Returns:
            DataFrame with columns: timestamp, poly_price, kalshi_price,
            spread, abs_spread
        """
        poly_hist = self.get_polymarket_history(poly_id, outcome)
        kalshi_hist = self.get_kalshi_history(kalshi_ticker)

        if poly_hist.empty or kalshi_hist.empty:
            return pd.DataFrame()

        # Convert Polymarket timestamps to datetime
        if not pd.api.types.is_datetime64_any_dtype(poly_hist["timestamp"]):
            poly_hist["datetime"] = pd.to_datetime(
                poly_hist["timestamp"], unit="s", errors="coerce", utc=True
            )
        else:
            poly_hist["datetime"] = poly_hist["timestamp"]

        # Convert Kalshi timestamps
        kalshi_hist["datetime"] = pd.to_datetime(
            kalshi_hist["timestamp"], unit="s", errors="coerce", utc=True
        )

        # Resample to common frequency. Buckets are labelled at their END so the
        # value at time t only uses data observed at or before t; ffill is bounded.
        poly_resampled = resample_last_no_lookahead(
            poly_hist.set_index("datetime")["price"], resample_freq, self.max_ffill_periods
        )
        kalshi_resampled = resample_last_no_lookahead(
            kalshi_hist.dropna(subset=["close"]).set_index("datetime")["close"],
            resample_freq,
            self.max_ffill_periods,
        )

        # Align on overlapping dates
        aligned = pd.DataFrame({
            "poly_price": poly_resampled,
            "kalshi_price": kalshi_resampled,
        }).dropna()

        if aligned.empty:
            return pd.DataFrame()

        aligned["spread"] = aligned["poly_price"] - aligned["kalshi_price"]
        aligned["abs_spread"] = aligned["spread"].abs()
        aligned = aligned.reset_index().rename(columns={"datetime": "timestamp"})
        # NOTE: astype("int64") // 10**9 is wrong for datetime64[s] (pandas>=2) -> 1
        aligned["timestamp_unix"] = _to_epoch_seconds(aligned["timestamp"])

        return aligned

    def get_all_spreads_at(
        self,
        pairs: List[Tuple[str, str]],
        timestamp: int,
        outcome: str = "YES",
    ) -> pd.DataFrame:
        """
        Get spreads for multiple pairs at a single timestamp.

        Args:
            pairs: List of (polymarket_id, kalshi_ticker) tuples
            timestamp: Unix timestamp
            outcome: YES or NO

        Returns:
            DataFrame with one row per pair.
        """
        rows = []
        for poly_id, kalshi_ticker in pairs:
            spread_data = self.get_spread(poly_id, kalshi_ticker, timestamp, outcome)
            if spread_data is not None:
                rows.append({
                    "polymarket_id": poly_id,
                    "kalshi_ticker": kalshi_ticker,
                    **spread_data,
                })

        return pd.DataFrame(rows) if rows else pd.DataFrame()

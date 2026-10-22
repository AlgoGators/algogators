"""
Parquet-based storage for liquidity snapshots.

Follows the TradeStore/PriceStore pattern with monthly partitioning.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import json
import logging

import pandas as pd

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = None
    pq = None

from .base import BaseStore

if TYPE_CHECKING:
    from src.collection.liquidity import LiquiditySnapshot


DEFAULT_LIQUIDITY_DIR = "data/polymarket/liquidity"


class LiquidityStore(BaseStore["LiquiditySnapshot"]):
    """
    Store for liquidity snapshots with monthly partitioning.

    Files are stored as: liquidity_YYYY-MM.parquet
    """

    def __init__(
        self,
        base_dir: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        super().__init__(
            base_dir=base_dir or DEFAULT_LIQUIDITY_DIR,
            name="liquidity",
            logger=logger,
        )

    def available(self) -> bool:
        """Check if parquet liquidity files exist."""
        return self.base_dir.exists() and any(
            self.base_dir.glob("liquidity_*.parquet")
        )

    def list_months(self) -> List[str]:
        """List available months (YYYY-MM)."""
        files = sorted(self.base_dir.glob("liquidity_*.parquet"))
        return [f.stem.replace("liquidity_", "") for f in files]

    def append(
        self,
        data: List["LiquiditySnapshot"],
        **kwargs,
    ) -> int:
        """
        Append liquidity snapshots to the store.

        Snapshots are partitioned by month.

        Args:
            data: List of LiquiditySnapshot objects

        Returns:
            Number of records stored
        """
        if not data:
            return 0

        if pa is None or pq is None:
            raise RuntimeError("pyarrow required for parquet storage")

        self.ensure_dir()

        # Group by month
        by_month: Dict[str, List[Dict[str, Any]]] = {}
        for snapshot in data:
            month = snapshot.timestamp.strftime("%Y-%m")
            if month not in by_month:
                by_month[month] = []
            by_month[month].append(snapshot.to_dict())

        total_stored = 0

        for month, records in by_month.items():
            filepath = self.base_dir / f"liquidity_{month}.parquet"

            # Convert to DataFrame
            df = pd.DataFrame(records)

            if filepath.exists():
                # Append to existing file
                existing = pd.read_parquet(filepath)
                df = pd.concat([existing, df], ignore_index=True)

                # Deduplicate by market_id + token_id + timestamp
                df = df.drop_duplicates(
                    subset=["market_id", "token_id", "timestamp"],
                    keep="last",
                )

            # Write with Zstd compression for better ratio (optimized for 100GB+ datasets)
            table = pa.Table.from_pandas(df, preserve_index=False)
            pq.write_table(
                table,
                filepath,
                compression="zstd",
                compression_level=3,
                row_group_size=500_000,  # Optimize row groups for predicate pushdown
            )

            total_stored += len(records)

        return total_stored

    def load(
        self,
        market_id: Optional[str] = None,
        token_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Load liquidity snapshots from the store.

        Args:
            market_id: Filter by market ID
            token_id: Filter by token ID
            start_date: Filter records after this date (YYYY-MM-DD)
            end_date: Filter records before this date (YYYY-MM-DD)
            limit: Maximum records to return

        Returns:
            DataFrame with liquidity data
        """
        files = self._select_files(start_date, end_date)
        if not files:
            return pd.DataFrame()

        dfs = []
        total_rows = 0

        for f in files:
            try:
                df = pd.read_parquet(f)
            except Exception as e:
                self.logger.warning(f"Error reading {f}: {e}")
                continue

            # Apply filters
            if market_id and "market_id" in df.columns:
                df = df[df["market_id"] == market_id]

            if token_id and "token_id" in df.columns:
                df = df[df["token_id"] == token_id]

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

    def _select_files(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Path]:
        """Select parquet files based on date range."""
        files = sorted(self.base_dir.glob("liquidity_*.parquet"))
        if not files:
            return []

        if start_date:
            start_month = start_date[:7]  # YYYY-MM
            files = [
                f for f in files
                if f.stem.replace("liquidity_", "") >= start_month
            ]

        if end_date:
            end_month = end_date[:7]
            files = [
                f for f in files
                if f.stem.replace("liquidity_", "") <= end_month
            ]

        return files

    def get_depth_at(
        self,
        market_id: str,
        timestamp: datetime,
        token_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the depth snapshot closest to a given timestamp.

        Args:
            market_id: Market identifier
            timestamp: Target timestamp
            token_id: Optional token filter

        Returns:
            Dict with snapshot data or None
        """
        # Load relevant month
        month = timestamp.strftime("%Y-%m")
        filepath = self.base_dir / f"liquidity_{month}.parquet"

        if not filepath.exists():
            return None

        try:
            df = pd.read_parquet(filepath)
        except Exception:
            return None

        # Filter to market
        df = df[df["market_id"] == market_id]
        if token_id:
            df = df[df["token_id"] == token_id]

        if df.empty:
            return None

        # Convert timestamp column to datetime if needed
        if df["timestamp"].dtype == object:
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Find closest timestamp
        target_ts = pd.Timestamp(timestamp)
        df["time_diff"] = (df["timestamp"] - target_ts).abs()
        closest = df.loc[df["time_diff"].idxmin()]

        return closest.to_dict()

    def get_latest(
        self,
        market_id: str,
        token_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the most recent snapshot for a market.

        Args:
            market_id: Market identifier
            token_id: Optional token filter

        Returns:
            Dict with snapshot data or None
        """
        # Get the latest month
        months = self.list_months()
        if not months:
            return None

        latest_month = months[-1]
        filepath = self.base_dir / f"liquidity_{latest_month}.parquet"

        try:
            df = pd.read_parquet(filepath)
        except Exception:
            return None

        df = df[df["market_id"] == market_id]
        if token_id:
            df = df[df["token_id"] == token_id]

        if df.empty:
            return None

        # Get last row
        if "timestamp" in df.columns:
            df = df.sort_values("timestamp")

        return df.iloc[-1].to_dict()

    def compute_liquidity_stats(
        self,
        market_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Compute aggregate liquidity statistics.

        Args:
            market_id: Filter by market
            start_date: Start date filter
            end_date: End date filter

        Returns:
            DataFrame with liquidity statistics per market
        """
        df = self.load(
            market_id=market_id,
            start_date=start_date,
            end_date=end_date,
        )

        if df.empty:
            return pd.DataFrame()

        # Aggregate by market
        stats = df.groupby("market_id").agg({
            "mid_price": ["mean", "std", "min", "max"],
            "spread": ["mean", "std", "max"],
            "depth_1pct": ["mean", "min"],
            "depth_5pct": ["mean", "min"],
            "total_bid_depth": "mean",
            "total_ask_depth": "mean",
        })

        # Flatten column names
        stats.columns = ["_".join(col).strip() for col in stats.columns.values]
        stats = stats.reset_index()

        return stats


__all__ = ["LiquidityStore"]

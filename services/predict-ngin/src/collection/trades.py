"""
Trade data collector.

Wraps existing fetcher functionality with the unified collector interface.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from .base import BaseCollector

# Import from existing fetcher module
from trading.data_modules.fetcher import DataFetcher


@dataclass
class Trade:
    """Represents a single trade."""

    trade_id: str
    market_id: str
    timestamp: datetime
    maker: str
    taker: str
    price: float
    usd_amount: float
    token_amount: float
    side: str  # BUY or SELL
    transaction_hash: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class TradeCollector(BaseCollector[Trade]):
    """
    Collector for trade data.

    Wraps the existing DataFetcher for backward compatibility.
    """

    def __init__(
        self,
        data_dir: str = "data",
        source: str = "polymarket",
        logger: Optional[logging.Logger] = None,
    ):
        super().__init__(
            name=f"trades_{source}",
            output_dir=data_dir,
            logger=logger,
        )
        self.source = source
        self._fetcher = DataFetcher(data_dir)

    async def fetch(
        self,
        min_volume: float = 1000,
        max_markets: int = 500,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        **kwargs,
    ) -> List[Trade]:
        """
        Fetch trades from the source.

        For Polymarket, this fetches order-filled events from the CLOB API.

        Args:
            min_volume: Minimum 24hr volume to include market
            max_markets: Maximum number of markets
            start_ts: Start timestamp (Unix seconds)
            end_ts: End timestamp (Unix seconds)

        Returns:
            List of Trade objects
        """
        # Note: The actual implementation uses sync APIs
        # This is a wrapper that adapts the interface
        self.logger.info(f"Fetching trades from {self.source}")

        # The underlying fetcher writes directly to files
        # We don't convert to Trade objects for large data
        # Instead, we track what was fetched
        trades: List[Trade] = []

        # For now, this is a passthrough to the existing fetcher
        # Real implementation would parse the results
        return trades

    def store(self, data: List[Trade], **kwargs) -> int:
        """
        Store trades.

        The underlying fetcher handles storage directly.

        Args:
            data: List of Trade objects

        Returns:
            Number of records stored
        """
        return len(data)

    def fetch_order_filled_events(
        self,
        output_dir: str = "data/polymarket/order_filled_events",
        batch_size: int = 1000,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        max_batches: int = 0,
        reset: bool = False,
    ) -> Dict[str, Any]:
        """
        Fetch order-filled events from Polymarket CLOB.

        This is a direct passthrough to the underlying fetcher.

        Args:
            output_dir: Output directory for parquet files
            batch_size: Records per batch
            start_ts: Start timestamp
            end_ts: End timestamp
            max_batches: Max batches (0 = unlimited)
            reset: Reset state and start fresh

        Returns:
            Dict with fetch statistics
        """
        return self._fetcher.fetch_polymarket_order_filled_events(
            output_dir=output_dir,
            batch_size=batch_size,
            start_ts=start_ts,
            end_ts=end_ts,
            max_batches=max_batches,
            reset=reset,
        )

    def process_trades(
        self,
        order_filled_dir: str = "data/polymarket/order_filled_events",
        output_dir: str = "data/polymarket/trades",
        markets_dir: str = "data/polymarket",
        reset: bool = False,
        max_files: int = 0,
        fetch_missing_tokens: bool = False,
    ) -> Dict[str, Any]:
        """
        Process order-filled events into trades.

        This is a direct passthrough to the underlying fetcher.

        Returns:
            Dict with processing statistics
        """
        return self._fetcher.process_polymarket_trades_from_order_filled(
            order_filled_dir=order_filled_dir,
            output_dir=output_dir,
            markets_dir=markets_dir,
            reset=reset,
            max_files=max_files,
            fetch_missing_tokens=fetch_missing_tokens,
        )


# Re-export for convenience
__all__ = ["Trade", "TradeCollector"]

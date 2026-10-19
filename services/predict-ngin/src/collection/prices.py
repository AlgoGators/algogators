"""
Price data collector.

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
class PricePoint:
    """Represents a single price/probability observation."""

    market_id: str
    token_id: str
    timestamp: datetime
    price: float
    outcome: str  # YES or NO
    metadata: Optional[Dict[str, Any]] = None


class PriceCollector(BaseCollector[PricePoint]):
    """
    Collector for historical price data.

    Wraps the existing DataFetcher for backward compatibility.
    """

    def __init__(
        self,
        data_dir: str = "data",
        source: str = "polymarket",
        logger: Optional[logging.Logger] = None,
    ):
        super().__init__(
            name=f"prices_{source}",
            output_dir=data_dir,
            logger=logger,
        )
        self.source = source
        self._fetcher = DataFetcher(data_dir)

    async def fetch(
        self,
        token_id: Optional[str] = None,
        market_ids: Optional[List[str]] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        max_days: int = 365,
        **kwargs,
    ) -> List[PricePoint]:
        """
        Fetch price history from the source.

        Args:
            token_id: Single token ID to fetch
            market_ids: List of market IDs to fetch
            start_ts: Start timestamp (Unix seconds)
            end_ts: End timestamp (Unix seconds)
            max_days: Maximum days of history

        Returns:
            List of PricePoint objects
        """
        self.logger.info(f"Fetching prices from {self.source}")

        prices: List[PricePoint] = []

        if token_id:
            # Fetch single token history
            history = self._fetcher.fetch_polymarket_price_history(
                token_id=token_id,
                start_ts=start_ts,
                end_ts=end_ts,
                max_days=max_days,
            )

            for point in history:
                prices.append(
                    PricePoint(
                        market_id="",  # Not available from this endpoint
                        token_id=token_id,
                        timestamp=datetime.utcfromtimestamp(point.get("t", 0)),
                        price=point.get("p", 0),
                        outcome="YES",  # Assumed for token1
                    )
                )

        return prices

    def store(self, data: List[PricePoint], **kwargs) -> int:
        """
        Store price data.

        Args:
            data: List of PricePoint objects

        Returns:
            Number of records stored
        """
        # The underlying fetcher handles storage
        return len(data)

    def fetch_clob_data(
        self,
        min_volume: float = 10000,
        max_markets: int = 1000,
        max_days: int = 90,
        workers: int = 20,
        include_closed: bool = False,
    ) -> int:
        """
        Fetch CLOB price history for multiple markets.

        This is a direct passthrough to the underlying fetcher.

        Args:
            min_volume: Minimum 24hr volume
            max_markets: Maximum markets to fetch
            max_days: Days of history per market
            workers: Concurrent workers
            include_closed: Include closed markets

        Returns:
            Number of markets fetched
        """
        return self._fetcher.fetch_polymarket_clob_data(
            min_volume=min_volume,
            max_markets=max_markets,
            max_days=max_days,
            workers=workers,
            include_closed=include_closed,
        )


# Re-export for convenience
__all__ = ["PricePoint", "PriceCollector"]

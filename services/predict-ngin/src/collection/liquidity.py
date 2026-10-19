"""
Liquidity snapshot collector for orderbook data.

Captures orderbook depth at regular intervals for liquidity analysis.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import asyncio
import json
import logging
import time

import requests

from .base import BaseCollector

# Polymarket CLOB API
POLYMARKET_CLOB_API = "https://clob.polymarket.com"


@dataclass
class LiquiditySnapshot:
    """
    Represents an orderbook snapshot at a point in time.

    Captures bid/ask depth and derived metrics for liquidity analysis.
    """

    market_id: str
    token_id: str
    timestamp: datetime
    bid_depth: Dict[float, float]  # price -> size in USD
    ask_depth: Dict[float, float]  # price -> size in USD
    mid_price: float
    spread: float
    depth_1pct: float  # Liquidity within 1% of mid
    depth_5pct: float  # Liquidity within 5% of mid
    best_bid: float = 0.0
    best_ask: float = 1.0
    total_bid_depth: float = 0.0
    total_ask_depth: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_orderbook(
        cls,
        market_id: str,
        token_id: str,
        bids: List[Tuple[float, float]],  # (price, size) pairs
        asks: List[Tuple[float, float]],
        timestamp: Optional[datetime] = None,
    ) -> "LiquiditySnapshot":
        """
        Create a LiquiditySnapshot from raw orderbook data.

        Args:
            market_id: Market identifier
            token_id: Token identifier
            bids: List of (price, size) tuples for bids
            asks: List of (price, size) tuples for asks
            timestamp: Snapshot timestamp (default: now)

        Returns:
            LiquiditySnapshot with computed metrics
        """
        timestamp = timestamp or datetime.utcnow()

        # Convert to dicts
        bid_depth = {float(p): float(s) for p, s in bids}
        ask_depth = {float(p): float(s) for p, s in asks}

        # Compute metrics
        best_bid = max(bid_depth.keys()) if bid_depth else 0.0
        best_ask = min(ask_depth.keys()) if ask_depth else 1.0

        # Spread and mid only valid when both sides have orders
        has_both_sides = bool(bid_depth and ask_depth)
        mid_price = (best_bid + best_ask) / 2 if has_both_sides else 0.5
        spread = (best_ask - best_bid) if has_both_sides else 0.0

        total_bid = sum(bid_depth.values())
        total_ask = sum(ask_depth.values())

        # Depth within 1% and 5% of mid
        depth_1pct = cls._compute_depth_within_pct(
            bid_depth, ask_depth, mid_price, 0.01
        )
        depth_5pct = cls._compute_depth_within_pct(
            bid_depth, ask_depth, mid_price, 0.05
        )

        return cls(
            market_id=market_id,
            token_id=token_id,
            timestamp=timestamp,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            mid_price=mid_price,
            spread=spread,
            depth_1pct=depth_1pct,
            depth_5pct=depth_5pct,
            best_bid=best_bid,
            best_ask=best_ask,
            total_bid_depth=total_bid,
            total_ask_depth=total_ask,
        )

    @staticmethod
    def _compute_depth_within_pct(
        bid_depth: Dict[float, float],
        ask_depth: Dict[float, float],
        mid_price: float,
        pct: float,
    ) -> float:
        """Compute total depth within pct of mid price."""
        lower = mid_price * (1 - pct)
        upper = mid_price * (1 + pct)

        bid_sum = sum(s for p, s in bid_depth.items() if p >= lower)
        ask_sum = sum(s for p, s in ask_depth.items() if p <= upper)

        return bid_sum + ask_sum

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "market_id": self.market_id,
            "token_id": self.token_id,
            "timestamp": self.timestamp.isoformat(),
            "mid_price": self.mid_price,
            "spread": self.spread,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "depth_1pct": self.depth_1pct,
            "depth_5pct": self.depth_5pct,
            "total_bid_depth": self.total_bid_depth,
            "total_ask_depth": self.total_ask_depth,
            "bid_depth": json.dumps(self.bid_depth),
            "ask_depth": json.dumps(self.ask_depth),
        }


class LiquidityCollector(BaseCollector[LiquiditySnapshot]):
    """
    Collector for orderbook liquidity snapshots.

    Captures orderbook state at regular intervals for analysis.
    """

    def __init__(
        self,
        output_dir: str = "data/liquidity",
        api_base: str = POLYMARKET_CLOB_API,
        logger: Optional[logging.Logger] = None,
    ):
        super().__init__(
            name="liquidity",
            output_dir=output_dir,
            logger=logger,
        )
        self.api_base = api_base
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "PredictionMarketResearch/1.0"
        })

    async def fetch(
        self,
        market_id: Optional[str] = None,
        token_id: Optional[str] = None,
        **kwargs,
    ) -> List[LiquiditySnapshot]:
        """
        Fetch orderbook snapshot for a market.

        Args:
            market_id: Market identifier
            token_id: Token identifier (used for API call)

        Returns:
            List containing single LiquiditySnapshot
        """
        if not token_id:
            return []

        snapshot = await self._fetch_orderbook(market_id or "", token_id)
        return [snapshot] if snapshot else []

    async def _fetch_orderbook(
        self,
        market_id: str,
        token_id: str,
    ) -> Optional[LiquiditySnapshot]:
        """
        Fetch orderbook from CLOB API.

        Args:
            market_id: Market identifier
            token_id: Token identifier

        Returns:
            LiquiditySnapshot or None on error
        """
        try:
            response = self._session.get(
                f"{self.api_base}/book",
                params={"token_id": token_id},
                timeout=30,
            )

            if response.status_code != 200:
                self.logger.warning(
                    f"Failed to fetch orderbook for {token_id}: {response.status_code}"
                )
                return None

            data = response.json()

            # Parse bids and asks
            bids = [
                (float(level.get("price", 0)), float(level.get("size", 0)))
                for level in data.get("bids", [])
            ]
            asks = [
                (float(level.get("price", 0)), float(level.get("size", 0)))
                for level in data.get("asks", [])
            ]

            return LiquiditySnapshot.from_orderbook(
                market_id=market_id,
                token_id=token_id,
                bids=bids,
                asks=asks,
            )

        except Exception as e:
            self.logger.error(f"Error fetching orderbook for {token_id}: {e}")
            return None

    async def capture_snapshot(
        self,
        market_id: str,
        token_id: str,
    ) -> Optional[LiquiditySnapshot]:
        """
        Capture a single orderbook snapshot.

        Args:
            market_id: Market identifier
            token_id: Token identifier

        Returns:
            LiquiditySnapshot or None on error
        """
        return await self._fetch_orderbook(market_id, token_id)

    async def capture_all_active(
        self,
        markets: List[Dict[str, Any]],
        min_volume: float = 1000,
        max_markets: int = 500,
    ) -> List[LiquiditySnapshot]:
        """
        Capture snapshots for all active markets.

        Args:
            markets: List of market dicts with token_id and volume info
            min_volume: Minimum 24hr volume to include
            max_markets: Maximum markets to capture

        Returns:
            List of LiquiditySnapshot objects
        """
        # Filter and sort by volume
        filtered = [
            m for m in markets
            if float(m.get("volume24hr", 0) or 0) >= min_volume
        ]
        filtered.sort(key=lambda x: float(x.get("volume24hr", 0) or 0), reverse=True)
        filtered = filtered[:max_markets]

        snapshots: List[LiquiditySnapshot] = []

        for market in filtered:
            market_id = str(market.get("id", ""))
            token_ids = market.get("clobTokenIds", [])

            if isinstance(token_ids, str):
                try:
                    token_ids = json.loads(token_ids)
                except json.JSONDecodeError:
                    token_ids = []

            for token_id in token_ids[:2]:  # YES and NO tokens
                snapshot = await self._fetch_orderbook(market_id, str(token_id))
                if snapshot:
                    snapshots.append(snapshot)

                # Rate limiting
                await asyncio.sleep(0.1)

        return snapshots

    def run_capture_loop(
        self,
        markets: List[Dict[str, Any]],
        interval_minutes: int = 5,
        min_volume: float = 1000,
        max_markets: int = 500,
        max_iterations: int = 0,
        on_snapshot: Optional[callable] = None,
    ) -> None:
        """
        Run continuous capture loop.

        Args:
            markets: List of market dicts
            interval_minutes: Capture interval
            min_volume: Minimum volume filter
            max_markets: Max markets per capture
            max_iterations: Max iterations (0 = infinite)
            on_snapshot: Callback for each snapshot batch
        """
        iteration = 0

        while max_iterations == 0 or iteration < max_iterations:
            try:
                self.logger.info(f"Starting capture iteration {iteration + 1}")
                start = time.time()

                # Run async capture
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    snapshots = loop.run_until_complete(
                        self.capture_all_active(
                            markets=markets,
                            min_volume=min_volume,
                            max_markets=max_markets,
                        )
                    )
                finally:
                    loop.close()

                if snapshots:
                    stored = self.store(snapshots)
                    self.logger.info(f"Captured {len(snapshots)} snapshots, stored {stored}")

                    if on_snapshot:
                        on_snapshot(snapshots)

                elapsed = time.time() - start
                sleep_time = max(0, interval_minutes * 60 - elapsed)

                if sleep_time > 0:
                    self.logger.info(f"Sleeping {sleep_time:.0f}s until next capture")
                    time.sleep(sleep_time)

            except KeyboardInterrupt:
                self.logger.info("Capture loop interrupted")
                break
            except Exception as e:
                self.logger.error(f"Capture loop error: {e}")
                time.sleep(60)  # Wait before retry

            iteration += 1

    def store(self, data: List[LiquiditySnapshot], **kwargs) -> int:
        """
        Store liquidity snapshots.

        Uses the LiquidityStore for parquet storage.

        Args:
            data: List of LiquiditySnapshot objects

        Returns:
            Number of records stored
        """
        if not data:
            return 0

        try:
            from src.storage.liquidity import LiquidityStore
            store = LiquidityStore(str(self.output_dir))
            return store.append(data)
        except ImportError:
            # Fallback to JSON if storage module not available
            self.ensure_output_dir()
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filepath = self.output_dir / f"liquidity_{timestamp}.json"

            with open(filepath, "w") as f:
                json.dump([s.to_dict() for s in data], f)

            return len(data)


__all__ = ["LiquiditySnapshot", "LiquidityCollector"]

"""
Whale following strategy implementation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Any, Set

from .base import BaseStrategy, StrategyConfig, Signal, SignalType


class WhaleFollowingStrategy(BaseStrategy):
    """
    Follow trades from identified whale traders.

    Generates BUY signals when whales buy, SELL signals when whales sell.
    """

    def __init__(
        self,
        whale_addresses: Set[str],
        config: Optional[StrategyConfig] = None,
    ):
        super().__init__(config or StrategyConfig(
            name="whale_following",
            parameters={"min_whale_trade_size": 100},
        ))
        self.whale_addresses = whale_addresses

    def generate_signals(
        self,
        market_data: Dict[str, Any],
        timestamp: datetime,
    ) -> List[Signal]:
        signals: List[Signal] = []

        # Check for whale trades in recent data
        trades = market_data.get("recent_trades", [])

        for trade in trades:
            trader = trade.get("maker") or trade.get("taker")
            if trader not in self.whale_addresses:
                continue

            market_id = trade.get("market_id")
            if not market_id:
                continue

            # Skip if we already have a position
            if self.has_position(market_id):
                continue

            direction = trade.get("direction", "buy").upper()
            size = trade.get("usd_amount", self.config.position_size)

            # Check minimum size
            min_size = self.config.parameters.get("min_whale_trade_size", 100)
            if size < min_size:
                continue

            signal = Signal(
                strategy_name=self.name,
                market_id=market_id,
                signal_type=SignalType.BUY if direction == "BUY" else SignalType.SELL,
                timestamp=timestamp,
                outcome=trade.get("outcome", "YES"),
                size=min(size, self.config.position_size),
                category=trade.get("category", "unknown"),
                liquidity=trade.get("liquidity", 0),
                volume_24h=trade.get("volume_24h", 0),
                confidence=0.7,  # Moderate confidence
                reason=f"Whale {trader[:10]}... traded",
                features={
                    "whale_address": trader,
                    "whale_trade_size": size,
                    "price_at_signal": trade.get("price", 0),
                },
            )
            signals.append(signal)

        return signals


__all__ = ["WhaleFollowingStrategy"]

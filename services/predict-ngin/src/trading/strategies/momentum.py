"""
Momentum strategy for prediction markets.

Generates signals when 24h returns exceed a threshold.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Any, List, Optional

from .base import BaseStrategy, StrategyConfig, Signal, SignalType


class MomentumStrategy(BaseStrategy):
    """Simple price momentum strategy."""

    def __init__(
        self,
        config: Optional[StrategyConfig] = None,
        threshold: float = 0.05,
    ):
        parameters = {"threshold": threshold}
        super().__init__(config or StrategyConfig(name="momentum", parameters=parameters))

    def generate_signals(
        self,
        market_data: Dict[str, Any],
        timestamp: datetime,
    ) -> List[Signal]:
        signals: List[Signal] = []
        markets = market_data.get("markets", [])

        threshold = float(self.config.parameters.get("threshold", 0.05))

        for market in markets:
            market_id = market.get("id")
            if not market_id:
                continue

            ret_24h = market.get("return_24h")
            if ret_24h is None:
                continue

            if abs(ret_24h) < threshold:
                continue

            signal_type = SignalType.BUY if ret_24h > 0 else SignalType.SELL
            confidence = min(abs(ret_24h) / max(threshold, 1e-6), 1.0)

            signals.append(
                Signal(
                    strategy_name=self.name,
                    market_id=market_id,
                    signal_type=signal_type,
                    timestamp=timestamp,
                    price=market.get("price"),
                    category=market.get("category", "unknown"),
                    liquidity=float(market.get("liquidity", 0) or 0),
                    volume_24h=float(market.get("volume_24h", market.get("volume24hr", 0)) or 0),
                    confidence=confidence,
                    reason="momentum",
                    features={"return_24h": ret_24h},
                )
            )

        return signals


__all__ = ["MomentumStrategy"]

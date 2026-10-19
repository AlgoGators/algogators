"""
Mean reversion strategy for prediction markets.

Looks for sharp 1h moves that stabilize over 6h and bets on reversion.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Any, List, Optional

from .base import BaseStrategy, StrategyConfig, Signal, SignalType


class MeanReversionStrategy(BaseStrategy):
    """
    Mean reversion strategy.

    Generates BUY after sharp dips and SELL after sharp spikes,
    provided the 6h move is relatively stable.
    """

    def __init__(
        self,
        config: Optional[StrategyConfig] = None,
        shock_threshold: float = 0.08,
        stabilize_threshold: float = 0.02,
        min_liquidity: float = 0.0,
        min_volume_24h: float = 0.0,
        max_volatility: float = 1.0,
    ):
        parameters = {
            "shock_threshold": shock_threshold,
            "stabilize_threshold": stabilize_threshold,
            "min_liquidity": min_liquidity,
            "min_volume_24h": min_volume_24h,
            "max_volatility": max_volatility,
        }
        super().__init__(config or StrategyConfig(name="mean_reversion", parameters=parameters))

    def generate_signals(
        self,
        market_data: Dict[str, Any],
        timestamp: datetime,
    ) -> List[Signal]:
        signals: List[Signal] = []
        markets = market_data.get("markets", [])

        params = self.config.parameters
        shock_threshold = float(params.get("shock_threshold", 0.08))
        stabilize_threshold = float(params.get("stabilize_threshold", 0.02))
        min_liquidity = float(params.get("min_liquidity", 0.0))
        min_volume_24h = float(params.get("min_volume_24h", 0.0))
        max_volatility = float(params.get("max_volatility", 1.0))

        for market in markets:
            market_id = market.get("id")
            if not market_id:
                continue

            price = market.get("price")
            ret_1h = market.get("return_1h")
            ret_6h = market.get("return_6h")

            if price is None or ret_1h is None or ret_6h is None:
                continue

            liquidity = float(market.get("liquidity", 0) or 0)
            volume_24h = float(market.get("volume_24h", market.get("volume24hr", 0)) or 0)
            volatility = float(market.get("volatility", 0.0) or 0.0)

            if liquidity < min_liquidity or volume_24h < min_volume_24h:
                continue
            if volatility > max_volatility:
                continue
            if abs(ret_1h) < shock_threshold:
                continue
            if abs(ret_6h) > stabilize_threshold:
                continue

            signal_type = SignalType.BUY if ret_1h < 0 else SignalType.SELL
            confidence = min(abs(ret_1h) / max(shock_threshold, 1e-6), 1.0)

            signals.append(
                Signal(
                    strategy_name=self.name,
                    market_id=market_id,
                    signal_type=signal_type,
                    timestamp=timestamp,
                    price=price,
                    category=market.get("category", "unknown"),
                    liquidity=liquidity,
                    volume_24h=volume_24h,
                    volatility=volatility,
                    confidence=confidence,
                    reason="mean_reversion",
                    features={
                        "return_1h": ret_1h,
                        "return_6h": ret_6h,
                    },
                )
            )

        return signals


__all__ = ["MeanReversionStrategy"]

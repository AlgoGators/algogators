"""
Composite strategy that combines multiple strategies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Any

from .base import BaseStrategy, StrategyConfig, Signal


class CompositeStrategy(BaseStrategy):
    """
    Combine multiple strategies with voting/weighting.

    Generates signals based on agreement between sub-strategies.
    """

    def __init__(
        self,
        strategies: List[BaseStrategy],
        config: Optional[StrategyConfig] = None,
        min_agreement: int = 2,  # Minimum strategies that must agree
    ):
        super().__init__(config or StrategyConfig(name="composite"))
        self.strategies = strategies
        self.min_agreement = min_agreement

    def generate_signals(
        self,
        market_data: Dict[str, Any],
        timestamp: datetime,
    ) -> List[Signal]:
        # Collect signals from all strategies
        all_signals: Dict[str, List[Signal]] = {}

        for strategy in self.strategies:
            if not strategy.config.enabled:
                continue

            signals = strategy.generate_signals(market_data, timestamp)

            for signal in signals:
                key = f"{signal.market_id}_{signal.signal_type.value}"
                if key not in all_signals:
                    all_signals[key] = []
                all_signals[key].append(signal)

        # Filter to signals with enough agreement
        composite_signals: List[Signal] = []

        for signals in all_signals.values():
            if len(signals) < self.min_agreement:
                continue

            # Combine signals
            base = signals[0]
            avg_confidence = sum(s.confidence for s in signals) / len(signals)
            contributing = [s.strategy_name for s in signals]

            composite = Signal(
                strategy_name=self.name,
                market_id=base.market_id,
                signal_type=base.signal_type,
                timestamp=timestamp,
                outcome=base.outcome,
                size=base.size,
                price=base.price,
                category=base.category,
                liquidity=base.liquidity,
                volume_24h=base.volume_24h,
                confidence=avg_confidence,
                reason=f"Agreement from: {', '.join(contributing)}",
                features={
                    "contributing_strategies": contributing,
                    "agreement_count": len(signals),
                },
            )
            composite_signals.append(composite)

        return composite_signals

    def on_position_opened(self, market_id: str, signal: Signal):
        super().on_position_opened(market_id, signal)
        for strategy in self.strategies:
            strategy.on_position_opened(market_id, signal)

    def on_position_closed(self, market_id: str, pnl: float):
        super().on_position_closed(market_id, pnl)
        for strategy in self.strategies:
            strategy.on_position_closed(market_id, pnl)

    def reset(self):
        super().reset()
        for strategy in self.strategies:
            strategy.reset()


__all__ = ["CompositeStrategy"]

from datetime import datetime

from trading.engine import TradingEngine, EngineConfig
from trading.strategy import Strategy, Signal, SignalType, StrategyConfig


class AlwaysBuyStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__(StrategyConfig(name="always_buy", position_size=100))

    def generate_signals(self, market_data, timestamp):
        signals = []
        for market in market_data.get("markets", []):
            market_id = market.get("id")
            if not market_id:
                continue
            signals.append(
                Signal(
                    strategy_name=self.name,
                    market_id=market_id,
                    signal_type=SignalType.BUY,
                    timestamp=timestamp,
                    outcome="YES",
                    size=self.config.position_size,
                    price=market.get("price"),
                    category=market.get("category", "unknown"),
                    liquidity=market.get("liquidity", 0),
                    volume_24h=market.get("volume_24h", 0),
                    confidence=0.9,
                    reason="test",
                )
            )
        return signals


def test_engine_runs_backtest_with_price_events() -> None:
    engine = TradingEngine(config=EngineConfig(starting_capital=10000, fill_latency_ms=0))
    engine.add_strategy(AlwaysBuyStrategy())

    price_events = [
        {
            "timestamp": 1_700_000_000,
            "market_id": "m1",
            "outcome": "YES",
            "price": 0.55,
            "liquidity": 1000,
            "volume_24h": 200,
        }
    ]

    result = engine.run_backtest(price_events=iter(price_events))

    assert result.trade_count == 1
    assert result.total_trades == 1
    assert result.open_positions == 1


def test_engine_closes_on_resolution() -> None:
    engine = TradingEngine(config=EngineConfig(starting_capital=10000, fill_latency_ms=0))
    engine.add_strategy(AlwaysBuyStrategy())

    price_events = [
        {"timestamp": 1_700_000_000, "market_id": "m2", "outcome": "YES", "price": 0.4, "liquidity": 1000, "volume_24h": 200}
    ]
    resolution_events = [
        {"timestamp": 1_700_000_100, "market_id": "m2", "resolution": 1.0}
    ]

    result = engine.run_backtest(price_events=iter(price_events), resolution_events=iter(resolution_events))

    assert result.closed_trades == 1
    assert result.open_positions == 0

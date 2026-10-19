import pandas as pd

from trading.signals import SignalEngine, SignalConfig, SignalContext


def test_signal_engine_generates_signal() -> None:
    config = SignalConfig(
        min_volume24hr=0,
        min_liquidity=0,
        min_history_points=2,
        min_price=0.01,
        max_price=0.99,
        shock_return_1h=0.05,
        stabilize_return_6h=0.2,
        feature_window_hours=24,
    )
    engine = SignalEngine(config=config)

    history = pd.DataFrame(
        {
            "timestamp": [0, 18000, 21600],
            "price": [0.5, 0.5, 0.45],
        }
    )
    context = SignalContext(
        market_info={"id": "m1", "slug": "m1", "question": "Test?", "volume24hr": 100, "liquidity": 100},
        history=history,
        as_of=pd.Timestamp("2026-01-01T02:00:00Z"),
    )

    signals = engine.evaluate(context)
    assert len(signals) == 1
    assert signals[0].market_id == "m1"

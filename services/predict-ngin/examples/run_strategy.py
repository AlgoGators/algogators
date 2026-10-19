#!/usr/bin/env python3
"""
Example: Running strategies with the generalized trading engine.

This example shows how to:
1. Configure risk limits
2. Create and combine strategies
3. Run a backtest
4. Analyze results
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from datetime import datetime
from trading import (
    # Data loading
    PredictionMarketDB,

    # Risk management
    RiskLimits,
    RiskManager,

    # Strategies
    Strategy,
    StrategyConfig,
    StrategyManager,
    WhaleFollowingStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    CompositeStrategy,
    Signal,
    SignalType,

    # Engine
    TradingEngine,
    EngineConfig,
    BacktestResult,
)
from whale_strategy.polymarket_whales import (
    build_price_snapshot,
    identify_polymarket_whales,
)


def load_sample_trades(min_usd: float = 100, start: str = "2026-09-01", days: int = 1) -> "pd.DataFrame":
    """A small slice of Polymarket fills from the data lake (docs/DATA_LAKE.md), in the legacy trade columns.

    The old SQLite table / data/polymarket/trades files that load_polymarket_trades() read are gone; the
    lake is the canonical source. Reads only the month folder that contains the window.
    """
    import pandas as pd
    from predict_ngin import lake
    from predict_ngin.tapes.polymarket import clean_trades_sql

    t0 = int(pd.Timestamp(start, tz="UTC").timestamp())
    cols = ["ts_chain", "market_id", "maker", "taker", "maker_direction", "taker_direction",
            "yes_price", "usd_amount", "token_amount"]
    try:
        sql = clean_trades_sql(cols, start_ts=t0, end_ts=t0 + days * 86400, maker_rows_only=True)
        df = lake.connect().execute(f"select * from ({sql}) where usd_amount >= {float(min_usd)}").df()
    except Exception as exc:  # no lake on this machine (fresh clone / data on cold storage)
        raise SystemExit(f"Polymarket fills are not available ({exc}). "
                         "Build them with `uv run predict-ngin data build polymarket` (docs/DATA_LAKE.md).")
    df = df.rename(columns={"yes_price": "price"})
    df["datetime"] = pd.to_datetime(df["ts_chain"], unit="s")
    df["timestamp"] = df["datetime"]
    df["maker_direction"] = df["maker_direction"].str.lower()
    df["taker_direction"] = df["taker_direction"].str.lower()
    return df.sort_values("datetime", kind="stable").reset_index(drop=True)


def example_basic_strategy():
    """Basic example: Single strategy with default risk."""
    print("=" * 60)
    print("EXAMPLE 1: Basic Whale Following Strategy")
    print("=" * 60)

    # Load whale addresses
    print("\nLoading Polymarket trades...")
    trades = load_sample_trades(min_usd=100)
    print(f"Loaded {len(trades):,} trades")

    # Identify whales (win rate is marked to each market's last traded price)
    print("\nIdentifying whales (60%+ win rate)...")
    whales = identify_polymarket_whales(trades, method="win_rate_60pct", min_trades=20,
                                        price_snapshot=build_price_snapshot(trades))
    print(f"Found {len(whales)} whales")

    # Create strategy
    strategy = WhaleFollowingStrategy(
        whale_addresses=whales,
        config=StrategyConfig(
            name="whale_60pct",
            position_size=100,
            max_positions=50,
        ),
    )

    # Create strategy manager with risk
    manager = StrategyManager(
        capital=10000,
        risk_limits=RiskLimits(
            max_position_size=500,
            max_total_positions=50,
            max_total_exposure=8000,
            max_daily_drawdown_pct=0.05,
        ),
    )
    manager.add_strategy(strategy)

    # Simulate some signals
    print("\nSimulating signals...")
    sample_trades = trades[trades["maker"].isin(whales)].head(100).to_dict("records")  # whale prints

    market_data = {
        "recent_trades": sample_trades[:10],
        "markets": [],
    }

    signals = manager.generate_signals(market_data, datetime.now())

    print(f"Generated {len(signals)} signals")
    for sig in signals[:5]:
        status = "APPROVED" if sig.risk_check_passed else f"REJECTED: {sig.risk_rejection_reason}"
        print(f"  {sig.market_id[:20]}... {sig.signal_type.value} ${sig.effective_size:.0f} - {status}")

    print(f"\nRisk Report:")
    report = manager.get_risk_report()
    for key, value in report.items():
        if isinstance(value, float):
            print(f"  {key}: {value:,.2f}")
        else:
            print(f"  {key}: {value}")


def example_multi_strategy():
    """Example: Multiple strategies with composite signals."""
    print("\n" + "=" * 60)
    print("EXAMPLE 2: Multi-Strategy Composite")
    print("=" * 60)

    # Create individual strategies
    mean_rev = MeanReversionStrategy(
        config=StrategyConfig(
            name="mean_reversion",
            position_size=100,
            parameters={
                "shock_threshold": 0.05,
                "stabilize_threshold": 0.02,
            },
        )
    )

    momentum = MomentumStrategy(
        config=StrategyConfig(
            name="momentum",
            position_size=100,
            parameters={
                "min_return": 0.03,
            },
        )
    )

    # Create composite strategy
    composite = CompositeStrategy(
        strategies=[mean_rev, momentum],
        min_agreement=2,  # Both must agree
    )

    # Create manager
    manager = StrategyManager(capital=10000)
    manager.add_strategy(composite)

    # Simulate market data
    market_data = {
        "markets": [
            {
                "id": "market_1",
                "price": 0.45,
                "return_1h": -0.08,  # 8% drop
                "return_6h": 0.01,   # Stabilized
                "return_24h": -0.10,  # Down 10%
                "liquidity": 5000,
                "volume_24h": 10000,
            },
            {
                "id": "market_2",
                "price": 0.70,
                "return_1h": 0.02,
                "return_6h": 0.05,
                "return_24h": 0.15,  # Strong momentum
                "liquidity": 8000,
                "volume_24h": 20000,
            },
        ],
        "recent_trades": [],
    }

    signals = manager.generate_signals(market_data, datetime.now())

    print(f"\nGenerated {len(signals)} composite signals:")
    for sig in signals:
        print(f"  {sig.market_id}: {sig.signal_type.value}")
        print(f"    Confidence: {sig.confidence:.2f}")
        print(f"    Reason: {sig.reason}")
        print(f"    Contributing: {sig.features.get('contributing_strategies', [])}")


def example_custom_risk():
    """Example: Custom risk limits."""
    print("\n" + "=" * 60)
    print("EXAMPLE 3: Custom Risk Configuration")
    print("=" * 60)

    # Conservative risk limits
    conservative = RiskLimits(
        # Position limits
        max_position_size=200,
        min_position_size=50,
        max_total_positions=20,

        # Exposure limits
        max_total_exposure=5000,
        max_exposure_pct_of_capital=0.50,
        max_single_position_pct=0.05,

        # Drawdown limits
        max_daily_drawdown_pct=0.02,
        max_total_drawdown_pct=0.10,
        drawdown_halt_pct=0.15,

        # Market quality
        min_liquidity=1000,
        min_volume_24h=500,
        max_spread_pct=0.05,

        # Volatility
        vol_scaling_enabled=True,
        target_portfolio_volatility=0.10,
    )

    # Aggressive risk limits
    aggressive = RiskLimits(
        max_position_size=2000,
        max_total_positions=100,
        max_total_exposure=50000,
        max_exposure_pct_of_capital=0.90,
        max_daily_drawdown_pct=0.10,
        max_total_drawdown_pct=0.30,
        min_liquidity=100,
        vol_scaling_enabled=False,
    )

    print("\nConservative Risk Limits:")
    print(f"  Max position: ${conservative.max_position_size}")
    print(f"  Max exposure: ${conservative.max_total_exposure}")
    print(f"  Max drawdown: {conservative.max_total_drawdown_pct*100}%")
    print(f"  Halt at: {conservative.drawdown_halt_pct*100}%")

    print("\nAggressive Risk Limits:")
    print(f"  Max position: ${aggressive.max_position_size}")
    print(f"  Max exposure: ${aggressive.max_total_exposure}")
    print(f"  Max drawdown: {aggressive.max_total_drawdown_pct*100}%")

    # Test a signal against both
    test_signal = {
        "market_id": "test_market",
        "size": 500,
        "category": "politics",
        "liquidity": 2000,
        "volume_24h": 5000,
        "spread": 0.03,
        "volatility": 0.25,
    }

    print("\nTesting signal (size=$500, liquidity=$2000):")

    for name, limits in [("Conservative", conservative), ("Aggressive", aggressive)]:
        manager = RiskManager(limits=limits)
        manager.initialize(capital=10000)
        result = manager.check_signal(test_signal)

        print(f"\n  {name}: {result.action.value}")
        if result.adjusted_size:
            print(f"    Adjusted size: ${result.adjusted_size:.0f}")
        if result.reason:
            print(f"    Reason: {result.reason}")


def example_engine_backtest():
    """Example: Full backtest with trading engine."""
    print("\n" + "=" * 60)
    print("EXAMPLE 4: Trading Engine Backtest")
    print("=" * 60)

    # Configure engine
    config = EngineConfig(
        starting_capital=10000,
        fill_latency_ms=100,
        enable_stop_loss=True,
        stop_loss_pct=0.15,
        enable_take_profit=True,
        take_profit_pct=0.30,
    )

    # Create engine
    engine = TradingEngine(
        config=config,
        risk_limits=RiskLimits(
            max_position_size=500,
            max_total_positions=20,
        ),
    )

    # Add strategies
    engine.add_strategy(MeanReversionStrategy())
    engine.add_strategy(MomentumStrategy())

    # Create sample price events
    import random
    random.seed(42)

    price_events = []
    base_time = int(datetime(2024, 1, 1).timestamp())

    for i in range(1000):
        price_events.append({
            "timestamp": base_time + i * 3600,  # Hourly
            "market_id": f"market_{i % 10}",
            "outcome": "YES",
            "price": 0.3 + random.random() * 0.4,  # 0.3-0.7
            "liquidity": 5000 + random.random() * 10000,
            "volume_24h": 10000 + random.random() * 20000,
        })

    # Create resolution events
    resolution_events = []
    for i in range(10):
        resolution_events.append({
            "timestamp": base_time + 900 * 3600,  # End of period
            "market_id": f"market_{i}",
            "resolution": 1.0 if random.random() > 0.5 else 0.0,
        })

    print("\nRunning backtest...")
    print(f"  Price events: {len(price_events)}")
    print(f"  Resolution events: {len(resolution_events)}")

    result = engine.run_backtest(
        price_events=iter(price_events),
        resolution_events=iter(resolution_events),
    )

    if result.trades_df.empty:  # BacktestResult.summary() needs at least one trade
        print(f"\nNo trades: {result.signal_count} signals, none filled.")
        return
    print(result.summary())

    if len(result.trades_df) > 0:
        print("\nPerformance by Strategy:")
        print(result.by_strategy())


if __name__ == "__main__":
    example_basic_strategy()
    example_multi_strategy()
    example_custom_risk()
    example_engine_backtest()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)

#!/usr/bin/env python3
"""
Run momentum, mean reversion, smart money, and breakout strategies with zero fees.

Polymarket has no transaction fees. Uses POLYMARKET_ZERO_COST_MODEL (1 cent slippage, no impact).
Outputs QuantStats report per strategy and prints Sharpe ratios to terminal.

Usage:
    python scripts/backtest/run_all_strategies.py
    python scripts/backtest/run_all_strategies.py --trades-dir data/polymarket/trades --start-date 2024-01-01
"""

import argparse
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

from trading.data_modules.parquet_store import TradeStore
from trading.data_modules.trade_price_store import TradeBasedPriceStore
from trading.momentum_signals_from_trades import generate_momentum_signals_from_trades
from trading.strategies_from_trades import (
    generate_mean_reversion_signals_from_trades,
    generate_smart_money_signals_from_trades,
    generate_breakout_signals_from_trades,
)
from trading import (
    PolymarketBacktestConfig,
    run_polymarket_backtest,
    signals_dataframe_to_backtest_format,
    generate_quantstats_report,
    POLYMARKET_ZERO_COST_MODEL,
    SpreadEstimator,
    SlippageModel,
    LiquidityFilter,
    LiquidityFilterConfig,
)
from trading.execution import TradeBasedExecutionEngine


def _make_sample_trades():
    """Create synthetic trades for testing (no real data required)."""
    import numpy as np
    base = pd.Timestamp("2024-01-01", tz="UTC")
    n_days = 90
    markets = [f"m{i}" for i in range(20)]
    rows = []
    for d in range(n_days):
        ts = base + pd.Timedelta(days=d)
        for m in markets:
            price = 0.4 + 0.2 * np.sin(d / 7 + hash(m) % 10) + 0.05 * np.random.randn()
            price = max(0.05, min(0.95, price))
            rows.append({
                "market_id": m,
                "timestamp": ts,
                "datetime": ts,
                "price": price,
                "usd_amount": 50 + np.random.rand() * 200,
                "maker_direction": "BUY" if np.random.rand() > 0.5 else "SELL",
                "taker_direction": "SELL" if np.random.rand() > 0.5 else "BUY",
                "outcome": "YES",
            })
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def _load_trades(trades_dir: str, research_dir: str, start_date: str, end_date: str, min_usd: float, use_sample: bool = False):
    """Load trades from TradeStore or research_data_loader."""
    if use_sample:
        return _make_sample_trades()

    # Try TradeStore (data/polymarket/trades or data/parquet/trades)
    store = TradeStore(base_dir=trades_dir)
    if store.available():
        df = store.load_trades(min_usd=min_usd, start_date=start_date, end_date=end_date)
        if not df.empty:
            if "timestamp" not in df.columns and "datetime" in df.columns:
                df = df.copy()
                df["timestamp"] = df["datetime"]
            if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
                # TradeStore serves 'YYYY-MM-DD HH:MM:SS' UTC strings
                df = df.copy()
                df["timestamp"] = pd.to_datetime(df["timestamp"])
            return df

    # Fallback: research data (data/research/{category}/trades.parquet)
    research_path = Path(research_dir)
    if research_path.exists():
        try:
            from src.whale_strategy.research_data_loader import load_research_trades, get_research_categories
            cats = get_research_categories(research_path)
            if cats:
                df = load_research_trades(
                    research_path,
                    categories=cats,
                    min_usd=min_usd,
                    start_date=start_date,
                    end_date=end_date,
                )
                if not df.empty:
                    df["timestamp"] = df["datetime"]
                    return df
        except Exception:
            pass

    return None


def _run_strategy(
    name: str,
    signals_df,
    price_store,
    config,
    output_dir: Path,
    position_size: float,
    execution_engine=None,
) -> dict:
    """Run backtest, save QuantStats, return metrics."""
    if signals_df is None or signals_df.empty:
        print(f"  {name}: No signals, skipping")
        return {"strategy": name, "sharpe": None, "trades": 0}

    try:
        signals_df = signals_dataframe_to_backtest_format(signals_df)
    except ValueError as e:
        print(f"  {name}: {e}")
        return {"strategy": name, "sharpe": None, "trades": 0}

    bt_config = PolymarketBacktestConfig(
        strategy_name=name,
        position_size=position_size,
        starting_capital=config.get("starting_capital", 10000),
        fill_latency_seconds=config.get("fill_latency_seconds", 60),
        max_holding_seconds=config.get("max_holding_seconds", 172800),
    )

    result = run_polymarket_backtest(
        signals=signals_df,
        price_store=price_store,
        config=bt_config,
        cost_model=POLYMARKET_ZERO_COST_MODEL,
        execution_engine=execution_engine,
    )

    sharpe = result.summary.metrics.sharpe_ratio if result.summary else None
    n_trades = len(result.trades_df) if result.trades_df is not None else 0

    # QuantStats report
    if result.daily_returns is not None and len(result.daily_returns) >= 5:
        qs_path = output_dir / f"quantstats_{name.replace(' ', '_')}.html"
        ok = generate_quantstats_report(
            result.daily_returns,
            str(qs_path),
            title=f"{name} Backtest (Polymarket, 1c slippage)",
        )
        if ok:
            print(f"  QuantStats: {qs_path}")

    return {
        "strategy": name,
        "sharpe": sharpe,
        "trades": n_trades,
        "roi": result.summary.metrics.roi_pct if result.summary else None,
        "max_dd": result.summary.metrics.max_drawdown if result.summary else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run momentum, mean reversion, smart money, breakout (1c slippage, QuantStats, Sharpe).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--trades-dir", default="data/polymarket/trades", help="Trades parquet directory")
    parser.add_argument("--research-dir", default="data/research", help="Research data (fallback)")
    parser.add_argument("--output-dir", default="data/output/backtests", help="Output for QuantStats HTML")
    parser.add_argument("--start-date", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--position-size", type=float, default=250.0, help="Position size per trade")
    parser.add_argument("--max-markets", type=int, default=1000, help="Max markets per strategy")
    parser.add_argument("--min-usd", type=float, default=10.0, help="Min trade size (USD)")
    parser.add_argument("--sample", action="store_true", help="Use synthetic sample data (for testing)")
    parser.add_argument("--realistic-execution", action="store_true", default=True,
                        help="Use trade-tape execution engine (spread, slippage, liquidity)")
    parser.add_argument("--no-realistic-execution", dest="realistic_execution", action="store_false",
                        help="Disable trade-tape execution, use flat cost model")
    args = parser.parse_args()

    print("Loading trades...")
    trades_df = _load_trades(
        args.trades_dir,
        args.research_dir,
        args.start_date,
        args.end_date,
        args.min_usd,
        use_sample=args.sample,
    )
    if trades_df is None or trades_df.empty:
        print("Error: No trades found. Ensure data/polymarket/trades or data/research has trade data.")
        return 1

    print(f"  Loaded {len(trades_df):,} trades, {trades_df['market_id'].nunique():,} markets")
    print(f"  Date range: {trades_df['timestamp'].min()} to {trades_df['timestamp'].max()}")

    price_store = TradeBasedPriceStore(trades_df, cache_size=50)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build realistic execution engine
    execution_engine = None
    if args.realistic_execution:
        print("Building realistic execution engine...")
        spread_est = SpreadEstimator(trades_df, window_seconds=3600)
        slippage_model = SlippageModel(trades_df, calibrate=True)
        liquidity_filter = LiquidityFilter(trades_df, LiquidityFilterConfig(
            min_volume_window_hours=24,
            min_volume_usd=500.0,
            max_size_to_volume_ratio=0.10,
        ))
        execution_engine = TradeBasedExecutionEngine(
            trades_df,
            spread_estimator=spread_est,
            slippage_model=slippage_model,
            liquidity_filter=liquidity_filter,
        )
        print("  Spread estimator, slippage model, liquidity filter ready")
    else:
        print("Using flat cost model (1c slippage, no impact)")

    config = {"starting_capital": 10000, "fill_latency_seconds": 60, "max_holding_seconds": 172800}
    results = []

    # Momentum
    print("\n--- Momentum ---")
    mom_signals = generate_momentum_signals_from_trades(
        trades_df,
        threshold=0.05,
        eval_freq_hours=24,
        position_size=args.position_size,
        max_markets=args.max_markets,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    r = _run_strategy("Momentum", mom_signals, price_store, config, output_dir, args.position_size, execution_engine)
    results.append(r)

    # Mean reversion
    print("\n--- Mean Reversion ---")
    mr_signals = generate_mean_reversion_signals_from_trades(
        trades_df,
        shock_threshold=0.08,
        stabilize_threshold=0.02,
        eval_freq_hours=1,
        position_size=args.position_size,
        max_markets=args.max_markets,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    r = _run_strategy("Mean Reversion", mr_signals, price_store, config, output_dir, args.position_size, execution_engine)
    results.append(r)

    # Smart money
    print("\n--- Smart Money ---")
    sm_signals = generate_smart_money_signals_from_trades(
        trades_df,
        min_trade_size=1000,
        aggregation_window_hours=4,
        imbalance_threshold=0.6,
        min_volume=10000,
        min_trades=3,
        eval_freq_hours=4,
        position_size=args.position_size,
        max_markets=args.max_markets,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    r = _run_strategy("Smart Money", sm_signals, price_store, config, output_dir, args.position_size, execution_engine)
    results.append(r)

    # Breakout
    print("\n--- Breakout ---")
    bo_signals = generate_breakout_signals_from_trades(
        trades_df,
        lookback_hours=24,
        breakout_threshold=0.05,
        min_range_width=0.02,
        max_range_width=0.20,
        position_size=args.position_size,
        max_markets=args.max_markets,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    r = _run_strategy("Breakout", bo_signals, price_store, config, output_dir, args.position_size, execution_engine)
    results.append(r)

    # Print Sharpe ratios
    exec_label = "tape execution" if args.realistic_execution else "1c slippage, no impact"
    print("\n" + "=" * 50)
    print(f"SHARPE RATIOS (Polymarket, {exec_label})")
    print("=" * 50)
    for r in results:
        sharpe_str = f"{r['sharpe']:.3f}" if r["sharpe"] is not None else "N/A"
        trades_str = str(r["trades"])
        roi_str = f"{r['roi']:.1f}%" if r.get("roi") is not None else "N/A"
        print(f"  {r['strategy']:20}  Sharpe: {sharpe_str:>8}  Trades: {trades_str:>6}  ROI: {roi_str}")
    print("=" * 50)

    return 0


if __name__ == "__main__":
    sys.exit(main())

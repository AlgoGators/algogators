#!/usr/bin/env python3
"""
Run momentum backtest using only trade data (no price files needed).

Extracts prices from trades, generates momentum signals, and runs backtest.

Usage:
    python scripts/backtest/run_momentum_backtest_from_trades.py
    python scripts/backtest/run_momentum_backtest_from_trades.py --start-date 2024-01-01 --threshold 0.03
"""

import argparse
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
import uuid

import pandas as pd
from pathlib import Path

# Add project root and src for imports
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

from trading.data_modules.parquet_store import TradeStore
from trading.data_modules.trade_price_store import TradeBasedPriceStore
from trading.momentum_signals_from_trades import generate_momentum_signals_from_trades
from trading import (
    PolymarketBacktestConfig,
    run_polymarket_backtest,
    print_polymarket_result,
    signals_dataframe_to_backtest_format,
    generate_quantstats_report,
)
from src.backtest.storage import save_backtest_result


def _load_config():
    try:
        from config import get_config
        return get_config()
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run momentum backtest using trade data (no price files needed).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--trades-dir",
        default=None,
        help="Parquet trades directory (default from config or data/polymarket/trades)",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Start date YYYY-MM-DD (optional)",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="End date YYYY-MM-DD (optional)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Min |return_24h| to emit signal (default: 0.05)",
    )
    parser.add_argument(
        "--eval-freq-hours",
        type=int,
        default=24,
        help="Evaluation interval in hours (e.g. 24 = daily)",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=1000,
        help="Cap number of markets (default: 1000)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for reports (default from config backtest.output_dir)",
    )
    parser.add_argument(
        "--no-quantstats",
        action="store_true",
        help="Skip QuantStats HTML report",
    )
    parser.add_argument(
        "--position-size",
        type=float,
        default=None,
        help="Position size per trade (default from config backtest.default_position_size)",
    )
    parser.add_argument(
        "--no-tracker",
        action="store_true",
        help="Skip experiment tracker integration",
    )
    parser.add_argument(
        "--backtests-dir",
        default="data/output/backtests",
        help="Directory for organized backtest storage (default: backtests)",
    )
    parser.add_argument(
        "--min-usd",
        type=float,
        default=10.0,
        help="Minimum USD trade size to include (default: 10.0)",
    )
    parser.add_argument(
        "--max-trades",
        type=int,
        default=None,
        help="Maximum number of trades to load (for memory-constrained systems, default: None = all)",
    )
    parser.add_argument(
        "--cache-size",
        type=int,
        default=50,
        help="LRU cache size for price store (default: 50, lower = less memory)",
    )
    parser.add_argument(
        "--columns-only",
        action="store_true",
        help="Load only essential columns (market_id, timestamp, price, outcome) to save memory",
    )
    args = parser.parse_args()

    config = _load_config()
    trades_dir = args.trades_dir or (config and config.database.parquet_dir + "/trades") or "data/polymarket/trades"
    output_dir = args.output_dir or (config and config.backtest.output_dir) or "data/research"
    position_size = args.position_size
    if position_size is None and config:
        position_size = getattr(config.backtest, "default_position_size", 250)
    if position_size is None:
        position_size = 250.0

    print("Momentum backtest from trades")
    print("  trades_dir     ", trades_dir)
    print("  start_date     ", args.start_date or "(all)")
    print("  end_date       ", args.end_date or "(all)")
    print("  threshold      ", args.threshold)
    print("  eval_freq_hours", args.eval_freq_hours)
    print("  position_size  ", position_size)
    print("  output_dir     ", output_dir)
    print("  min_usd        ", args.min_usd)
    print("  max_trades     ", args.max_trades or "(all)")
    print("  cache_size     ", args.cache_size)
    print("  columns_only   ", args.columns_only)

    # Load trades
    print("\nLoading trades...")
    trade_store = TradeStore(base_dir=trades_dir)
    
    if not trade_store.available():
        print(f"Error: No trade files found in {trades_dir}")
        print("Make sure you have trades parquet files in the trades directory.")
        return 1
    
    # Determine which columns to load
    columns = None
    if args.columns_only:
        columns = ["market_id", "timestamp", "price", "outcome", "usd_amount", "token_amount", 
                   "maker_direction", "taker_direction"]
    
    trades_df = trade_store.load_trades(
        min_usd=args.min_usd,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.max_trades,
        columns=columns,
    )
    
    if trades_df.empty:
        print("No trades found. Check date range and min_usd filter.")
        return 1
    
    if not pd.api.types.is_datetime64_any_dtype(trades_df["timestamp"]):
        # TradeStore serves 'YYYY-MM-DD HH:MM:SS' UTC strings; with pandas' str dtype
        # TradeBasedPriceStore would coerce them to NaN (dtype != "object").
        trades_df["timestamp"] = pd.to_datetime(trades_df["timestamp"])

    print(f"  Loaded {len(trades_df):,} trades")
    print(f"  Date range: {trades_df['timestamp'].min()} to {trades_df['timestamp'].max()}")

    # Generate momentum signals from trades
    print("\nGenerating momentum signals from trades...")
    signals_df = generate_momentum_signals_from_trades(
        trades_df=trades_df,
        threshold=args.threshold,
        eval_freq_hours=args.eval_freq_hours,
        outcome="YES",
        position_size=position_size,
        max_markets=args.max_markets,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    if signals_df is None or signals_df.empty:
        print("No signals generated. Try lowering threshold or checking date range.")
        return 1

    print(f"  Generated {len(signals_df):,} signals")

    try:
        signals_df = signals_dataframe_to_backtest_format(signals_df)
    except ValueError as e:
        print(f"  Error: {e}")
        return 1

    # Create price store from trades
    print("\nCreating price store from trades...")
    price_store = TradeBasedPriceStore(trades_df, cache_size=args.cache_size)
    
    # Free memory: delete trades_df after creating price store
    # The price store has its own copy, and we only need signals_df going forward
    del trades_df
    import gc
    gc.collect()

    # Prepare config
    _bt = config.backtest if config else None
    bt_config = PolymarketBacktestConfig(
        strategy_name="Momentum (from trades)",
        position_size=float(position_size),
        starting_capital=getattr(_bt, "starting_capital", 10000) if _bt else 10000,
        fill_latency_seconds=getattr(_bt, "fill_latency_seconds", 60) if _bt else 60,
        max_holding_seconds=getattr(_bt, "max_holding_seconds", 172800) if _bt else 172800,
        include_open=getattr(_bt, "include_open", True) if _bt else True,
        enforce_one_position_per_market=getattr(_bt, "enforce_one_position_per_market", True) if _bt else True,
        min_liquidity=getattr(_bt, "min_liquidity", 0) if _bt else 0,
        min_volume=getattr(_bt, "min_volume", 0) if _bt else 0,
    )

    # Prepare parameters for tracking
    parameters = {
        "threshold": args.threshold,
        "position_size": position_size,
        "eval_freq_hours": args.eval_freq_hours,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "min_usd": args.min_usd,
        "max_markets": args.max_markets,
        "source": "trades",  # Indicate we're using trades, not price files
    }

    # Prepare config snapshot
    config_snapshot = {}
    if config:
        config_snapshot = {
            "backtest": {
                "starting_capital": bt_config.starting_capital,
                "fill_latency_seconds": bt_config.fill_latency_seconds,
                "max_holding_seconds": bt_config.max_holding_seconds,
            },
        }

    # Run backtest
    print("\nRunning backtest...")
    result = run_polymarket_backtest(
        signals=signals_df,
        price_store=price_store,
        config=bt_config,
    )

    print_polymarket_result(result)

    # Generate quantstats report
    quantstats_path = None
    if not args.no_quantstats and result.daily_returns is not None and len(result.daily_returns) >= 5:
        import tempfile
        temp_dir = Path(tempfile.gettempdir())
        temp_quantstats = temp_dir / f"quantstats_temp_{uuid.uuid4().hex[:8]}.html"
        ok = generate_quantstats_report(
            result.daily_returns,
            str(temp_quantstats),
            title="Momentum Backtest (from trades)",
        )
        if ok:
            quantstats_path = temp_quantstats

    # Save to organized storage
    try:
        saved_run_id = save_backtest_result(
            strategy_name="momentum_from_trades",
            result=result,
            config=config_snapshot,
            base_dir=args.backtests_dir,
            tags=["momentum", "trades"],
            notes=f"Threshold: {args.threshold}, Position Size: {position_size}, Source: trades",
            signals_df=signals_df,
            quantstats_html_path=quantstats_path,
            auto_index=True,
        )
        print(f"\n✓ Backtest saved: {saved_run_id}")
        if quantstats_path:
            print(f"  QuantStats report: {args.backtests_dir}/momentum_from_trades/{saved_run_id}/results/quantstats.html")
    except Exception as e:
        print(f"\n⚠ Warning: Failed to save to organized storage: {e}")
        import traceback
        traceback.print_exc()

    # Also save to legacy output_dir for backward compatibility
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    if quantstats_path:
        legacy_path = Path(output_dir) / "quantstats_momentum_from_trades.html"
        import shutil
        shutil.copy2(quantstats_path, legacy_path)
        print(f"  Legacy report: {legacy_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

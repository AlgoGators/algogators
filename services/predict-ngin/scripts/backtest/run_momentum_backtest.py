#!/usr/bin/env python3
"""
Run momentum trading backtest with QuantStats reporting.

Generates momentum signals from parquet or SQLite price data (optimized for
large datasets), runs the Polymarket backtest, and writes QuantStats HTML
plus optional summary/trades CSVs.

Usage:
    python scripts/backtest/run_momentum_backtest.py
    python scripts/backtest/run_momentum_backtest.py --start-date 2024-06-01 --end-date 2024-12-31
    python scripts/backtest/run_momentum_backtest.py --threshold 0.03 --source parquet --max-markets 500
"""

import argparse
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
import uuid
from pathlib import Path

# Add project root and src for imports
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

from trading import (
    generate_momentum_signals,
    signals_dataframe_to_backtest_format,
    ClobPriceStore,
    PolymarketBacktestConfig,
    run_polymarket_backtest,
    print_polymarket_result,
    generate_quantstats_report,
    generate_all_reports,
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
        description="Run momentum backtest with QuantStats reporting.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--parquet-dir",
        default=None,
        help="Parquet prices directory (default from config or data/polymarket/prices)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite database path (default from config or data/prediction_markets.db)",
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
        default=None,
        help="Min |return_24h| to emit signal (default 0.05 or config strategies.momentum.min_return)",
    )
    parser.add_argument(
        "--eval-freq-hours",
        type=int,
        default=24,
        help="Evaluation interval in hours (e.g. 24 = daily)",
    )
    parser.add_argument(
        "--source",
        choices=("auto", "parquet", "sqlite"),
        default="auto",
        help="Data source: auto (parquet if available else sqlite), parquet, or sqlite",
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
    args = parser.parse_args()

    config = _load_config()
    parquet_dir = args.parquet_dir or (config and config.database.parquet_dir + "/prices") or "data/polymarket/prices"
    db_path = args.db_path or (config and config.database.path) or "data/prediction_markets.db"
    output_dir = args.output_dir or (config and config.backtest.output_dir) or "data/research"
    position_size = args.position_size
    if position_size is None and config:
        position_size = getattr(config.backtest, "default_position_size", 250)
    if position_size is None:
        position_size = 250.0

    threshold = args.threshold
    if threshold is None and config and hasattr(config, "strategies"):
        momentum_cfg = getattr(config.strategies, "momentum", None)
        if momentum_cfg is not None and hasattr(momentum_cfg, "min_return"):
            threshold = float(momentum_cfg.min_return)
    if threshold is None:
        threshold = 0.05

    print("Momentum backtest")
    print("  parquet_dir   ", parquet_dir)
    print("  db_path       ", db_path)
    print("  start_date    ", args.start_date or "(all)")
    print("  end_date      ", args.end_date or "(all)")
    print("  threshold     ", threshold)
    print("  eval_freq_hours", args.eval_freq_hours)
    print("  source        ", args.source)
    print("  position_size ", position_size)
    print("  output_dir    ", output_dir)

    print("\nGenerating momentum signals...")
    signals_df = generate_momentum_signals(
        parquet_dir=parquet_dir,
        db_path=db_path,
        start_date=args.start_date,
        end_date=args.end_date,
        threshold=threshold,
        eval_freq_hours=args.eval_freq_hours,
        outcome="YES",
        position_size=position_size,
        max_markets=args.max_markets,
        source=args.source,
    )

    if signals_df is None or signals_df.empty:
        print("No signals generated. Check data path and date range.")
        return 1

    print(f"  Generated {len(signals_df):,} signals")

    try:
        signals_df = signals_dataframe_to_backtest_format(signals_df)
    except ValueError as e:
        print(f"  Error: {e}")
        return 1

    # Price store: use parquet if available
    use_parquet = Path(parquet_dir).exists() and any(Path(parquet_dir).glob("prices_*.parquet"))
    if args.source == "sqlite":
        use_parquet = False
    elif args.source == "parquet":
        use_parquet = True

    price_store = ClobPriceStore(
        db_path=db_path,
        use_parquet=use_parquet,
        parquet_dir=parquet_dir,
    )

    _bt = config.backtest if config else None
    bt_config = PolymarketBacktestConfig(
        strategy_name="Momentum",
        position_size=float(position_size),
        starting_capital=getattr(_bt, "starting_capital", 10000) if _bt else 10000,
        fill_latency_seconds=getattr(_bt, "fill_latency_seconds", 60) if _bt else 60,
        max_holding_seconds=getattr(_bt, "max_holding_seconds", 172800) if _bt else 172800,
        include_open=getattr(_bt, "include_open", True) if _bt else True,
        enforce_one_position_per_market=getattr(_bt, "enforce_one_position_per_market", True) if _bt else True,
        min_liquidity=getattr(_bt, "min_liquidity", 0) if _bt else 0,
        min_volume=getattr(_bt, "min_volume", 0) if _bt else 0,
    )

    print("\nRunning backtest...")
    
    # Prepare parameters for tracking
    parameters = {
        "threshold": threshold,
        "position_size": position_size,
        "eval_freq_hours": args.eval_freq_hours,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "source": args.source,
        "max_markets": args.max_markets,
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
            "strategies": {
                "momentum": {
                    "min_return": threshold,
                }
            } if config and hasattr(config, "strategies") else {},
        }
    
    # Run backtest
    result = run_polymarket_backtest(
        signals=signals_df,
        price_store=price_store,
        config=bt_config,
    )
    price_store.close()

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
            title="Momentum Backtest Report",
        )
        if ok:
            quantstats_path = temp_quantstats

    # Save to organized storage
    try:
        saved_run_id = save_backtest_result(
            strategy_name="momentum",
            result=result,
            config=config_snapshot,
            base_dir=args.backtests_dir,
            tags=["momentum", "polymarket"],
            notes=f"Threshold: {threshold}, Position Size: {position_size}",
            signals_df=signals_df,
            quantstats_html_path=quantstats_path,
            auto_index=True,
        )
        print(f"\n✓ Backtest saved: {saved_run_id}")
        if quantstats_path:
            print(f"  QuantStats report: {args.backtests_dir}/momentum/{saved_run_id}/results/quantstats.html")
    except Exception as e:
        print(f"\n⚠ Warning: Failed to save to organized storage: {e}")
        import traceback
        traceback.print_exc()

    # Also save to legacy output_dir for backward compatibility
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    if quantstats_path:
        legacy_path = Path(output_dir) / "quantstats_momentum.html"
        import shutil
        shutil.copy2(quantstats_path, legacy_path)
        print(f"  Legacy report: {legacy_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

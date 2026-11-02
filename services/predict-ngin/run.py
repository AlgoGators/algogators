#!/usr/bin/env python3
"""
Whale Following Strategy - Main Entry Point

Run the complete whale following strategy analysis on Polymarket data.

Usage:
    python run.py                    # Full analysis with default settings (Polymarket)
    python run.py --method win_rate_60pct  # Specific whale method
    python run.py --position-size 500      # Custom position size
    python run.py --manifold-whales        # Run Manifold whale strategy instead
    python run.py --help             # Show all options

Output:
    data/research/summary.csv          # Strategy comparison
    data/research/diagnostics.csv      # Data quality diagnostics
    data/research/trades_*.csv         # Individual trade logs
    data/research/quantstats_*.html    # Interactive QuantStats reports
"""

import argparse
import os
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# Ensure src is importable
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

from trading import (
    load_manifold_data,
    load_markets,
    build_resolution_map,
    train_test_split,
    generate_all_reports,
    categorize_market,
    CostModel,
    DataFetcher,
    COST_ASSUMPTIONS,
    build_database,
    PolymarketBacktestConfig,
    run_polymarket_backtest,
    print_polymarket_result,
    generate_quantstats_report,
    save_trades_csv,
    save_summary_csv,
    save_diagnostics_csv,
)
from trading.config import load_config
from whale_strategy import (
    identify_whales,
    run_backtest,
    print_result,
    run_position_size_analysis,
    print_position_size_analysis,
    run_rolling_backtest,
    WHALE_METHODS,
)
from whale_strategy.polymarket_whales import (
    load_polymarket_trades,
    load_resolution_winners,
    POLY_WHALE_METHODS,
)
from whale_strategy.polymarket_realistic_backtest import (
    run_rolling_realistic_backtest,
    print_realistic_result,
)


def _using_test_window(args) -> bool:
    return any(
        getattr(args, name, None) is not None
        for name in ("test_days", "test_months", "test_start", "test_end")
    )


def _describe_test_window(args) -> str:
    if args.test_days is not None:
        return f"last {args.test_days} days"
    if args.test_months is not None:
        return f"last {args.test_months} months"
    if args.test_start is not None:
        if args.test_end is not None:
            return f"{args.test_start} to {args.test_end}"
        return f"since {args.test_start}"
    if args.test_end is not None:
        return f"up to {args.test_end}"
    return "custom window"


def _split_train_test(df, args):
    return train_test_split(
        df,
        train_ratio=args.train_ratio,
        test_start=args.test_start,
        test_end=args.test_end,
        test_days=args.test_days,
        test_months=args.test_months,
    )


def _print_split_summary(train_df, test_df, args, label: str) -> None:
    if _using_test_window(args):
        start = test_df["datetime"].min() if not test_df.empty else None
        end = test_df["datetime"].max() if not test_df.empty else None
        window_desc = _describe_test_window(args)
        if start is not None and end is not None:
            print(f"    Test window ({window_desc}): {start} to {end}")
        else:
            print(f"    Test window ({window_desc}): <empty>")
        print(f"    Training: {len(train_df):,} {label}")
        print(f"    Testing:  {len(test_df):,} {label}")
    else:
        split_date = train_df["datetime"].max() if not train_df.empty else None
        print(f"    Split at {split_date}")
        print(f"    Training: {len(train_df):,} {label} ({args.train_ratio*100:.0f}%)")
        print(f"    Testing:  {len(test_df):,} {label} ({(1-args.train_ratio)*100:.0f}%)")


def _run_polymarket_whales(args) -> None:
    print("\n" + "=" * 70)
    print("POLYMARKET WHALE STRATEGY")
    print("=" * 70)

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"\nDatabase not found: {db_path}")
        print("Build it with: python run.py --build-db --import-poly-trades")
        return

    print("\n[1] Loading Polymarket trades...")
    poly_start = getattr(args, "poly_start_date", None)
    poly_end = getattr(args, "poly_end_date", None)
    poly_limit = getattr(args, "poly_limit", None)
    if poly_start:
        print(f"    Start date filter: {poly_start}")
    if poly_end:
        print(f"    End date filter: {poly_end}")
    if poly_limit:
        print(f"    Limit: {poly_limit:,} trades")
    poly_columns = [
        "timestamp",
        "market_id",
        "maker",
        "taker",
        "maker_direction",
        "taker_direction",
        "price",
        "usd_amount",
        "token_amount",
        "market_cum_liquidity_usd",
    ]
    trades = load_polymarket_trades(
        db_path=str(db_path),
        min_usd=args.poly_min_usd,
        start_date=poly_start,
        end_date=poly_end,
        limit=poly_limit,
        columns=poly_columns,
    )
    print(f"    Loaded {len(trades):,} trades")
    print(f"    Date range: {trades['datetime'].min()} to {trades['datetime'].max()}")

    if trades.empty:
        print("    No trades available. Exiting.")
        return

    print("\n[2] Backtest configuration...")
    resolution_winners = None
    if args.poly_method == "mid_price_accuracy":
        print("    Loading resolution winners from database...")
        resolution_winners = load_resolution_winners(str(db_path))
        print(f"    Loaded {len(resolution_winners):,} resolution winners")

    cost_model = CostModel.from_assumptions(args.cost_model)
    print(f"\n[3] Cost model: {COST_ASSUMPTIONS[args.cost_model].name}")
    print(f"    Base spread: {cost_model.base_spread*100:.1f}%")
    print(f"    Base slippage: {cost_model.base_slippage*100:.1f}%")

    # Polymarket backtests always use the rolling realistic engine

    if args.rolling:
        print(f"\n[4] Running ROLLING REALISTIC backtest ({args.lookback}-month lookback)...")
        max_capital = getattr(args, "max_capital", None)
        if max_capital is not None:
            print(f"    (Capital cap: ${max_capital:,.0f}, Position size: ${args.position_size:,.0f})")
        else:
            print(f"    (Unlimited capital, Position size: ${args.position_size:,.0f})")
        if getattr(args, "poly_max_liquidity_pct", None) is not None:
            print(f"    (Liquidity cap: {args.poly_max_liquidity_pct*100:.1f}% of market cumulative liquidity)")
        resolved_only = getattr(args, "poly_resolved_only", False)
        if resolved_only:
            print("    (Filtering to resolved markets only)")

        result = run_rolling_realistic_backtest(
            trades_df=trades,
            method=args.poly_method,
            role=args.poly_role,
            cost_model=cost_model,
            position_size=args.position_size,
            starting_capital=args.starting_capital,
            min_usd=args.poly_min_usd,
            resolved_only=resolved_only,
            max_capital=max_capital,
            lookback_months=args.lookback,
            resolution_winners=resolution_winners,
            min_trades=args.poly_min_trades,
            min_volume=args.poly_min_volume,
            return_lookback_months=args.poly_return_months,
            return_top_pct=args.poly_return_top_pct,
            max_position_liquidity_pct=args.poly_max_liquidity_pct,
            test_start=args.test_start,
            test_end=args.test_end,
        )

        if result:
            print_realistic_result(result)

            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            safe_name = f"polymarket_rolling_realistic_{args.poly_method}".replace(" ", "_").lower()
            save_trades_csv(result.trades_df, output_dir / f"polymarket_trades_{safe_name}.csv")
            print(f"\nSaved trades: {output_dir}/polymarket_trades_{safe_name}.csv")

            if not args.no_reports:
                report_path = output_dir / f"quantstats_{safe_name}.html"
                report_ok = generate_quantstats_report(
                    result.daily_equity,
                    str(report_path),
                    title=f"Polymarket Whales (Rolling Realistic): {args.poly_method}",
                )
                if report_ok:
                    print(f"Saved QuantStats report: {report_path}")
                else:
                    print("QuantStats report was not generated (insufficient data or missing package).")
        else:
            print("\nNo valid trades found.")
    print("\n" + "=" * 70)
    print("POLYMARKET WHALE RUN COMPLETE")
    print("=" * 70)


def _write_arb_csv(pairs, price_store, output_path: Path) -> None:
    """Write matched pairs with spread (edge) to CSV, sorted by edge descending."""
    import pandas as pd

    rows = []
    for i, p in enumerate(pairs):
        if (i + 1) % 100 == 0 or i == 0:
            print(f"    Computing spreads {i+1}/{len(pairs)}...", flush=True)
        spread_df = price_store.build_spread_series(p.polymarket_id, p.kalshi_ticker)
        if spread_df.empty:
            row = {
                "edge": 0.0,
                "abs_spread": 0.0,
                "spread": 0.0,
                "direction": "",
                "poly_price": None,
                "kalshi_price": None,
                "timestamp": None,
            }
        else:
            last = spread_df.iloc[-1]
            spread = float(last["spread"])
            abs_spread = float(last["abs_spread"])
            direction = "buy_kalshi" if spread > 0 else "buy_poly"
            row = {
                "edge": abs_spread,
                "abs_spread": abs_spread,
                "spread": spread,
                "direction": direction,
                "poly_price": float(last["poly_price"]),
                "kalshi_price": float(last["kalshi_price"]),
                "timestamp": last.get("timestamp_unix"),
            }
        row.update({
            "polymarket_id": p.polymarket_id,
            "kalshi_ticker": p.kalshi_ticker,
            "polymarket_question": p.polymarket_question[:300],
            "kalshi_title": p.kalshi_title[:300],
            "confidence": p.confidence,
            "poly_volume": p.polymarket_volume,
            "kalshi_volume": p.kalshi_volume,
            "category": p.category,
        })
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.sort_values("edge", ascending=False).reset_index(drop=True)
    # Reorder: edge first, then identifiers, then details
    col_order = [
        "edge", "abs_spread", "spread", "direction",
        "polymarket_id", "kalshi_ticker",
        "polymarket_question", "kalshi_title",
        "poly_price", "kalshi_price", "timestamp",
        "confidence", "poly_volume", "kalshi_volume", "category",
    ]
    df = df[[c for c in col_order if c in df.columns]]
    df.to_csv(output_path, index=False)
    print(f"  Wrote {len(df):,} pairs to {output_path} (sorted by edge descending)")


def main():
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--config",
        help="Path to YAML config override (default: config/default.yaml + config/local.yaml)",
    )
    pre_args, _ = pre_parser.parse_known_args()
    config = load_config(pre_args.config)
    require_polars = bool(config.get("backtest", {}).get("require_polars", False))
    if require_polars:
        os.environ["PREDICT_NGIN_FORCE_POLARS"] = "1"

    parser = argparse.ArgumentParser(
        description="Whale Following Strategy Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python run.py
    python run.py --method win_rate_60pct --position-size 250
    python run.py --all-methods
    python run.py --manifold-whales
    python run.py --category ai_tech
        """,
    )

    parser.add_argument(
        "--config",
        help="Path to YAML config override (default: config/default.yaml + config/local.yaml)",
    )

    cfg_backtest = config.get("backtest", {}) or {}
    cfg_whales = config.get("whale_strategy", {}) or {}
    cfg_db = config.get("database", {}) or {}
    cfg_data = config.get("data", {}) or {}
    cfg_poly = cfg_data.get("polymarket", {}) or {}
    cfg_manifold = cfg_data.get("manifold", {}) or {}
    cfg_costs = config.get("costs", {}) or {}

    config_defaults = {}

    if "method" in cfg_whales:
        config_defaults["method"] = cfg_whales["method"]
    if "train_ratio" in cfg_backtest:
        config_defaults["train_ratio"] = cfg_backtest["train_ratio"]
    if "test_days" in cfg_backtest:
        config_defaults["test_days"] = cfg_backtest["test_days"]
    if "test_months" in cfg_backtest:
        config_defaults["test_months"] = cfg_backtest["test_months"]
    if "test_start" in cfg_backtest:
        config_defaults["test_start"] = cfg_backtest["test_start"]
    if "test_end" in cfg_backtest:
        config_defaults["test_end"] = cfg_backtest["test_end"]
    if "default_market" in cfg_backtest:
        default_market = str(cfg_backtest["default_market"]).strip().lower()
        if default_market in ("polymarket", "poly"):
            config_defaults["polymarket_whales"] = True
        elif default_market in ("manifold", "mani"):
            config_defaults["polymarket_whales"] = False
    if "default_position_size" in cfg_backtest:
        config_defaults["position_size"] = cfg_backtest["default_position_size"]
    if "starting_capital" in cfg_backtest:
        config_defaults["starting_capital"] = cfg_backtest["starting_capital"]
    if "output_dir" in cfg_backtest:
        config_defaults["output_dir"] = cfg_backtest["output_dir"]
    if "generate_html_report" in cfg_backtest:
        config_defaults["no_reports"] = not bool(cfg_backtest["generate_html_report"])
    if "lookback_months" in cfg_whales:
        config_defaults["lookback"] = cfg_whales["lookback_months"]
    if "min_trades" in cfg_whales:
        config_defaults["min_trades"] = cfg_whales["min_trades"]
    if "min_volume" in cfg_whales:
        config_defaults["poly_min_volume"] = cfg_whales["min_volume"]
    if "min_signal_usd" in cfg_whales:
        config_defaults["poly_min_usd"] = cfg_whales["min_signal_usd"]
    if "fill_latency_seconds" in cfg_backtest:
        config_defaults["poly_fill_latency"] = cfg_backtest["fill_latency_seconds"]
    if "max_holding_seconds" in cfg_backtest:
        config_defaults["poly_max_holding_seconds"] = cfg_backtest["max_holding_seconds"]
    if "min_liquidity" in cfg_backtest:
        config_defaults["poly_market_min_liquidity"] = cfg_backtest["min_liquidity"]
    if "min_volume" in cfg_backtest:
        config_defaults["poly_market_min_volume"] = cfg_backtest["min_volume"]
    if "max_position_liquidity_pct" in cfg_backtest:
        config_defaults["poly_max_liquidity_pct"] = cfg_backtest["max_position_liquidity_pct"]
    if "path" in cfg_db:
        config_defaults["db_path"] = cfg_db["path"]
    if "polymarket_trades_path" in cfg_db:
        config_defaults["poly_trades_path"] = cfg_db["polymarket_trades_path"]
    if "data_dir" in cfg_manifold:
        config_defaults["data_dir"] = cfg_manifold["data_dir"]
    if "min_volume_24h" in cfg_poly:
        config_defaults["clob_min_volume"] = cfg_poly["min_volume_24h"]
    if "max_markets" in cfg_poly:
        config_defaults["clob_max_markets"] = cfg_poly["max_markets"]
    if "category" in cfg_costs:
        config_defaults["cost_model"] = cfg_costs["category"]

    parser.add_argument(
        "--method",
        choices=list(WHALE_METHODS.keys()),
        default="volume_pct95",
        help="Whale identification method (default: volume_pct95)",
    )
    parser.add_argument(
        "--all-methods",
        action="store_true",
        help="Run all whale identification methods",
    )
    parser.add_argument(
        "--position-size",
        type=float,
        default=100,
        help="Position size in dollars (default: 100)",
    )
    parser.add_argument(
        "--starting-capital",
        type=float,
        default=10000,
        help="Starting capital for Polymarket backtests (default: 10000)",
    )
    parser.add_argument(
        "--max-capital",
        type=float,
        default=None,
        help="Maximum total deployed capital for realistic backtest (default: unlimited)",
    )
    parser.add_argument(
        "--cost-model",
        choices=list(COST_ASSUMPTIONS.keys()),
        default="small",
        help="Cost model assumptions (default: small)",
    )
    parser.add_argument(
        "--category",
        help="Filter to specific market category",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.3,
        help="Train/test split ratio (default: 0.3)",
    )
    parser.add_argument(
        "--test-days",
        type=int,
        help="Use most recent N days as test window (overrides train-ratio)",
    )
    parser.add_argument(
        "--test-months",
        type=int,
        help="Use most recent N months as test window (overrides train-ratio)",
    )
    parser.add_argument(
        "--test-start",
        help="Start date for test window (YYYY-MM-DD, overrides train-ratio)",
    )
    parser.add_argument(
        "--test-end",
        help="End date for test window (YYYY-MM-DD, defaults to most recent date)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=20,
        help="Minimum trades for whale qualification (default: 20)",
    )
    parser.add_argument(
        "--data-dir",
        default="data/manifold",
        help="Directory with Manifold data",
    )
    parser.add_argument(
        "--output-dir",
        default="data/research",
        help="Output directory for reports",
    )
    parser.add_argument(
        "--no-reports",
        action="store_true",
        help="Skip generating HTML reports",
    )
    parser.add_argument(
        "--polymarket-whales",
        dest="polymarket_whales",
        action="store_true",
        default=True,
        help="Run the Polymarket whale strategy (default)",
    )
    parser.add_argument(
        "--manifold-whales",
        dest="polymarket_whales",
        action="store_false",
        help="Run the Manifold whale strategy instead of Polymarket",
    )
    parser.add_argument(
        "--poly-method",
        choices=list(POLY_WHALE_METHODS.keys()),
        default="win_rate_60pct",
        help="Polymarket whale identification method (default: win_rate_60pct)",
    )
    parser.add_argument(
        "--poly-role",
        choices=["maker", "taker"],
        default="maker",
        help="Use maker or taker trades for whale identification (default: maker)",
    )
    parser.add_argument(
        "--poly-min-trades",
        type=int,
        default=20,
        help="Minimum trades for Polymarket whale qualification (default: 20)",
    )
    parser.add_argument(
        "--poly-min-volume",
        type=float,
        default=1000,
        help="Minimum total volume for Polymarket whale qualification (default: 1000)",
    )
    parser.add_argument(
        "--poly-return-months",
        type=int,
        default=3,
        help="Lookback months for top_returns whale method (default: 3)",
    )
    parser.add_argument(
        "--poly-return-top-pct",
        type=float,
        default=10.0,
        help="Top percent to keep for top_returns whale method (default: 10)",
    )
    parser.add_argument(
        "--poly-min-usd",
        type=float,
        default=100,
        help="Minimum whale trade size to trigger a signal (default: 100)",
    )
    parser.add_argument(
        "--poly-max-liquidity-pct",
        type=float,
        default=0.05,
        help="Max position size as fraction of market cumulative liquidity (default: 0.05)",
    )
    parser.add_argument(
        "--poly-fill-latency",
        type=int,
        default=60,
        help="Fill latency in seconds for Polymarket backtest (default: 60)",
    )
    parser.add_argument(
        "--poly-max-holding-seconds",
        type=int,
        default=None,
        help="Max holding period in seconds for Polymarket backtest",
    )
    parser.add_argument(
        "--poly-market-min-liquidity",
        type=float,
        default=0,
        help="Minimum market liquidity for Polymarket backtest (default: 0)",
    )
    parser.add_argument(
        "--poly-market-min-volume",
        type=float,
        default=0,
        help="Minimum market volume for Polymarket backtest (default: 0)",
    )
    parser.add_argument(
        "--poly-start-date",
        help="Start date for Polymarket trades (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--poly-end-date",
        help="End date for Polymarket trades (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--poly-limit",
        type=int,
        help="Maximum number of Polymarket trades to load",
    )
    parser.add_argument(
        "--poly-resolved-only",
        action="store_true",
        help="Only trade Polymarket markets that have resolved (final price near 0 or 1)",
    )
    parser.add_argument(
        "--poly-realistic",
        action="store_true",
        help="(Deprecated) Polymarket backtests are always realistic",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch fresh data from APIs before running",
    )
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Only fetch data, don't run backtest",
    )
    parser.add_argument(
        "--analyze-sizes",
        action="store_true",
        help="Analyze different position sizes ($100-$10k)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100000,
        help="Total capital for position size analysis (default: 100000)",
    )
    parser.add_argument(
        "--rolling",
        action="store_true",
        help="Run rolling monthly whale identification and backtest",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=3,
        help="Months of lookback for rolling whale identification (default: 3)",
    )
    parser.add_argument(
        "--include-open",
        action="store_true",
        help="Include unresolved markets (mark-to-market with current probability)",
    )
    parser.add_argument(
        "--fetch-clob",
        action="store_true",
        help="Fetch Polymarket CLOB price history data",
    )
    parser.add_argument(
        "--clob-min-volume",
        type=float,
        default=100000,
        help="Minimum 24hr volume for CLOB markets (default: 100000)",
    )
    parser.add_argument(
        "--clob-max-markets",
        type=int,
        default=500,
        help="Maximum markets to fetch CLOB data for (default: 500, ignored if --clob-markets-parquet provided)",
    )
    parser.add_argument(
        "--clob-markets-parquet",
        type=str,
        default=None,
        help="Path to markets.parquet file to use instead of fetching from API (default: None)",
    )
    parser.add_argument(
        "--fetch-kalshi",
        action="store_true",
        help="Fetch Kalshi events, markets, trades, and price data",
    )
    parser.add_argument(
        "--kalshi-min-volume",
        type=float,
        default=100000,
        help="Minimum volume (contracts) for Kalshi markets (default: 100000)",
    )
    parser.add_argument(
        "--kalshi-no-trades",
        action="store_true",
        help="Skip fetching Kalshi trades (only events, markets, prices)",
    )
    parser.add_argument(
        "--kalshi-no-prices",
        action="store_true",
        help="Skip fetching Kalshi candlestick price data",
    )
    parser.add_argument(
        "--kalshi-workers",
        type=int,
        default=20,
        help="Number of parallel workers for Kalshi fetch (default: 20)",
    )
    parser.add_argument(
        "--kalshi-max-pages",
        type=int,
        default=0,
        help="Max event pages to scan (0=all, ~15 covers popular markets, default: 0)",
    )
    parser.add_argument(
        "--kalshi-flush-interval",
        type=int,
        default=10,
        help="Flush Kalshi data to disk every N markets (default: 10, reduce for low memory)",
    )
    parser.add_argument(
        "--clob-flush-interval",
        type=int,
        default=10,
        help="Flush Polymarket CLOB data to disk every N markets (default: 10, reduce for low memory)",
    )
    parser.add_argument(
        "--clob-max-memory-rows",
        type=int,
        default=100000,
        help="Max rows to accumulate before flush for Polymarket (default: 100000)",
    )
    # Cross-platform arbitrage
    parser.add_argument(
        "--arbitrage",
        action="store_true",
        help="Run cross-platform arbitrage backtest (Polymarket vs Kalshi)",
    )
    parser.add_argument(
        "--arb-entry-spread",
        type=float,
        default=0.05,
        help="Minimum spread to enter arb position (default: 0.05)",
    )
    parser.add_argument(
        "--arb-exit-spread",
        type=float,
        default=0.01,
        help="Close arb when spread narrows to this (default: 0.01)",
    )
    parser.add_argument(
        "--arb-show-matches",
        action="store_true",
        help="Show matched markets and exit (no backtest)",
    )
    parser.add_argument(
        "--arb-max-kalshi",
        type=int,
        default=50_000,
        help="Max Kalshi markets to use for matching (default: 50000, avoids OOM)",
    )
    parser.add_argument(
        "--arb-max-poly",
        type=int,
        default=None,
        help="Max Polymarket markets for matching (default: all). Use e.g. 20000 for faster runs.",
    )
    parser.add_argument(
        "--arb-output-csv",
        type=str,
        default=None,
        metavar="PATH",
        help="Write matched pairs with spread (edge) to CSV, sorted by edge descending",
    )
    parser.add_argument(
        "--build-db",
        action="store_true",
        help="Build SQLite database from JSON data files",
    )
    parser.add_argument(
        "--import-poly-trades",
        action="store_true",
        help="Import processed Polymarket trades CSV into the database",
    )
    parser.add_argument(
        "--poly-trades-path",
        default="data/poly_data/processed/trades.csv",
        help="Path to processed Polymarket trades CSV",
    )
    parser.add_argument(
        "--db-path",
        default="data/prediction_markets.db",
        help="[DEPRECATED] SQLite path. Use data/research parquet instead.",
    )

    # New research pipeline options
    parser.add_argument(
        "--experiment-name",
        help="Name for experiment tracking (enables experiment logging)",
    )
    parser.add_argument(
        "--track-experiment",
        action="store_true",
        help="Enable experiment tracking for this run",
    )
    parser.add_argument(
        "--validate-data",
        action="store_true",
        help="Run data quality checks before backtest",
    )
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run walk-forward validation instead of single backtest",
    )
    parser.add_argument(
        "--n-splits",
        type=int,
        default=5,
        help="Number of splits for walk-forward validation (default: 5)",
    )
    parser.add_argument(
        "--capture-liquidity",
        action="store_true",
        help="Capture liquidity snapshots from orderbooks",
    )
    parser.add_argument(
        "--liquidity-interval",
        type=int,
        default=5,
        help="Interval in minutes for liquidity capture (default: 5)",
    )
    parser.add_argument(
        "--generate-plots",
        action="store_true",
        help="Generate distribution plots for analysis",
    )

    if config_defaults:
        parser.set_defaults(**config_defaults)

    args = parser.parse_args()
    if args.polymarket_whales:
        if "--rolling" not in sys.argv:
            args.rolling = True
    split_mode = sum(
        value is not None for value in (args.test_days, args.test_months, args.test_start)
    )
    if split_mode > 1:
        parser.error("Use only one of --test-days, --test-months, or --test-start.")
    if args.test_end is not None and split_mode == 0:
        parser.error("--test-end requires --test-start, --test-days, or --test-months.")
    if args.test_days is not None and args.test_days <= 0:
        parser.error("--test-days must be positive.")
    if args.test_months is not None and args.test_months <= 0:
        parser.error("--test-months must be positive.")

    print("=" * 70)
    print("WHALE FOLLOWING STRATEGY BACKTEST")
    print("=" * 70)

    # Handle database build (DEPRECATED: use data/research parquet instead)
    if args.build_db:
        print("\n⚠️  DEPRECATED: --build-db is deprecated. Use data/research parquet instead.")
        print("   Database generation will be removed in a future version.\n")
        print("[0] Building SQLite database from JSON files...")
        db = build_database(
            db_path=args.db_path,
            import_polymarket=True,
            import_manifold=True,
            import_polymarket_trades=args.import_poly_trades,
            polymarket_trades_path=args.poly_trades_path,
        )
        db.close()
        return

    # Handle Kalshi-only fetch early (light imports, no need for full trading package)
    if args.fetch_kalshi and not args.fetch and not args.fetch_only and not args.fetch_clob:
        print("\n[0] Fetching Kalshi data from API...")
        from trading.data_modules.fetcher import DataFetcher as _KalshiFetcher
        fetcher = _KalshiFetcher(str(Path(args.data_dir).parent))
        fetcher.fetch_kalshi_data(
            min_volume=args.kalshi_min_volume,
            fetch_trades=not args.kalshi_no_trades,
            fetch_prices=not args.kalshi_no_prices,
            workers=args.kalshi_workers,
            max_event_pages=args.kalshi_max_pages,
            flush_interval=args.kalshi_flush_interval,
        )
        print("\nKalshi fetch complete.")
        return

    # Handle fetch options
    if args.fetch or args.fetch_only or args.fetch_clob or args.fetch_kalshi:
        print("\n[0] Fetching data from APIs...")
        fetcher = DataFetcher(str(Path(args.data_dir).parent))

        if args.fetch_kalshi:
            # Fetch Kalshi data via events endpoint (fast discovery)
            fetcher.fetch_kalshi_data(
                min_volume=args.kalshi_min_volume,
                fetch_trades=not args.kalshi_no_trades,
                fetch_prices=not args.kalshi_no_prices,
                workers=args.kalshi_workers,
                max_event_pages=args.kalshi_max_pages,
                flush_interval=args.kalshi_flush_interval,
            )
            if not args.fetch and not args.fetch_only and not args.fetch_clob:
                print("\nKalshi fetch complete.")
                return

        if args.fetch_clob:
            # Only fetch CLOB data
            fetcher.fetch_polymarket_clob_data(
                min_volume=args.clob_min_volume,
                max_markets=args.clob_max_markets if not args.clob_markets_parquet else None,
                markets_parquet_path=args.clob_markets_parquet,
                flush_interval=args.clob_flush_interval,
                max_memory_rows=args.clob_max_memory_rows,
            )
            if not args.fetch and not args.fetch_only:
                print("\nCLOB fetch complete.")
                return
        elif not args.fetch_kalshi:
            fetcher.fetch_all()

        if args.fetch_only:
            print("\nFetch complete. Use 'python run.py' to run backtest.")
            return

    # Handle cross-platform arbitrage
    if args.arbitrage:
        print("\n[ARB] Cross-Platform Arbitrage Backtest")
        print("      Polymarket vs Kalshi")

        from src.trading.arbitrage import (
            MarketMatcher,
            CrossPlatformPriceStore,
            ArbitrageStrategy,
            ArbitrageBacktestConfig,
            run_arbitrage_backtest,
            print_arbitrage_result,
        )
        from src.trading.arbitrage.arbitrage_strategy import PlatformFees

        data_root = Path(args.data_dir).parent

        # Load markets
        poly_path = data_root / "polymarket" / "markets.parquet"
        kalshi_path = data_root / "kalshi" / "markets.parquet"

        if not poly_path.exists() or not kalshi_path.exists():
            missing = []
            if not poly_path.exists():
                missing.append("Polymarket (run --fetch-clob)")
            if not kalshi_path.exists():
                missing.append("Kalshi (run --fetch-kalshi)")
            print(f"  Missing data: {', '.join(missing)}")
            return

        import pandas as pd
        poly_markets = pd.read_parquet(poly_path)
        kalshi_markets = pd.read_parquet(kalshi_path)
        print(f"  Polymarket: {len(poly_markets):,} markets")
        print(f"  Kalshi:     {len(kalshi_markets):,} markets")

        if args.arb_max_poly and len(poly_markets) > args.arb_max_poly:
            vol_col = "volume" if "volume" in poly_markets.columns else "volume24hr"
            if vol_col in poly_markets.columns:
                vol = pd.to_numeric(poly_markets[vol_col], errors="coerce").fillna(0)
                poly_markets = poly_markets.loc[vol.nlargest(args.arb_max_poly).index].reset_index(drop=True)
            else:
                poly_markets = poly_markets.head(args.arb_max_poly)
            print(f"  Polymarket (top {args.arb_max_poly:,} by volume): {len(poly_markets):,} markets")

        # Limit Kalshi to high-volume markets (TF-IDF on 17M+ markets needs ~5TB RAM)
        arb_max_kalshi = args.arb_max_kalshi
        if len(kalshi_markets) > arb_max_kalshi:
            vol_col = "volume" if "volume" in kalshi_markets.columns else "volume_24h"
            if vol_col in kalshi_markets.columns:
                vol = pd.to_numeric(kalshi_markets[vol_col], errors="coerce").fillna(0)
                kalshi_markets = kalshi_markets.loc[vol.nlargest(arb_max_kalshi).index].reset_index(drop=True)
            else:
                kalshi_markets = kalshi_markets.head(arb_max_kalshi)
            print(f"  Kalshi (top {arb_max_kalshi:,} by volume): {len(kalshi_markets):,} markets")

        matcher = MarketMatcher()
        pairs = matcher.match(poly_markets, kalshi_markets)
        print(f"  Matched pairs: {len(pairs)}")

        if not pairs:
            print("  No matched pairs found.")
            return

        price_store = CrossPlatformPriceStore(
            polymarket_prices_dir=str(data_root / "polymarket" / "prices"),
            kalshi_prices_dir=str(data_root / "kalshi" / "prices"),
        )

        if args.arb_output_csv:
            _write_arb_csv(pairs, price_store, Path(args.arb_output_csv))

        if args.arb_show_matches:
            for p in pairs[:20]:
                print(f"    [{p.confidence:.3f}] {p.polymarket_question[:45]} = {p.kalshi_title[:45]}")
            return

        strategy = ArbitrageStrategy(
            entry_spread=args.arb_entry_spread,
            exit_spread=args.arb_exit_spread,
        )
        signals = strategy.generate_signals(pairs, price_store)
        print(f"  Signals: {len(signals)}")

        if signals:
            result = run_arbitrage_backtest(
                signals=signals,
                price_store=price_store,
                config=ArbitrageBacktestConfig(
                    position_size=float(getattr(args, "position_size", 100)),
                    starting_capital=float(getattr(args, "starting_capital", 10000)),
                ),
            )
            result.pairs_analyzed = len(pairs)
            print_arbitrage_result(result)
        else:
            print("  No arbitrage opportunities found.")
        return

    # Handle liquidity capture
    if args.capture_liquidity:
        print("\n[0] Starting liquidity capture...")
        try:
            from src.collection.liquidity import LiquidityCollector
            import asyncio

            collector = LiquidityCollector()

            # Fetch active markets first
            fetcher = DataFetcher(str(Path(args.data_dir).parent))
            print("    Fetching active markets...")
            fetcher.fetch_polymarket_markets(limit=args.clob_max_markets)

            # Load markets for capture
            import json
            markets_dir = Path(args.data_dir).parent / "polymarket"
            markets = []
            for f in markets_dir.glob("markets_*.json"):
                with open(f) as mf:
                    markets.extend(json.load(mf))

            print(f"    Capturing snapshots every {args.liquidity_interval} minutes...")
            print("    Press Ctrl+C to stop.")
            collector.run_capture_loop(
                markets=markets,
                interval_minutes=args.liquidity_interval,
                min_volume=args.clob_min_volume,
                max_markets=args.clob_max_markets,
            )
        except KeyboardInterrupt:
            print("\n    Liquidity capture stopped.")
        except ImportError as e:
            print(f"    Error: {e}")
        return

    if args.polymarket_whales:
        _run_polymarket_whales(args)
        return

    # Load data
    print("\n[1] Loading data...")
    df = load_manifold_data(args.data_dir)
    print(f"    Loaded {len(df):,} bets")
    print(f"    Date range: {df['datetime'].min()} to {df['datetime'].max()}")

    markets_df = load_markets(args.data_dir)
    print(f"    Loaded {len(markets_df):,} markets")

    # Build resolution map
    print("\n[2] Building resolution map...")
    resolution_data = build_resolution_map(markets_df, include_open=args.include_open)
    resolved_count = sum(1 for d in resolution_data.values() if d.get("is_resolved", True))
    open_count = len(resolution_data) - resolved_count
    print(f"    Resolved YES/NO markets: {resolved_count:,}")
    if args.include_open:
        print(f"    Open/unresolved markets: {open_count:,}")

    # Add categories
    for mid, data in resolution_data.items():
        data["category"] = categorize_market(data.get("question", ""))

    # Data validation (if requested)
    if args.validate_data:
        print("\n[2b] Running data quality checks...")
        try:
            from src.validation.quality import DataQualityMonitor
            monitor = DataQualityMonitor(fail_on_error=False)
            monitor.add_default_checks(date_column="datetime")
            report = monitor.run_all_checks(df, "bets data")
            print(report.summary())
            if report.has_errors():
                print("    Warning: Data quality issues found. See report above.")
        except ImportError as e:
            print(f"    Skipping validation: {e}")

    # Train/test split
    print("\n[3] Train/test split...")
    train_df, test_df = _split_train_test(df, args)
    _print_split_summary(train_df, test_df, args, label="bets")

    # Cost model
    cost_model = CostModel.from_assumptions(args.cost_model)
    print(f"\n[4] Cost model: {COST_ASSUMPTIONS[args.cost_model].name}")
    print(f"    Base spread: {cost_model.base_spread*100:.1f}%")
    print(f"    Base slippage: {cost_model.base_slippage*100:.1f}%")

    # Determine methods to run
    methods = list(WHALE_METHODS.keys()) if args.all_methods else [args.method]

    # Run backtests
    print(f"\n[5] Running backtests...")
    results = {}

    for method in methods:
        print(f"\n    Method: {method} - {WHALE_METHODS[method]}")

        # Identify whales
        whales = identify_whales(
            train_df,
            resolution_data,
            method,
            min_trades=args.min_trades,
        )
        print(f"    Identified {len(whales)} whales")

        # Run backtest
        result = run_backtest(
            test_df=test_df,
            whale_set=whales,
            resolution_data=resolution_data,
            strategy_name=f"{method}",
            cost_model=cost_model,
            position_size=args.position_size,
            category_filter=args.category,
            include_open=args.include_open,
        )

        if result:
            results[method] = result
            print_result(result)
        else:
            print("    No valid trades")
            results[method] = None

    # Position size analysis
    if args.analyze_sizes:
        print(f"\n[6] Position size analysis...")
        # Use the best method for size analysis
        best_method = "win_rate_60pct" if "win_rate_60pct" in methods else methods[0]
        whales = identify_whales(
            train_df,
            resolution_data,
            best_method,
            min_trades=args.min_trades,
        )

        size_df = run_position_size_analysis(
            test_df=test_df,
            whale_set=whales,
            resolution_data=resolution_data,
            total_capital=args.capital,
            include_open=args.include_open,
        )
        print_position_size_analysis(size_df, args.capital)

        # Save to CSV
        size_df.to_csv(Path(args.output_dir) / "position_size_analysis.csv", index=False)
        print(f"\nSaved: {args.output_dir}/position_size_analysis.csv")

    # Rolling whale identification
    if args.rolling:
        step = 7 if args.analyze_sizes else 6
        print(f"\n[{step}] Rolling monthly whale identification...")

        rolling_df = run_rolling_backtest(
            df=df,
            resolution_data=resolution_data,
            method=args.method,
            lookback_months=args.lookback,
            position_size=args.position_size,
            cost_model=cost_model,
            include_open=args.include_open,
        )

        # Save to CSV
        if len(rolling_df) > 0:
            rolling_df.to_csv(Path(args.output_dir) / "rolling_monthly_results.csv", index=False)
            print(f"\nSaved: {args.output_dir}/rolling_monthly_results.csv")

    # Generate reports
    if not args.no_reports:
        step = 8 if args.rolling else (7 if args.analyze_sizes else 6)
        print(f"\n[{step}] Generating reports...")
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        generate_all_reports(results, args.output_dir)

    # Generate distribution plots
    if args.generate_plots:
        step = 9 if args.rolling else (8 if args.analyze_sizes else 7)
        print(f"\n[{step}] Generating analysis plots...")
        try:
            from src.analysis.distributions import DistributionPlotter
            plotter = DistributionPlotter(output_dir=args.output_dir)

            # Get trades from first successful result
            for method, result in results.items():
                if result and hasattr(result, "trades_df") and not result.trades_df.empty:
                    report_path = plotter.generate_analysis_report(
                        result.trades_df,
                        dimensions=["category"] if "category" in result.trades_df.columns else [],
                    )
                    print(f"    Plots saved to: {report_path}")
                    break
        except ImportError as e:
            print(f"    Skipping plots: {e}")

    # Experiment tracking
    if args.track_experiment or args.experiment_name:
        step = 10 if args.rolling else (9 if args.analyze_sizes else 8)
        print(f"\n[{step}] Logging experiment...")
        try:
            from src.experiments.tracker import ExperimentTracker
            tracker = ExperimentTracker()

            exp_name = args.experiment_name or f"backtest_{args.method}"
            parameters = {
                "method": args.method,
                "position_size": args.position_size,
                "cost_model": args.cost_model,
                "train_ratio": args.train_ratio,
                "lookback": args.lookback,
            }

            with tracker.run(exp_name, parameters) as run:
                # Log metrics from results
                for method, result in results.items():
                    if result:
                        tracker.log_metrics(run.run_id, {
                            f"{method}_win_rate": result.win_rate,
                            f"{method}_sharpe": result.sharpe_ratio,
                            f"{method}_net_pnl": result.total_net_pnl,
                            f"{method}_trades": result.total_trades,
                        })

                # Log output files as artifacts
                output_path = Path(args.output_dir)
                for csv_file in output_path.glob("*.csv"):
                    tracker.log_artifact(run.run_id, csv_file.stem, str(csv_file))

            print(f"    Experiment logged: {run.run_id}")
        except ImportError as e:
            print(f"    Skipping experiment tracking: {e}")

    print("\n" + "=" * 70)
    print("BACKTEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()

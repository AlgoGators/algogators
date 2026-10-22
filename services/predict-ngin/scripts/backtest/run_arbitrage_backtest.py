#!/usr/bin/env python3
"""
Cross-platform arbitrage backtest: Polymarket vs Kalshi.

Matches equivalent markets across both platforms, identifies spread
opportunities, and backtests a convergence-based arbitrage strategy.

Two modes:

  --mode signals (default, legacy): TF-IDF matcher (now with hard contract
      checks) -> z-score spread signals on daily candles -> signal-driven engine.

  --mode resolution (recommended): rule-based matcher (Fed / election / games /
      futures) -> hourly as-of observations from offset-corrected Kalshi trades
      and Polymarket prices -> hold-to-resolution arb with per-fill fees,
      half-spreads, volume caps and capital lockup (resolution_arb.py).

Usage:
    python scripts/backtest/run_arbitrage_backtest.py --mode resolution --capital 5000
    python scripts/backtest/run_arbitrage_backtest.py
    python scripts/backtest/run_arbitrage_backtest.py --entry-spread 0.08 --position-size 200
    python scripts/backtest/run_arbitrage_backtest.py --min-similarity 0.3 --show-matches
"""

from __future__ import annotations

import argparse
import json
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
import time
from pathlib import Path

# Project root setup
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.append(str(_project_root))  # predict_ngin (appended: root config/ must not shadow src/config.py)

import pandas as pd


def _load_config():
    """Load configuration (mirrors pattern from other backtest scripts)."""
    try:
        from config import get_config
        return get_config()
    except Exception:
        return None


def _load_markets(platform: str, data_dir: Path) -> pd.DataFrame:
    """Load market metadata from parquet."""
    if platform == "polymarket":
        markets_path = data_dir / "polymarket" / "markets.parquet"
        if not markets_path.exists():
            # Check alternative location
            markets_path = data_dir / "parquet" / "markets" / "markets.parquet"
        if not markets_path.exists():
            # Try to find any markets parquet
            for p in (data_dir / "polymarket").glob("*.parquet"):
                if "market" in p.stem.lower():
                    markets_path = p
                    break
    elif platform == "kalshi":
        markets_path = data_dir / "kalshi" / "markets.parquet"
    else:
        raise ValueError(f"Unknown platform: {platform}")

    if not markets_path.exists():
        return pd.DataFrame()

    return pd.read_parquet(markets_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-platform arbitrage backtest: Polymarket vs Kalshi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic run with defaults
    python scripts/backtest/run_arbitrage_backtest.py

    # Tighter spreads, bigger positions
    python scripts/backtest/run_arbitrage_backtest.py --entry-spread 0.03 --position-size 500

    # Show matched markets without running backtest
    python scripts/backtest/run_arbitrage_backtest.py --show-matches --min-confidence 0.5

    # Custom data directories
    python scripts/backtest/run_arbitrage_backtest.py --data-dir ./my_data
        """,
    )

    # Data paths
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help="Root data directory (default: data)",
    )

    # Market matching parameters
    match_group = parser.add_argument_group("Market Matching")
    match_group.add_argument(
        "--min-similarity",
        type=float,
        default=0.25,
        help="Minimum TF-IDF cosine similarity for matching (default: 0.25)",
    )
    match_group.add_argument(
        "--min-confidence",
        type=float,
        default=0.40,
        help="Minimum overall match confidence (default: 0.40)",
    )
    match_group.add_argument(
        "--max-pairs",
        type=int,
        default=200,
        help="Maximum number of matched pairs (default: 200)",
    )
    match_group.add_argument(
        "--max-kalshi",
        type=int,
        default=50000,
        help="Cap Kalshi markets by volume to avoid OOM (default: 50000)",
    )
    match_group.add_argument(
        "--max-poly",
        type=int,
        default=None,
        help="Cap Polymarket markets by volume (default: None)",
    )
    match_group.add_argument(
        "--show-matches",
        action="store_true",
        help="Show matched markets and exit (no backtest)",
    )

    # Strategy parameters
    strat_group = parser.add_argument_group("Strategy")
    strat_group.add_argument(
        "--entry-spread",
        type=float,
        default=0.05,
        help="Minimum spread to enter arb position (default: 0.05 = 5 cents)",
    )
    strat_group.add_argument(
        "--exit-spread",
        type=float,
        default=0.01,
        help="Close when spread narrows to this (default: 0.01)",
    )
    strat_group.add_argument(
        "--min-profit",
        type=float,
        default=0.02,
        help="Minimum expected profit after fees (default: 0.02)",
    )
    strat_group.add_argument(
        "--z-threshold",
        type=float,
        default=1.5,
        help="Z-score threshold for entry signals (default: 1.5)",
    )
    strat_group.add_argument(
        "--lookback",
        type=int,
        default=30,
        help="Rolling lookback periods for spread stats (default: 30)",
    )
    strat_group.add_argument(
        "--max-hold",
        type=int,
        default=None,
        help="Maximum holding periods before forced exit (default: None)",
    )
    strat_group.add_argument(
        "--resample-freq",
        type=str,
        default="1D",
        help="Price resampling frequency (default: 1D = daily)",
    )

    # Backtest parameters
    bt_group = parser.add_argument_group("Backtest")
    bt_group.add_argument(
        "--position-size",
        type=float,
        default=100.0,
        help="Position size per side in USD (default: 100)",
    )
    bt_group.add_argument(
        "--starting-capital",
        type=float,
        default=10000.0,
        help="Starting capital in USD (default: 10000)",
    )
    bt_group.add_argument(
        "--max-positions",
        type=int,
        default=20,
        help="Maximum concurrent arb positions (default: 20)",
    )
    bt_group.add_argument(
        "--slippage-bps",
        type=float,
        default=50.0,
        help="Slippage in basis points per side (default: 50 = 0.5%%)",
    )

    # Fee overrides
    fee_group = parser.add_argument_group("Fees / execution")
    fee_group.add_argument("--kalshi-fee-coef", type=float, default=0.07,
                           help="Kalshi taker fee coefficient: ceil_cent(coef*C*P*(1-P)) per fill (default 0.07)")
    fee_group.add_argument("--kalshi-maker-coef", type=float, default=0.0175,
                           help="Kalshi maker fee coefficient (default 0.0175)")
    fee_group.add_argument("--no-poly-fees", action="store_true",
                           help="Disable Polymarket taker fees (category rates otherwise)")
    fee_group.add_argument("--kalshi-half-spread", type=float, default=0.01,
                           help="Half-spread paid per Kalshi fill when only last/close prices exist (default 0.01)")
    fee_group.add_argument("--poly-half-spread", type=float, default=0.0075,
                           help="Half-spread paid per Polymarket fill (default 0.0075)")
    fee_group.add_argument("--volume-cap-frac", type=float, default=0.05,
                           help="Max size as fraction of contemporaneous volume (default 0.05)")
    fee_group.add_argument("--poly-profit-fee", type=float, default=None, help=argparse.SUPPRESS)
    fee_group.add_argument("--kalshi-profit-fee", type=float, default=None, help=argparse.SUPPRESS)

    res_group = parser.add_argument_group("Resolution mode")
    res_group.add_argument("--mode", choices=["signals", "resolution"], default="signals")
    res_group.add_argument("--capital", type=float, default=5000.0, help="Total capital (resolution mode)")
    res_group.add_argument("--max-per-pair", type=float, default=500.0, help="Max capital locked per pair")
    res_group.add_argument("--min-edge", type=float, default=0.005,
                           help="Min net edge per contract pair after fees/spreads (default 0.005)")
    res_group.add_argument("--settle-lag-hours", type=float, default=12.0,
                           help="Hours after outcome known until both venues pay out (default 12)")
    res_group.add_argument("--start", default="2024-05", help="First month (YYYY-MM)")
    res_group.add_argument("--end", default="2026-01", help="Last month (YYYY-MM)")
    res_group.add_argument("--include-inplay", action="store_true", help="Include in-play sports/election hours")
    res_group.add_argument("--kalshi-trades-glob", default=None,
                           help="Offset-corrected Kalshi trades parquet glob (default: the full tape via "
                                "predict_ngin.tapes.kalshi)")
    res_group.add_argument("--cache-dir", default="backtests/arbitrage/cache",
                           help="Cache for extracted per-pair data")
    res_group.add_argument("--pm-markets-cache", default=None,
                           help="Optional parquet of PM markets with price data (id, question, slug, end_date, outcome_prices)")
    res_group.add_argument("--kalshi-tickers-cache", default=None,
                           help="Optional parquet with a 'ticker' column (Kalshi universe)")
    res_group.add_argument("--pm-prices-cache", default=None, help="Optional parquet (market_id, ts, price)")
    res_group.add_argument("--pm-hourly-vol-cache", default=None, help="Optional parquet (market_id, hr, usd)")
    res_group.add_argument("--no-sensitivities", action="store_true")

    # Output
    parser.add_argument(
        "--output-dir",
        type=str,
        default="backtests/arbitrage",
        help="Output directory for results (default: backtests/arbitrage)",
    )
    parser.add_argument(
        "--save-matches",
        type=str,
        default=None,
        help="Save matched pairs to CSV (optional)",
    )

    args = parser.parse_args()
    t0 = time.time()
    data_dir = Path(args.data_dir)
    if args.poly_profit_fee is not None or args.kalshi_profit_fee is not None:
        print("  WARNING: --*-profit-fee are deprecated and ignored: Kalshi charges "
              "ceil_cent(0.07*C*P*(1-P)) per fill, not a profit fee. Use --kalshi-fee-coef.")
    if args.mode == "resolution":
        return run_resolution_mode(args)

    print("=" * 65)
    print("CROSS-PLATFORM ARBITRAGE BACKTEST")
    print("Polymarket vs Kalshi")
    print("=" * 65)

    # ====================================================================
    # Step 1: Load market metadata
    # ====================================================================
    print("\n[1/4] Loading market metadata...")

    poly_markets = _load_markets("polymarket", data_dir)
    kalshi_markets = _load_markets("kalshi", data_dir)

    # Cap by volume to avoid OOM (17M Kalshi markets = huge TF-IDF)
    if args.max_poly is not None and len(poly_markets) > args.max_poly:
        vol_col = "volume" if "volume" in poly_markets.columns else "volume24hr"
        if vol_col in poly_markets.columns:
            vol = pd.to_numeric(poly_markets[vol_col], errors="coerce").fillna(0)
            poly_markets = poly_markets.loc[vol.nlargest(args.max_poly).index].reset_index(drop=True)
        else:
            poly_markets = poly_markets.head(args.max_poly)
        print(f"  Polymarket capped to top {args.max_poly:,} by volume")
    if args.max_kalshi and len(kalshi_markets) > args.max_kalshi:
        vol_col = "volume" if "volume" in kalshi_markets.columns else "volume_24h"
        if vol_col in kalshi_markets.columns:
            vol = pd.to_numeric(kalshi_markets[vol_col], errors="coerce").fillna(0)
            kalshi_markets = kalshi_markets.loc[vol.nlargest(args.max_kalshi).index].reset_index(drop=True)
        else:
            kalshi_markets = kalshi_markets.head(args.max_kalshi)
        print(f"  Kalshi capped to top {args.max_kalshi:,} by volume")

    if poly_markets.empty:
        print("  ERROR: No Polymarket markets found.")
        print(f"  Expected: {data_dir / 'polymarket' / 'markets.parquet'}")
        print("  Run: python run.py --fetch-clob")
        return 1

    if kalshi_markets.empty:
        print("  ERROR: No Kalshi markets found.")
        print(f"  Expected: {data_dir / 'kalshi' / 'markets.parquet'}")
        print("  Run: python run.py --fetch-kalshi --kalshi-min-volume 100000")
        return 1

    print(f"  Polymarket: {len(poly_markets):,} markets")
    print(f"  Kalshi:     {len(kalshi_markets):,} markets")

    # ====================================================================
    # Step 2: Match markets across platforms
    # ====================================================================
    print("\n[2/4] Matching markets across platforms...")

    from trading.arbitrage.market_matcher import MarketMatcher

    matcher = MarketMatcher(
        min_similarity=args.min_similarity,
        min_confidence=args.min_confidence,
    )

    pairs = matcher.match(poly_markets, kalshi_markets, max_pairs=args.max_pairs)
    print(f"  Found {len(pairs):,} matched pairs")

    if not pairs:
        print("  No matches found. Try lowering --min-similarity or --min-confidence.")
        return 1

    # Display top matches
    print(f"\n  Top matches:")
    for p in pairs[:10]:
        print(f"    [{p.confidence:.3f}] {p.polymarket_question[:40]}")
        print(f"             = {p.kalshi_title[:40]}")

    if args.save_matches:
        matches_df = matcher.match_to_dataframe(
            poly_markets, kalshi_markets, max_pairs=args.max_pairs
        )
        matches_df.to_csv(args.save_matches, index=False)
        print(f"\n  Saved {len(matches_df)} matches to {args.save_matches}")

    if args.show_matches:
        print("\n  --show-matches: exiting without backtest")
        return 0

    # ====================================================================
    # Step 3: Generate arbitrage signals
    # ====================================================================
    print("\n[3/4] Generating arbitrage signals...")

    from trading.arbitrage.cross_platform_price_store import CrossPlatformPriceStore
    from trading.arbitrage.arbitrage_strategy import ArbitrageStrategy

    price_store = CrossPlatformPriceStore(
        polymarket_prices_dir=str(data_dir / "polymarket" / "prices"),
        kalshi_prices_dir=str(data_dir / "kalshi" / "prices"),
    )

    poly_avail, kalshi_avail = price_store.available()
    print(f"  Polymarket prices: {'available' if poly_avail else 'NOT FOUND'}")
    print(f"  Kalshi prices:     {'available' if kalshi_avail else 'NOT FOUND'}")

    if not poly_avail or not kalshi_avail:
        print("\n  ERROR: Price data missing for one or both platforms.")
        if not poly_avail:
            print("  Run: python run.py --fetch-clob")
        if not kalshi_avail:
            print("  Run: python run.py --fetch-kalshi --kalshi-min-volume 100000")
        return 1

    from trading.arbitrage.fees import FeeModel

    fee_model = FeeModel(
        kalshi_taker_coef=args.kalshi_fee_coef,
        kalshi_maker_coef=args.kalshi_maker_coef,
        poly_fees_enabled=not args.no_poly_fees,
    )

    strategy = ArbitrageStrategy(
        entry_spread=args.entry_spread,
        exit_spread=args.exit_spread,
        min_expected_profit=args.min_profit,
        lookback_periods=args.lookback,
        z_score_threshold=args.z_threshold,
        max_holding_periods=args.max_hold,
        fee_model=fee_model,
        kalshi_half_spread=args.kalshi_half_spread,
        poly_half_spread=args.poly_half_spread,
    )

    print(f"  Strategy parameters:")
    for k, v in strategy.get_parameters().items():
        if not isinstance(v, dict):
            print(f"    {k}: {v}")

    signals = strategy.generate_signals(
        pairs=pairs,
        price_store=price_store,
        resample_freq=args.resample_freq,
    )

    entry_signals = [s for s in signals if s.signal_type == "entry"]
    exit_signals = [s for s in signals if s.signal_type == "exit"]
    print(f"\n  Generated {len(signals):,} signals ({len(entry_signals)} entries, {len(exit_signals)} exits)")

    if not signals:
        print("  No arbitrage opportunities found.")
        print("  Try: lower --entry-spread, lower --z-threshold, or more data.")
        return 0

    # ====================================================================
    # Step 4: Run backtest
    # ====================================================================
    print("\n[4/4] Running arbitrage backtest...")

    from trading.arbitrage.cross_platform_backtest import (
        ArbitrageBacktestConfig,
        run_arbitrage_backtest,
        print_arbitrage_result,
    )

    bt_config = ArbitrageBacktestConfig(
        strategy_name=f"Poly-Kalshi Arb (entry={args.entry_spread})",
        position_size=args.position_size,
        starting_capital=args.starting_capital,
        max_concurrent_positions=args.max_positions,
        fee_model=fee_model,
        kalshi_half_spread=args.kalshi_half_spread,
        poly_half_spread=args.poly_half_spread,
        volume_cap_frac=args.volume_cap_frac,
    )

    result = run_arbitrage_backtest(
        signals=signals,
        price_store=price_store,
        config=bt_config,
    )
    result.pairs_analyzed = len(pairs)

    # Print results
    print_arbitrage_result(result)

    # ====================================================================
    # Save results
    # ====================================================================
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"run_{timestamp_str}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Save trades
    if not result.trades_df.empty:
        trades_path = run_dir / "trades.parquet"
        result.trades_df.to_parquet(trades_path, index=False)
        print(f"\n  Trades saved: {trades_path}")

        # Also save CSV for easy inspection
        csv_path = run_dir / "trades.csv"
        result.trades_df.to_csv(csv_path, index=False)

    # Save config
    config_data = {
        "strategy": strategy.get_parameters(),
        "backtest": {
            "position_size": args.position_size,
            "starting_capital": args.starting_capital,
            "max_positions": args.max_positions,
            "slippage_bps": args.slippage_bps,
        },
        "matching": matcher.get_parameters(),
        "pairs_matched": len(pairs),
        "signals_generated": len(signals),
        "elapsed_seconds": time.time() - t0,
    }
    with open(run_dir / "config.json", "w") as f:
        json.dump(config_data, f, indent=2, default=str)

    # Save matched pairs
    matches_df = matcher.match_to_dataframe(
        poly_markets, kalshi_markets, max_pairs=args.max_pairs,
    )
    if not matches_df.empty:
        matches_df.to_csv(run_dir / "matched_pairs.csv", index=False)

    elapsed = time.time() - t0
    print(f"\n  Results saved: {run_dir}")
    print(f"  Total time: {elapsed:.1f}s")

    return 0


# ---------------------------------------------------------------------------
# Resolution mode
# ---------------------------------------------------------------------------

def _month_ts(month: str, end: bool = False) -> int:
    t = pd.Timestamp(month + "-01", tz="UTC")
    if end:
        t = t + pd.offsets.MonthBegin(1)
    return int(t.timestamp())


def run_resolution_mode(args) -> int:
    import duckdb
    import numpy as np

    from trading.arbitrage.fees import FeeModel
    from trading.arbitrage.pair_observations import (
        build_observations, extract_kalshi_trades, extract_pm_hourly_volume, extract_pm_prices, polarity_check,
    )
    from trading.arbitrage.resolution_arb import ResolutionArbConfig, run_resolution_arb
    from trading.arbitrage.rule_matcher import RuleMatcher

    data_dir = Path(args.data_dir)
    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.output_dir) / f"resolution_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[1/5] Universe + rule-based matching...")
    pm_cols = ["id", "question", "slug", "end_date", "outcome_prices", "volume"]
    if args.pm_markets_cache:
        pm = pd.read_parquet(args.pm_markets_cache)
    else:
        pm = duckdb.sql(f"select {', '.join(pm_cols)} from '{data_dir / 'polymarket' / 'markets.parquet'}'").df()
    k_sql = None
    if not args.kalshi_trades_glob:
        # full tape via predict_ngin.tapes.kalshi (trades_fixed alone ends 2026-01-27)
        from predict_ngin.tapes.kalshi import tape_sql
        k_sql = tape_sql(["ticker", "timestamp_unix", "yes_price", "count", "taker_side"])
    k_src = f"({k_sql})" if k_sql else f"read_parquet('{args.kalshi_trades_glob}', union_by_name=true)"
    if args.kalshi_tickers_cache:
        kt = pd.read_parquet(args.kalshi_tickers_cache, columns=["ticker"])
    else:
        kt = duckdb.sql(f"select distinct ticker from {k_src}").df()
    pairs = RuleMatcher().match(pm, kt)
    pairs.to_csv(out_dir / "matched_pairs.csv", index=False)
    print(f"  {len(pairs)} pairs: {pairs.family.value_counts().to_dict()}")

    print("[2/5] Extracting per-pair data (cached)...")
    ktp = cache / "k_trades_fixed.parquet"
    if not ktp.exists():
        extract_kalshi_trades(args.kalshi_trades_glob, pairs.k_ticker, ktp, source_sql=k_sql)
    if args.pm_prices_cache:
        pmp = pd.read_parquet(args.pm_prices_cache, columns=["market_id", "ts", "price"])
    else:
        pp_path = cache / "pm_prices.parquet"
        if not pp_path.exists():
            extract_pm_prices(str(data_dir / "polymarket" / "prices" / "*.parquet"), pairs.pm_id, pp_path)
        pmp = pd.read_parquet(pp_path)
    if args.pm_hourly_vol_cache:
        pv = pd.read_parquet(args.pm_hourly_vol_cache)
    else:
        pv_path = cache / "pm_hourly_vol.parquet"
        if not pv_path.exists():
            # the monthly extract was deleted 2026-10-17; extract_pm_hourly_volume then reads trades.parquet
            extract_pm_hourly_volume(str(data_dir / "polymarket" / "trades" / "*.parquet"), pairs.pm_id, pv_path)
        pv = pd.read_parquet(pv_path)
    if np.issubdtype(pv["hr"].dtype, np.datetime64):
        pv["hr"] = ((pd.to_datetime(pv["hr"], utc=True) - pd.Timestamp("1970-01-01", tz="UTC"))
                    // pd.Timedelta(hours=1)).astype("int64")
    km = duckdb.sql(f"select ticker, result from '{data_dir / 'kalshi' / 'markets.parquet'}'").df()

    print("[3/5] Building hourly as-of observations...")
    built = build_observations(pairs, ktp, pmp, pv, km, pm)
    obs, P = built["obs"], built["pairs"]
    pol = polarity_check(obs)
    bad = set(pol.loc[pol["corr"] < 0, "pair_id"])
    obs = obs[~obs.pair_id.isin(bad)]
    lo, hi = _month_ts(args.start), _month_ts(args.end, end=True)
    obs = obs[(obs.ts >= lo) & (obs.ts < hi)]
    if not args.include_inplay:
        obs = obs[~obs.in_play]
    resolved = P.dropna(subset=["k_outcome", "pm_outcome"])
    agree = float((resolved.k_outcome == resolved.pm_outcome).mean()) if len(resolved) else float("nan")
    print(f"  obs={len(obs):,} pairs_with_obs={obs.pair_id.nunique()} polarity_dropped={len(bad)} "
          f"outcome_agreement={agree:.3%} (n={len(resolved)})")
    obs.to_parquet(out_dir / "observations.parquet")
    P.to_csv(out_dir / "pairs_resolution.csv", index=False)

    def cfg(**over):
        base = dict(
            total_capital=args.capital, max_capital_per_pair=args.max_per_pair,
            min_net_edge_per_contract=args.min_edge, kalshi_half_spread=args.kalshi_half_spread,
            poly_half_spread=args.poly_half_spread, volume_cap_frac=args.volume_cap_frac,
            settle_lag_seconds=args.settle_lag_hours * 3600,
            fees=FeeModel(kalshi_taker_coef=args.kalshi_fee_coef, kalshi_maker_coef=args.kalshi_maker_coef,
                          poly_fees_enabled=not args.no_poly_fees),
        )
        base.update(over)
        return ResolutionArbConfig(**base)

    print("[4/5] Running hold-to-resolution arb...")
    base = run_resolution_arb(obs, P, cfg())
    base.trades.to_csv(out_dir / "trades_base.csv", index=False)
    base.monthly.to_csv(out_dir / "monthly_base.csv", index=False)
    fam = P.set_index("pair_id")["family"]
    if not base.trades.empty:
        bt = base.trades.assign(family=base.trades.pair_id.map(fam))
        by_fam = bt.groupby("family").agg(n=("pnl", "size"), deployed=("cost", "sum"), pnl=("pnl", "sum"),
                                          mismatch=("outcome_mismatch", "sum"))
        by_fam.to_csv(out_dir / "by_family_base.csv")
        print(by_fam.round(2).to_string())
    pd.set_option("display.width", 250)
    print(base.monthly.round(2).to_string(index=False))
    print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in base.summary.items()})

    results = {"base": base.summary}
    if not args.no_sensitivities:
        print("[5/5] Sensitivities...")
        variants = {
            "no_poly_fees": dict(fees=FeeModel(kalshi_taker_coef=args.kalshi_fee_coef, poly_fees_enabled=False)),
            "tight_spreads_0.5c": dict(kalshi_half_spread=0.005, poly_half_spread=0.005),
            "wide_spreads_2c_1.5c": dict(kalshi_half_spread=0.02, poly_half_spread=0.015),
            "vol_cap_2pct": dict(volume_cap_frac=0.02),
            "vol_cap_10pct": dict(volume_cap_frac=0.10),
            "capital_1k": dict(total_capital=1000.0, max_capital_per_pair=min(args.max_per_pair, 1000.0)),
            "capital_25k": dict(total_capital=25000.0, max_capital_per_pair=2500.0),
            "capital_unlimited": dict(total_capital=1e9, max_capital_per_pair=1e9),
            "min_edge_2c": dict(min_net_edge_per_contract=0.02),
        }
        for name, over in variants.items():
            r = run_resolution_arb(obs, P, cfg(**over))
            results[name] = r.summary
            r.monthly.to_csv(out_dir / f"monthly_{name}.csv", index=False)
        # 1-hour execution delay: decide at hour h, trade at hour h+1 prices only if still profitable
        o2 = obs.sort_values(["pair_id", "ts"]).copy()
        o2["prev_hr"] = o2.groupby("pair_id")["hr"].shift()
        delayed = o2[o2["hr"] - o2["prev_hr"] == 1]
        prev_edge = o2.groupby("pair_id")["k_price"].shift() - o2.groupby("pair_id")["pm_price"].shift()
        same_dir = np.sign(prev_edge.loc[delayed.index]) == np.sign(delayed["k_price"] - delayed["pm_price"])
        r = run_resolution_arb(delayed[same_dir.values], P, cfg())
        results["persist_1h"] = r.summary
        r.monthly.to_csv(out_dir / "monthly_persist_1h.csv", index=False)
        if not args.include_inplay:
            ob_all = built["obs"]
            ob_all = ob_all[(~ob_all.pair_id.isin(bad)) & (ob_all.ts >= lo) & (ob_all.ts < hi)]
            r = run_resolution_arb(ob_all, P, cfg())
            results["with_inplay"] = r.summary
            r.monthly.to_csv(out_dir / "monthly_with_inplay.csv", index=False)
        print(pd.DataFrame(results).T[["n_trades", "net_pnl", "deployed", "fees", "avg_locked", "max_locked",
                                       "ann_return_on_locked", "mismatch_pnl"]].round(3).to_string())

    with open(out_dir / "summary.json", "w") as f:
        json.dump({"results": results, "pairs": len(P), "polarity_dropped": len(bad),
                   "outcome_agreement": agree, "args": vars(args)}, f, indent=2, default=str)
    print(f"Saved -> {out_dir}  ({time.time() - _T0:.0f}s)")
    return 0


_T0 = time.time()

if __name__ == "__main__":
    sys.exit(main())

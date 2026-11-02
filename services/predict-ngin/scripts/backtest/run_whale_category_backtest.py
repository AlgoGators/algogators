#!/usr/bin/env python3
"""
Run whale-following backtest at category level using data/research.

Strategy: Follow high-conviction whale trades with category-level limits (30% max
per category), position sizing (Kelly), and "last signal wins" for conflicts.

Requires:
- data/research/{category}/trades.parquet, prices.parquet, markets_filtered.csv
- data/research/resolutions.csv (market_id, winner) OR prediction_markets.db for resolutions

Usage:
    python scripts/backtest/run_whale_category_backtest.py
    python scripts/backtest/run_whale_category_backtest.py --capital 1000000 --min-usd 1000
"""

import argparse
import dataclasses
import os
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import hashlib
import json
from typing import Dict, Optional

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

import numpy as np
import pandas as pd

from src.whale_strategy.research_data_loader import (
    load_research_trades,
    load_research_markets,
    load_resolution_winners,
    ResearchPriceStore,
    get_research_categories,
)
from src.whale_strategy.polymarket_whales import (
    identify_polymarket_whales,
    build_price_snapshot,
)
from src.whale_strategy.whale_following_strategy import (
    filter_and_score_signals,
    find_conflicting_position,
    handle_conflicting_signal,
    calculate_position_size,
    StrategyState,
    Position,
    WhaleSignal,
)
from src.whale_strategy.whale_config import load_whale_config, WhaleConfig
from src.whale_strategy.whale_scoring import WHALE_CRITERIA, MIN_WHALE_SCORE
from src.whale_strategy.whale_surprise import (
    build_surprise_positive_whale_set,
    build_volume_whale_set,
)
from trading.data_modules.costs import CostModel, DEFAULT_COST_MODEL
from src.whale_strategy.execution_model import (
    FillConfig,
    SizingConfig,
    TradeTape,
    aggregate_partial_exits,
    entry_fill,
    market_exit_fill,
    size_position_pit,
)
from src.whale_strategy.retest_stats import daily_returns_full_calendar
from trading.reporting import generate_quantstats_report


def _whale_qualifying_params_hash(cfg: "WhaleConfig") -> str:
    """Stable hash of the parameters that define which whales qualify.

    If any qualifying parameter changes, the hash changes and all cached
    whale sets are ignored (effectively invalidated by missing cache files).
    """
    params = {
        "volume_percentile": cfg.volume_percentile,
        "min_surprise": cfg.min_surprise,
        "min_trades_for_surprise": cfg.min_trades_for_surprise,
        "require_positive_surprise": cfg.require_positive_surprise,
        "recency_halflife_days": cfg.recency_halflife_days,
        "bayes_prior_alpha": cfg.bayes_prior_alpha,
        "bayes_prior_beta": cfg.bayes_prior_beta,
    }
    return hashlib.sha256(
        json.dumps(params, sort_keys=True).encode()
    ).hexdigest()[:16]


def _whale_cache_path(cache_dir: Path, cutoff: "pd.Timestamp", params_hash: str) -> Path:
    week_str = pd.Timestamp(cutoff).strftime("%Y-%m-%d")
    return cache_dir / f"whale_{week_str}_{params_hash}.json"


def _load_whale_set_cache(cache_dir: Path, cutoff: "pd.Timestamp", params_hash: str):
    """Return (whale_set, scores, winrates) from cache, or None if not cached."""
    path = _whale_cache_path(cache_dir, cutoff, params_hash)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return set(data["whale_set"]), data["scores"], data["winrates"]
    except Exception:
        return None


def _save_whale_set_cache(
    cache_dir: Path,
    cutoff: "pd.Timestamp",
    params_hash: str,
    whale_set,
    scores,
    winrates,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _whale_cache_path(cache_dir, cutoff, params_hash)
    with open(path, "w") as f:
        json.dump(
            {"whale_set": list(whale_set), "scores": scores, "winrates": winrates},
            f,
        )


def _weekly_whale_worker(args):
    """Top-level worker: build surprise-positive whale set for one week.

    Reads trades from a temp parquet path so the large DataFrame is not
    serialized over pickle — the OS page cache makes re-reads fast.

    Checks the whale-set cache first (keyed by cutoff date + params hash).
    Writes result to cache after computing.
    """
    (week_key, cutoff_str, trades_parquet_path, resolution_winners,
     min_surprise, min_trades_for_surprise,
     require_positive_surprise, volume_percentile,
     recency_halflife_days, bayes_prior_alpha, bayes_prior_beta,
     cache_dir_str, params_hash) = args

    from src.whale_strategy.whale_surprise import build_surprise_positive_whale_set

    cutoff = pd.Timestamp(cutoff_str)

    # Check cache first
    if cache_dir_str:
        cached = _load_whale_set_cache(Path(cache_dir_str), cutoff, params_hash)
        if cached is not None:
            return week_key, cached

    trades = pd.read_parquet(trades_parquet_path)
    hist_df = trades[trades["datetime"] <= cutoff]
    if len(hist_df) < 1000:
        return week_key, (set(), {}, {})

    w, s, wr = build_surprise_positive_whale_set(
        hist_df,
        resolution_winners,
        min_surprise=min_surprise,
        min_trades=min_trades_for_surprise,
        require_positive_surprise=require_positive_surprise,
        volume_percentile=volume_percentile,
        cutoff=cutoff,
        recency_halflife_days=recency_halflife_days,
        bayes_prior_alpha=bayes_prior_alpha,
        bayes_prior_beta=bayes_prior_beta,
    )

    if cache_dir_str:
        try:
            _save_whale_set_cache(Path(cache_dir_str), cutoff, params_hash, w, s, wr)
        except Exception:
            pass  # Cache write failure is non-fatal

    return week_key, (w, s, wr)


def _category_backtest_worker(args):
    """Top-level worker: run whale backtest for a single category."""
    (cat, research_dir_str, capital, min_usd, position_size, train_ratio,
     start_date, end_date, db_path, whale_config_dict, rebalance_freq) = args

    whale_config = WhaleConfig(**whale_config_dict)
    result = run_whale_category_backtest(
        research_dir=Path(research_dir_str),
        capital=capital,
        min_usd=min_usd,
        position_size=position_size,
        train_ratio=train_ratio,
        start_date=start_date,
        end_date=end_date,
        categories=[cat],
        db_path=db_path,
        whale_config=whale_config,
        surprise_only=whale_config.surprise_only,
        volume_only=whale_config.volume_only,
        unfavored_only=whale_config.unfavored_only,
        rebalance_freq=rebalance_freq,
        n_workers=1,
    )
    return cat, result


def _naive_utc(value) -> pd.Timestamp:
    """Timestamp as tz-naive UTC (trade datetimes are naive UTC)."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def _to_datetime_safe(value) -> pd.Timestamp:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return pd.NaT
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000
        return pd.to_datetime(ts, unit="s", errors="coerce")
    return pd.to_datetime(value, errors="coerce")


def _market_resolution_dates(markets_df: pd.DataFrame) -> dict:
    """
    Build {market_id: pd.Timestamp} of actual resolution/close dates from markets_df.

    Used to filter resolution_winners to only markets whose outcome was publicly
    known at a given cutoff date, preventing look-ahead bias in whale scoring.
    """
    dates = {}
    if markets_df.empty:
        return dates
    for _, row in markets_df.iterrows():
        mid = str(row.get("market_id", row.get("conditionId", ""))).strip().replace(".0", "")
        if not mid:
            continue
        for k in ("closedTime", "endDateIso", "endDate"):
            if k in row and pd.notna(row.get(k)):
                try:
                    ts = _to_datetime_safe(row[k])
                    if pd.notna(ts):
                        dates[mid] = ts
                        break
                except Exception:
                    pass
    return dates


def _filter_winners_at_cutoff(
    resolution_winners: dict,
    resolution_dates: dict,
    cutoff: pd.Timestamp,
) -> dict:
    """
    Return only resolution_winners for markets confirmed resolved by cutoff.

    Markets without a known resolution date are excluded — we cannot confirm
    the outcome was publicly available before the cutoff, so including them
    in whale scoring would be look-ahead biased.
    """
    cutoff_date = pd.Timestamp(cutoff).date()
    return {
        mid: winner
        for mid, winner in resolution_winners.items()
        if mid in resolution_dates
        and pd.Timestamp(resolution_dates[mid]).date() <= cutoff_date
    }


def _build_scheduled_end_dates(markets_df: pd.DataFrame) -> dict:
    """
    Build {market_id: pd.Timestamp} of scheduled close dates using only published fields.

    Uses endDateIso and endDate (known at trade time). Deliberately excludes closedTime,
    which records the actual resolution timestamp — using it would be look-ahead biased.
    """
    dates = {}
    if markets_df.empty:
        return dates
    for _, row in markets_df.iterrows():
        mid = str(row.get("market_id", row.get("conditionId", ""))).strip().replace(".0", "")
        if not mid:
            continue
        for k in ("endDateIso", "endDate"):
            if k in row and pd.notna(row.get(k)):
                try:
                    ts = _to_datetime_safe(row[k])
                    if pd.notna(ts):
                        dates[mid] = ts
                        break
                except Exception:
                    pass
    return dates


def build_resolution_map_from_winners(
    resolution_winners: dict,
    markets_df: pd.DataFrame,
) -> dict:
    """Build resolution map {market_id: {resolution, resolution_date, ...}} from winners + markets."""
    resolutions = {}
    if markets_df.empty:
        return resolutions

    for _, row in markets_df.iterrows():
        mid = str(row.get("market_id", row.get("conditionId", ""))).strip().replace(".0", "")
        if not mid:
            continue

        winner = resolution_winners.get(mid)
        if winner not in ("YES", "NO"):
            resolutions[mid] = {
                "resolution": None,
                "is_resolved": False,
                "resolution_date": None,
                "market_close_date": None,
                "has_actual_resolution": False,
            }
            continue

        resolution = 1.0 if winner == "YES" else 0.0
        for k in ("closedTime", "endDateIso", "endDate"):
            if k in row and pd.notna(row.get(k)):
                try:
                    resolution_date = _to_datetime_safe(row[k])
                    if pd.notna(resolution_date):
                        break
                except Exception:
                    pass
        else:
            resolution_date = None

        resolutions[mid] = {
            "resolution": resolution,
            "is_resolved": True,
            "resolution_date": resolution_date,
            "market_close_date": resolution_date,
            "has_actual_resolution": True,
        }

    return resolutions


def run_whale_category_backtest(
    research_dir: Path,
    capital: float = 1_000_000,
    min_usd: float = 100,
    position_size: float = 25_000,
    train_ratio: float = 0.3,
    start_date: str = None,
    end_date: str = None,
    categories: list = None,
    db_path: str = None,
    whale_config: WhaleConfig = None,
    surprise_only: bool = None,
    volume_only: bool = None,
    unfavored_only: bool = None,
    unfavored_max_price: float = None,
    rebalance_freq: str = "1W",
    n_workers: int = 1,
    extra_resolutions_dir: Path = None,
    whale_cache_dir: Path = None,
    fill_config: FillConfig = None,
    sizing: SizingConfig = None,
) -> dict:
    """
    Run whale-following backtest with category limits.
    Uses whale_config for whale definition; CLI args override config when provided.

    fill_config: defaults to FillConfig() = realistic (next print + 5s latency,
        1c half-spread, Polymarket taker fee). FillConfig.original() reproduces the
        legacy "fill at the whale's price, 3% PnL haircut" model.
    sizing: defaults to SizingConfig() = point-in-time (trailing 24h volume cap).
        SizingConfig.legacy() reproduces lifetime-volumeClob sizing (look-ahead).
    """
    ctx = prepare_whale_backtest(
        research_dir=research_dir, min_usd=min_usd, train_ratio=train_ratio,
        start_date=start_date, end_date=end_date, categories=categories, db_path=db_path,
        whale_config=whale_config, surprise_only=surprise_only, volume_only=volume_only,
        unfavored_only=unfavored_only, unfavored_max_price=unfavored_max_price,
        rebalance_freq=rebalance_freq, n_workers=n_workers,
        extra_resolutions_dir=extra_resolutions_dir, whale_cache_dir=whale_cache_dir,
    )
    if "error" in ctx:
        return ctx
    try:
        return simulate_whale_backtest(ctx, capital=capital, fill_config=fill_config, sizing=sizing)
    finally:
        ctx["price_store"].close()


def prepare_whale_backtest(
    research_dir: Path,
    min_usd: float = 100,
    train_ratio: float = 0.3,
    start_date: str = None,
    end_date: str = None,
    categories: list = None,
    db_path: str = None,
    whale_config: WhaleConfig = None,
    surprise_only: bool = None,
    volume_only: bool = None,
    unfavored_only: bool = None,
    unfavored_max_price: float = None,
    rebalance_freq: str = "1W",
    n_workers: int = 1,
    extra_resolutions_dir: Path = None,
    whale_cache_dir: Path = None,
) -> dict:
    """
    Load data, select whales point-in-time and build candidate signals.

    Returns a context dict consumed by simulate_whale_backtest (or {"error": ...}).
    The context can be simulated many times (different fills/sizing/signal sources).
    """
    research_dir = Path(research_dir)
    cfg = whale_config or load_whale_config()
    surprise_only = surprise_only if surprise_only is not None else cfg.surprise_only
    volume_only = volume_only if volume_only is not None else cfg.volume_only
    unfavored_only = unfavored_only if unfavored_only is not None else cfg.unfavored_only
    unfavored_max_price = unfavored_max_price if unfavored_max_price is not None else cfg.unfavored_max_price
    min_usd = max(min_usd, cfg.min_usd)

    categories = categories or get_research_categories(research_dir)
    if not categories:
        return {"error": "No categories with trades.parquet found"}

    # Load data
    trades_df = load_research_trades(
        research_dir,
        categories=categories,
        min_usd=min_usd,
        start_date=start_date,
        end_date=end_date,
    )
    if trades_df.empty:
        return {"error": "No trades loaded"}

    markets_df = load_research_markets(research_dir, categories=categories)
    resolution_winners = load_resolution_winners(research_dir, db_path=db_path)
    # Merge extra resolutions (e.g. poly_cat has 51k vs research's 2k)
    if extra_resolutions_dir is not None:
        extra = load_resolution_winners(Path(extra_resolutions_dir))
        resolution_winners = {**extra, **resolution_winners}  # research takes precedence

    # Build resolution date map for look-ahead-free whale scoring.
    # Used to filter resolution_winners to only markets whose outcome was
    # publicly known at each rolling cutoff (see _filter_winners_at_cutoff).
    market_resolution_dates = _market_resolution_dates(markets_df)

    if not resolution_winners:
        if surprise_only:
            return {"error": "Surprise-only mode requires resolutions. Run --extract-resolutions first."}
        if cfg.require_positive_surprise:
            return {"error": "Performance filter (positive surprise) requires resolutions. Run --extract-resolutions first."}
        print("Warning: No resolution data. Using simplified whale identification (no scoring).")

    # Train/test split
    split_date = trades_df["datetime"].quantile(train_ratio)
    train_df = trades_df[trades_df["datetime"] <= split_date]
    test_df = trades_df[trades_df["datetime"] > split_date]

    # Performance filter: positive expected return (capital-weighted shrunk WR > expected WR)
    use_performance_filter = resolution_winners and cfg.require_positive_surprise

    # Build weekly whale sets for rolling rebalancing (when resolutions + rebalance_freq).
    # Weekly granularity ensures whale qualification reflects the most recent information.
    # Results are cached on disk keyed by (cutoff_date, params_hash) — if qualifying
    # parameters change, the hash changes and cached files are automatically bypassed.
    weekly_whale_sets: dict = {}  # week_key -> (whales, scores, winrates)
    whale_scores_override = None
    whale_winrates_override = None
    whales = set()

    if whale_cache_dir is None:
        whale_cache_dir = _project_root / "cache" / "whale_sets"
    params_hash = _whale_qualifying_params_hash(cfg)

    if use_performance_filter and rebalance_freq:
        # Rolling weekly: re-identify whales at start of each week from all data up to end of prior week.
        test_weeks = test_df["datetime"].dt.to_period("W").unique()
        if n_workers > 1 and len(test_weeks) > 1:
            # Write trades to temp parquet so workers load from disk (no pickle of large DataFrame)
            _tmp_fd, _tmp_path = tempfile.mkstemp(suffix=".parquet")
            os.close(_tmp_fd)
            try:
                trades_df.to_parquet(_tmp_path)
                week_args = []
                for p in sorted(test_weeks):
                    cutoff = p.start_time - pd.Timedelta(days=1)  # end of previous week
                    week_key = str(p.start_time.date())
                    week_args.append((
                        week_key,
                        str(cutoff),
                        _tmp_path,
                        _filter_winners_at_cutoff(resolution_winners, market_resolution_dates, cutoff),
                        cfg.min_surprise, cfg.min_trades_for_surprise,
                        cfg.require_positive_surprise, cfg.volume_percentile,
                        cfg.recency_halflife_days, cfg.bayes_prior_alpha, cfg.bayes_prior_beta,
                        str(whale_cache_dir), params_hash,
                    ))
                n_week_workers = min(n_workers, len(week_args))
                print(f"  Parallel weekly whale sets: {len(week_args)} weeks, {n_week_workers} workers")
                with ProcessPoolExecutor(max_workers=n_week_workers) as pool:
                    for wk, whale_data in pool.map(_weekly_whale_worker, week_args):
                        weekly_whale_sets[wk] = whale_data
                        whales |= whale_data[0]
            finally:
                Path(_tmp_path).unlink(missing_ok=True)
        else:
            for period in sorted(test_weeks):
                week_key = str(period.start_time.date())
                cutoff = period.start_time - pd.Timedelta(days=1)  # end of previous week

                # Check cache
                cached = _load_whale_set_cache(whale_cache_dir, cutoff, params_hash)
                if cached is not None:
                    weekly_whale_sets[week_key] = cached
                    whales |= cached[0]
                    continue

                hist_df = trades_df[trades_df["datetime"] <= cutoff]
                if len(hist_df) < 1000:
                    weekly_whale_sets[week_key] = (set(), {}, {})
                    continue
                cutoff_winners = _filter_winners_at_cutoff(
                    resolution_winners, market_resolution_dates, cutoff
                )
                w, s, wr = build_surprise_positive_whale_set(
                    hist_df,
                    cutoff_winners,
                    min_surprise=cfg.min_surprise,
                    min_trades=cfg.min_trades_for_surprise,
                    require_positive_surprise=cfg.require_positive_surprise,
                    volume_percentile=cfg.volume_percentile,
                    cutoff=cutoff,
                    recency_halflife_days=cfg.recency_halflife_days,
                    bayes_prior_alpha=cfg.bayes_prior_alpha,
                    bayes_prior_beta=cfg.bayes_prior_beta,
                )
                weekly_whale_sets[week_key] = (w, s, wr)
                whales |= w
                try:
                    _save_whale_set_cache(whale_cache_dir, cutoff, params_hash, w, s, wr)
                except Exception:
                    pass
        print(f"  Rolling weekly: {len(whales)} qualified whales (positive surprise) across {len(weekly_whale_sets)} weeks")
        # In rolling mode the per-week check (lines ~839-844) is the authoritative score gate.
        # Assign every whale-set member a permissive score (= MIN_WHALE_SCORE) so that
        # filter_and_score_signals lets ALL whale trades through as candidate signals.
        # The per-week loop then replaces each signal's score with the week-specific snapshot
        # and re-applies the MIN_WHALE_SCORE threshold, ensuring temporal correctness with
        # no look-ahead.  Winrates use the most-recent week they appeared in.
        whale_scores_override = {addr: MIN_WHALE_SCORE for addr in whales}
        whale_winrates_override = {}
        for _wk_data in weekly_whale_sets.values():
            _w, _s, _wr = _wk_data
            whale_winrates_override.update(_wr)
    elif use_performance_filter and resolution_winners:
        # Single train-period whale set — filter to resolutions known by split date
        split_cutoff_winners = _filter_winners_at_cutoff(
            resolution_winners, market_resolution_dates, split_date
        )
        # Check cache for the single-period whale set
        cached = _load_whale_set_cache(whale_cache_dir, split_date, params_hash)
        if cached is not None:
            whales, whale_scores_override, whale_winrates_override = cached
        else:
            whales, whale_scores_override, whale_winrates_override = build_surprise_positive_whale_set(
                train_df,
                split_cutoff_winners,
                min_surprise=cfg.min_surprise,
                min_trades=cfg.min_trades_for_surprise,
                require_positive_surprise=cfg.require_positive_surprise,
                volume_percentile=cfg.volume_percentile,
                cutoff=split_date,
                recency_halflife_days=cfg.recency_halflife_days,
                bayes_prior_alpha=cfg.bayes_prior_alpha,
                bayes_prior_beta=cfg.bayes_prior_beta,
            )
            try:
                _save_whale_set_cache(whale_cache_dir, split_date, params_hash,
                                      whales, whale_scores_override, whale_winrates_override)
            except Exception:
                pass
        print(f"  Qualified whales: {len(whales)} (positive expected return)")
    elif volume_only and not resolution_winners:
        whales, whale_scores_override, whale_winrates_override = build_volume_whale_set(
            train_df, volume_percentile=cfg.volume_percentile
        )
        print(f"  Volume-only (no resolutions): {len(whales)} whales")
    elif volume_only:
        whales, whale_scores_override, whale_winrates_override = build_volume_whale_set(
            train_df, volume_percentile=cfg.volume_percentile
        )
        print(f"  Volume-only (no perf filter): {len(whales)} whales")
    else:
        price_snapshot = build_price_snapshot(train_df)
        try:
            whales = identify_polymarket_whales(
                train_df,
                method="mid_price_accuracy" if resolution_winners else "top_returns",
                role="maker",
                min_trades=10,
                min_volume=1000,
                resolution_winners=resolution_winners if resolution_winners else None,
                price_snapshot=price_snapshot,
            )
        except ValueError:
            # Fallback when resolution_winners empty and top_returns fails
            whales = identify_polymarket_whales(
                train_df,
                method="volume_top10",
                role="maker",
                min_trades=10,
                min_volume=1000,
            )

    if not whales:
        # Fallback: top by volume
        stats = train_df.groupby("maker").agg(
            total_volume=("usd_amount", "sum"),
            trade_count=("usd_amount", "count"),
        ).reset_index()
        stats = stats[stats["trade_count"] >= 10]
        if stats.empty:
            return {"error": "No qualifying whales"}
        whales = set(stats.nlargest(50, "total_volume")["maker"])

    # Build resolution map
    resolutions = build_resolution_map_from_winners(resolution_winners, markets_df)

    # Scheduled end dates: published close dates known at trade time (for TTR filter).
    # Uses endDateIso/endDate only — excludes closedTime (actual resolution, look-ahead biased).
    scheduled_end_dates = _build_scheduled_end_dates(markets_df)

    # Market liquidity + metadata lookup dicts (market_id → value)
    market_liquidity = {}
    market_titles: dict = {}   # market_id → question/title string
    market_slugs: dict = {}    # market_id → slug for URL construction
    for _, row in markets_df.iterrows():
        mid = str(row.get("market_id", "")).strip().replace(".0", "")
        liq = row.get("volumeClob") or row.get("liquidityNum") or row.get("liquidity") or row.get("volumeNum") or row.get("volume")
        market_liquidity[mid] = float(liq) if liq is not None else 100_000
        if not market_titles.get(mid):
            market_titles[mid] = str(row.get("question") or row.get("title") or "")
        if not market_slugs.get(mid):
            market_slugs[mid] = str(row.get("slug") or row.get("eventSlug") or "")

    # Also pull titles/slugs from trades data (trades have title/slug per row)
    for col_mid, col_title, col_slug in [("market_id", "title", "slug"), ("market_id", "title", "eventSlug")]:
        if col_title in trades_df.columns and col_mid in trades_df.columns:
            for _, row in trades_df.drop_duplicates(subset=[col_mid]).iterrows():
                mid = str(row[col_mid]).strip().replace(".0", "")
                if mid and not market_titles.get(mid):
                    market_titles[mid] = str(row.get(col_title) or "")
                if mid and not market_slugs.get(mid) and col_slug in trades_df.columns:
                    market_slugs[mid] = str(row.get(col_slug) or "")
            break

    # Price store — created here (before IC computation and backtest loop)
    price_store = ResearchPriceStore(research_dir, categories=categories)

    # IC computation: measure short-term directional accuracy on training period.
    # Blended into whale scores: final_score = (1-w)*surprise_score + w*ic_score_normalized.
    whale_ic_scores: Dict[str, float] = {}
    if cfg.ic_score_weight > 0 and whales:
        from src.whale_strategy.whale_surprise import compute_whale_ic
        whale_ic_scores = compute_whale_ic(
            train_df[train_df["maker"].isin(whales)],
            price_lookup_fn=price_store.price_at_or_before,
            horizon_days=cfg.ic_horizon_days,
            min_trades=cfg.ic_min_trades,
            max_horizon_date=split_date,  # prevent leaking test-period prices into IC
        )
        if whale_ic_scores:
            # Blend IC into weekly whale scores. IC is normalized to [0, 10] same scale as surprise score.
            # IC = 0.50 → neutral (score 5.0); IC = 1.0 → perfect (10.0); IC = 0.0 → (0.0)
            for wk, (w_set, w_scores, w_wr) in weekly_whale_sets.items():
                blended = {}
                for addr, s_score in w_scores.items():
                    ic = whale_ic_scores.get(addr)
                    if ic is not None:
                        ic_score_norm = max(0.0, min(ic * 10.0, 10.0))
                        blended[addr] = (1.0 - cfg.ic_score_weight) * s_score + cfg.ic_score_weight * ic_score_norm
                    else:
                        blended[addr] = s_score
                weekly_whale_sets[wk] = (w_set, blended, w_wr)
            # Also blend into single-period override if used
            if whale_scores_override:
                for addr in list(whale_scores_override.keys()):
                    ic = whale_ic_scores.get(addr)
                    if ic is not None:
                        ic_score_norm = max(0.0, min(ic * 10.0, 10.0))
                        whale_scores_override[addr] = (
                            (1.0 - cfg.ic_score_weight) * whale_scores_override[addr]
                            + cfg.ic_score_weight * ic_score_norm
                        )
            print(f"  IC computed for {len(whale_ic_scores)} whales (horizon={cfg.ic_horizon_days}d, weight={cfg.ic_score_weight:.0%})")

    # Filter to unfavored trades only (underdog: BUY <=40c, SELL >=60c)
    signals_df = test_df
    if unfavored_only:
        direction = test_df["maker_direction"].str.upper()
        price = test_df["price"].astype(float)
        mask = (
            ((direction == "BUY") & (price <= unfavored_max_price)) |
            ((direction == "SELL") & (price >= (1 - unfavored_max_price)))
        )
        signals_df = test_df[mask].copy()
        print(f"  Unfavored-only: {len(signals_df):,} / {len(test_df):,} test trades")

    # Get signals (use union of all monthly whale sets when rolling, so we capture all potential signals)
    signal_min_usd = min(min_usd, 1000)  # Lower for research data
    min_pos_override = 1000 if (volume_only or unfavored_only or use_performance_filter) else None
    min_liq_override = 10000 if (volume_only or unfavored_only or use_performance_filter) else None
    signals = filter_and_score_signals(
        signals_df,
        resolution_winners or {},
        markets_df,
        whale_set=whales,
        min_usd=signal_min_usd,
        role="maker",
        whale_scores_override=whale_scores_override,
        whale_winrates_override=whale_winrates_override,
        min_position_size_override=min_pos_override,
        min_market_liquidity_override=min_liq_override,
    )

    if not signals:
        # Fallback: use whale trades directly as signals
        whale_trades = signals_df[
            (signals_df["maker"].isin(whales)) &
            (signals_df["usd_amount"] >= min_usd)
        ]
        signals = []
        for _, row in whale_trades.iterrows():
            mid = str(row["market_id"]).strip().replace(".0", "")
            signals.append(WhaleSignal(
                market_id=mid,
                category=row.get("category", "Unknown"),
                whale_address=row["maker"],
                side=row.get("maker_direction", "BUY").upper(),
                price=float(row["price"]),
                size_usd=float(row["usd_amount"]),
                score=7.0,  # Default
                datetime=row["datetime"],
                historical_winrate=0.55,
            ))

    # Full trade tape (all sizes, YES-normalised) for realistic fills and point-in-time sizing
    prints_df = load_research_trades(
        research_dir, categories=categories, min_usd=0.0, start_date=start_date, end_date=end_date,
    )
    tape = TradeTape(prints_df)
    keep_cols = [c for c in ("market_id", "datetime", "price", "usd_amount", "category", "maker") if c in prints_df.columns]
    test_prints = prints_df.loc[prints_df["datetime"] > split_date, keep_cols].reset_index(drop=True)
    del prints_df

    return {
        "cfg": cfg,
        "signals": signals,
        "weekly_whale_sets": weekly_whale_sets,
        "whales": whales,
        "resolutions": resolutions,
        "scheduled_end_dates": scheduled_end_dates,
        "market_titles": market_titles,
        "market_slugs": market_slugs,
        "market_liquidity": market_liquidity,
        "min_market_liquidity": min_liq_override,
        "price_store": price_store,
        "tape": tape,
        "test_prints": test_prints,
        "categories": categories,
        "test_start": split_date,
        "test_end": test_df["datetime"].max(),
    }


def simulate_whale_backtest(
    ctx: dict,
    signals: list = None,
    capital: float = 1_000_000,
    fill_config: FillConfig = None,
    sizing: SizingConfig = None,
    apply_whale_filter: bool = True,
) -> dict:
    """
    Event loop over calendar days. One position per market; fills per fill_config.

    signals: override ctx["signals"] (e.g. favourite-baseline signals).
    apply_whale_filter: when False the weekly active-whale gate is skipped
        (required for baseline signals, which carry no whale identity).
    """
    cfg: WhaleConfig = ctx["cfg"]
    fill_config = fill_config or FillConfig()
    sizing = sizing or SizingConfig()
    signals = ctx["signals"] if signals is None else signals
    weekly_whale_sets = ctx.get("weekly_whale_sets") or {}
    resolutions = ctx["resolutions"]
    scheduled_end_dates = ctx.get("scheduled_end_dates", {})
    market_titles = ctx.get("market_titles", {})
    market_slugs = ctx.get("market_slugs", {})
    market_liquidity = ctx.get("market_liquidity", {})
    price_store = ctx["price_store"]
    tape: TradeTape = ctx.get("tape")
    realistic = fill_config.realistic

    state = StrategyState(total_capital=capital)
    open_positions: Dict[str, Position] = {}
    closed_trades = []
    skips: Dict[str, int] = {}
    next_pid = [1]
    market_supporting_whales: Dict[str, set] = {}

    def _skip(reason):
        skips[reason] = skips.get(reason, 0) + 1

    def _pos_meta(pos: Position, exit_mid: str) -> dict:
        """Extra columns for manual verification of a closed trade."""
        slug = market_slugs.get(exit_mid, "")
        url = f"https://polymarket.com/event/{slug}" if slug else f"https://polymarket.com/market/{exit_mid}"
        res = resolutions.get(exit_mid, {})
        resolution_val = res.get("resolution")
        if resolution_val is None:
            resolution_outcome = ""
        else:
            resolution_outcome = "YES" if resolution_val == 1.0 else "NO"
        sched_end = scheduled_end_dates.get(exit_mid)
        return {
            "market_title":          market_titles.get(exit_mid, ""),
            "market_url":            url,
            "whale_token_side":      pos.whale_token_side,
            "whale_score":           round(pos.whale_score, 3),
            "whale_winrate":         round(pos.whale_winrate, 3),
            "taker_address":         pos.taker_address,
            "signal_trade_size_usd": round(pos.signal_trade_size_usd, 2),
            "market_end_date":       str(sched_end.date()) if sched_end is not None and pd.notna(sched_end) else "",
            "resolution_outcome":    resolution_outcome,
        }

    def _entry_token(pos: Position) -> float:
        return pos.entry_price if pos.side.upper() == "BUY" else 1.0 - pos.entry_price

    def _record_exit(pos: Position, mid: str, exit_date, reason: str,
                     exit_token: float, exit_fee_ps: float, size_usd: float) -> None:
        """Append one exit fill (full or partial) for pos."""
        entry_tok = max(_entry_token(pos), 1e-6)
        shares = size_usd / entry_tok
        gross = (exit_token - entry_tok) * shares
        if realistic:
            entry_fee_part = pos.fees_usd * (size_usd / pos.size_usd) if pos.size_usd > 0 else 0.0
            fees = entry_fee_part + shares * exit_fee_ps
            net = gross - fees
        else:
            fees = 0.0
            net = gross * (1.0 - fill_config.original_cost_haircut)
        closed_trades.append({
            "position_id": pos.position_id,
            "market_id": mid,
            "entry_date": pos.entry_date,
            "entry_ts": pos.entry_ts,
            "exit_date": pd.Timestamp(exit_date).normalize(),
            "direction": pos.side.upper(),
            "entry_price": pos.entry_price,           # YES-equivalent
            "entry_token_price": entry_tok,           # price paid for the token held
            "exit_price": exit_token,                 # token price received (legacy semantics)
            "gross_pnl": gross,
            "fees_usd": fees,
            "net_pnl": net,
            "position_size": size_usd,
            "whale_address": pos.whale_address,
            "category": pos.category,
            "reason": reason,
            **_pos_meta(pos, mid),
        })

    def _remove(pos: Position, mid: str) -> None:
        open_positions.pop(mid, None)
        state.positions = [p for p in state.positions if p.market_id != mid]
        state.category_exposure[pos.category] = state.category_exposure.get(pos.category, 0) - pos.size_usd
        state.whale_exposure[pos.whale_address] = state.whale_exposure.get(pos.whale_address, 0) - pos.size_usd
        state.market_exposure.pop(mid, None)
        market_supporting_whales.pop(mid, None)

    def _yes_at_day_start(mid: str, day) -> Optional[float]:
        """YES price known at the start of `day` (no intraday look-ahead in realistic mode)."""
        if realistic:
            return tape.price_at_or_before(mid, pd.Timestamp(day).normalize(), strict=True) if tape else None
        return price_store.price_at_or_before(mid, day)  # legacy: end-of-day price

    def _yes_after_signal(mid: str, sig_ts, day) -> Optional[float]:
        """YES price at which a signal-driven exit executes."""
        if realistic:
            if tape is None:
                return None
            nxt = tape.next_print(mid, sig_ts, fill_config.latency_s)
            return nxt[1] if nxt else tape.price_at_or_before(mid, sig_ts)
        return price_store.price_at_or_before(mid, day)

    def _market_exit(pos: Position, mid: str, yes_mid: Optional[float], exit_date, reason: str) -> None:
        if yes_mid is None:
            yes_mid = pos.entry_price  # fallback: flat at entry (YES-equivalent)
        f = market_exit_fill(pos.side, yes_mid, pos.category, fill_config, exit_date)
        _record_exit(pos, mid, exit_date, reason, f.token_price, f.fee_per_share, pos.size_usd)

    def _size(sig: WhaleSignal, mid: str) -> Optional[float]:
        if sizing.mode == "point_in_time":
            liq = tape.trailing_volume(mid, sig.datetime, sizing.volume_window_s) if tape else 0.0
        else:  # legacy: lifetime volumeClob (look-ahead)
            liq = market_liquidity.get(mid, 100_000)
        if sizing.use_kelly:
            size = calculate_position_size(
                sig, state, liq, liquidity_cap_pct=sizing.volume_cap_pct,
                min_position_usd=sizing.min_position_usd, max_position_usd=sizing.max_position_usd,
            )
        else:
            size = size_position_pit(liq, state.available(), sizing)
        if size is None:
            return None
        # max_position_usd is a per-market cap: layering may only fill the remaining room
        if sizing.max_position_usd is not None:
            room = sizing.max_position_usd - state.market_exposure.get(mid, 0.0)
            size = min(size, room)
            if size < sizing.min_position_usd:
                return None
        return size

    # Sort signals by datetime; bucket by day
    from collections import defaultdict
    signals_sorted = sorted(signals, key=lambda s: s.datetime)
    signals_by_day: dict = defaultdict(list)
    for _sig in signals_sorted:
        signals_by_day[_sig.datetime.normalize()].append(_sig)

    signal_history: dict = defaultdict(list)
    for _sig in signals_sorted:
        signal_history[(_sig.market_id, _sig.side)].append((_sig.datetime, _sig.whale_address))

    test_start = pd.Timestamp(ctx.get("test_start") or (signals_sorted[0].datetime if signals_sorted else pd.Timestamp.now()))
    test_end = pd.Timestamp(ctx.get("test_end") or (signals_sorted[-1].datetime if signals_sorted else test_start))
    if signals_sorted:
        test_start = min(test_start, signals_sorted[0].datetime)
        test_end = max(test_end, signals_sorted[-1].datetime)
    calendar = list(pd.date_range(test_start.normalize(), test_end.normalize(), freq="D"))
    deployed_by_day = {}

    for current_date in calendar:
        # 1. Close positions that resolve / hit max hold
        to_close = []
        for mid, pos in open_positions.items():
            if cfg.max_hold_days > 0:
                hold_days = (current_date.date() - pos.entry_date.date()).days
                if hold_days >= cfg.max_hold_days:
                    to_close.append((mid, "max_hold", None))
                    continue
            res = resolutions.get(mid, {})
            res_date = res.get("resolution_date")
            if res_date is not None and pd.notna(res_date) and pd.to_datetime(res_date).date() <= current_date.date():
                to_close.append((mid, "resolution", res.get("resolution")))
                continue
            close_date = res.get("market_close_date")
            if close_date is not None and pd.notna(close_date) and pd.to_datetime(close_date).date() <= current_date.date():
                to_close.append((mid, "market_close", None))

        for mid, reason, resolution in to_close:
            pos = open_positions[mid]
            if reason == "resolution" and resolution is not None:
                payout = resolution if pos.side.upper() == "BUY" else 1.0 - resolution
                _record_exit(pos, mid, current_date, reason, payout, 0.0, pos.size_usd)
            else:
                _market_exit(pos, mid, _yes_at_day_start(mid, current_date), current_date, reason)
            _remove(pos, mid)

        # 1b. Partial exit: lock in gains when unrealized gain >= threshold (once per position).
        if cfg.partial_exit_gain_threshold > 0 and cfg.partial_exit_fraction > 0:
            for mid, pos in list(open_positions.items()):
                if pos.partial_exit_done:
                    continue
                yes_now = _yes_at_day_start(mid, current_date)
                if yes_now is None:
                    continue
                f = market_exit_fill(pos.side, yes_now, pos.category, fill_config, current_date)
                entry_tok = max(_entry_token(pos), 1e-6)
                gain_pct = (f.token_price - entry_tok) / entry_tok
                if gain_pct < cfg.partial_exit_gain_threshold:
                    continue
                exit_size = pos.size_usd * cfg.partial_exit_fraction
                _record_exit(pos, mid, current_date, "PARTIAL_EXIT", f.token_price, f.fee_per_share, exit_size)
                pos.fees_usd *= (1.0 - cfg.partial_exit_fraction)
                pos.size_usd -= exit_size
                pos.partial_exit_done = True
                state.category_exposure[pos.category] = state.category_exposure.get(pos.category, 0) - exit_size
                state.whale_exposure[pos.whale_address] = state.whale_exposure.get(pos.whale_address, 0) - exit_size
                state.market_exposure[mid] = pos.size_usd

        # 2. Process signals for today
        for sig in signals_by_day.get(current_date, []):
            if weekly_whale_sets and apply_whale_filter:
                week_key = str(sig.datetime.to_period("W").start_time.date())
                active_whales, active_scores, active_winrates = weekly_whale_sets.get(week_key, (set(), {}, {}))
                if sig.whale_address not in active_whales:
                    continue
                if active_scores:
                    sig.score = active_scores.get(sig.whale_address, sig.score)
                if sig.score < MIN_WHALE_SCORE:
                    continue
                if active_winrates:
                    sig.historical_winrate = active_winrates.get(sig.whale_address, sig.historical_winrate)
            mid = sig.market_id
            if mid in open_positions:
                pos = open_positions[mid]
                if pos.side == sig.side:
                    # Agreeing signal: track supporter and layer onto position if room.
                    market_supporting_whales.setdefault(mid, set()).add(sig.whale_address)
                    add_size = _size(sig, mid)
                    if add_size is None or state.available() < add_size:
                        continue
                    f = entry_fill(sig.side, sig.price, sig.datetime, mid, sig.category, tape, fill_config)
                    if f is None:
                        continue
                    old_tok = max(_entry_token(pos), 1e-6)
                    old_shares = pos.size_usd / old_tok
                    new_shares = add_size / f.token_price
                    pos.size_usd += add_size
                    new_tok = pos.size_usd / (old_shares + new_shares)
                    pos.entry_price = new_tok if pos.side == "BUY" else 1.0 - new_tok
                    pos.fees_usd += new_shares * f.fee_per_share
                    state.category_exposure[pos.category] = state.category_exposure.get(pos.category, 0) + add_size
                    state.whale_exposure[pos.whale_address] = state.whale_exposure.get(pos.whale_address, 0) + add_size
                    state.market_exposure[mid] = state.market_exposure.get(mid, 0) + add_size
                    continue
                else:
                    if sig.whale_address in market_supporting_whales.get(mid, set()):
                        # A supporting whale is reversing.
                        market_supporting_whales[mid].discard(sig.whale_address)
                        if not market_supporting_whales.get(mid):
                            _market_exit(pos, mid, _yes_after_signal(mid, sig.datetime, current_date),
                                         current_date, "WHALE_EXIT")
                            _remove(pos, mid)
                        continue  # Don't flip when a supporter exits
                    else:
                        # New counter-whale: close and flip.
                        yes_px = _yes_after_signal(mid, sig.datetime, current_date)
                        if yes_px is None:
                            continue
                        _market_exit(pos, mid, yes_px, current_date, "CONFLICTING_SIGNAL")
                        _remove(pos, mid)

            if mid in open_positions:
                continue

            res = resolutions.get(mid, {})
            res_date = res.get("resolution_date")
            if res_date is not None and pd.notna(res_date) and pd.to_datetime(res_date).date() <= current_date.date():
                _skip("resolution_day")
                continue

            if sig.price > cfg.max_entry_yes_price:
                _skip("price_gate")
                continue
            min_buy, max_sell = cfg.price_gates_for(sig.category)
            if sig.side == "BUY" and sig.price < min_buy:
                _skip("price_gate")
                continue
            if sig.side == "SELL" and sig.price > max_sell:
                _skip("price_gate")
                continue

            if cfg.min_confirmation_whales > 1:
                window_start = sig.datetime - pd.Timedelta(days=cfg.confirmation_window_days)
                recent_whales = {
                    w for dt, w in signal_history[(sig.market_id, sig.side)]
                    if window_start <= dt <= sig.datetime
                }
                if len(recent_whales) < cfg.min_confirmation_whales:
                    continue

            size = _size(sig, mid)
            if size is None:
                _skip("size")
                continue
            if state.available() < size:
                _skip("capital")
                continue

            f = entry_fill(sig.side, sig.price, sig.datetime, mid, sig.category, tape, fill_config)
            if f is None:
                _skip("no_fill")
                continue
            if realistic and res_date is not None and pd.notna(res_date) and f.ts >= _naive_utc(res_date):
                _skip("fill_after_close")
                continue

            entry_yes_equiv = f.token_price if sig.side == "BUY" else 1.0 - f.token_price
            pos = Position(
                market_id=mid,
                category=sig.category,
                side=sig.side,
                entry_price=entry_yes_equiv,
                size_usd=size,
                whale_address=sig.whale_address,
                whale_score=sig.score,
                entry_date=pd.to_datetime(current_date),
                whale_winrate=sig.historical_winrate,
                taker_address=sig.taker_address,
                whale_token_side=sig.whale_token_side,
                signal_trade_size_usd=sig.signal_trade_size_usd,
                position_id=next_pid[0],
                fees_usd=(size / f.token_price) * f.fee_per_share,
                entry_ts=f.ts,
            )
            next_pid[0] += 1
            open_positions[mid] = pos
            state.positions.append(pos)
            state.category_exposure[sig.category] = state.category_exposure.get(sig.category, 0) + size
            state.whale_exposure[sig.whale_address] = state.whale_exposure.get(sig.whale_address, 0) + size
            state.market_exposure[mid] = size
            market_supporting_whales[mid] = {sig.whale_address}

        deployed_by_day[current_date] = state.deployed()

    # Close remaining at last date (mark to last known price, pay exit costs)
    if calendar:
        last_date = calendar[-1]
        for mid, pos in list(open_positions.items()):
            if realistic:
                yes_px = tape.price_at_or_before(mid, last_date + pd.Timedelta(days=1), strict=True) if tape else None
            else:
                yes_px = price_store.price_at_or_before(mid, last_date)
            _market_exit(pos, mid, yes_px, last_date, "OPEN_AT_END")
            _remove(pos, mid)

    if not closed_trades:
        return {"error": "No closed trades", "whales": len(ctx.get("whales", [])),
                "signals": len(signals), "skips": skips, "total_trades": 0}

    fills_df = pd.DataFrame(closed_trades)
    df = aggregate_partial_exits(fills_df)   # one row per position
    total_pnl = df["net_pnl"].sum()
    wins = (df["net_pnl"] > 0).sum()
    total = len(df)

    daily_returns = daily_returns_full_calendar(fills_df, capital, calendar[0], calendar[-1])
    daily_pnl = fills_df.groupby(pd.to_datetime(fills_df["exit_date"]).dt.normalize())["net_pnl"].sum()
    daily_pnl = daily_pnl.reindex(daily_returns.index, fill_value=0.0)
    equity = capital + daily_pnl.cumsum()

    wins_pnl = df.loc[df["net_pnl"] > 0, "net_pnl"]
    losses_pnl = df.loc[df["net_pnl"] <= 0, "net_pnl"]
    avg_win = float(wins_pnl.mean()) if len(wins_pnl) else 0.0
    avg_loss = float(losses_pnl.mean()) if len(losses_pnl) else 0.0
    profit_factor = (wins_pnl.sum() / abs(losses_pnl.sum())) if losses_pnl.sum() != 0 else float("inf")

    sharpe = 0.0
    sd = daily_returns.std()
    if len(daily_returns) >= 5 and sd > 0:
        sharpe = float(daily_returns.mean() / sd * (365 ** 0.5))

    peak = equity.cummax()
    max_dd = float(((peak - equity) / peak).max()) if len(equity) else 0.0
    deployed = pd.Series(deployed_by_day, dtype=float)

    return {
        "total_trades": total,
        "win_rate": wins / total if total else 0,
        "total_net_pnl": total_pnl,
        "roi_pct": (total_pnl / capital) * 100,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_dd * 100,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "whales_followed": len(ctx.get("whales", [])),
        "signals_processed": len(signals),
        "categories": ctx.get("categories", []),
        "trades_df": df,
        "fills_df": fills_df,
        "daily_returns": daily_returns,
        "daily_equity": equity,
        "deployed_by_day": deployed,
        "skips": skips,
        "fill_mode": fill_config.mode,
    }


# One-at-a-time sensitivity grids for each tuneable parameter.
# Values span a wide range so we can see the full response curve, not just the optimum.
SENSITIVITY_GRIDS: dict = {
    # max_entry_yes_price: test whether the dust-exclusion threshold is robust.
    "max_entry_yes_price": [0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.98, 1.00],
    # min_confirmation_whales: test signal quality filter.
    # confirmation_window_days is omitted — it is flat while min_confirmation_whales=1
    # (the default disables the check); only meaningful if confirmation is enabled.
    "min_confirmation_whales": [1, 2, 3, 4],
    "max_hold_days": [0, 30, 60, 90, 180],
}


def run_sensitivity_test(
    research_dir: Path,
    capital: float,
    categories: list,
    db_path: str,
    whale_config_base: WhaleConfig,
    n_workers: int,
    output_dir: Path,
    params_to_test: list = None,
) -> pd.DataFrame:
    """
    One-at-a-time (OAT) parameter sensitivity test.

    Varies each parameter independently across a principled grid while holding all
    others at their base values in whale_config_base.  Purpose: detect p-hacking.

    A robust parameter shows a monotone or smooth response in profit factor.
    A non-monotone spike at a single value strongly suggests the parameter was
    cherry-picked to fit the test set and will not generalise.

    This is diagnostic only — do NOT use the output to re-select parameter values.
    """
    import copy

    grids = {
        k: v for k, v in SENSITIVITY_GRIDS.items()
        if params_to_test is None or k in params_to_test
    }

    all_rows = []

    print("\n" + "=" * 72)
    print("PARAMETER SENSITIVITY TEST  (one-at-a-time)")
    print("Diagnostic only — do NOT re-select values based on this output.")
    print("Robust: monotone response.  Suspect: single-value spike.")
    print("=" * 72)

    for param, values in grids.items():
        default_val = getattr(whale_config_base, param)
        print(f"\n--- {param}  (current={default_val}) ---")
        print(f"{'Value':>8}  {'Trades':>7}  {'Win%':>6}  {'PF':>6}  {'Sharpe':>7}  {'NetPnL':>12}")

        param_results = []
        for v in values:
            cfg = copy.deepcopy(whale_config_base)
            setattr(cfg, param, v)
            result = run_whale_category_backtest(
                research_dir=research_dir,
                capital=capital,
                categories=categories,
                db_path=db_path,
                whale_config=cfg,
                rebalance_freq="1W",
                n_workers=n_workers,
            )
            marker = " <-- current" if v == default_val else ""
            if "error" in result:
                print(f"{v:>8}  {'ERROR':>7}  {result['error']}{marker}")
                all_rows.append({"parameter": param, "value": v, "error": result["error"]})
                param_results.append(None)
                continue

            t = result["total_trades"]
            wr = result["win_rate"] * 100
            pf = min(result["profit_factor"], 99.0)  # cap inf for display
            sh = result["sharpe_ratio"]
            pnl = result["total_net_pnl"]
            print(f"{v:>8}  {t:>7,}  {wr:>5.1f}%  {pf:>6.2f}  {sh:>7.2f}  ${pnl:>11,.0f}{marker}")
            row = {
                "parameter": param, "value": v,
                "total_trades": t, "win_rate_pct": wr,
                "profit_factor": pf, "sharpe": sh, "net_pnl": pnl,
            }
            all_rows.append(row)
            param_results.append(pf)

        # Monotonicity verdict on profit factor
        valid = [(v, pf) for v, pf in zip(values, param_results) if pf is not None]
        if len(valid) >= 3:
            pf_vals = [x[1] for x in valid]
            ascending  = all(pf_vals[i] <= pf_vals[i + 1] for i in range(len(pf_vals) - 1))
            descending = all(pf_vals[i] >= pf_vals[i + 1] for i in range(len(pf_vals) - 1))
            pf_range = max(pf_vals) - min(pf_vals)
            peak_idx = int(np.argmax(pf_vals))
            at_boundary = peak_idx == 0 or peak_idx == len(pf_vals) - 1

            if pf_range < 0.05:
                verdict = "FLAT — parameter has negligible effect; consider removing it"
            elif ascending or descending:
                verdict = "MONOTONE — robust directional relationship, not cherry-picked"
            elif at_boundary:
                verdict = "MONOTONE-LIKE — peak at boundary; effectively monotone over this range"
            else:
                peak_val = valid[peak_idx][0]
                verdict = (
                    f"NON-MONOTONE — profit factor peaks at {peak_val} then declines. "
                    f"Verify this value was chosen from first principles, NOT from this output."
                )
            print(f"  Verdict: {verdict}")

    df = pd.DataFrame(all_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "sensitivity_test.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSensitivity results saved to {out_path}")
    return df


def _wf_cache_key(
    fold_start: str,
    test_start: str,
    test_end: str,
    whale_config: WhaleConfig,
    categories: list,
) -> str:
    """Stable hash key for one walk-forward fold."""
    payload = {
        "fold_start": fold_start,
        "test_start": test_start,
        "test_end": test_end,
        "config": dataclasses.asdict(whale_config),
        "categories": sorted(categories or []),
    }
    return hashlib.md5(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _wf_load_cache(cache_dir: Path, key: str) -> dict:
    path = cache_dir / f"{key}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _wf_save_cache(cache_dir: Path, key: str, row: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_dir / f"{key}.json", "w") as f:
        json.dump(row, f, default=str)


def run_walk_forward_backtest(
    research_dir: Path,
    capital: float,
    categories: list,
    db_path: str,
    whale_config: WhaleConfig,
    n_workers: int,
    window_months: int = 12,
    step_days: int = 7,
    min_train_months: int = 6,
    cache_dir: Path = None,
) -> pd.DataFrame:
    """
    Rolling walk-forward validation stepping one week at a time.

    Each fold:
    - Train: [fold_start, fold_start + min_train_months)
    - Test:  [fold_start + min_train_months, fold_start + window_months)
    - Step:  fold_start advances by step_days (default 7 = weekly)

    Caching: each fold's metrics are saved to cache_dir as a JSON file keyed by
    the fold date range + config hash.  On re-run, cached folds are loaded instantly
    without recomputing; only new folds are computed.

    Purpose: verify performance is stable across all time periods, not concentrated
    in the single backtest window.
    """
    from src.whale_strategy.research_data_loader import load_research_trades

    all_trades = load_research_trades(research_dir, categories=categories)
    if all_trades.empty:
        print("Walk-forward: No trades loaded.")
        return pd.DataFrame()

    date_min = all_trades["datetime"].min()
    date_max = all_trades["datetime"].max()
    del all_trades  # free memory; each fold re-loads with date filter

    if cache_dir is None:
        cache_dir = research_dir.parent / "output" / "whale_following" / "walk_forward_cache"

    # Count total folds upfront for progress display
    fold_start_probe = date_min
    total_folds = 0
    while True:
        if fold_start_probe + pd.DateOffset(months=window_months) > date_max:
            break
        total_folds += 1
        fold_start_probe += pd.Timedelta(days=step_days)

    print(f"\nWalk-forward: window={window_months}m, step={step_days}d, min_train={min_train_months}m")
    print(f"  Data range: {date_min.date()} → {date_max.date()}")
    print(f"  Total folds: {total_folds}  |  Cache: {cache_dir}")
    print(f"{'Fold':>5}  {'Train start':>12}  {'Test start':>12}  {'Test end':>12}  "
          f"{'Trades':>7}  {'Win%':>6}  {'Sharpe':>7}  {'NetPnL':>12}  {'Src':>5}")

    fold_rows = []
    fold_start = date_min
    fold_idx = 0
    cached_count = 0
    computed_count = 0

    while True:
        test_end = fold_start + pd.DateOffset(months=window_months)
        if test_end > date_max:
            break

        train_end = fold_start + pd.DateOffset(months=min_train_months)
        fold_start_str = str(fold_start.date())
        train_end_str = str(train_end.date())
        test_end_str = str(test_end.date())

        # Check cache
        key = _wf_cache_key(fold_start_str, train_end_str, test_end_str, whale_config, categories)
        cached = _wf_load_cache(cache_dir, key)

        if cached:
            row = cached
            src = "cache"
            cached_count += 1
        else:
            train_ratio = min_train_months / window_months
            result = run_whale_category_backtest(
                research_dir=research_dir,
                capital=capital,
                categories=categories,
                db_path=db_path,
                whale_config=whale_config,
                train_ratio=train_ratio,
                start_date=fold_start_str,
                end_date=test_end_str,
                rebalance_freq="1W",
                n_workers=n_workers,
            )
            src = "run"
            computed_count += 1

            if "error" in result:
                row = {
                    "fold": fold_idx, "train_start": fold_start_str,
                    "test_start": train_end_str, "test_end": test_end_str,
                    "error": result["error"],
                }
            else:
                row = {
                    "fold": fold_idx, "train_start": fold_start_str,
                    "test_start": train_end_str, "test_end": test_end_str,
                    "total_trades": result["total_trades"],
                    "win_rate_pct": result["win_rate"] * 100,
                    "sharpe": result["sharpe_ratio"],
                    "net_pnl": result["total_net_pnl"],
                    "roi_pct": result.get("roi_pct", 0),
                    "profit_factor": min(result.get("profit_factor", 0), 99.0),
                    "max_drawdown_pct": result.get("max_drawdown_pct", 0),
                }
            _wf_save_cache(cache_dir, key, row)

        # Always update fold index (cache may have stale index)
        row["fold"] = fold_idx

        if "error" in row:
            print(f"{fold_idx:>5}  {fold_start_str:>12}  {train_end_str:>12}  {test_end_str:>12}  "
                  f"  ERROR: {row['error']}  [{src}]")
        else:
            t = row.get("total_trades", 0)
            wr = row.get("win_rate_pct", 0)
            sh = row.get("sharpe", 0)
            pnl = row.get("net_pnl", 0)
            print(f"{fold_idx:>5}  {fold_start_str:>12}  {train_end_str:>12}  {test_end_str:>12}  "
                  f"{t:>7,}  {wr:>5.1f}%  {sh:>7.2f}  ${pnl:>11,.0f}  [{src}]")

        fold_rows.append(row)
        fold_start += pd.Timedelta(days=step_days)
        fold_idx += 1

    df = pd.DataFrame(fold_rows)
    valid = df.dropna(subset=["net_pnl"]) if "net_pnl" in df.columns else pd.DataFrame()

    print(f"\n{'='*72}")
    print(f"Walk-forward complete: {fold_idx} folds  ({cached_count} cached, {computed_count} computed)")
    if not valid.empty:
        profitable = (valid["net_pnl"] > 0).sum()
        has_trades = (valid["total_trades"] > 0).sum()
        print(f"  Folds with trades:    {has_trades}/{len(valid)}")
        print(f"  Profitable folds:     {profitable}/{has_trades} (of folds with trades)")
        print(f"  Median Sharpe:        {valid['sharpe'].median():.2f}")
        print(f"  Median win rate:      {valid.loc[valid['total_trades']>0,'win_rate_pct'].median():.1f}%")
        print(f"  Total PnL (sum):      ${valid['net_pnl'].sum():,.0f}")
        print(f"  Folds with 0 trades:  {(valid['total_trades']==0).sum()}")

    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="Whale-following backtest at category level")
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research")
    parser.add_argument(
        "--resolutions-dir", type=Path, default=_project_root / "data" / "poly_cat",
        help="Directory containing an extra resolutions.csv to merge (e.g. data/poly_cat has 51k resolved markets)",
    )
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--min-usd", type=float, default=100)
    parser.add_argument("--position-size", type=float, default=25_000)
    parser.add_argument("--train-ratio", type=float, default=0.3)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument(
        "--category",
        default=None,
        help="Single category to run (e.g. Tech). Overrides --categories.",
    )
    parser.add_argument(
        "--categories",
        default=None,
        help="Comma-separated categories (default: all). Ignored if --category set.",
    )
    parser.add_argument(
        "--mode",
        choices=["combined", "per-category"],
        default="combined",
        help="combined: all categories together; per-category: run each category separately",
    )
    parser.add_argument("--db-path", default=None, help="DB for resolutions if no CSV")
    parser.add_argument(
        "--extract-resolutions",
        action="store_true",
        help="Extract resolutions from markets_filtered.csv before backtest (run if no resolutions.csv)",
    )
    parser.add_argument(
        "--surprise-only",
        action="store_true",
        help="Only follow whales with positive surprise. Requires resolutions. Overrides config whale_mode.",
    )
    parser.add_argument(
        "--volume-only",
        action="store_true",
        help="Whales = Nth percentile volume in market only. Overrides config whale_mode.",
    )
    parser.add_argument(
        "--unfavored-only",
        action="store_true",
        help="Only follow unfavored (underdog) trades: BUY <=40c, SELL >=60c. Overrides config.",
    )
    parser.add_argument("--output", default=None, help="Output CSV for trades")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_project_root / "data" / "output" / "whale_following",
        help="Output directory for QuantStats tearsheet and trades (default: data/output/whale_following)",
    )
    parser.add_argument("--no-quantstats", action="store_true", help="Skip QuantStats tearsheet")
    parser.add_argument("--no-tracker", action="store_true", help="Skip experiment tracker (experiments.db)")
    parser.add_argument("--backtests-dir", type=Path, default=_project_root / "backtests", help="Directory for backtest storage and catalog.db (default: backtests/)")
    parser.add_argument("--no-rebalance", action="store_true", help="Disable monthly whale rebalancing (use single train-period whale set)")
    parser.add_argument("--max-entry-price", type=float, default=None, help="Max YES price for entry (default 0.98)")
    parser.add_argument("--min-buy-yes-price", type=float, default=None,
                        help="Skip BUY signals with YES price below this (default 0.0 = disabled)")
    parser.add_argument("--max-sell-yes-price", type=float, default=None,
                        help="Skip SELL signals with YES price above this (default 1.0 = disabled)")
    parser.add_argument("--min-confirmation-whales", type=int, default=None, help="Distinct whales required to confirm signal (default 1)")
    parser.add_argument("--confirmation-window-days", type=int, default=None, help="Rolling window for confirmation (default 7)")
    parser.add_argument("--max-hold-days", type=int, default=None, help="Force-close positions after N days (default 0=off)")
    parser.add_argument("--min-ttr-days", type=int, default=None, help="Min scheduled days-to-close before allowing entry (default 3)")
    parser.add_argument("--no-partial-exit", action="store_true", help="Disable partial exit on large gains")
    parser.add_argument("--no-ic", action="store_true", help="Disable Information Coefficient scoring (IC)")
    parser.add_argument("--no-bayes", action="store_true", help="Disable Bayesian shrinkage (use raw win rate)")
    parser.add_argument("--no-recency", action="store_true", help="Disable recency decay (use uniform weighting)")
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run rolling walk-forward validation instead of single backtest",
    )
    parser.add_argument("--wf-window-months", type=int, default=12, help="Walk-forward total window per fold in months (default 12)")
    parser.add_argument("--wf-step-days", type=int, default=7, help="Walk-forward step between folds in days (default 7 = weekly)")
    parser.add_argument("--wf-min-train-months", type=int, default=6, help="Walk-forward minimum training period in months (default 6)")
    parser.add_argument("--wf-cache-dir", type=Path, default=None, help="Directory for walk-forward fold cache (default: data/output/whale_following/walk_forward_cache)")
    parser.add_argument(
        "--whale-cache-dir", type=Path,
        default=_project_root / "cache" / "whale_sets",
        help="Directory for per-week whale set cache (keyed by cutoff + params hash). "
             "Cache is auto-invalidated when qualifying parameters change. "
             "(default: cache/whale_sets/)",
    )
    parser.add_argument(
        "--sensitivity",
        action="store_true",
        help="Run one-at-a-time parameter sensitivity test to detect p-hacking",
    )
    parser.add_argument(
        "--sensitivity-params",
        default=None,
        help="Comma-separated parameters to test (default: all). "
             "Options: max_entry_yes_price, min_confirmation_whales, max_hold_days",
    )
    parser.add_argument(
        "--workers", type=int, default=35,
        help="Parallel workers: per-category mode distributes categories; combined mode parallelises monthly whale building (default: all CPUs)",
    )
    args = parser.parse_args()

    research_dir = args.research_dir

    # Extract resolutions first if requested or missing
    resolutions_path = research_dir / "resolutions.csv"
    if args.extract_resolutions or (not resolutions_path.exists() and args.db_path is None):
        import subprocess
        extract_script = _project_root / "scripts" / "data" / "extract_resolutions_from_markets.py"
        if extract_script.exists():
            cmd = [sys.executable, str(extract_script), "--research-dir", str(research_dir)]
            if args.category:
                cmd += ["--categories", args.category]
            elif args.categories:
                cmd += ["--categories", args.categories]
            try:
                subprocess.run(cmd, cwd=str(_project_root), check=True)
            except subprocess.CalledProcessError as e:
                print(f"Warning: Resolution extraction failed: {e}")

    # Resolve categories
    if args.category:
        categories = [args.category]
    elif args.categories:
        categories = [c.strip() for c in args.categories.split(",")]
    else:
        categories = None

    # Resolve whale mode from CLI or config
    whale_config = load_whale_config()
    if args.volume_only:
        whale_config.mode = "volume_only"
    if args.surprise_only:
        whale_config.mode = "surprise_only"
    if args.max_entry_price is not None:
        whale_config.max_entry_yes_price = args.max_entry_price
    if args.min_buy_yes_price is not None:
        whale_config.min_buy_yes_price = args.min_buy_yes_price
    if args.max_sell_yes_price is not None:
        whale_config.max_sell_yes_price = args.max_sell_yes_price
    if args.min_confirmation_whales is not None:
        whale_config.min_confirmation_whales = args.min_confirmation_whales
    if args.confirmation_window_days is not None:
        whale_config.confirmation_window_days = args.confirmation_window_days
    if args.max_hold_days is not None:
        whale_config.max_hold_days = args.max_hold_days
    if args.min_ttr_days is not None:
        whale_config.min_ttr_entry_days = args.min_ttr_days
    if args.no_partial_exit:
        whale_config.partial_exit_gain_threshold = 0.0
    if args.no_ic:
        whale_config.ic_score_weight = 0.0
    if args.no_bayes:
        whale_config.bayes_prior_alpha = 0.0
        whale_config.bayes_prior_beta = 0.0
    if args.no_recency:
        whale_config.recency_halflife_days = 0.0
    surprise_only = whale_config.surprise_only
    volume_only = whale_config.volume_only
    unfavored_only = whale_config.unfavored_only

    if surprise_only and not (research_dir / "resolutions.csv").exists() and not args.db_path:
        print("Error: --surprise-only requires resolutions. Run with --extract-resolutions first.")
        return 1

    print("Whale Category Backtest")
    print("  research_dir ", research_dir)
    print("  capital      ", args.capital)
    print("  min_usd      ", args.min_usd)
    print("  mode         ", args.mode)
    print("  whale_config ", whale_config)
    print("  surprise_only", surprise_only)
    print("  volume_only  ", volume_only)
    print("  unfavored_only", unfavored_only)
    print("  categories   ", categories or "all")
    print("  workers      ", args.workers)

    # Walk-forward mode — runs before (and instead of) the main backtest
    if args.walk_forward:
        wf_df = run_walk_forward_backtest(
            research_dir=research_dir,
            capital=args.capital,
            categories=categories,
            db_path=args.db_path,
            whale_config=whale_config,
            n_workers=args.workers,
            window_months=args.wf_window_months,
            step_days=args.wf_step_days,
            min_train_months=args.wf_min_train_months,
            cache_dir=args.wf_cache_dir,
        )
        if not wf_df.empty:
            out_path = args.output_dir / "walk_forward_results.csv"
            args.output_dir.mkdir(parents=True, exist_ok=True)
            wf_df.to_csv(out_path, index=False)
            print(f"Walk-forward results saved to {out_path}")
        return 0

    # Sensitivity test mode — runs before (and instead of) the main backtest
    if args.sensitivity:
        params_to_test = None
        if args.sensitivity_params:
            params_to_test = [p.strip() for p in args.sensitivity_params.split(",")]
        run_sensitivity_test(
            research_dir=research_dir,
            capital=args.capital,
            categories=categories,
            db_path=args.db_path,
            whale_config_base=whale_config,
            n_workers=args.workers,
            output_dir=args.output_dir,
            params_to_test=params_to_test,
        )
        return 0

    if args.mode == "per-category":
        # Run each category separately
        cats_to_run = categories or get_research_categories(research_dir)
        if not cats_to_run:
            print("No categories found.")
            return 1

        output_dir = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        all_results = []

        def _save_cat_result(cat, result):
            if "error" in result:
                print(f"  {cat}: Error: {result['error']}")
                return None
            cat_out = output_dir / cat.replace(" ", "_")
            cat_out.mkdir(parents=True, exist_ok=True)
            print(f"  {cat}: Trades={result['total_trades']:,}  Win%={result['win_rate']*100:.1f}  "
                  f"P&L=${result['total_net_pnl']:,.0f}  ROI={result['roi_pct']:.1f}%")
            if "trades_df" in result:
                result["trades_df"].to_csv(cat_out / "whale_backtest_trades.csv", index=False)
            if not args.no_quantstats and "daily_returns" in result and len(result["daily_returns"]) >= 5:
                ok = generate_quantstats_report(
                    result["daily_returns"],
                    str(cat_out / "quantstats_whale_following.html"),
                    title=f"Whale Following - {cat}",
                )
                if ok:
                    print(f"  QuantStats: {cat_out / 'quantstats_whale_following.html'}")
            return (cat, result)

        n_cat_workers = min(args.workers, len(cats_to_run))
        if n_cat_workers > 1:
            print(f"Running {len(cats_to_run)} categories in parallel ({n_cat_workers} workers)...")
            worker_args_list = [
                (cat, str(research_dir), args.capital, args.min_usd, args.position_size,
                 args.train_ratio, args.start_date, args.end_date, args.db_path,
                 dataclasses.asdict(whale_config),
                 "1M" if not args.no_rebalance else None)
                for cat in cats_to_run
            ]
            with ProcessPoolExecutor(max_workers=n_cat_workers) as pool:
                futures = {pool.submit(_category_backtest_worker, wa): wa[0] for wa in worker_args_list}
                for future in as_completed(futures):
                    cat = futures[future]
                    try:
                        cat, result = future.result()
                        r = _save_cat_result(cat, result)
                        if r:
                            all_results.append(r)
                    except Exception as exc:
                        print(f"  {cat}: Exception: {exc}")
        else:
            for cat in cats_to_run:
                print(f"\n--- Category: {cat} ---")
                result = run_whale_category_backtest(
                    research_dir=research_dir,
                    capital=args.capital,
                    min_usd=args.min_usd,
                    position_size=args.position_size,
                    train_ratio=args.train_ratio,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    categories=[cat],
                    db_path=args.db_path,
                    whale_config=whale_config,
                    surprise_only=surprise_only,
                    volume_only=volume_only,
                    unfavored_only=unfavored_only,
                    rebalance_freq="1W" if not args.no_rebalance else None,
                    n_workers=1,
                    whale_cache_dir=args.whale_cache_dir,
                )
                r = _save_cat_result(cat, result)
                if r:
                    all_results.append(r)

        if not all_results:
            return 1

        print("\n" + "=" * 60)
        print("SUMMARY (per-category)")
        print("=" * 60)
        for cat, r in all_results:
            print(f"  {cat}: {r['total_trades']} trades, {r['win_rate']*100:.1f}% win, "
                  f"${r['total_net_pnl']:,.0f} P&L, {r['roi_pct']:.1f}% ROI")
        return 0

    # combined mode (default)
    result = run_whale_category_backtest(
        research_dir=research_dir,
        capital=args.capital,
        min_usd=args.min_usd,
        position_size=args.position_size,
        train_ratio=args.train_ratio,
        start_date=args.start_date,
        end_date=args.end_date,
        categories=categories,
        db_path=args.db_path,
        whale_config=whale_config,
        surprise_only=surprise_only,
        volume_only=volume_only,
        unfavored_only=unfavored_only,
        rebalance_freq="1W" if not args.no_rebalance else None,
        n_workers=args.workers,
        extra_resolutions_dir=args.resolutions_dir,
        whale_cache_dir=args.whale_cache_dir,
    )

    if "error" in result:
        print(f"\nError: {result['error']}")
        if "whales" in result:
            print(f"  Whales: {result['whales']}, Signals: {result.get('signals', 0)}")
        return 1

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Total Trades:     {result['total_trades']:,}")
    print(f"Win Rate:         {result['win_rate']*100:.1f}%")
    print(f"Net P&L:         ${result['total_net_pnl']:,.2f}")
    print(f"ROI:              {result['roi_pct']:.2f}%")
    print(f"Whales Followed:  {result['whales_followed']:,}")
    print(f"Signals:          {result['signals_processed']:,}")
    print(f"Categories:       {', '.join(result['categories'])}")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.output and "trades_df" in result:
        result["trades_df"].to_csv(args.output, index=False)
        print(f"\nTrades saved to {args.output}")
    elif "trades_df" in result:
        trades_path = output_dir / "whale_backtest_trades.csv"
        result["trades_df"].to_csv(trades_path, index=False)
        print(f"\nTrades saved to {trades_path}")

    quantstats_html_path = None
    if not args.no_quantstats and "daily_returns" in result and len(result["daily_returns"]) >= 5:
        quantstats_path = output_dir / "quantstats_whale_following.html"
        if generate_quantstats_report(
            result["daily_returns"],
            str(quantstats_path),
            title="Whale Following Strategy (Category-Level)",
        ):
            print(f"QuantStats tearsheet: {quantstats_path}")
            quantstats_html_path = quantstats_path

    if not args.no_tracker:
        try:
            from src.backtest.storage import save_backtest_result
            import dataclasses as _dc
            run_id = save_backtest_result(
                strategy_name="whale_following",
                result=result,
                config=_dc.asdict(whale_config),
                base_dir=args.backtests_dir,
                tags=[args.mode] + ([f"cat:{c}" for c in (categories or [])] if categories else []),
                notes=f"capital={args.capital} min_usd={args.min_usd} mode={args.mode}",
                quantstats_html_path=quantstats_html_path,
                auto_index=True,
            )
            print(f"Experiment saved: {args.backtests_dir}/whale_following/{run_id}")
        except Exception as _exc:
            print(f"Warning: experiment tracker failed: {_exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

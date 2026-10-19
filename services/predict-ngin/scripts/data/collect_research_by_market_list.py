#!/usr/bin/env python3
"""
Collect research data from Polymarket using a curated market list by category.

Reads market CSVs from Polymarket/ (one folder per category, each with markets.csv).
For each category, keeps top 500 markets by volume OR until cumulative volume >= 50,000
(whichever comes first). Then filters/copies trades and prices from existing parquet
(or DB) into data/research/{category}/.

Usage:
    # Use default Polymarket/ and data/polymarket, output to data/research
    python scripts/data/collect_research_by_market_list.py

    # Custom paths
    python scripts/data/collect_research_by_market_list.py \
        --market-list-dir Polymarket \
        --data-dir data/polymarket \
        --output-dir data/research

    # Only build filtered market list (no trades/prices copy)
    python scripts/data/collect_research_by_market_list.py --markets-only
"""

import argparse
import ast
import gc
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

# Project root
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

import numpy as np
import pandas as pd

from predict_ngin.markets.polymarket_tokens import parse_list_field


# --- Market list from Polymarket/ folder ---

def _safe_float(x, default: float = 0.0) -> float:
    try:
        return float(x) if x not in (None, "", "nan") else default
    except (TypeError, ValueError):
        return default


def _parse_clob_token_ids(raw) -> List[str]:
    """Parse clobTokenIds from CSV (JSON, Python repr or the doubled-quote export)."""
    return parse_list_field(raw)


def load_category_markets(market_list_dir: Path) -> Dict[str, pd.DataFrame]:
    """
    Load markets from Polymarket/{Category}/markets.csv for each category.
    Returns dict category_name -> DataFrame with id, conditionId, slug, volume, clobTokenIds, etc.
    """
    out: Dict[str, pd.DataFrame] = {}
    if not market_list_dir.exists():
        return out

    for sub in sorted(market_list_dir.iterdir()):
        if not sub.is_dir():
            continue
        csv_path = sub / "markets.csv"
        if not csv_path.exists():
            continue
        cat_name = sub.name
        try:
            df = pd.read_csv(csv_path, low_memory=False)
        except Exception as e:
            print(f"  Warning: could not read {csv_path}: {e}")
            continue
        if df.empty or "id" not in df.columns:
            continue
        # Normalize volume
        if "volume" not in df.columns and "volumeNum" in df.columns:
            df["volume"] = df["volumeNum"]
        df["volume"] = df["volume"].apply(_safe_float)
        out[cat_name] = df

    return out


def filter_markets_per_category(
    category_dfs: Dict[str, pd.DataFrame],
    top_n: int = 500,
    min_cum_volume: Optional[float] = None,
) -> Dict[str, pd.DataFrame]:
    """
    For each category, keep top `top_n` markets by volume. If min_cum_volume is set
    (e.g. 50_000), stop earlier when cumulative volume >= min_cum_volume (whichever comes first).
    Use min_cum_volume=None or 0 to take exactly top_n markets.
    """
    result: Dict[str, pd.DataFrame] = {}
    use_volume_cap = min_cum_volume is not None and min_cum_volume > 0
    for cat, df in category_dfs.items():
        df = df.sort_values("volume", ascending=False).reset_index(drop=True)
        idx = min(top_n, len(df))
        if use_volume_cap:
            cum = df["volume"].cumsum()
            for i in range(len(df)):
                if (i + 1) >= top_n or cum.iloc[i] >= min_cum_volume:
                    idx = i + 1
                    break
        result[cat] = df.iloc[:idx].copy()
    return result


def normalize_category_dir_name(name: str) -> str:
    """Use a filesystem-safe category folder name (e.g. 'Climate and Science' -> 'Climate_and_Science')."""
    return re.sub(r"[^\w\-]", "_", name).strip("_") or "unknown"


# --- Copy trades and prices from parquet into data/research ---

def copy_trades_by_market_ids_streaming(
    market_ids: Set[str],
    parquet_trades_dir: Path,
    output_dir: Path,
    flush_rows: int = 50_000,
) -> int:
    """Stream parquet trade files, filter by market_id, write to output_dir."""
    files = sorted(parquet_trades_dir.glob("trades_*.parquet"))
    if not files:
        return 0

    ids_str = {str(m) for m in market_ids}
    total = 0
    accum: List[pd.DataFrame] = []
    out_path = output_dir / "trades.parquet"

    for f in files:
        try:
            df = pd.read_parquet(f)
        except Exception:
            continue
        if "market_id" not in df.columns:
            continue
        df["market_id_str"] = df["market_id"].astype(str).str.replace(r"\.0$", "", regex=True)
        df = df[df["market_id_str"].isin(ids_str)].drop(columns=["market_id_str"], errors="ignore")
        if df.empty:
            continue
        accum.append(df)
        total += len(df)
        if sum(len(d) for d in accum) >= flush_rows:
            combined = pd.concat(accum, ignore_index=True)
            if out_path.exists():
                existing = pd.read_parquet(out_path)
                combined = pd.concat([existing, combined], ignore_index=True)
            combined.to_parquet(out_path, index=False)
            accum = []
            gc.collect()

    if accum:
        combined = pd.concat(accum, ignore_index=True)
        if out_path.exists():
            existing = pd.read_parquet(out_path)
            combined = pd.concat([existing, combined], ignore_index=True)
        combined.to_parquet(out_path, index=False)
        total = len(combined)

    return total


def copy_prices_by_market_ids(
    market_ids: Set[str],
    parquet_prices_dir: Path,
    output_dir: Path,
    flush_rows: int = 100_000,
) -> int:
    """Load prices for given market_ids from PriceStore and write to output_dir."""
    if not parquet_prices_dir.exists():
        return 0
    files = list(parquet_prices_dir.glob("prices_*.parquet"))
    if not files:
        return 0

    try:
        from trading.data_modules.parquet_store import PriceStore
    except ImportError:
        return 0

    store = PriceStore(str(parquet_prices_dir))
    if not store.available():
        return 0

    mid_list = [str(m) for m in market_ids]
    df = store.load_prices_for_markets(mid_list, outcome="YES")
    if df.empty:
        return 0
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "prices.parquet"
    df.to_parquet(out_path, index=False)
    return len(df)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect research data by category from Polymarket market list"
    )
    parser.add_argument(
        "--market-list-dir",
        type=Path,
        default=_project_root / "Polymarket",
        help="Root folder containing category subfolders with markets.csv",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_project_root / "data" / "polymarket",
        help="Parquet data root (trades/, prices/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_project_root / "data" / "research",
        help="Output root: data/research/{category}/",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=500,
        help="Max markets per category by volume (default 500)",
    )
    parser.add_argument(
        "--min-cum-volume",
        type=float,
        default=None,
        metavar="FLOAT",
        help="If set, stop when cumulative volume reaches this (default: unset = use only --top-n)",
    )
    parser.add_argument(
        "--markets-only",
        action="store_true",
        help="Only write filtered market lists; do not copy trades/prices",
    )
    parser.add_argument(
        "--flush-rows",
        type=int,
        default=50_000,
        help="Flush trades to disk after this many rows (default 50000)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("COLLECT RESEARCH DATA BY MARKET LIST")
    print("=" * 60)
    print(f"Market list dir: {args.market_list_dir}")
    print(f"Data dir:        {args.data_dir}")
    print(f"Output dir:      {args.output_dir}")
    cap = f" or cum volume >= {args.min_cum_volume:,.0f}" if (args.min_cum_volume is not None and args.min_cum_volume > 0) else ""
    print(f"Per category:    top {args.top_n} markets{cap}")
    print()

    # Load all category CSVs
    category_dfs = load_category_markets(args.market_list_dir)
    if not category_dfs:
        print("No category markets found. Ensure Polymarket/{Category}/markets.csv exist.")
        return 1

    print(f"Loaded {len(category_dfs)} categories: {list(category_dfs.keys())}")

    # Filter per category
    filtered = filter_markets_per_category(
        category_dfs,
        top_n=args.top_n,
        min_cum_volume=args.min_cum_volume,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    all_market_ids: Set[str] = set()
    for cat, df in filtered.items():
        cat_dir_name = normalize_category_dir_name(cat)
        out_cat = args.output_dir / cat_dir_name
        out_cat.mkdir(parents=True, exist_ok=True)
        # Save filtered market list
        out_csv = out_cat / "markets_filtered.csv"
        df.to_csv(out_csv, index=False)
        ids = set(df["id"].astype(str).str.replace(r"\.0$", "", regex=True))
        all_market_ids |= ids
        print(f"  {cat_dir_name}: {len(df)} markets, volume sum = {df['volume'].sum():,.0f} -> {out_csv.name}")

    if args.markets_only:
        print()
        print("Done (markets only). Run without --markets-only to copy trades and prices.")
        return 0

    # Copy trades and prices from parquet
    trades_dir = args.data_dir / "trades"
    prices_dir = args.data_dir / "prices"

    for cat, df in filtered.items():
        cat_dir_name = normalize_category_dir_name(cat)
        out_cat = args.output_dir / cat_dir_name
        ids = set(df["id"].astype(str).str.replace(r"\.0$", "", regex=True))

        if trades_dir.exists():
            n_trades = copy_trades_by_market_ids_streaming(
                ids, trades_dir, out_cat, flush_rows=args.flush_rows
            )
            print(f"  {cat_dir_name}: {n_trades:,} trades -> {out_cat / 'trades.parquet'}")
        else:
            print(f"  {cat_dir_name}: no trades dir at {trades_dir}")

        if prices_dir.exists():
            n_prices = copy_prices_by_market_ids(ids, prices_dir, out_cat, flush_rows=args.flush_rows)
            print(f"  {cat_dir_name}: {n_prices:,} price rows -> {out_cat / 'prices.parquet'}")
        else:
            print(f"  {cat_dir_name}: no prices dir at {prices_dir}")

    print()
    print("=" * 60)
    print("DONE. Research data under:", args.output_dir)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

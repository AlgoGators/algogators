#!/usr/bin/env python3
"""
Data integrity check for poly_cat / research directories.

Diagnoses why whale backtests produce zero or near-zero signals:
  - Trade row counts and date ranges per category
  - Market ID format consistency (trades vs resolutions vs markets CSV)
  - Resolution coverage (how many traded markets have a known winner)
  - proxyWallet / maker column presence (needed for whale identification)
  - Quick whale identification smoke test

Usage:
    python scripts/data/check_data_integrity.py
    python scripts/data/check_data_integrity.py --research-dir data/poly_cat
    python scripts/data/check_data_integrity.py --research-dir data/poly_cat --categories Politics,Geopolitics
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

SEP = "=" * 70


def fmt(n):
    return f"{n:,}" if isinstance(n, int) else str(n)


def check_resolutions(research_dir: Path):
    print(f"\n{SEP}")
    print("RESOLUTIONS")
    print(SEP)
    p = research_dir / "resolutions.csv"
    if not p.exists():
        print(f"  MISSING: {p}")
        print("  Fix: python scripts/data/extract_resolutions_from_markets.py --research-dir", research_dir)
        return {}
    df = pd.read_csv(p)
    print(f"  File   : {p}")
    print(f"  Rows   : {fmt(len(df))}")
    print(f"  Cols   : {list(df.columns)}")
    if "winner" in df.columns:
        print(f"  Winners: {df['winner'].value_counts().to_dict()}")
    if "market_id" in df.columns:
        sample = df["market_id"].dropna().iloc[0] if len(df) else "N/A"
        print(f"  ID sample : {sample}")
        starts_0x = df["market_id"].astype(str).str.startswith("0x").mean()
        print(f"  Starts 0x : {starts_0x:.1%}")
    return dict(zip(df["market_id"].astype(str), df["winner"])) if "market_id" in df.columns else {}


def check_category(cat_dir: Path, resolutions: dict):
    print(f"\n{SEP}")
    print(f"CATEGORY: {cat_dir.name}")
    print(SEP)

    # ── markets CSV ───────────────────────────────────────────────────────
    mcsv = cat_dir / "markets_filtered.csv"
    if mcsv.exists():
        mdf = pd.read_csv(mcsv, low_memory=False)
        print(f"  markets_filtered.csv : {fmt(len(mdf))} rows")
        if "conditionId" in mdf.columns:
            n_closed = mdf["closed"].sum() if "closed" in mdf.columns else "?"
            print(f"  closed markets       : {n_closed}")
            mids = set(mdf["conditionId"].astype(str))
            res_overlap = len(mids & set(resolutions.keys()))
            print(f"  markets with resolution : {fmt(res_overlap)} / {fmt(len(mids))}")
    else:
        print("  markets_filtered.csv : MISSING")

    # ── trades parquet ────────────────────────────────────────────────────
    tp = cat_dir / "trades.parquet"
    if not tp.exists():
        print("  trades.parquet       : MISSING")
        return
    df = pd.read_parquet(tp)
    print(f"\n  trades.parquet       : {fmt(len(df))} rows")
    print(f"  columns              : {list(df.columns)}")

    # market_id check
    mid_col = None
    for c in ["market_id", "conditionId"]:
        if c in df.columns:
            mid_col = c
            break
    if mid_col:
        n_markets = df[mid_col].nunique()
        sample_mid = df[mid_col].dropna().iloc[0] if len(df) else "N/A"
        starts_0x = df[mid_col].astype(str).str.startswith("0x").mean()
        trade_mids = set(df[mid_col].astype(str))
        res_overlap = len(trade_mids & set(resolutions.keys()))
        print(f"  market_id column     : {mid_col}")
        print(f"  unique markets       : {fmt(n_markets)}")
        print(f"  market_id sample     : {sample_mid}")
        print(f"  market_id starts 0x  : {starts_0x:.1%}")
        print(f"  markets with resolution : {fmt(res_overlap)} / {fmt(n_markets)}")
        if res_overlap == 0:
            print("  *** WARNING: zero overlap between trade market_ids and resolutions ***")
            print("  Likely cause: market_id format mismatch")
            print(f"  Trade ID  sample : {sample_mid}")
            if resolutions:
                print(f"  Resolution sample: {next(iter(resolutions))}")
    else:
        print("  *** WARNING: no market_id or conditionId column in trades ***")

    # timestamp / date range
    for ts_col in ["timestamp", "created_at", "datetime"]:
        if ts_col in df.columns:
            ts = pd.to_numeric(df[ts_col], errors="coerce").dropna()
            if len(ts):
                if ts.max() > 1e10:  # milliseconds
                    ts = ts / 1000
                t_min = pd.to_datetime(ts.min(), unit="s")
                t_max = pd.to_datetime(ts.max(), unit="s")
                print(f"  date range           : {t_min.date()} -> {t_max.date()}  (col={ts_col})")
            break

    # maker / proxyWallet check
    for wc in ["proxyWallet", "maker", "taker"]:
        if wc in df.columns:
            n_valid = df[wc].astype(str).str.startswith("0x").sum()
            print(f"  wallet col '{wc}'  : {fmt(n_valid)} valid 0x addresses ({n_valid/len(df):.1%})")
            break
    else:
        print("  *** WARNING: no proxyWallet/maker/taker column found ***")

    # usd_amount check
    if "usd_amount" in df.columns:
        usd = pd.to_numeric(df["usd_amount"], errors="coerce")
        print(f"  usd_amount           : min={usd.min():.2f}  median={usd.median():.2f}  max={usd.max():.0f}")
        print(f"  trades >= $10K       : {fmt((usd >= 10_000).sum())} ({(usd >= 10_000).mean():.1%})")
    elif "price" in df.columns and "size" in df.columns:
        usd = pd.to_numeric(df["price"], errors="coerce") * pd.to_numeric(df["size"], errors="coerce")
        print(f"  usd (price*size)     : median={usd.median():.2f}  max={usd.max():.0f}")
        print(f"  trades >= $10K       : {fmt((usd >= 10_000).sum())} ({(usd >= 10_000).mean():.1%})")
    else:
        print("  *** WARNING: no usd_amount, price, or size column ***")

    # prices parquet
    pp = cat_dir / "prices.parquet"
    if pp.exists():
        pf = pd.read_parquet(pp)
        print(f"\n  prices.parquet       : {fmt(len(pf))} rows, {pf['market_id'].nunique() if 'market_id' in pf.columns else '?'} markets")
    else:
        print(f"\n  prices.parquet       : MISSING")


def whale_smoke_test(research_dir: Path, categories: list):
    print(f"\n{SEP}")
    print("WHALE IDENTIFICATION SMOKE TEST")
    print(SEP)
    try:
        from whale_strategy.research_data_loader import (
            load_research_trades, load_resolution_winners
        )
        from whale_strategy.whale_surprise import identify_whales_rolling

        trades = load_research_trades(research_dir, categories=categories, min_usd=10.0)
        resolutions = load_resolution_winners(research_dir)

        print(f"  Loaded trades : {fmt(len(trades))} rows")
        print(f"  Resolutions   : {fmt(len(resolutions))}")

        if trades.empty:
            print("  *** FAIL: no trades loaded ***")
            return
        if not resolutions:
            print("  *** FAIL: no resolutions loaded — run extract_resolutions_from_markets.py first ***")
            return

        # Check market_id overlap
        trade_mids = set(trades["market_id"].astype(str))
        res_overlap = len(trade_mids & set(resolutions.keys()))
        print(f"  Trade markets with resolution: {fmt(res_overlap)} / {fmt(len(trade_mids))}")
        if res_overlap == 0:
            print("  *** FAIL: no market_id overlap — check ID format ***")
            return

        # Whale identification
        sample = trades.sample(min(50_000, len(trades)), random_state=42)
        wdf = identify_whales_rolling(sample, volume_only=True, volume_percentile=90.0)
        n_whale = wdf["is_whale"].sum()
        print(f"  Whale trades (90pct, sample) : {fmt(n_whale)} / {fmt(len(wdf))} ({n_whale/len(wdf):.1%})")
        if n_whale == 0:
            print("  *** WARNING: zero whale trades identified ***")
        else:
            print("  Smoke test PASSED")
    except Exception as e:
        print(f"  Smoke test ERROR: {e}")
        import traceback
        traceback.print_exc()


def main():
    p = argparse.ArgumentParser(description="Data integrity check for whale backtest pipeline")
    p.add_argument("--research-dir", type=Path, default=Path("data/poly_cat"))
    p.add_argument("--categories", default=None,
                   help="Comma-separated categories (default: all with trades.parquet)")
    args = p.parse_args()

    rd = args.research_dir
    if not rd.exists():
        print(f"ERROR: research dir not found: {rd}")
        sys.exit(1)

    cats = [c.strip() for c in args.categories.split(",")] if args.categories else None

    print(SEP)
    print(f"DATA INTEGRITY CHECK — {rd}")
    print(SEP)

    resolutions = check_resolutions(rd)

    cat_dirs = sorted([d for d in rd.iterdir() if d.is_dir()])
    if cats:
        cat_dirs = [d for d in cat_dirs if d.name in cats]

    for cat_dir in cat_dirs:
        if (cat_dir / "trades.parquet").exists() or (cat_dir / "markets_filtered.csv").exists():
            check_category(cat_dir, resolutions)

    whale_smoke_test(rd, cats)

    print(f"\n{SEP}")
    print("DONE")
    print(SEP)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Extract resolution data from data/research/{category}/markets_filtered.csv.

Parses outcomePrices for closed markets to determine winner (YES/NO).
Writes data/research/resolutions.csv for use by whale backtest and research.

Run this BEFORE whale backtest when you don't have prediction_markets.db.

Usage:
    python scripts/data/extract_resolutions_from_markets.py
    python scripts/data/extract_resolutions_from_markets.py --research-dir data/research --categories Tech,Politics
"""

import argparse
import ast
import re
import sys
from pathlib import Path

import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))


def _parse_outcome_prices(raw) -> list:
    """Parse outcomePrices from CSV (e.g. '[\"1\",\"0\"]' or similar)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    s = str(raw).strip()
    if not s or s == "[]":
        return []
    # Handle escaped-quote format from Polymarket CSV
    s = re.sub(r'"+', '"', s)
    try:
        out = ast.literal_eval(s)
        return [str(x).strip() for x in out] if isinstance(out, list) else []
    except (ValueError, SyntaxError):
        pass
    # Fallback: extract numbers
    nums = re.findall(r"[\d.]+", s)
    return nums[:2] if nums else []


def _determine_winner(outcome_prices: list) -> str | None:
    """YES if first ~1 and second ~0; NO if first ~0 and second ~1."""
    if not outcome_prices or len(outcome_prices) < 2:
        return None
    try:
        yes_p = float(outcome_prices[0])
        no_p = float(outcome_prices[1])
    except (ValueError, TypeError):
        return None
    if yes_p > 0.95 and no_p < 0.05:
        return "YES"
    if no_p > 0.95 and yes_p < 0.05:
        return "NO"
    return None


def extract_resolutions(
    research_dir: Path,
    categories: list[str] | None = None,
) -> pd.DataFrame:
    """Extract resolutions from markets_filtered.csv in each category."""
    research_dir = Path(research_dir)
    rows = []

    for cat_dir in research_dir.iterdir():
        if not cat_dir.is_dir():
            continue
        if categories and cat_dir.name not in categories:
            continue
        p = cat_dir / "markets_filtered.csv"
        if not p.exists():
            continue

        df = pd.read_csv(p, low_memory=False)
        if df.empty or "conditionId" not in df.columns:
            continue

        for _, row in df.iterrows():
            closed_val = row.get("closed", False)
            if isinstance(closed_val, str):
                closed_val = str(closed_val).lower() in ("true", "1", "yes")
            if not closed_val:
                continue
            cid = str(row.get("conditionId", "")).strip()
            if not cid or not cid.startswith("0x"):
                continue

            op = _parse_outcome_prices(row.get("outcomePrices"))
            winner = _determine_winner(op)
            if winner:
                rows.append({"market_id": cid, "winner": winner, "category": cat_dir.name})

    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract resolutions from research markets CSV")
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research")
    parser.add_argument("--categories", default=None, help="Comma-separated (default: all)")
    parser.add_argument("--output", type=Path, default=None, help="Output path (default: research_dir/resolutions.csv)")
    args = parser.parse_args()

    categories = [c.strip() for c in args.categories.split(",")] if args.categories else None

    print("Extracting resolutions from markets_filtered.csv...")
    df = extract_resolutions(args.research_dir, categories)

    if df.empty:
        print("No resolved markets found.")
        return 1

    out_path = args.output or args.research_dir / "resolutions.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df[["market_id", "winner"]].drop_duplicates(subset=["market_id"], keep="first").to_csv(
        out_path, index=False
    )
    print(f"Wrote {df['market_id'].nunique()} resolutions to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

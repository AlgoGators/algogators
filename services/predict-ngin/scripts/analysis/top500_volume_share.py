#!/usr/bin/env python3
"""
Compute what fraction of category volume the top 500 markets constitute.

Uses Polymarket/{Category}/markets.csv (reported volume per market).
For each category: total volume of all markets vs volume of top 500 by volume.
"""

import sys
from pathlib import Path

import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent


def _safe_float(x, default: float = 0.0) -> float:
    try:
        return float(x) if x not in (None, "", "nan") else default
    except (TypeError, ValueError):
        return default


def main() -> int:
    polymarket_dir = _project_root / "Polymarket"
    out_dir = _project_root / "data" / "research" / "user_research"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for sub in sorted(polymarket_dir.iterdir()):
        if not sub.is_dir():
            continue
        csv_path = sub / "markets.csv"
        if not csv_path.exists():
            continue
        cat_name = sub.name
        df = pd.read_csv(csv_path, low_memory=False)
        if df.empty or "id" not in df.columns:
            continue
        if "volume" not in df.columns and "volumeNum" in df.columns:
            df["volume"] = df["volumeNum"]
        df["volume"] = df["volume"].apply(_safe_float)
        df = df[df["volume"] > 0]

        total_vol = df["volume"].sum()
        df_sorted = df.sort_values("volume", ascending=False).reset_index(drop=True)
        top500 = df_sorted.iloc[:500]
        top500_vol = top500["volume"].sum()
        n_markets = len(df)
        pct = 100 * top500_vol / total_vol if total_vol > 0 else 0

        rows.append({
            "category": cat_name,
            "total_markets": n_markets,
            "total_volume_usd": total_vol,
            "top500_volume_usd": top500_vol,
            "top500_share_pct": pct,
            "top500_markets_used": min(500, n_markets),
        })
        print(f"  {cat_name}: top 500 = {pct:.1f}% of volume ({top500_vol:,.0f} / {total_vol:,.0f} USD)")

    if not rows:
        print("No categories found.")
        return 1

    out = pd.DataFrame(rows)
    out.to_csv(out_dir / "top500_volume_share_by_category.csv", index=False)
    print(f"\nWrote {out_dir / 'top500_volume_share_by_category.csv'}")

    # Also append to volume_trade_analysis.md
    md_path = out_dir / "volume_trade_analysis.md"
    with open(md_path, "a") as f:
        f.write("\n## Top 500 markets volume share\n")
        f.write("(From Polymarket reported volume in markets.csv)\n\n")
        f.write("| Category | Total volume | Top 500 volume | Share |\n")
        f.write("|---------|--------------|----------------|-------|\n")
        for r in rows:
            f.write(f"| {r['category']} | ${r['total_volume_usd']:,.0f} | ${r['top500_volume_usd']:,.0f} | {r['top500_share_pct']:.1f}% |\n")
    print(f"Updated {md_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

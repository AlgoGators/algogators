#!/usr/bin/env python3
"""
Analyze users per market from saved activity data and research market lists.

Reads activity_*.json from data/research/users/ (from fetch_user_activity_summary --save-activity),
builds per-market stats: unique users, trade count, volume. Optionally joins with
research categories. Writes CSV and a short summary report.

Usage:
    python scripts/analysis/analyze_users_per_market.py
    python scripts/analysis/analyze_users_per_market.py --activity-dir data/research/users --output-dir data/research/users
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))


def load_all_activity(activity_dir: Path) -> List[Dict[str, Any]]:
    """Load and flatten all activity_*.json files into a list of records."""
    records: List[Dict[str, Any]] = []
    for f in activity_dir.glob("activity_*.json"):
        try:
            with open(f) as fp:
                data = json.load(fp)
            if isinstance(data, list):
                for r in data:
                    r["_source_file"] = f.name
                    records.append(r)
            else:
                data["_source_file"] = f.name
                records.append(data)
        except Exception:
            continue
    return records


def extract_wallet_from_filename(name: str) -> str:
    """activity_0x1234567890.json -> 0x1234567890 (partial). Use _source_file to match."""
    if name.startswith("activity_") and name.endswith(".json"):
        return name.replace("activity_", "").replace(".json", "")
    return ""


def build_per_market_stats(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """Aggregate by conditionId (market): unique users, trade count, volume."""
    if not records:
        return pd.DataFrame(columns=["conditionId", "slug", "title", "unique_users", "trade_count", "volume_usd", "avg_trade_usd"])

    rows = []
    for r in records:
        if r.get("type") != "TRADE":
            continue
        cid = r.get("conditionId")
        if not cid:
            continue
        wallet = r.get("proxyWallet") or extract_wallet_from_filename(r.get("_source_file", ""))
        usd = None
        if r.get("usdcSize") is not None:
            try:
                usd = float(r["usdcSize"])
            except (TypeError, ValueError):
                pass
        if usd is None and r.get("size") is not None and r.get("price") is not None:
            try:
                usd = float(r["size"]) * float(r["price"])
            except (TypeError, ValueError):
                pass
        rows.append({
            "conditionId": cid,
            "slug": r.get("slug"),
            "title": (r.get("title") or "")[:200],
            "proxyWallet": wallet,
            "usd": usd or 0,
            "side": r.get("side"),
            "timestamp": r.get("timestamp"),
        })

    if not rows:
        return pd.DataFrame(columns=["conditionId", "slug", "title", "unique_users", "trade_count", "volume_usd", "avg_trade_usd"])

    df = pd.DataFrame(rows)
    agg = df.groupby("conditionId").agg(
        unique_users=("proxyWallet", "nunique"),
        trade_count=("proxyWallet", "count"),
        volume_usd=("usd", "sum"),
    ).reset_index()
    agg["avg_trade_usd"] = (agg["volume_usd"] / agg["trade_count"]).round(2)
    # Attach first slug/title for display
    meta = df.groupby("conditionId").agg(slug=("slug", "first"), title=("title", "first")).reset_index()
    agg = agg.merge(meta, on="conditionId", how="left")
    return agg[["conditionId", "slug", "title", "unique_users", "trade_count", "volume_usd", "avg_trade_usd"]]


def load_research_markets(research_dir: Path) -> pd.DataFrame:
    """Load all markets_filtered.csv and add category column."""
    dfs = []
    for cat_dir in research_dir.iterdir():
        if not cat_dir.is_dir():
            continue
        p = cat_dir / "markets_filtered.csv"
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, low_memory=False)
            df["category"] = cat_dir.name
            dfs.append(df)
        except Exception:
            continue
    if not dfs:
        return pd.DataFrame()
    out = pd.concat(dfs, ignore_index=True)
    if "conditionId" in out.columns:
        out["conditionId"] = out["conditionId"].astype(str).str.strip()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze users per market from activity data")
    parser.add_argument("--activity-dir", type=Path, default=_project_root / "data" / "research" / "users", help="Dir with activity_*.json and user_stats.csv")
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research", help="Research root with category/markets_filtered.csv")
    parser.add_argument("--output-dir", type=Path, default=None, help="Write outputs here (default: activity-dir)")
    args = parser.parse_args()
    out_dir = args.output_dir or args.activity_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load activity
    activity_records = load_all_activity(args.activity_dir)
    print(f"Loaded {len(activity_records)} activity records from {args.activity_dir}")

    if not activity_records:
        print("No activity records found. Run fetch_user_activity_summary.py with --save-activity first.")
        return 1

    # Per-market stats
    per_market = build_per_market_stats(activity_records)
    per_market = per_market.sort_values("unique_users", ascending=False).reset_index(drop=True)
    per_market.to_csv(out_dir / "analysis_users_per_market.csv", index=False)
    print(f"Wrote {out_dir / 'analysis_users_per_market.csv'} ({len(per_market)} markets)")

    # Join with research categories if available
    research_markets = load_research_markets(args.research_dir)
    if not research_markets.empty and "conditionId" in research_markets.columns:
        research_markets["conditionId"] = research_markets["conditionId"].astype(str).str.strip()
        per_market = per_market.merge(
            research_markets[["conditionId", "category", "question", "volume"]].drop_duplicates("conditionId"),
            on="conditionId",
            how="left",
        )
        per_market.to_csv(out_dir / "analysis_users_per_market.csv", index=False)
        by_cat = per_market.groupby("category").agg(
            markets_with_activity=("conditionId", "count"),
            total_unique_users=("unique_users", "sum"),  # not unique across cats
            total_trades=("trade_count", "sum"),
            total_volume=("volume_usd", "sum"),
        ).reset_index()
        by_cat.to_csv(out_dir / "analysis_by_category.csv", index=False)
        print(f"Wrote {out_dir / 'analysis_by_category.csv'}")

    # Unique users overall (from raw records)
    trade_records = [r for r in activity_records if r.get("type") == "TRADE"]
    unique_wallets = set()
    for r in trade_records:
        w = r.get("proxyWallet")
        if w:
            unique_wallets.add(str(w).strip())
        else:
            w = extract_wallet_from_filename(r.get("_source_file", ""))
            if w:
                unique_wallets.add(w)

    summary = {
        "total_activity_records": len(activity_records),
        "markets_with_activity": len(per_market),
        "unique_users_overall": len(unique_wallets),
        "total_trades": int(per_market["trade_count"].sum()),
        "total_volume_usd": float(per_market["volume_usd"].sum()),
        "top_5_markets_by_users": per_market.head(5).to_dict("records"),
    }
    def _sanitize(obj):
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(x) for x in obj]
        if isinstance(obj, float) and (obj != obj or obj == float("inf") or obj == float("-inf")):
            return None
        return obj

    report_path = out_dir / "analysis_users_per_market_report.json"
    with open(report_path, "w") as f:
        json.dump(_sanitize(summary), f, indent=2)
    print(f"Wrote {report_path}")

    # Load overall user profile summary if present
    profile_path = args.activity_dir / "user_activity_summary.json"
    if profile_path.exists():
        with open(profile_path) as f:
            profile = json.load(f)
        print("\n--- User profile summary (from user_activity_summary.json) ---")
        for k, v in profile.items():
            print(f"  {k}: {v}")

    print("\n--- Per-market summary (top 10 by unique users) ---")
    print(per_market.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Run full user-behavior research on ALL trade data (no sampling).

Loads every trade from data/research/{category}/trades.parquet, computes:
- Core: holding duration, markets per user, trade size, concurrent markets
- Extra: tenure (first/last trade), buy/sell ratio, trades per day, trade size percentiles
- Activity: trades by hour of day, by day of week
- Cross-category: category overlap (users in multiple categories)

Writes to data/research/user_research/: all CSVs, JSON, txt, md, and charts.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns
    HAS_PLOT = True
except ImportError:
    HAS_PLOT = False


def load_trades(path: Path) -> pd.DataFrame:
    """Load trades.parquet and normalize columns."""
    df = pd.read_parquet(path)
    if "proxyWallet" not in df.columns or df["proxyWallet"].isna().all():
        if "taker" in df.columns:
            df["user"] = df["taker"].fillna(df.get("maker", "")).astype(str)
        elif "maker" in df.columns:
            df["user"] = df["maker"].astype(str)
        else:
            df["user"] = ""
    else:
        df["user"] = df["proxyWallet"].fillna("").astype(str)
    df = df[df["user"].str.startswith("0x", na=False)]
    p = pd.to_numeric(df.get("price", 0), errors="coerce")
    s = pd.to_numeric(df.get("size", 0), errors="coerce")
    fallback_usd = p * s
    if "usd_amount" in df.columns:
        df["usd"] = pd.to_numeric(df["usd_amount"], errors="coerce").fillna(fallback_usd)
    else:
        df["usd"] = fallback_usd
    df["usd"] = df["usd"].fillna(0).clip(lower=0)
    if "timestamp" in df.columns:
        df["ts"] = pd.to_numeric(df["timestamp"], errors="coerce").fillna(0).astype("int64")
    else:
        df["ts"] = 0
    df["side"] = df.get("side", "").fillna("").astype(str).str.upper()
    if df["side"].isin(["BUY", "SELL"]).sum() == 0 and "taker_direction" in df.columns:
        df["side"] = df["taker_direction"].fillna("").astype(str).str.upper()
    return df[["user", "market_id", "ts", "side", "usd"]].dropna(subset=["user", "market_id"])


def holding_durations_single_pass(df: pd.DataFrame) -> pd.Series:
    """Vectorized: sort by user, market_id, ts; SELL rows get duration = ts - prev_ts when prev was BUY."""
    df = df.sort_values(["user", "market_id", "ts"]).copy()
    df["prev_ts"] = df.groupby(["user", "market_id"])["ts"].shift(1)
    df["prev_side"] = df.groupby(["user", "market_id"])["side"].shift(1)
    sell = df[(df["side"] == "SELL") & (df["prev_side"] == "BUY")]
    sell = sell[sell["prev_ts"].notna()]
    sell["dur"] = (sell["ts"] - sell["prev_ts"]).astype("float64")
    sell = sell[(sell["dur"] > 0) & (sell["dur"] < 10 * 365 * 24 * 3600)]
    if sell.empty:
        return pd.Series(dtype=float)
    return sell.groupby("user")["dur"].mean()


def concurrent_markets_fast(df: pd.DataFrame) -> pd.Series:
    """Avg concurrent markets: for each user (first_ts, last_ts) and per-market (min_ts, max_ts)."""
    span = df.groupby("user")["ts"].agg(["min", "max"]).reset_index()
    span["total_sec"] = (span["max"] - span["min"]).clip(lower=1)
    market_sec = df.groupby(["user", "market_id"])["ts"].agg(["min", "max"]).reset_index()
    market_sec["sec"] = market_sec["max"] - market_sec["min"]
    overlap = market_sec.groupby("user")["sec"].sum().reset_index()
    merged = span.merge(overlap, on="user", how="left")
    merged["avg_concurrent"] = merged["sec"].fillna(0) / merged["total_sec"]
    return merged.set_index("user")["avg_concurrent"]


def per_user_metrics_full(df: pd.DataFrame, holding_avg: pd.Series, conc_series: pd.Series) -> pd.DataFrame:
    """Full per-user metrics including tenure, buy/sell ratio, trades_per_day, size percentiles."""
    agg = df.groupby("user").agg(
        num_trades=("usd", "count"),
        num_markets=("market_id", "nunique"),
        total_usd=("usd", "sum"),
        first_ts=("ts", "min"),
        last_ts=("ts", "max"),
        buy_count=("side", lambda x: (x == "BUY").sum()),
        sell_count=("side", lambda x: (x == "SELL").sum()),
    ).reset_index()
    agg["avg_trade_usd"] = agg["total_usd"] / agg["num_trades"]
    agg["holding_duration_avg_sec"] = agg["user"].map(holding_avg)
    agg["avg_concurrent_markets"] = agg["user"].map(conc_series)
    agg["tenure_days"] = ((agg["last_ts"] - agg["first_ts"]) / 86400).clip(lower=0)
    # Trades per day: use at least 1 day to avoid explosion when tenure is same-day
    agg["trades_per_day"] = agg["num_trades"] / agg["tenure_days"].clip(lower=1)
    agg["buy_sell_ratio"] = agg["buy_count"] / agg["sell_count"].replace(0, np.nan)
    return agg


def aggregate_profile(per_user: pd.DataFrame) -> Dict[str, Any]:
    def safe(v):
        return float(v) if pd.notna(v) and np.isfinite(v) else None
    pu = per_user
    out = {
        "num_users": int(len(pu)),
        "mean_trades_per_user": safe(pu["num_trades"].mean()),
        "median_trades_per_user": safe(pu["num_trades"].median()),
        "mean_markets_per_user": safe(pu["num_markets"].mean()),
        "median_markets_per_user": safe(pu["num_markets"].median()),
        "mean_avg_trade_size_usd": safe(pu["avg_trade_usd"].mean()),
        "median_avg_trade_size_usd": safe(pu["avg_trade_usd"].median()),
        "mean_total_volume_usd": safe(pu["total_usd"].mean()),
        "median_total_volume_usd": safe(pu["total_usd"].median()),
        "total_volume_all_users_usd": safe(pu["total_usd"].sum()),
        "mean_holding_duration_seconds": safe(pu["holding_duration_avg_sec"].dropna().mean()),
        "median_holding_duration_seconds": safe(pu["holding_duration_avg_sec"].dropna().median()),
        "users_with_holding_duration": int(pu["holding_duration_avg_sec"].notna().sum()),
        "mean_tenure_days": safe(pu["tenure_days"].replace(0, np.nan).dropna().mean()),
        "median_tenure_days": safe(pu["tenure_days"].replace(0, np.nan).dropna().median()),
        "mean_trades_per_day": safe(pu["trades_per_day"].dropna().mean()),
        "mean_buy_sell_ratio": safe(pu["buy_sell_ratio"].dropna().mean()),
    }
    if "avg_concurrent_markets" in pu.columns:
        out["mean_concurrent_markets"] = safe(pu["avg_concurrent_markets"].dropna().mean())
        out["median_concurrent_markets"] = safe(pu["avg_concurrent_markets"].dropna().median())
    return out


def run_category(path: Path, cat_name: str) -> Tuple[pd.DataFrame, Dict[str, Any], pd.DataFrame]:
    df = load_trades(path)
    if df.empty or df["user"].nunique() == 0:
        return pd.DataFrame(), {}, pd.DataFrame()
    print(f"    Holding duration (single-pass)...")
    holding_avg = holding_durations_single_pass(df)
    print(f"    Concurrent markets...")
    conc = concurrent_markets_fast(df)
    print(f"    Per-user metrics...")
    per_user = per_user_metrics_full(df, holding_avg, conc)
    agg = aggregate_profile(per_user)
    agg["category"] = cat_name
    agg["total_trades"] = int(len(df))
    # Global trade size percentiles (from all trades in this category)
    usd_positive = df["usd"][df["usd"] > 0]
    if not usd_positive.empty:
        for q in [10, 25, 50, 75, 90]:
            agg[f"trade_size_p{q}_usd"] = float(np.nanpercentile(usd_positive, q))
    return df, agg, per_user


def activity_by_hour_and_dow(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Trades by hour (0-23) and by day of week (0-6)."""
    if df.empty or "ts" not in df.columns:
        return pd.DataFrame(), pd.DataFrame()
    t = pd.to_datetime(df["ts"], unit="s", utc=True)
    by_hour = t.dt.hour.value_counts().sort_index().reset_index()
    by_hour.columns = ["hour", "count"]
    by_dow = t.dt.dayofweek.value_counts().sort_index().reset_index()
    by_dow.columns = ["day_of_week", "count"]
    return by_hour, by_dow


def category_overlap(per_user_by_cat: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Rows: cat1, cat2, num_users_both."""
    cats = list(per_user_by_cat.keys())
    rows = []
    for i, c1 in enumerate(cats):
        for c2 in cats[i:]:
            u1 = set(per_user_by_cat[c1]["user"].dropna().astype(str))
            u2 = set(per_user_by_cat[c2]["user"].dropna().astype(str))
            both = len(u1 & u2)
            rows.append({"category_1": c1, "category_2": c2, "users_in_both": both})
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Full user research on all trade data")
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research")
    parser.add_argument("--out-dir", type=Path, default=_project_root / "data" / "research" / "user_research")
    parser.add_argument("--no-charts", action="store_true")
    args = parser.parse_args()

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    research_dir = args.research_dir

    categories: List[Tuple[str, Path]] = []
    for d in sorted(research_dir.iterdir()):
        if not d.is_dir() or d.name in ("users", "user_profiles"):
            continue
        p = d / "trades.parquet"
        if p.exists():
            categories.append((d.name, p))

    if not categories:
        print("No trades.parquet found.")
        return 1

    print("Full user research (all data, no sampling)")
    print("Categories:", [c[0] for c in categories])
    all_aggs: List[Dict[str, Any]] = []
    all_per_user: List[pd.DataFrame] = []
    global_trades: List[pd.DataFrame] = []
    per_user_by_cat: Dict[str, pd.DataFrame] = {}
    activity_hour_dfs: List[pd.DataFrame] = []
    activity_dow_dfs: List[pd.DataFrame] = []

    for cat_name, path in categories:
        print(f"  [{cat_name}] Loading...")
        df, agg, per_user = run_category(path, cat_name)
        if agg:
            all_aggs.append(agg)
            per_user["category"] = cat_name
            all_per_user.append(per_user)
            per_user_by_cat[cat_name] = per_user
        if not df.empty:
            global_trades.append(df)
            h, d = activity_by_hour_and_dow(df)
            if not h.empty:
                h["category"] = cat_name
                activity_hour_dfs.append(h)
            if not d.empty:
                d["category"] = cat_name
                activity_dow_dfs.append(d)

    # Global
    print("  [ALL] Computing global...")
    global_df = pd.concat(global_trades, ignore_index=True) if global_trades else pd.DataFrame()
    if not global_df.empty:
        holding_avg_g = holding_durations_single_pass(global_df)
        conc_g = concurrent_markets_fast(global_df)
        per_user_g = per_user_metrics_full(global_df, holding_avg_g, conc_g)
        agg_g = aggregate_profile(per_user_g)
        agg_g["category"] = "ALL"
        agg_g["total_trades"] = int(len(global_df))
        all_aggs.append(agg_g)
        per_user_g["category"] = "ALL"
        all_per_user.append(per_user_g)
        per_user_by_cat["ALL"] = per_user_g
        h_g, d_g = activity_by_hour_and_dow(global_df)
        if not h_g.empty:
            h_g["category"] = "ALL"
            activity_hour_dfs.append(h_g)
        if not d_g.empty:
            d_g["category"] = "ALL"
            activity_dow_dfs.append(d_g)

    # --- Write all outputs ---
    def json_clean(obj):
        if isinstance(obj, dict):
            return {k: json_clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [json_clean(x) for x in obj]
        if isinstance(obj, (float, np.floating)) and (np.isnan(obj) or np.isinf(obj)):
            return None
        return obj

    summary_df = pd.DataFrame(all_aggs)
    summary_df.to_csv(out / "summary_by_category.csv", index=False)
    with open(out / "summary_by_category.json", "w") as f:
        json.dump(json_clean(all_aggs), f, indent=2)
    print(f"  Wrote summary_by_category.csv, .json")

    # Readable txt
    with open(out / "summary_readable.txt", "w") as f:
        f.write("USER PROFILE SUMMARY (FULL DATA – all trades)\n")
        f.write("=" * 60 + "\n\n")
        for row in all_aggs:
            cat = row.get("category", "?")
            f.write(f"--- {cat} ---\n")
            f.write(f"  Users: {row.get('num_users', 0):,}\n")
            f.write(f"  Total trades: {row.get('total_trades', 0):,}\n")
            f.write(f"  Total volume (USD): ${row.get('total_volume_all_users_usd') or 0:,.2f}\n")
            f.write(f"  Mean trades per user: {row.get('mean_trades_per_user') or 0:.1f}\n")
            f.write(f"  Mean markets per user: {row.get('mean_markets_per_user') or 0:.1f}\n")
            f.write(f"  Mean avg trade size (USD): ${row.get('mean_avg_trade_size_usd') or 0:,.2f}\n")
            f.write(f"  Mean holding duration: {(row.get('mean_holding_duration_seconds') or 0) / 3600:.2f} hours\n")
            f.write(f"  Median holding duration: {(row.get('median_holding_duration_seconds') or 0) / 3600:.2f} hours\n")
            f.write(f"  Mean tenure (days): {row.get('mean_tenure_days') or 0:.1f}\n")
            f.write(f"  Mean trades per day: {row.get('mean_trades_per_day') or 0:.2f}\n")
            f.write(f"  Mean buy/sell ratio: {row.get('mean_buy_sell_ratio') or 0:.2f}\n")
            if row.get("mean_concurrent_markets") is not None:
                f.write(f"  Mean concurrent markets: {row.get('mean_concurrent_markets') or 0:.2f}\n")
            f.write("\n")
    print(f"  Wrote summary_readable.txt")

    # Per-user (global only to avoid huge file; optional: also save per category)
    per_user_all = pd.concat(all_per_user, ignore_index=True) if all_per_user else pd.DataFrame()
    pu_global = per_user_all[per_user_all["category"] == "ALL"] if "ALL" in per_user_all["category"].values else per_user_all
    if not pu_global.empty:
        pu_global.to_csv(out / "per_user_metrics.csv", index=False)
        print(f"  Wrote per_user_metrics.csv ({len(pu_global):,} users)")

    # Activity by hour (combined)
    if activity_hour_dfs:
        act_hour = pd.concat(activity_hour_dfs, ignore_index=True)
        act_hour.to_csv(out / "activity_by_hour.csv", index=False)
        print(f"  Wrote activity_by_hour.csv")
    if activity_dow_dfs:
        act_dow = pd.concat(activity_dow_dfs, ignore_index=True)
        act_dow.to_csv(out / "activity_by_dow.csv", index=False)
        print(f"  Wrote activity_by_dow.csv")

    # Category overlap (exclude ALL for pairwise)
    cat_only = {k: v for k, v in per_user_by_cat.items() if k != "ALL"}
    overlap_df = None
    if len(cat_only) >= 2:
        overlap_df = category_overlap(cat_only)
        overlap_df.to_csv(out / "category_overlap.csv", index=False)
        print(f"  Wrote category_overlap.csv")

    # Report .md
    with open(out / "report.md", "w") as f:
        f.write("# User behavior research – full data\n\n")
        f.write("All metrics computed on **all** trades (no sampling).\n\n")
        f.write("## Output files\n\n")
        f.write("| File | Description |\n|------|-------------|\n")
        f.write("| summary_by_category.csv | Aggregate stats by category and ALL |\n")
        f.write("| summary_by_category.json | Same in JSON |\n")
        f.write("| summary_readable.txt | Human-readable summary |\n")
        f.write("| per_user_metrics.csv | One row per user (global) |\n")
        f.write("| activity_by_hour.csv | Trade count by hour (0-23 UTC) |\n")
        f.write("| activity_by_dow.csv | Trade count by day of week (0=Mon) |\n")
        f.write("| category_overlap.csv | Users trading in both category_1 and category_2 |\n")
        f.write("| report.md | This file |\n")
        f.write("\n## Metrics\n\n")
        f.write("- **Holding duration**: Mean time from BUY to SELL per market, then averaged across users.\n")
        f.write("- **Tenure**: Days between first and last trade.\n")
        f.write("- **Trades per day**: num_trades / tenure_days.\n")
        f.write("- **Buy/sell ratio**: buy_count / sell_count per user.\n")
        f.write("- **Trade size p10/p25/p50/p75/p90**: Percentiles of USD per trade per user.\n")
        f.write("- **Concurrent markets**: Approx. number of markets open at a typical time.\n")
        f.write("- **Trade size p10/p25/p50/p75/p90 (USD)**: Percentiles of trade size in USD (from all trades in that category).\n")
    print(f"  Wrote report.md")

    # Charts
    if HAS_PLOT and not args.no_charts:
        sns.set_style("whitegrid")
        cat_agg = summary_df[summary_df["category"] != "ALL"]
        if not pu_global.empty:
            fig, axes = plt.subplots(2, 2, figsize=(12, 10))
            if pu_global["holding_duration_avg_sec"].notna().any():
                dur = pu_global["holding_duration_avg_sec"].dropna() / 3600
                dur = dur[dur < dur.quantile(0.99)]
                axes[0, 0].hist(dur, bins=50, edgecolor="black", alpha=0.7)
            axes[0, 0].set_xlabel("Avg holding duration (hours)")
            axes[0, 0].set_ylabel("Users")
            axes[0, 0].set_title("Holding duration")
            if "num_markets" in pu_global.columns:
                m = pu_global["num_markets"].clip(upper=pu_global["num_markets"].quantile(0.99))
                axes[0, 1].hist(m, bins=50, edgecolor="black", alpha=0.7)
            axes[0, 1].set_xlabel("Markets per user")
            axes[0, 1].set_title("Markets per user")
            if "avg_trade_usd" in pu_global.columns:
                at = pu_global["avg_trade_usd"].replace(0, np.nan).dropna()
                at = at[at < at.quantile(0.99)]
                axes[1, 0].hist(np.log10(at + 1), bins=50, edgecolor="black", alpha=0.7)
            axes[1, 0].set_xlabel("Log10(avg trade USD + 1)")
            axes[1, 0].set_title("Avg trade size")
            if not cat_agg.empty:
                x = range(len(cat_agg))
                axes[1, 1].bar([i - 0.2 for i in x], cat_agg["mean_markets_per_user"].fillna(0), width=0.35, label="Markets/user")
                ax2 = axes[1, 1].twinx()
                ax2.bar([i + 0.2 for i in x], (cat_agg["mean_holding_duration_seconds"] / 3600).fillna(0), width=0.35, color="orange", alpha=0.7, label="Holding (h)")
                axes[1, 1].set_xticks(x)
                axes[1, 1].set_xticklabels(cat_agg["category"], rotation=45, ha="right")
            plt.tight_layout()
            plt.savefig(out / "user_profile_charts.png", dpi=150, bbox_inches="tight")
            plt.close()
            print(f"  Wrote user_profile_charts.png")

        if not cat_agg.empty and "total_volume_all_users_usd" in cat_agg.columns:
            fig, ax = plt.subplots(figsize=(10, 5))
            vol = (cat_agg["total_volume_all_users_usd"] / 1e6).fillna(0)
            ax.bar(cat_agg["category"], vol, color="steelblue", edgecolor="black")
            ax.set_ylabel("Total volume (USD millions)")
            ax.set_title("Volume by category (full data)")
            plt.xticks(rotation=45, ha="right")
            plt.tight_layout()
            plt.savefig(out / "volume_by_category.png", dpi=150, bbox_inches="tight")
            plt.close()
            print(f"  Wrote volume_by_category.png")

        if activity_hour_dfs:
            hour_global = next((h for h in activity_hour_dfs if h["category"].iloc[0] == "ALL"), activity_hour_dfs[0])
            if not hour_global.empty and "hour" in hour_global.columns:
                fig, ax = plt.subplots(figsize=(10, 4))
                ax.bar(hour_global["hour"], hour_global["count"], color="teal", alpha=0.8)
                ax.set_xlabel("Hour (UTC)")
                ax.set_ylabel("Number of trades")
                ax.set_title("Activity by hour (full data)")
                plt.tight_layout()
                plt.savefig(out / "activity_by_hour.png", dpi=150, bbox_inches="tight")
                plt.close()
                print(f"  Wrote activity_by_hour.png")

        if activity_dow_dfs:
            dow_global = next((d for d in activity_dow_dfs if d["category"].iloc[0] == "ALL"), activity_dow_dfs[0])
            if not dow_global.empty and "day_of_week" in dow_global.columns:
                fig, ax = plt.subplots(figsize=(8, 4))
                days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                ax.bar([days[int(i)] if i < 7 else str(i) for i in dow_global["day_of_week"]], dow_global["count"], color="coral", alpha=0.8)
                ax.set_xlabel("Day of week")
                ax.set_ylabel("Number of trades")
                ax.set_title("Activity by day of week (full data)")
                plt.tight_layout()
                plt.savefig(out / "activity_by_dow.png", dpi=150, bbox_inches="tight")
                plt.close()
                print(f"  Wrote activity_by_dow.png")

        if len(cat_only) >= 2 and overlap_df is not None and not overlap_df.empty:
            # Pivot for heatmap
            piv = overlap_df.pivot_table(index="category_1", columns="category_2", values="users_in_both", fill_value=0)
            fig, ax = plt.subplots(figsize=(10, 8))
            sns.heatmap(piv, annot=True, fmt=".0f", cmap="YlOrRd", ax=ax)
            ax.set_title("Category overlap (users trading in both)")
            plt.tight_layout()
            plt.savefig(out / "category_overlap_heatmap.png", dpi=150, bbox_inches="tight")
            plt.close()
            print(f"  Wrote category_overlap_heatmap.png")

    print("\nDone. Outputs in", out)
    print(summary_df[["category", "num_users", "total_trades", "mean_markets_per_user", "mean_avg_trade_size_usd"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

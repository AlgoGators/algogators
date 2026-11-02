#!/usr/bin/env python3
"""
Analyze volume distribution, avg user trade size distribution, and trade size over market period.

For each category in data/research/{category}/trades.parquet:
- Volume distribution: percentiles of volume per user, concentration (top N% users = X% volume)
- Trade size distribution: percentiles of avg trade size per user, raw trade size percentiles
- Trade size over market period: avg trade size in early/mid/late buckets of each market's lifecycle

Outputs: CSVs and charts to data/research/user_research/
"""

import sys
from pathlib import Path
from typing import Dict, List, Tuple

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
    """Load and normalize trades."""
    df = pd.read_parquet(path)
    if "proxyWallet" not in df.columns or df["proxyWallet"].isna().all():
        df["user"] = df.get("taker", df.get("maker", "")).fillna("").astype(str)
    else:
        df["user"] = df["proxyWallet"].fillna("").astype(str)
    df = df[df["user"].str.startswith("0x", na=False)]
    p = pd.to_numeric(df.get("price", 0), errors="coerce")
    s = pd.to_numeric(df.get("size", 0), errors="coerce")
    fallback = p * s
    if "usd_amount" in df.columns:
        df["usd"] = pd.to_numeric(df["usd_amount"], errors="coerce").fillna(fallback)
    else:
        df["usd"] = fallback
    df["usd"] = df["usd"].fillna(0).clip(lower=0)
    df["ts"] = pd.to_numeric(df.get("timestamp", 0), errors="coerce").fillna(0).astype("int64")
    return df[["user", "market_id", "ts", "usd"]].dropna(subset=["user", "market_id"])


def volume_distribution(per_user_vol: pd.Series) -> Dict:
    """Volume per user: percentiles and concentration."""
    v = per_user_vol[per_user_vol > 0]
    if v.empty:
        return {}
    v = v.sort_values(ascending=False).values
    total = v.sum()
    cum = np.cumsum(v)
    out = {}
    for q in [10, 25, 50, 75, 90, 95, 99]:
        out[f"volume_p{q}"] = float(np.percentile(per_user_vol, q))
    # Top N% of users account for X% of volume
    n = len(v)
    for pct in [5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 90, 95, 100]:
        k = max(1, int(n * pct / 100))
        out[f"top_{pct}pct_users_volume_pct"] = float(100 * cum[k - 1] / total) if total > 0 else 0
    return out


def trade_size_distribution(df: pd.DataFrame, per_user: pd.DataFrame) -> Dict:
    """Trade size: raw percentiles and per-user avg percentiles."""
    out = {}
    usd = df["usd"][df["usd"] > 0]
    if not usd.empty:
        for q in [10, 25, 50, 75, 90, 95, 99]:
            out[f"trade_size_raw_p{q}"] = float(np.percentile(usd, q))
    at = per_user["avg_trade_usd"][per_user["avg_trade_usd"] > 0]
    if not at.empty:
        for q in [10, 25, 50, 75, 90, 95, 99]:
            out[f"trade_size_user_avg_p{q}"] = float(np.percentile(at, q))
    return out


def trade_size_over_market_period(df: pd.DataFrame) -> pd.DataFrame:
    """For each trade, compute position in market lifecycle (0-1). Bucket and aggregate."""
    period = df.groupby("market_id")["ts"].agg(["min", "max"]).reset_index()
    period["span"] = (period["max"] - period["min"]).clip(lower=1)
    df = df.merge(period[["market_id", "min", "span"]], on="market_id", how="left")
    df["frac"] = (df["ts"] - df["min"]) / df["span"]
    df["bucket"] = pd.cut(df["frac"], bins=[0, 0.25, 0.5, 0.75, 1.0], labels=["early", "mid_early", "mid_late", "late"], include_lowest=True)
    agg = df.groupby("bucket", observed=True).agg(
        avg_trade_size_usd=("usd", "mean"),
        median_trade_size_usd=("usd", "median"),
        trade_count=("usd", "count"),
        total_volume_usd=("usd", "sum"),
    ).reset_index()
    return agg


def main() -> int:
    research_dir = _project_root / "data" / "research"
    out_dir = _project_root / "data" / "research" / "user_research"
    out_dir.mkdir(parents=True, exist_ok=True)

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

    vol_dist_rows: List[dict] = []
    trade_size_dist_rows: List[dict] = []
    trade_size_over_period_rows: List[dict] = []

    for cat_name, path in categories:
        print(f"  [{cat_name}] Loading...")
        df = load_trades(path)
        if df.empty:
            continue
        per_user = df.groupby("user").agg(total_usd=("usd", "sum"), num_trades=("usd", "count")).reset_index()
        per_user["avg_trade_usd"] = per_user["total_usd"] / per_user["num_trades"]

        # Volume distribution
        vol_dist = volume_distribution(per_user["total_usd"])
        for k, v in vol_dist.items():
            vol_dist_rows.append({"category": cat_name, "metric": k, "value": v})

        # Trade size distribution
        ts_dist = trade_size_distribution(df, per_user)
        for k, v in ts_dist.items():
            trade_size_dist_rows.append({"category": cat_name, "metric": k, "value": v})

        # Trade size over market period
        period_df = trade_size_over_market_period(df)
        for _, row in period_df.iterrows():
            trade_size_over_period_rows.append({
                "category": cat_name,
                "period_bucket": str(row["bucket"]),
                "avg_trade_size_usd": float(row["avg_trade_size_usd"]),
                "median_trade_size_usd": float(row["median_trade_size_usd"]),
                "trade_count": int(row["trade_count"]),
                "total_volume_usd": float(row["total_volume_usd"]),
            })

    # Write CSVs
    pd.DataFrame(vol_dist_rows).to_csv(out_dir / "volume_distribution_by_category.csv", index=False)
    pd.DataFrame(trade_size_dist_rows).to_csv(out_dir / "trade_size_distribution_by_category.csv", index=False)
    pd.DataFrame(trade_size_over_period_rows).to_csv(out_dir / "trade_size_over_market_period_by_category.csv", index=False)
    print(f"Wrote volume_distribution_by_category.csv, trade_size_distribution_by_category.csv, trade_size_over_market_period_by_category.csv")

    # Pivot for easier reading: category x metric
    vol_pivot = pd.DataFrame(vol_dist_rows).pivot(index="category", columns="metric", values="value")
    vol_pivot.to_csv(out_dir / "volume_distribution_pivot.csv")
    ts_pivot = pd.DataFrame(trade_size_dist_rows).pivot(index="category", columns="metric", values="value")
    ts_pivot.to_csv(out_dir / "trade_size_distribution_pivot.csv")
    period_pivot = pd.DataFrame(trade_size_over_period_rows).pivot_table(
        index="category", columns="period_bucket", values="avg_trade_size_usd"
    )
    period_pivot.to_csv(out_dir / "trade_size_over_period_pivot.csv")
    print(f"Wrote pivot CSVs")

    # Charts
    if HAS_PLOT:
        # 1) Volume distribution: top N% users = X% volume, by category
        vol_df = pd.DataFrame(vol_dist_rows)
        top_cols = [c for c in vol_df["metric"].unique() if "top_" in str(c) and "volume_pct" in str(c)]
        if top_cols:
            plot_df = vol_df[vol_df["metric"].isin(top_cols)].copy()
            plot_df["pct_users"] = plot_df["metric"].str.extract(r"top_(\d+)pct")[0].astype(int)
            fig, ax = plt.subplots(figsize=(10, 5))
            for cat in plot_df["category"].unique():
                sub = plot_df[plot_df["category"] == cat]
                ax.plot(sub["pct_users"], sub["value"], marker="o", label=cat)
            ax.set_xlabel("Top X% of users (by volume)")
            ax.set_ylabel("% of total volume")
            ax.set_title("Volume concentration by category")
            ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
            ax.set_xticks(sorted(plot_df["pct_users"].unique()))
            plt.tight_layout()
            plt.savefig(out_dir / "volume_concentration_by_category.png", dpi=150, bbox_inches="tight")
            plt.close()
            print(f"Wrote volume_concentration_by_category.png")

        # 2) Trade size over market period: grouped bar by category
        period_df = pd.DataFrame(trade_size_over_period_rows)
        if not period_df.empty:
            fig, ax = plt.subplots(figsize=(12, 5))
            cats = period_df["category"].unique()
            buckets = ["early", "mid_early", "mid_late", "late"]
            x = np.arange(len(buckets))
            w = 0.8 / len(cats)
            for i, cat in enumerate(cats):
                sub = period_df[period_df["category"] == cat]
                vals = [sub[sub["period_bucket"] == b]["avg_trade_size_usd"].values[0] if len(sub[sub["period_bucket"] == b]) else 0 for b in buckets]
                ax.bar(x + i * w, vals, width=w, label=cat)
            ax.set_xticks(x + w * (len(cats) - 1) / 2)
            ax.set_xticklabels(buckets)
            ax.set_ylabel("Avg trade size (USD)")
            ax.set_xlabel("Market period (fraction of lifecycle)")
            ax.set_title("Trade size over market period by category")
            ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
            plt.tight_layout()
            plt.savefig(out_dir / "trade_size_over_period_by_category.png", dpi=150, bbox_inches="tight")
            plt.close()
            print(f"Wrote trade_size_over_period_by_category.png")

        # 3) Volume percentiles by category (p10, p25, p50, p75, p90, p95, p99)
        vol_metrics = [f"volume_p{q}" for q in [10, 25, 50, 75, 90, 95, 99] if any(f"volume_p{q}" in str(m) for m in vol_df["metric"])]
        if vol_metrics:
            plot_v = vol_df[vol_df["metric"].isin(vol_metrics)].copy()
            plot_v["percentile"] = plot_v["metric"].str.extract(r"volume_p(\d+)")[0].astype(int)
            fig, ax = plt.subplots(figsize=(10, 5))
            for cat in plot_v["category"].unique():
                sub = plot_v[plot_v["category"] == cat]
                ax.plot(sub["percentile"], sub["value"], marker="o", label=cat)
            ax.set_xlabel("Volume percentile (per user)")
            ax.set_ylabel("Volume (USD)")
            ax.set_title("Volume distribution by category")
            ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
            plt.tight_layout()
            plt.savefig(out_dir / "volume_percentiles_by_category.png", dpi=150, bbox_inches="tight")
            plt.close()
            print(f"Wrote volume_percentiles_by_category.png")

        # 4) Avg user trade size distribution: p50 by category
        ts_user = [c for c in trade_size_dist_rows if "trade_size_user_avg" in str(c.get("metric", ""))]
        if ts_user:
            ts_df = pd.DataFrame(trade_size_dist_rows)
            ts_df = ts_df[ts_df["metric"].str.contains("trade_size_user_avg", na=False)]
            if not ts_df.empty:
                fig, ax = plt.subplots(figsize=(10, 5))
                cats = ts_df["category"].unique()
                metrics = sorted(ts_df["metric"].unique(), key=lambda m: int(m.split("p")[-1]) if "p" in str(m) else 0)
                x = np.arange(len(cats))
                w = 0.8 / len(metrics)
                for i, m in enumerate(metrics):
                    sub = ts_df[ts_df["metric"] == m]
                    vals = [sub[sub["category"] == c]["value"].values[0] if len(sub[sub["category"] == c]) else 0 for c in cats]
                    ax.bar(x + i * w, vals, width=w, label=m.replace("trade_size_user_avg_", "p"))
                ax.set_xticks(x + w * (len(metrics) - 1) / 2)
                ax.set_xticklabels(cats, rotation=45, ha="right")
                ax.set_ylabel("Trade size (USD)")
                ax.set_title("Per-user avg trade size distribution by category")
                ax.legend()
                plt.tight_layout()
                plt.savefig(out_dir / "trade_size_user_avg_by_category.png", dpi=150, bbox_inches="tight")
                plt.close()
                print(f"Wrote trade_size_user_avg_by_category.png")

    # Summary .md
    with open(out_dir / "volume_trade_analysis.md", "w") as f:
        f.write("# Volume and trade size analysis by category\n\n")
        f.write("## Volume distribution\n")
        f.write("- **volume_p10, p25, p50, p75, p90, p99**: Percentiles of total volume per user (USD).\n")
        f.write("- **top_10pct_users_volume_pct**: Share of total volume from top 10% of users (by volume).\n")
        f.write("- **top_20pct_users_volume_pct**, **top_50pct_users_volume_pct**: Same for top 20% and 50%.\n\n")
        f.write("## Trade size distribution\n")
        f.write("- **trade_size_raw_p***: Percentiles of each individual trade's USD amount.\n")
        f.write("- **trade_size_user_avg_p***: Percentiles of each user's average trade size.\n\n")
        f.write("## Trade size over market period\n")
        f.write("- **early**: First 25% of market lifecycle (first trade to last trade).\n")
        f.write("- **mid_early**: 25–50%, **mid_late**: 50–75%, **late**: 75–100%.\n")
        f.write("- Avg/median trade size and trade count in each bucket.\n")
    print(f"Wrote volume_trade_analysis.md")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

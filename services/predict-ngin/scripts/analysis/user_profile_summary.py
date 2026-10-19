#!/usr/bin/env python3
"""
Compute user profile statistics from research trade data (category and global).

Uses data/research/{category}/trades.parquet. Computes per-user then aggregate:
- Holding duration (avg time from BUY to SELL per market)
- Markets per user (distinct markets traded, and avg concurrent open markets)
- Average trade size (USD)
- Total volume, trade count, etc.

Outputs: summary tables (JSON + CSV), distribution charts (PNG).

Usage:
    python scripts/analysis/user_profile_summary.py
    python scripts/analysis/user_profile_summary.py --research-dir data/research --out-dir data/research/user_profiles
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

# Optional matplotlib
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
    # User: prefer proxyWallet, else maker/taker (taker is often the active trader)
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
    # USD amount: prefer usd_amount, fallback to price * size
    p = pd.to_numeric(df.get("price", 0), errors="coerce")
    s = pd.to_numeric(df.get("size", 0), errors="coerce")
    fallback_usd = p * s
    if "usd_amount" in df.columns:
        df["usd"] = pd.to_numeric(df["usd_amount"], errors="coerce").fillna(fallback_usd)
    else:
        df["usd"] = fallback_usd
    df["usd"] = df["usd"].fillna(0).clip(lower=0)
    # Timestamp
    if "timestamp" in df.columns:
        df["ts"] = pd.to_numeric(df["timestamp"], errors="coerce").fillna(0).astype("int64")
    else:
        df["ts"] = 0
    # Side
    df["side"] = df.get("side", "").fillna("").astype(str).str.upper()
    if df["side"].isin(["BUY", "SELL"]).sum() == 0 and "taker_direction" in df.columns:
        df["side"] = df["taker_direction"].fillna("").astype(str).str.upper()
    return df[["user", "market_id", "ts", "side", "usd"]].dropna(subset=["user", "market_id"])


def holding_durations_per_user(df: pd.DataFrame) -> pd.Series:
    """For each user, compute average holding duration (seconds) from BUY->SELL pairs per market."""
    user_durs: Dict[str, List[float]] = {}
    for (user, market), g in df.groupby(["user", "market_id"]):
        g = g.sort_values("ts")
        buys = g[g["side"] == "BUY"]["ts"].tolist()
        sells = g[g["side"] == "SELL"]["ts"].tolist()
        if not buys or not sells:
            continue
        # Pair first buy with first sell after it, etc.
        j = 0
        for b in buys:
            while j < len(sells) and sells[j] <= b:
                j += 1
            if j < len(sells) and sells[j] > b:
                d = sells[j] - b
                if 0 < d < 10 * 365 * 24 * 3600:  # cap 10 years
                    user_durs.setdefault(user, []).append(d)
                j += 1
    if not user_durs:
        return pd.Series(dtype=float)
    return pd.Series({u: np.mean(d) for u, d in user_durs.items()})


def per_user_metrics(df: pd.DataFrame, holding_avg: pd.Series) -> pd.DataFrame:
    """One row per user: num_trades, num_markets, total_usd, avg_trade_usd, holding_duration_avg_sec."""
    agg = df.groupby("user").agg(
        num_trades=("usd", "count"),
        num_markets=("market_id", "nunique"),
        total_usd=("usd", "sum"),
    ).reset_index()
    agg["avg_trade_usd"] = agg["total_usd"] / agg["num_trades"]
    agg["holding_duration_avg_sec"] = agg["user"].map(holding_avg)
    return agg


def concurrent_markets_approx(df: pd.DataFrame) -> pd.Series:
    """Approximate avg concurrent markets per user: sum of (duration per market) / total time span."""
    user_span: Dict[str, Tuple[int, int]] = {}
    user_market_days: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for (user, market), g in df.groupby(["user", "market_id"]):
        g = g.sort_values("ts")
        ts = g["ts"].values
        if len(ts) < 2:
            continue
        t_min, t_max = int(ts.min()), int(ts.max())
        if user not in user_span:
            user_span[user] = (t_min, t_max)
        else:
            a, b = user_span[user]
            user_span[user] = (min(a, t_min), max(b, t_max))
        user_market_days.setdefault(user, {})[market] = (t_min, t_max)
    result = {}
    for user, (span_lo, span_hi) in user_span.items():
        if span_hi <= span_lo:
            continue
        total_sec = span_hi - span_lo
        overlap_sec = 0
        for market, (m_lo, m_hi) in user_market_days.get(user, {}).items():
            overlap_sec += max(0, min(m_hi, span_hi) - max(m_lo, span_lo))
        # Avg concurrent = total "market-seconds" / total seconds
        result[user] = overlap_sec / total_sec if total_sec > 0 else 0
    return pd.Series(result)


def aggregate_profile(per_user: pd.DataFrame) -> Dict[str, Any]:
    """Summarize per-user stats into mean/median/std."""
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
    }
    if "avg_concurrent_markets" in pu.columns:
        out["mean_concurrent_markets"] = safe(pu["avg_concurrent_markets"].dropna().mean())
        out["median_concurrent_markets"] = safe(pu["avg_concurrent_markets"].dropna().median())
    return out


def run_category(path: Path, category_name: str, max_trades: Optional[int] = None) -> Tuple[pd.DataFrame, Dict[str, Any], pd.DataFrame]:
    """Load trades, compute per-user metrics and aggregate for one category."""
    df = load_trades(path)
    if df.empty or df["user"].nunique() == 0:
        return pd.DataFrame(), {}, pd.DataFrame()
    if max_trades and len(df) > max_trades:
        df = df.sample(n=max_trades, random_state=42)
    holding_avg = holding_durations_per_user(df)
    per_user = per_user_metrics(df, holding_avg)
    try:
        conc = concurrent_markets_approx(df)
        per_user["avg_concurrent_markets"] = per_user["user"].map(conc)
    except Exception:
        pass
    agg = aggregate_profile(per_user)
    agg["category"] = category_name
    agg["total_trades"] = int(len(df))
    return df, agg, per_user


def main() -> int:
    parser = argparse.ArgumentParser(description="User profile summary from research trades")
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research")
    parser.add_argument("--out-dir", type=Path, default=_project_root / "data" / "research" / "user_profiles")
    parser.add_argument("--no-charts", action="store_true", help="Skip generating charts")
    parser.add_argument("--categories", type=str, default=None, help="Comma-separated category names (default: all)")
    parser.add_argument("--max-trades", type=int, default=None, help="Max trades per category to load (for speed; default: all)")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    research_dir = args.research_dir

    # Categories: dirs that have trades.parquet
    cat_filter = [c.strip() for c in args.categories.split(",")] if args.categories else None
    categories: List[Tuple[str, Path]] = []
    for d in sorted(research_dir.iterdir()):
        if not d.is_dir() or d.name == "users" or d.name == "user_profiles":
            continue
        if cat_filter and d.name not in cat_filter:
            continue
        p = d / "trades.parquet"
        if p.exists():
            categories.append((d.name, p))

    if not categories:
        print("No trades.parquet found under", research_dir)
        return 1

    print("Categories:", [c[0] for c in categories])
    all_aggs: List[Dict[str, Any]] = []
    all_per_user: List[pd.DataFrame] = []
    global_trades: List[pd.DataFrame] = []

    for cat_name, path in categories:
        print(f"  Processing {cat_name}...")
        df, agg, per_user = run_category(path, cat_name, max_trades=args.max_trades)
        if agg:
            all_aggs.append(agg)
            per_user["category"] = cat_name
            all_per_user.append(per_user)
        if not df.empty:
            global_trades.append(df)

    if not all_aggs:
        print("No data to summarize.")
        return 1

    # Global (pool all trades)
    print("  Computing global...")
    global_df = pd.concat(global_trades, ignore_index=True) if global_trades else pd.DataFrame()
    if not global_df.empty:
        holding_avg_g = holding_durations_per_user(global_df)
        per_user_g = per_user_metrics(global_df, holding_avg_g)
        agg_g = aggregate_profile(per_user_g)
        agg_g["category"] = "ALL"
        agg_g["total_trades"] = int(len(global_df))
        all_aggs.append(agg_g)
        per_user_g["category"] = "ALL"
        all_per_user.append(per_user_g)

    # Save summary by category
    summary_df = pd.DataFrame(all_aggs)
    summary_df.to_csv(args.out_dir / "summary_by_category.csv", index=False)
    def _json_clean(obj):
        if isinstance(obj, dict):
            return {k: _json_clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_json_clean(x) for x in obj]
        if isinstance(obj, (float, np.floating)) and (np.isnan(obj) or np.isinf(obj)):
            return None
        return obj

    with open(args.out_dir / "summary_by_category.json", "w") as f:
        json.dump(_json_clean(all_aggs), f, indent=2)
    print(f"Wrote {args.out_dir / 'summary_by_category.csv'}")

    # Per-user (sample or all) for charts
    per_user_all = pd.concat(all_per_user, ignore_index=True) if all_per_user else pd.DataFrame()

    # Human-readable summary
    with open(args.out_dir / "summary_readable.txt", "w") as f:
        f.write("USER PROFILE SUMMARY (from research trades)\n")
        f.write("=" * 60 + "\n\n")
        for row in all_aggs:
            cat = row.get("category", "?")
            f.write(f"--- {cat} ---\n")
            f.write(f"  Users: {row.get('num_users', 0):,}\n")
            f.write(f"  Total trades: {row.get('total_trades', 0):,}\n")
            f.write(f"  Mean trades per user: {row.get('mean_trades_per_user') or 0:.1f}\n")
            f.write(f"  Mean markets per user: {row.get('mean_markets_per_user') or 0:.1f}\n")
            f.write(f"  Total volume (USD): ${row.get('total_volume_all_users_usd') or 0:,.2f}\n")
            f.write(f"  Mean avg trade size (USD): ${row.get('mean_avg_trade_size_usd') or 0:,.2f}\n")
            f.write(f"  Mean holding duration: {(row.get('mean_holding_duration_seconds') or 0) / 3600:.2f} hours\n")
            f.write(f"  Median holding duration: {(row.get('median_holding_duration_seconds') or 0) / 3600:.2f} hours\n")
            if row.get("mean_concurrent_markets") is not None:
                f.write(f"  Mean concurrent markets (approx): {row.get('mean_concurrent_markets') or 0:.2f}\n")
            f.write("\n")
    print(f"Wrote {args.out_dir / 'summary_readable.txt'}")

    # Charts
    if HAS_PLOT and not args.no_charts and not per_user_all.empty:
        sns.set_style("whitegrid")
        fig, axes = plt.subplots(2, 2, figsize=(12, 10))

        # 1) Holding duration distribution (hours) - global users
        pu_global = per_user_all[per_user_all["category"] == "ALL"] if "ALL" in per_user_all["category"].values else per_user_all
        if not pu_global.empty and pu_global["holding_duration_avg_sec"].notna().any():
            dur_h = pu_global["holding_duration_avg_sec"].dropna() / 3600
            dur_h = dur_h[dur_h < dur_h.quantile(0.99)]
            axes[0, 0].hist(dur_h, bins=50, edgecolor="black", alpha=0.7)
            axes[0, 0].set_xlabel("Avg holding duration (hours)")
            axes[0, 0].set_ylabel("Number of users")
            axes[0, 0].set_title("Holding duration (user avg)")

        # 2) Markets per user distribution
        if "num_markets" in pu_global.columns:
            m = pu_global["num_markets"]
            m = m[m < m.quantile(0.99)]
            axes[0, 1].hist(m, bins=50, edgecolor="black", alpha=0.7)
            axes[0, 1].set_xlabel("Markets traded per user")
            axes[0, 1].set_ylabel("Number of users")
            axes[0, 1].set_title("Markets per user")

        # 3) Avg trade size (USD) distribution (log scale)
        if "avg_trade_usd" in pu_global.columns:
            at = pu_global["avg_trade_usd"].replace(0, np.nan).dropna()
            at = at[at < at.quantile(0.99)]
            axes[1, 0].hist(np.log10(at + 1), bins=50, edgecolor="black", alpha=0.7)
            axes[1, 0].set_xlabel("Log10(avg trade size USD + 1)")
            axes[1, 0].set_ylabel("Number of users")
            axes[1, 0].set_title("Avg trade size (USD)")

        # 4) Category comparison: mean markets per user, mean holding duration
        cat_agg = summary_df[summary_df["category"] != "ALL"]
        if not cat_agg.empty:
            x = range(len(cat_agg))
            w = 0.35
            axes[1, 1].bar([i - w/2 for i in x], cat_agg["mean_markets_per_user"], width=w, label="Mean markets/user")
            ax2 = axes[1, 1].twinx()
            ax2.bar([i + w/2 for i in x], (cat_agg["mean_holding_duration_seconds"] / 3600).fillna(0), width=w, color="orange", alpha=0.7, label="Mean holding (h)")
            axes[1, 1].set_xticks(x)
            axes[1, 1].set_xticklabels(cat_agg["category"], rotation=45, ha="right")
            axes[1, 1].set_ylabel("Mean markets per user")
            ax2.set_ylabel("Mean holding duration (hours)")
            axes[1, 1].set_title("By category")

        plt.tight_layout()
        plt.savefig(args.out_dir / "user_profile_charts.png", dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Wrote {args.out_dir / 'user_profile_charts.png'}")

        # Extra: bar chart of key metrics by category
        fig2, ax = plt.subplots(figsize=(10, 5))
        cat_agg = summary_df[summary_df["category"] != "ALL"]
        if not cat_agg.empty:
            metrics = ["mean_markets_per_user", "mean_avg_trade_size_usd", "mean_trades_per_user"]
            X = np.arange(len(cat_agg))
            width = 0.25
            for i, m in enumerate(metrics):
                vals = cat_agg[m].fillna(0)
                if "trade_size" in m:
                    vals = vals / 100  # scale for visibility
                ax.bar(X + i * width, vals, width, label=m.replace("_", " ").title())
            ax.set_xticks(X + width)
            ax.set_xticklabels(cat_agg["category"], rotation=45, ha="right")
            ax.legend()
            ax.set_title("User profile metrics by category")
            plt.tight_layout()
            plt.savefig(args.out_dir / "category_metrics_bars.png", dpi=150, bbox_inches="tight")
            plt.close()
            print(f"Wrote {args.out_dir / 'category_metrics_bars.png'}")

        # Volume by category
        fig3, ax = plt.subplots(figsize=(10, 5))
        cat_agg = summary_df[summary_df["category"] != "ALL"]
        if not cat_agg.empty and "total_volume_all_users_usd" in cat_agg.columns:
            vol = (cat_agg["total_volume_all_users_usd"] / 1e6).fillna(0)
            bars = ax.bar(cat_agg["category"], vol, color="steelblue", edgecolor="black")
            ax.set_ylabel("Total volume (USD millions)")
            ax.set_xlabel("Category")
            ax.set_title("Total trading volume by category (sample)")
            plt.xticks(rotation=45, ha="right")
            plt.tight_layout()
            plt.savefig(args.out_dir / "volume_by_category.png", dpi=150, bbox_inches="tight")
            plt.close()
            print(f"Wrote {args.out_dir / 'volume_by_category.png'}")

    print("\nDone. Summary:")
    print(summary_df[["category", "num_users", "mean_markets_per_user", "mean_avg_trade_size_usd", "mean_holding_duration_seconds"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Plot YES price change from beginning to end of market, normalized across markets.

For each category:
- Normalize each market's timeline to [0, 1] (start = first trade, end = last trade)
- At each normalized time bin, compute mean YES price across markets
- Plot mean ± 95% CI over normalized time to reveal trends

Uses trades.parquet (price from trades; YES price = price when outcomeIndex=0, else 1-price when outcomeIndex=1).
"""

import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_PLOT = True
except ImportError:
    HAS_PLOT = False


def load_yes_prices(path: Path) -> pd.DataFrame:
    """Load trades and extract (market_id, ts, yes_price)."""
    df = pd.read_parquet(path, columns=["market_id", "timestamp", "price", "outcomeIndex"])
    df["ts"] = pd.to_numeric(df["timestamp"], errors="coerce").fillna(0).astype("int64")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["outcomeIndex"] = pd.to_numeric(df["outcomeIndex"], errors="coerce")

    # YES price: outcomeIndex 0 -> price, outcomeIndex 1 -> 1-price
    mask0 = df["outcomeIndex"] == 0
    mask1 = df["outcomeIndex"] == 1
    df["yes_price"] = np.nan
    df.loc[mask0, "yes_price"] = df.loc[mask0, "price"]
    df.loc[mask1, "yes_price"] = 1.0 - df.loc[mask1, "price"]

    df = df[df["yes_price"].notna() & (df["yes_price"] > 0) & (df["yes_price"] < 1)]
    df = df[df["ts"] > 0]
    return df[["market_id", "ts", "yes_price"]].dropna()


def market_price_at_frac(
    ts: np.ndarray, prices: np.ndarray, frac: float
) -> float:
    """Interpolate price at normalized fraction of market lifecycle."""
    if len(ts) < 2:
        return float(prices[0]) if len(prices) > 0 else np.nan
    t_min, t_max = ts.min(), ts.max()
    if t_max <= t_min:
        return float(prices[-1])
    t_target = t_min + frac * (t_max - t_min)
    # Find surrounding points
    idx = np.searchsorted(ts, t_target, side="right")
    if idx == 0:
        return float(prices[0])
    if idx >= len(ts):
        return float(prices[-1])
    # Linear interpolation
    t0, t1 = ts[idx - 1], ts[idx]
    p0, p1 = prices[idx - 1], prices[idx]
    if t1 == t0:
        return float(p0)
    w = (t_target - t0) / (t1 - t0)
    return float(p0 + w * (p1 - p0))


def build_normalized_series(
    df: pd.DataFrame, n_bins: int = 50
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    For each market, sample price at normalized times 0, 1/n, 2/n, ..., 1.
    Returns: fracs, mean_price, ci_lower, ci_upper (95% CI).
    """
    fracs = np.linspace(0, 1, n_bins + 1)

    # Per market: (ts, price) sorted, sample at each frac
    all_prices: List[np.ndarray] = []
    for mid, grp in df.groupby("market_id"):
        grp = grp.sort_values("ts")
        ts = grp["ts"].values
        prices = grp["yes_price"].values
        if len(ts) < 2:
            continue
        row = [market_price_at_frac(ts, prices, f) for f in fracs]
        all_prices.append(row)

    if not all_prices:
        return fracs, np.full_like(fracs, np.nan), np.full_like(fracs, np.nan), np.full_like(fracs, np.nan)

    arr = np.array(all_prices)
    mean_p = np.nanmean(arr, axis=0)
    std_p = np.nanstd(arr, axis=0)
    n = np.sum(np.isfinite(arr), axis=0)
    se = np.where(n > 1, std_p / np.sqrt(n), 0)
    ci_half = 1.96 * se
    ci_lower = mean_p - ci_half
    ci_upper = mean_p + ci_half
    return fracs, mean_p, ci_lower, ci_upper


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

    n_bins = 50
    results = {}

    for cat_name, path in categories:
        print(f"  [{cat_name}] Loading...")
        df = load_yes_prices(path)
        if df.empty or len(df) < 100:
            print(f"    Skipped: too few rows")
            continue
        x, mean_p, ci_lo, ci_hi = build_normalized_series(df, n_bins=n_bins)
        results[cat_name] = (x, mean_p, ci_lo, ci_hi)
        print(f"    {len(df.groupby('market_id'))} markets, {len(df)} price points")

    if not results:
        print("No data to plot.")
        return 1

    if HAS_PLOT:
        # Single figure: all categories, one subplot each or overlaid
        n_cats = len(results)
        n_cols = 2
        n_rows = (n_cats + n_cols - 1) // n_cols
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(12, 4 * n_rows))
        if n_rows == 1:
            axes = axes.reshape(1, -1)
        axes = axes.flatten()

        for i, (cat_name, (x, mean_p, ci_lo, ci_hi)) in enumerate(results.items()):
            ax = axes[i]
            ax.fill_between(x, ci_lo, ci_hi, alpha=0.3)
            ax.plot(x, mean_p, lw=2, label=cat_name)
            ax.set_xlabel("Normalized market period (0=start, 1=end)")
            ax.set_ylabel("Mean YES price")
            ax.set_title(cat_name)
            ax.set_ylim(0, 1)
            ax.set_xlim(0, 1)
            ax.axhline(0.5, color="gray", ls="--", alpha=0.5)
            ax.grid(True, alpha=0.3)

        for j in range(i + 1, len(axes)):
            axes[j].set_visible(False)
        plt.tight_layout()
        plt.savefig(out_dir / "price_over_market_period_by_category.png", dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Wrote price_over_market_period_by_category.png")

        # Overlay all categories on one plot (for trend comparison)
        fig, ax = plt.subplots(figsize=(10, 6))
        colors = plt.cm.tab10(np.linspace(0, 1, len(results)))
        for (cat_name, (x, mean_p, ci_lo, ci_hi)), c in zip(results.items(), colors):
            ax.fill_between(x, ci_lo, ci_hi, alpha=0.2, color=c)
            ax.plot(x, mean_p, lw=2, label=cat_name, color=c)
        ax.set_xlabel("Normalized market period (0=start, 1=end)")
        ax.set_ylabel("Mean YES price")
        ax.set_title("YES price over market lifecycle by category (mean ± 95% CI)")
        ax.set_ylim(0, 1)
        ax.set_xlim(0, 1)
        ax.axhline(0.5, color="gray", ls="--", alpha=0.5)
        ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(out_dir / "price_over_market_period_overlay.png", dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Wrote price_over_market_period_overlay.png")

    # Export CSV for inspection
    rows = []
    for cat_name, (x, mean_p, ci_lo, ci_hi) in results.items():
        for i in range(len(x)):
            rows.append({
                "category": cat_name,
                "frac": float(x[i]),
                "mean_yes_price": float(mean_p[i]),
                "ci_95_lower": float(ci_lo[i]),
                "ci_95_upper": float(ci_hi[i]),
            })
    pd.DataFrame(rows).to_csv(out_dir / "price_over_market_period.csv", index=False)
    print(f"Wrote price_over_market_period.csv")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

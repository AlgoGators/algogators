#!/usr/bin/env python3
"""
Comprehensive parameter sweep for the whale-following strategy with HTML report.

Sweeps all key knobs — win-rate threshold, surprise, volume percentile, train
ratio, unfavored-only filter, rebalance frequency, and more — then writes a
self-contained HTML report with distribution charts, per-parameter effect
plots, a Sharpe heatmap, and a ranked results table.

Usage:
    # Full grid (~864 combinations) using all CPUs
    python scripts/backtest/run_whale_full_sweep.py --workers 35

    # Quick smoke test (8 combinations)
    python scripts/backtest/run_whale_full_sweep.py --quick

    # Restrict categories
    python scripts/backtest/run_whale_full_sweep.py --categories Politics,Tech --workers 35

    # Custom output paths
    python scripts/backtest/run_whale_full_sweep.py \\
        --output data/output/full_sweep.csv \\
        --report data/output/full_sweep.html
"""

import argparse
import base64
import io
import itertools
import os
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# ── matplotlib: use headless backend and override font BEFORE any other import
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.sans-serif": ["DejaVu Sans"]})
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
_scripts_backtest = Path(__file__).resolve().parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_scripts_backtest))

# ---------------------------------------------------------------------------
# Parameter grids
# ---------------------------------------------------------------------------

PARAM_KEYS = [
    "min_surprise",
    "volume_percentile",
    "train_ratio",
    "require_positive_surprise",
    "unfavored_only",
    "unfavored_max_price",
    "rebalance_freq",
]

FULL_GRID = {
    "min_surprise":              [0.0, 0.02, 0.05],
    "volume_percentile":         [85.0, 90.0, 95.0],
    "train_ratio":               [0.3, 0.5, 0.7],
    "require_positive_surprise": [True, False],
    "unfavored_only":            [False, True],
    "unfavored_max_price":       [0.40],        # only one value; interesting when unfavored_only=True
    "rebalance_freq":            ["1W", "2W"],
}

QUICK_GRID = {
    "min_surprise":              [0.0, 0.05],
    "volume_percentile":         [90.0, 95.0],
    "train_ratio":               [0.3],
    "require_positive_surprise": [True],
    "unfavored_only":            [False],
    "unfavored_max_price":       [0.40],
    "rebalance_freq":            ["1W"],
}

METRIC_COLS = ["sharpe", "calmar", "roi_pct", "win_rate", "total_trades", "whales", "max_dd_pct"]


# ---------------------------------------------------------------------------
# Worker  (must be top-level for ProcessPoolExecutor pickling on Windows)
# ---------------------------------------------------------------------------

def _sweep_worker(args):
    """Run one whale backtest configuration. Returns (params, metrics, error)."""
    (project_root_str, research_dir_str, capital, min_usd, position_size,
     start_date, end_date, categories_list,
     min_surprise, volume_percentile, train_ratio,
     require_positive_surprise, unfavored_only, unfavored_max_price,
     rebalance_freq) = args

    # Re-insert sys.path in subprocess (Windows spawn resets it)
    import sys
    from pathlib import Path
    root = Path(project_root_str)
    for p in [str(root), str(root / "src"), str(root / "scripts" / "backtest")]:
        if p not in sys.path:
            sys.path.insert(0, p)

    from src.whale_strategy.whale_config import WhaleConfig
    from run_whale_category_backtest import run_whale_category_backtest

    params = {
        "min_surprise":              min_surprise,
        "volume_percentile":         volume_percentile,
        "train_ratio":               train_ratio,
        "require_positive_surprise": require_positive_surprise,
        "unfavored_only":            unfavored_only,
        "unfavored_max_price":       unfavored_max_price,
        "rebalance_freq":            rebalance_freq,
    }

    whale_config = WhaleConfig(
        mode="volume_only",
        min_surprise=min_surprise,
        volume_percentile=volume_percentile,
        require_positive_surprise=require_positive_surprise,
        unfavored_only=unfavored_only,
        unfavored_max_price=unfavored_max_price,
    )

    try:
        result = run_whale_category_backtest(
            research_dir=Path(research_dir_str),
            capital=capital,
            min_usd=min_usd,
            position_size=position_size,
            train_ratio=train_ratio,
            start_date=start_date,
            end_date=end_date,
            categories=categories_list if categories_list else None,
            whale_config=whale_config,
            volume_only=True,
            rebalance_freq=rebalance_freq,
            n_workers=1,
        )

        if "error" in result:
            return params, None, result["error"]

        # Derived metrics
        sharpe = 0.0
        max_dd_pct = 0.0
        calmar = 0.0

        dr = result.get("daily_returns")
        eq = result.get("daily_equity")

        if dr is not None and len(dr) >= 2 and dr.std() > 0:
            ann_ret = float(dr.mean() * 252)
            sharpe = float(dr.mean() / dr.std() * (252 ** 0.5))
            if eq is not None and len(eq) > 0:
                roll_max = eq.cummax()
                dd_series = (eq - roll_max) / roll_max.replace(0, float("nan"))
                max_dd_pct = float(dd_series.min() * 100)
                if max_dd_pct < 0:
                    calmar = round(ann_ret / abs(max_dd_pct / 100), 4)

        metrics = {
            "sharpe":       round(sharpe, 4),
            "calmar":       calmar,
            "roi_pct":      round(result.get("roi_pct", 0), 2),
            "win_rate":     round(result.get("win_rate", 0) * 100, 2),
            "total_pnl":    round(result.get("total_net_pnl", 0), 0),
            "total_trades": result.get("total_trades", 0),
            "whales":       result.get("whales_followed", 0),
            "max_dd_pct":   round(max_dd_pct, 2),
        }
        return params, metrics, None

    except Exception as exc:
        return params, None, str(exc)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return data


def _bar_param_effect(df: pd.DataFrame, param: str, metric: str, ax) -> None:
    """Draw mean ± std of *metric* per unique value of *param*."""
    grp = df.groupby(param)[metric].agg(["mean", "std"]).sort_index()
    x = range(len(grp))
    cmap = plt.cm.RdYlGn
    norm = mcolors.Normalize(vmin=grp["mean"].min(), vmax=grp["mean"].max())
    colors = [cmap(norm(v)) for v in grp["mean"]]
    bars = ax.bar(x, grp["mean"], yerr=grp["std"].fillna(0),
                  color=colors, edgecolor="white", capsize=4)
    ax.set_xticks(list(x))
    ax.set_xticklabels([str(v) for v in grp.index], rotation=30, ha="right")
    ax.set_title(param, fontsize=9)
    ax.set_ylabel(f"Mean {metric}", fontsize=8)
    ax.grid(axis="y", alpha=0.3)


def generate_report(
    df: pd.DataFrame,
    param_keys: list,
    output_html: str,
    n_errors: int,
    n_total: int,
    runtime_min: float,
    capital: float,
    categories_str: str,
    sort_by: str = "sharpe",
) -> None:
    """Build a self-contained HTML report with embedded PNG charts."""
    charts = []   # list of (title, b64_png)

    varied_params = [k for k in param_keys if k in df.columns and df[k].nunique() > 1]

    # ── Chart 1: Metrics distributions ──────────────────────────────────────
    metrics_to_plot = [(c, lbl, col) for c, lbl, col in [
        ("sharpe",   "Annualised Sharpe", "#2196F3"),
        ("calmar",   "Calmar Ratio",      "#9C27B0"),
        ("roi_pct",  "ROI (%)",           "#4CAF50"),
        ("win_rate", "Win Rate (%)",      "#FF9800"),
        ("max_dd_pct", "Max Drawdown (%)", "#f44336"),
    ] if c in df.columns and df[c].notna().any()]

    if metrics_to_plot:
        fig, axes = plt.subplots(1, len(metrics_to_plot),
                                 figsize=(4.5 * len(metrics_to_plot), 4))
        if len(metrics_to_plot) == 1:
            axes = [axes]
        fig.suptitle("Performance Distribution Across All Configurations", fontsize=12)
        for ax, (col, label, color) in zip(axes, metrics_to_plot):
            vals = df[col].dropna()
            ax.hist(vals, bins=25, color=color, alpha=0.82, edgecolor="white")
            ax.axvline(vals.median(), color="black",  ls="--", lw=1.5,
                       label=f"Median {vals.median():.3f}")
            ax.axvline(vals.mean(),   color="crimson", ls=":",  lw=1.5,
                       label=f"Mean {vals.mean():.3f}")
            ax.set_title(label, fontsize=10)
            ax.legend(fontsize=7)
            ax.grid(axis="y", alpha=0.3)
        plt.tight_layout()
        charts.append(("Performance Distributions", _fig_to_b64(fig)))

    # ── Chart 2: Per-parameter effect on Sharpe ─────────────────────────────
    if varied_params:
        n_cols = min(4, len(varied_params))
        n_rows = (len(varied_params) + n_cols - 1) // n_cols
        fig, axes = plt.subplots(n_rows, n_cols,
                                 figsize=(5.5 * n_cols, 3.8 * n_rows), squeeze=False)
        fig.suptitle("Mean Sharpe by Parameter Value  (bars = ±1 std)", fontsize=12)
        flat = axes.flatten()
        for ax, key in zip(flat, varied_params):
            _bar_param_effect(df, key, "sharpe", ax)
        for ax in flat[len(varied_params):]:
            ax.set_visible(False)
        plt.tight_layout()
        charts.append(("Parameter Effect on Sharpe", _fig_to_b64(fig)))

    # ── Chart 3: Same for ROI ────────────────────────────────────────────────
    if varied_params and "roi_pct" in df.columns:
        n_cols = min(4, len(varied_params))
        n_rows = (len(varied_params) + n_cols - 1) // n_cols
        fig, axes = plt.subplots(n_rows, n_cols,
                                 figsize=(5.5 * n_cols, 3.8 * n_rows), squeeze=False)
        fig.suptitle("Mean ROI (%) by Parameter Value  (bars = ±1 std)", fontsize=12)
        flat = axes.flatten()
        for ax, key in zip(flat, varied_params):
            _bar_param_effect(df, key, "roi_pct", ax)
        for ax in flat[len(varied_params):]:
            ax.set_visible(False)
        plt.tight_layout()
        charts.append(("Parameter Effect on ROI", _fig_to_b64(fig)))

    # ── Chart 4: ROI vs Win Rate scatter (colour = Sharpe) ──────────────────
    if "win_rate" in df.columns and "roi_pct" in df.columns:
        fig, ax = plt.subplots(figsize=(8, 5))
        lo, hi = df["sharpe"].quantile(0.05), df["sharpe"].quantile(0.95)
        sc = ax.scatter(df["win_rate"], df["roi_pct"],
                        c=df["sharpe"], cmap="RdYlGn",
                        alpha=0.65, s=25, vmin=lo, vmax=hi)
        plt.colorbar(sc, ax=ax, label="Sharpe")
        ax.set_xlabel("Win Rate (%)")
        ax.set_ylabel("ROI (%)")
        ax.set_title("ROI vs Win Rate  (colour = Sharpe)")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        charts.append(("ROI vs Win Rate", _fig_to_b64(fig)))

    # ── Chart 5: Sharpe heatmap for the two most impactful parameters ────────
    if len(varied_params) >= 2:
        param_var = {
            k: df.groupby(k)["sharpe"].mean().std()
            for k in varied_params
            if df[k].nunique() > 1
        }
        top2 = sorted(param_var, key=lambda k: -param_var.get(k, 0))[:2]
        if len(top2) == 2:
            try:
                pivot = df.pivot_table(
                    values="sharpe", index=top2[0], columns=top2[1], aggfunc="mean"
                )
                nr, nc = len(pivot.index), len(pivot.columns)
                fig, ax = plt.subplots(figsize=(max(5, nc * 1.4), max(3, nr * 0.9)))
                im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto")
                plt.colorbar(im, ax=ax, label="Mean Sharpe")
                ax.set_xticks(range(nc))
                ax.set_xticklabels([str(c) for c in pivot.columns], rotation=30)
                ax.set_yticks(range(nr))
                ax.set_yticklabels([str(i) for i in pivot.index])
                ax.set_xlabel(top2[1])
                ax.set_ylabel(top2[0])
                ax.set_title(f"Mean Sharpe: {top2[0]}  ×  {top2[1]}")
                for i in range(nr):
                    for j in range(nc):
                        v = pivot.values[i, j]
                        if not np.isnan(v):
                            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                                    fontsize=8,
                                    color="white" if abs(v - pivot.values.mean()) > 0.3 else "black")
                plt.tight_layout()
                charts.append((f"Sharpe Heatmap: {top2[0]} × {top2[1]}", _fig_to_b64(fig)))
            except Exception:
                pass

    # ── Chart 6: Calmar heatmap (same top-2 params) ─────────────────────────
    if len(varied_params) >= 2 and "calmar" in df.columns and df["calmar"].notna().any():
        top2 = sorted(param_var, key=lambda k: -param_var.get(k, 0))[:2]
        if len(top2) == 2:
            try:
                pivot = df.pivot_table(
                    values="calmar", index=top2[0], columns=top2[1], aggfunc="mean"
                )
                nr, nc = len(pivot.index), len(pivot.columns)
                fig, ax = plt.subplots(figsize=(max(5, nc * 1.4), max(3, nr * 0.9)))
                im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto")
                plt.colorbar(im, ax=ax, label="Mean Calmar")
                ax.set_xticks(range(nc))
                ax.set_xticklabels([str(c) for c in pivot.columns], rotation=30)
                ax.set_yticks(range(nr))
                ax.set_yticklabels([str(i) for i in pivot.index])
                ax.set_xlabel(top2[1])
                ax.set_ylabel(top2[0])
                ax.set_title(f"Mean Calmar: {top2[0]}  ×  {top2[1]}")
                for i in range(nr):
                    for j in range(nc):
                        v = pivot.values[i, j]
                        if not np.isnan(v):
                            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8)
                plt.tight_layout()
                charts.append((f"Calmar Heatmap: {top2[0]} × {top2[1]}", _fig_to_b64(fig)))
            except Exception:
                pass

    # ── Build HTML ───────────────────────────────────────────────────────────
    df_sorted = df.sort_values(sort_by, ascending=False).reset_index(drop=True)
    display_cols = [c for c in (METRIC_COLS + param_keys) if c in df_sorted.columns]
    top20_html = (
        df_sorted[display_cols]
        .head(20)
        .to_html(classes="table", index=True, float_format="{:.3f}".format, border=0)
    )

    best = df_sorted.iloc[0]
    best_rows = "".join(
        f"<tr><td class='k'>{k}</td><td class='v'>{v}</td></tr>"
        for k, v in best.items()
    )

    chart_html = "\n".join(
        f'<h2>{title}</h2>'
        f'<img src="data:image/png;base64,{b64}" style="max-width:100%;margin-bottom:24px">'
        for title, b64 in charts
    )

    # Summary stats row
    def stat(value, label):
        return (f'<div class="stat">'
                f'<div class="value">{value}</div>'
                f'<div class="label">{label}</div></div>')

    summary_html = "".join([
        stat(n_total,               "Combinations"),
        stat(len(df),               "Successes"),
        stat(n_errors,              "Errors"),
        stat(f"{runtime_min:.1f} min", "Runtime"),
        stat(f"${capital:,.0f}",    "Capital"),
        stat(categories_str,        "Categories"),
        stat(f"{df['sharpe'].max():.3f}", "Best Sharpe"),
        stat(f"{df['roi_pct'].max():.1f}%", "Best ROI"),
    ])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Whale Strategy — Full Parameter Sweep</title>
<style>
  body   {{ font-family: "DejaVu Sans", Arial, sans-serif; margin: 40px;
            background: #f0f2f5; color: #222; }}
  h1     {{ color: #1a237e; margin-bottom: 6px; }}
  h2     {{ color: #283593; border-bottom: 2px solid #c5cae9;
            padding-bottom: 6px; margin-top: 40px; }}
  .meta  {{ color: #555; margin-bottom: 28px; font-size: 0.9em; }}
  .summary {{ display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 32px; }}
  .stat  {{ background: white; padding: 14px 22px; border-radius: 8px;
            box-shadow: 0 1px 4px rgba(0,0,0,.12); min-width: 130px; }}
  .stat .value {{ font-size: 1.55em; font-weight: 700; color: #1a237e; }}
  .stat .label {{ font-size: 0.82em; color: #666; margin-top: 2px; }}
  .best  {{ background: white; padding: 20px 28px; border-radius: 8px;
            box-shadow: 0 1px 4px rgba(0,0,0,.12); display: inline-block;
            margin-bottom: 16px; }}
  .best table {{ border-collapse: collapse; }}
  .best td   {{ padding: 4px 14px; border-bottom: 1px solid #eee; }}
  .best td.k {{ color: #555; font-size: 0.88em; }}
  .best td.v {{ font-weight: 600; }}
  .table  {{ border-collapse: collapse; width: 100%; background: white;
             font-size: 0.84em; border-radius: 8px; overflow: hidden;
             box-shadow: 0 1px 4px rgba(0,0,0,.12); }}
  .table th {{ background: #283593; color: white; padding: 9px 12px;
               text-align: right; font-weight: 600; }}
  .table td {{ padding: 7px 12px; border-bottom: 1px solid #e8eaf6;
               text-align: right; }}
  .table tr:nth-child(1) td {{ background: #fffde7; }}
  .table tr:nth-child(2) td {{ background: #f9fbe7; }}
  .table tr:nth-child(3) td {{ background: #f3f4fd; }}
  .table tr:hover td {{ background: #e8eaf6; }}
  img    {{ border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,.15); }}
</style>
</head>
<body>
<h1>Whale Strategy — Full Parameter Sweep</h1>
<p class="meta">Sorted by <strong>{sort_by}</strong> &nbsp;|&nbsp;
   Generated on {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="summary">{summary_html}</div>

<h2>Best Configuration (by {sort_by})</h2>
<div class="best"><table>{best_rows}</table></div>

{chart_html}

<h2>Top 20 Configurations (sorted by {sort_by})</h2>
{top20_html}
</body>
</html>"""

    Path(output_html).parent.mkdir(parents=True, exist_ok=True)
    with open(output_html, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"  HTML report : {output_html}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Full parameter sweep for the whale-following strategy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--research-dir",
                        default=str(_project_root / "data" / "research"))
    parser.add_argument("--capital",        type=float, default=1_000_000)
    parser.add_argument("--min-usd",        type=float, default=100.0)
    parser.add_argument("--position-size",  type=float, default=25_000.0)
    parser.add_argument("--workers",        type=int,
                        default=os.cpu_count() or 1,
                        help="Parallel workers (default: all CPUs)")
    parser.add_argument("--start-date",     default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date",       default=None, help="YYYY-MM-DD")
    parser.add_argument("--categories",     default=None,
                        help="Comma-separated list (default: all)")
    parser.add_argument("--output",
                        default=str(_project_root / "data" / "output" / "full_sweep.csv"),
                        help="CSV results path")
    parser.add_argument("--report",
                        default=str(_project_root / "data" / "output" / "full_sweep.html"),
                        help="HTML report path")
    parser.add_argument("--quick",    action="store_true",
                        help="Use small grid (8 combos) for a fast smoke test")
    parser.add_argument("--top-n",   type=int, default=20,
                        help="Rows to print in console summary")
    parser.add_argument("--sort-by", default="sharpe",
                        choices=["sharpe", "calmar", "roi_pct", "win_rate"],
                        help="Metric to rank by")
    args = parser.parse_args()

    grid = QUICK_GRID if args.quick else FULL_GRID
    keys = list(grid.keys())
    combos = list(itertools.product(*grid.values()))
    categories_list = (
        [c.strip() for c in args.categories.split(",")]
        if args.categories else []
    )
    categories_str = args.categories or "all"

    print("=" * 70)
    print("WHALE STRATEGY — FULL PARAMETER SWEEP")
    print("=" * 70)
    print(f"  Combinations : {len(combos)}")
    print(f"  Workers      : {min(args.workers, len(combos))}")
    print(f"  Research dir : {args.research_dir}")
    print(f"  Capital      : ${args.capital:,.0f}")
    print(f"  Categories   : {categories_str}")
    print(f"  Sort by      : {args.sort_by}")
    print()
    for k in keys:
        print(f"  {k}: {grid[k]}")
    print()

    worker_args = [
        (
            str(_project_root), args.research_dir,
            args.capital, args.min_usd, args.position_size,
            args.start_date, args.end_date, categories_list,
            *[combo[i] for i in range(len(keys))],
        )
        for combo in combos
    ]

    results = []
    n_errors = 0
    t0 = time.time()
    n_workers = min(args.workers, len(combos))

    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_sweep_worker, wa): i for i, wa in enumerate(worker_args)}
        for n_done, future in enumerate(as_completed(futures), start=1):
            elapsed = time.time() - t0
            eta = (len(combos) - n_done) / (n_done / elapsed) if elapsed > 0 else 0
            params, metrics, error = future.result()
            label = "  ".join(f"{k}={v}" for k, v in params.items())
            if error:
                n_errors += 1
                print(f"  [{n_done:>4}/{len(combos)}] ERROR  {label}  →  {error[:80]}")
            else:
                results.append({**params, **metrics})
                print(
                    f"  [{n_done:>4}/{len(combos)}] "
                    f"Sharpe={metrics['sharpe']:>7.3f}  "
                    f"Calmar={metrics['calmar']:>7.3f}  "
                    f"ROI={metrics['roi_pct']:>7.2f}%  "
                    f"Win={metrics['win_rate']:>5.1f}%  "
                    f"DD={metrics['max_dd_pct']:>6.1f}%  "
                    f"ETA={eta:.0f}s  "
                    f"{label}"
                )

    runtime_min = (time.time() - t0) / 60

    if not results:
        print("No successful runs.")
        return 1

    df = (
        pd.DataFrame(results)
        .sort_values(args.sort_by, ascending=False)
        .reset_index(drop=True)
    )

    # Save CSV
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)

    # Console summary
    display_cols = [c for c in (METRIC_COLS + keys) if c in df.columns]
    print()
    print("=" * 70)
    print(f"TOP {min(args.top_n, len(df))} CONFIGURATIONS  (ranked by {args.sort_by})")
    print("=" * 70)
    print(df[display_cols].head(args.top_n).to_string(index=True))
    print()
    print(f"  Errors  : {n_errors} / {len(combos)}")
    print(f"  Results : {args.output}")
    print(f"  Runtime : {runtime_min:.1f} min")

    # Generate HTML report
    print("\nGenerating HTML report...")
    try:
        generate_report(
            df=df,
            param_keys=keys,
            output_html=args.report,
            n_errors=n_errors,
            n_total=len(combos),
            runtime_min=runtime_min,
            capital=args.capital,
            categories_str=categories_str,
            sort_by=args.sort_by,
        )
    except Exception as exc:
        warnings.warn(f"Report generation failed: {exc}")
        import traceback
        traceback.print_exc()

    return 0


if __name__ == "__main__":
    sys.exit(main())

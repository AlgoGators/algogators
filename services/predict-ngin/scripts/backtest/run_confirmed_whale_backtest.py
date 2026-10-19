#!/usr/bin/env python3
"""
Confirmed-whale backtest.

Signal pipeline
───────────────
  Raw trades
    → [1] Size filter          : usd_amount > $10 K
    → [2] Liquidity filter     : market volume > $500 K
    → [3] Category filter      : politics / geopolitical (configurable)
    → [4] Wallet reputation    : historical win rate > 60 %
    → [5] Price impact         : trade_size / market_volume < 5 %
    → [6] Confirmation         : 2+ distinct whales same direction
                                 within a rolling 24-h window
    → EXECUTE within 1 h  |  max 3 % of bankroll per trade

Usage
─────
    python scripts/backtest/run_confirmed_whale_backtest.py

    # Custom thresholds
    python scripts/backtest/run_confirmed_whale_backtest.py \\
        --min-size 15000 --min-liquidity 750000 --min-wr 0.65

    # Restrict categories
    python scripts/backtest/run_confirmed_whale_backtest.py \\
        --categories "Politics,Geopolitics,Elections"

    # Full date range
    python scripts/backtest/run_confirmed_whale_backtest.py \\
        --start-date 2024-01-01 --end-date 2024-12-31
"""

import argparse
import base64
import io
import sys
for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams.update({"font.family": "DejaVu Sans",
                             "font.sans-serif": ["DejaVu Sans"]})
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

from src.whale_strategy.research_data_loader import (
    load_research_trades,
    load_research_markets,
    load_resolution_winners,
    get_research_categories,
)
from src.whale_strategy.whale_surprise import (
    identify_whales_rolling,
    build_surprise_positive_whale_set,
)
from src.whale_strategy.confirmed_signals import extract_confirmed_signals
from src.trading.reporting import generate_quantstats_report


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def simulate_trades(
    signals: pd.DataFrame,
    resolution_winners: dict,
    capital: float,
    max_position_pct: float = 0.03,
    max_holding_days: int = 180,
) -> pd.DataFrame:
    """
    Simulate P&L for each confirmed signal.

    Position size = min(capital * max_position_pct, $50 K).
    Exit at binary resolution (1.0 YES / 0.0 NO).
    Open positions excluded from P&L.
    """
    pos_size = min(capital * max_position_pct, 50_000)
    rows = []

    for _, sig in signals.iterrows():
        mid       = sig["market_id"]
        direction = sig["direction"]
        entry     = float(sig["price"])
        sig_time  = sig["datetime"]

        winner = resolution_winners.get(mid)
        if winner not in ("YES", "NO"):
            rows.append({**sig.to_dict(), "status": "open",
                         "pnl": 0.0, "roi": 0.0, "correct": None, "pos_size": pos_size})
            continue

        resolution = 1.0 if winner == "YES" else 0.0
        if direction == "buy":
            roi = (resolution - entry) / max(entry, 0.01)
            correct = winner == "YES"
        else:
            roi = (entry - resolution) / max(1 - entry, 0.01)
            correct = winner == "NO"

        pnl = roi * pos_size
        rows.append({**sig.to_dict(), "status": "closed",
                     "pnl": pnl, "roi": roi, "correct": correct, "pos_size": pos_size})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(trades_df: pd.DataFrame, capital: float) -> dict:
    closed = trades_df[trades_df["status"] == "closed"].copy()
    if closed.empty:
        return {"total_signals": len(trades_df), "closed_trades": 0}

    wins  = (closed["correct"] == True).sum()
    total = len(closed)
    win_rate = wins / total

    total_pnl = closed["pnl"].sum()
    roi_pct   = (total_pnl / capital) * 100

    # Daily equity curve
    closed["_date"] = pd.to_datetime(closed["datetime"]).dt.normalize()
    daily_pnl  = closed.groupby("_date")["pnl"].sum()
    date_range = pd.date_range(daily_pnl.index.min(), daily_pnl.index.max(), freq="D")
    daily_pnl  = daily_pnl.reindex(date_range, fill_value=0).sort_index()
    equity     = capital + daily_pnl.cumsum()
    daily_ret  = equity.pct_change().dropna()

    sharpe = 0.0
    if len(daily_ret) >= 2 and daily_ret.std() > 0:
        sharpe = float(daily_ret.mean() / daily_ret.std() * (252 ** 0.5))

    max_dd_pct = 0.0
    calmar     = 0.0
    roll_max   = equity.cummax()
    dd_series  = (equity - roll_max) / roll_max.replace(0, np.nan)
    if not dd_series.empty:
        max_dd_pct = float(dd_series.min() * 100)
    if max_dd_pct < 0:
        ann_ret = float(daily_ret.mean() * 252)
        calmar  = round(ann_ret / abs(max_dd_pct / 100), 4)

    avg_whales = closed["confirming_whales"].mean() if "confirming_whales" in closed else 0

    return {
        "total_signals":    len(trades_df),
        "closed_trades":    total,
        "open_trades":      (trades_df["status"] == "open").sum(),
        "win_rate_pct":     round(win_rate * 100, 2),
        "total_pnl":        round(total_pnl, 2),
        "roi_pct":          round(roi_pct, 2),
        "sharpe":           round(sharpe, 4),
        "calmar":           calmar,
        "max_dd_pct":       round(max_dd_pct, 2),
        "avg_pnl":          round(closed["pnl"].mean(), 2),
        "avg_roi_pct":      round(closed["roi"].mean() * 100, 2),
        "avg_whales":       round(avg_whales, 2),
        "daily_returns":    daily_ret,
        "equity":           equity,
    }


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _fig_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return b64


def build_report(
    metrics: dict,
    trades_df: pd.DataFrame,
    pipeline_params: dict,
    output_path: str,
) -> None:
    charts = []

    # ── Equity curve ─────────────────────────────────────────────────────────
    equity = metrics.get("equity")
    if equity is not None and len(equity) > 1:
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(equity.index, equity.values, color="#1565C0", lw=1.8, label="Equity")
        ax.fill_between(equity.index, equity.values, equity.iloc[0],
                        alpha=0.12, color="#1565C0")
        ax.axhline(equity.iloc[0], color="#aaa", lw=0.8, ls="--")
        ax.set_title("Equity Curve — Confirmed Whale Strategy", fontsize=12)
        ax.set_ylabel("Portfolio Value ($)")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax.grid(alpha=0.25)
        ax.legend()
        plt.tight_layout()
        charts.append(("Equity Curve", _fig_b64(fig)))

    # ── Daily returns distribution ────────────────────────────────────────────
    dr = metrics.get("daily_returns")
    if dr is not None and len(dr) > 5:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(dr.values * 100, bins=30, color="#42A5F5", edgecolor="white", alpha=0.85)
        ax.axvline(0, color="black", lw=1)
        ax.axvline(float(dr.mean() * 100), color="red", ls="--", lw=1.5,
                   label=f"Mean {dr.mean()*100:.3f}%")
        ax.set_xlabel("Daily Return (%)")
        ax.set_title("Daily Return Distribution")
        ax.legend()
        ax.grid(alpha=0.25)
        plt.tight_layout()
        charts.append(("Daily Return Distribution", _fig_b64(fig)))

    # ── Per-trade P&L waterfall ───────────────────────────────────────────────
    closed = trades_df[trades_df["status"] == "closed"].copy()
    if not closed.empty:
        closed_s = closed.sort_values("datetime")
        colors = ["#43A047" if v >= 0 else "#E53935" for v in closed_s["pnl"]]
        fig, ax = plt.subplots(figsize=(max(8, len(closed_s) * 0.18), 4))
        ax.bar(range(len(closed_s)), closed_s["pnl"], color=colors, width=0.8)
        ax.axhline(0, color="black", lw=0.8)
        ax.set_xlabel("Trade #")
        ax.set_ylabel("P&L ($)")
        ax.set_title(f"Per-trade P&L  ({len(closed_s)} resolved trades)")
        ax.grid(axis="y", alpha=0.25)
        plt.tight_layout()
        charts.append(("Per-Trade P&L", _fig_b64(fig)))

    # ── Confirmation depth distribution ──────────────────────────────────────
    if "confirming_whales" in trades_df.columns:
        fig, ax = plt.subplots(figsize=(6, 3))
        trades_df["confirming_whales"].value_counts().sort_index().plot.bar(
            ax=ax, color="#7E57C2", edgecolor="white")
        ax.set_xlabel("# Confirming Whales")
        ax.set_ylabel("Signals")
        ax.set_title("Confirmation Depth")
        ax.grid(axis="y", alpha=0.25)
        plt.tight_layout()
        charts.append(("Confirmation Depth", _fig_b64(fig)))

    # ── Assemble HTML ─────────────────────────────────────────────────────────
    def stat(v, lbl):
        return (f'<div class="stat"><div class="value">{v}</div>'
                f'<div class="label">{lbl}</div></div>')

    pipeline_rows = "".join(
        f"<tr><td class='k'>{k}</td><td class='v'>{v}</td></tr>"
        for k, v in pipeline_params.items()
    )

    metrics_excl = {"daily_returns", "equity"}
    summary_stats = {k: v for k, v in metrics.items() if k not in metrics_excl}
    stat_html = "".join(
        stat(f"{v:.3f}" if isinstance(v, float) else v, k.replace("_", " "))
        for k, v in summary_stats.items()
    )

    chart_html = "\n".join(
        f'<h2>{t}</h2><img src="data:image/png;base64,{b}" style="max-width:100%">'
        for t, b in charts
    )

    # Trade table (last 200 for file size)
    display_cols = [c for c in [
        "datetime", "market_id", "category", "direction", "price",
        "confirming_whales", "pos_size", "pnl", "roi", "correct",
        "status", "market_liquidity_usd", "price_impact_pct",
    ] if c in trades_df.columns]
    table_html = (
        trades_df[display_cols]
        .sort_values("datetime", ascending=False)
        .head(200)
        .reset_index(drop=True)
        .to_html(classes="table", index=True, float_format="{:.4f}".format, border=0)
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Confirmed Whale Backtest</title>
<style>
  body  {{ font-family: "DejaVu Sans", Arial, sans-serif; margin: 40px;
           background: #f0f2f5; }}
  h1    {{ color: #1a237e; }}
  h2    {{ color: #283593; border-bottom: 2px solid #c5cae9; padding-bottom: 6px;
           margin-top: 40px; }}
  .meta {{ color: #555; margin-bottom: 28px; font-size: 0.9em; }}
  .summary {{ display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 32px; }}
  .stat {{ background: white; padding: 14px 22px; border-radius: 8px;
           box-shadow: 0 1px 4px rgba(0,0,0,.12); min-width: 130px; }}
  .stat .value {{ font-size: 1.5em; font-weight: 700; color: #1a237e; }}
  .stat .label {{ font-size: 0.82em; color: #666; margin-top: 2px; }}
  .pipeline {{ background: white; padding: 20px 28px; border-radius: 8px;
               box-shadow: 0 1px 4px rgba(0,0,0,.12); display:inline-block;
               margin-bottom: 20px; }}
  .pipeline table {{ border-collapse: collapse; }}
  .pipeline td {{ padding: 5px 14px; border-bottom: 1px solid #eee; }}
  .pipeline td.k {{ color: #555; font-size: 0.88em; }}
  .pipeline td.v {{ font-weight: 600; }}
  .table  {{ border-collapse: collapse; width: 100%; background: white;
             font-size: 0.82em; border-radius: 8px; overflow: hidden;
             box-shadow: 0 1px 4px rgba(0,0,0,.12); }}
  .table th {{ background: #283593; color: white; padding: 8px 10px; text-align: right; }}
  .table td {{ padding: 6px 10px; border-bottom: 1px solid #e8eaf6; text-align: right; }}
  .table tr:hover td {{ background: #e8eaf6; }}
  img  {{ border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,.15);
          margin-bottom: 24px; }}
</style>
</head>
<body>
<h1>Confirmed Whale Strategy — Backtest Report</h1>
<p class="meta">Generated {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}</p>

<h2>Pipeline Configuration</h2>
<div class="pipeline"><table>{pipeline_rows}</table></div>

<h2>Summary Metrics</h2>
<div class="summary">{stat_html}</div>

{chart_html}

<h2>Signal Log  (most recent 200)</h2>
{table_html}
</body>
</html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"  Report: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DEFAULT_CATEGORIES = [
    "Politics", "Geopolitics", "Elections", "Political",
    "US Elections", "World", "International",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Confirmed-whale backtest (politics/geo, 2+ whales, 3% bankroll)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--research-dir", type=Path,
                        default=_project_root / "data" / "research")
    parser.add_argument("--capital",        type=float, default=1_000_000)
    parser.add_argument("--max-pos-pct",    type=float, default=0.03,
                        help="Max position as fraction of capital (3%% = 0.03)")
    parser.add_argument("--categories",     default=None,
                        help="Comma-separated list; default = politics/geo set")
    parser.add_argument("--start-date",     default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date",       default=None, help="YYYY-MM-DD")

    # Pipeline thresholds
    parser.add_argument("--min-size",       type=float, default=10_000,
                        help="Step 1: min trade size USD")
    parser.add_argument("--min-liquidity",  type=float, default=500_000,
                        help="Step 2: min market volume USD")
    parser.add_argument("--min-wr",         type=float, default=0.60,
                        help="Step 4: min wallet historical win rate")
    parser.add_argument("--max-impact",     type=float, default=0.05,
                        help="Step 5: max price impact (trade/market vol)")
    parser.add_argument("--min-confirms",   type=int,   default=2,
                        help="Step 6: distinct whales needed to confirm")
    parser.add_argument("--window-hours",   type=int,   default=24,
                        help="Confirmation look-back window (hours)")
    parser.add_argument("--cooldown-hours", type=int,   default=168,
                        help="Silence window after a signal fires (hours)")
    parser.add_argument("--volume-pct",     type=float, default=90.0,
                        help="Whale volume percentile threshold")

    parser.add_argument("--output",  default=str(_project_root / "data" / "output" / "confirmed_whale.csv"))
    parser.add_argument("--report",  default=str(_project_root / "data" / "output" / "confirmed_whale.html"))
    parser.add_argument("--quantstats", default=str(_project_root / "data" / "output" / "confirmed_whale_qs.html"),
                        help="Path for QuantStats HTML report")
    args = parser.parse_args()

    categories = (
        [c.strip() for c in args.categories.split(",")]
        if args.categories
        else DEFAULT_CATEGORIES
    )

    pipeline_params = {
        "Step 1 — min trade size":         f"${args.min_size:,.0f}",
        "Step 2 — min market volume":      f"${args.min_liquidity:,.0f}",
        "Step 3 — categories":             ", ".join(categories),
        "Step 4 — min wallet win rate":    f"{args.min_wr*100:.0f}%",
        "Step 5 — max price impact":       f"{args.max_impact*100:.0f}%",
        "Step 6 — min confirming whales":  args.min_confirms,
        "Confirmation window":             f"{args.window_hours}h",
        "Signal cooldown":                 f"{args.cooldown_hours}h  ({args.cooldown_hours//24}d)",
        "Position size":                   f"max {args.max_pos_pct*100:.0f}% of ${args.capital:,.0f}",
        "Whale volume percentile":         f"{args.volume_pct}th",
    }

    print("=" * 65)
    print("CONFIRMED WHALE BACKTEST")
    print("=" * 65)
    for k, v in pipeline_params.items():
        print(f"  {k:<35} {v}")
    print()

    # ── Load data ────────────────────────────────────────────────────────────
    available_cats = get_research_categories(args.research_dir)
    run_cats = [c for c in categories if c in available_cats]
    if not run_cats:
        # Try case-insensitive match
        avail_lower = {c.lower(): c for c in available_cats}
        run_cats = [avail_lower[c.lower()] for c in categories if c.lower() in avail_lower]
    if not run_cats:
        print(f"No matching categories found. Available: {available_cats}")
        return 1

    print(f"Categories found  : {run_cats}")
    print("Loading trades...")
    trades_df = load_research_trades(
        args.research_dir,
        categories=run_cats,
        min_usd=1.0,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if trades_df.empty:
        print("No trades loaded.")
        return 1
    print(f"  {len(trades_df):,} trades  |  "
          f"{trades_df['datetime'].min().date()} → {trades_df['datetime'].max().date()}")

    print("Loading markets...")
    markets_df = load_research_markets(args.research_dir, categories=run_cats)
    print(f"  {len(markets_df):,} markets")

    print("Loading resolutions...")
    resolution_winners = load_resolution_winners(args.research_dir)
    print(f"  {len(resolution_winners):,} resolved markets")

    # ── Build market liquidity map ───────────────────────────────────────────
    market_liquidity: dict = {}
    if not markets_df.empty and "market_id" in markets_df.columns:
        mdf = markets_df.copy()
        mdf["_mid"] = mdf["market_id"].astype(str).str.strip()
        liq_col = next(
            (c for c in ["volumeNum", "volume", "liquidityNum", "liquidity"]
             if c in mdf.columns),
            None,
        )
        if liq_col:
            market_liquidity = dict(zip(
                mdf["_mid"],
                pd.to_numeric(mdf[liq_col], errors="coerce").fillna(0),
            ))

    # ── Identify whale addresses ─────────────────────────────────────────────
    print("\nIdentifying whales...")
    whale_set, whale_scores, whale_winrates = build_surprise_positive_whale_set(
        train_trades=trades_df,
        resolution_winners=resolution_winners,
        volume_percentile=args.volume_pct,
        require_positive_surprise=True,
    )
    print(f"  {len(whale_set):,} qualifying whale addresses")

    if not whale_set:
        print("No whales found. Lower --min-wr or --volume-pct.")
        return 1

    # ── Run pipeline ─────────────────────────────────────────────────────────
    print("\nRunning confirmation pipeline...")
    signals = extract_confirmed_signals(
        trades_df=trades_df,
        whale_set=whale_set,
        whale_winrates=whale_winrates,
        market_liquidity=market_liquidity,
        allowed_categories=run_cats,
        min_size_usd=args.min_size,
        min_market_volume=args.min_liquidity,
        min_wallet_wr=args.min_wr,
        max_price_impact=args.max_impact,
        min_confirmations=args.min_confirms,
        confirmation_window_hours=args.window_hours,
        cooldown_hours=args.cooldown_hours,
    )

    if signals.empty:
        print("No confirmed signals generated.")
        print("Tips: lower --min-wr, --min-size, --min-liquidity, or increase --window-hours")
        return 1

    print(f"  {len(signals):,} confirmed signals")
    if "confirming_whales" in signals.columns:
        print(f"  Avg confirming whales : {signals['confirming_whales'].mean():.1f}")

    # ── Simulate P&L ─────────────────────────────────────────────────────────
    print("\nSimulating trades...")
    trades_result = simulate_trades(
        signals=signals,
        resolution_winners=resolution_winners,
        capital=args.capital,
        max_position_pct=args.max_pos_pct,
    )

    # ── Metrics ──────────────────────────────────────────────────────────────
    metrics = compute_metrics(trades_result, args.capital)

    print()
    print("=" * 65)
    print("RESULTS")
    print("=" * 65)
    skip = {"daily_returns", "equity"}
    for k, v in metrics.items():
        if k in skip:
            continue
        label = k.replace("_", " ")
        if isinstance(v, float):
            print(f"  {label:<30} {v:.4f}")
        else:
            print(f"  {label:<30} {v}")

    # ── Save CSV ──────────────────────────────────────────────────────────────
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    trades_result.drop(
        columns=[c for c in ["whale_addresses"] if c in trades_result.columns],
        inplace=True,
        errors="ignore",
    )
    trades_result.to_csv(args.output, index=False)
    print(f"\n  Trades CSV : {args.output}")

    # ── Custom HTML report ────────────────────────────────────────────────────
    print("  Building report...")
    build_report(
        metrics=metrics,
        trades_df=trades_result,
        pipeline_params=pipeline_params,
        output_path=args.report,
    )

    # ── QuantStats HTML report ────────────────────────────────────────────────
    dr = metrics.get("daily_returns")
    if dr is not None and len(dr) >= 5:
        print("  Building QuantStats report...")
        ok = generate_quantstats_report(
            dr,
            args.quantstats,
            title="Confirmed Whale Strategy",
        )
        if ok:
            print(f"  QuantStats  : {args.quantstats}")
        else:
            print("  QuantStats  : skipped (install quantstats to enable)")
    else:
        print("  QuantStats  : skipped (not enough daily return data)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Generate a QuantStats HTML tearsheet from whale backtest trades or walk-forward folds.

Primary mode (--trades-csv): builds equity curve from actual trade exit dates and net_pnl.
  Each trade's net_pnl is assigned to its exit_date. Daily returns are
  net_pnl / running_equity, giving honest drawdowns and win rates.

Fallback mode (--wf-csv only): selects non-overlapping 6-month test slices from the
  walk-forward summary CSV and distributes PnL as end-of-period step functions.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "src"))

OUTPUT_DIR = _root / "data" / "output" / "whale_following"


def build_equity_from_trades(trades_csv: Path, capital: float = 1_000_000.0) -> pd.Series:
    """
    Build daily equity curve from individual trade records.

    Each trade's net_pnl is attributed to its exit_date. Capital compounding
    is applied day-by-day, giving realistic drawdowns and daily return volatility.
    """
    df = pd.read_csv(trades_csv)
    df["exit_date"] = pd.to_datetime(df["exit_date"]).dt.normalize()
    df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0.0)

    # Daily PnL: sum trades closing on same day
    daily_pnl = df.groupby("exit_date")["net_pnl"].sum().sort_index()

    # Build continuous date range from first to last trade
    date_range = pd.date_range(daily_pnl.index[0], daily_pnl.index[-1], freq="D")
    daily_pnl = daily_pnl.reindex(date_range, fill_value=0.0)

    # Equity curve (running sum starting from capital)
    equity = capital + daily_pnl.cumsum()

    print(f"Trades: {len(df)}, date range: {daily_pnl.index[0].date()} → {daily_pnl.index[-1].date()}")
    print(f"Days with trades: {(daily_pnl != 0).sum()}")
    return equity


def build_equity_from_wf(wf_csv: Path, capital: float = 1_000_000.0) -> pd.Series:
    """
    Select non-overlapping 6-month test windows from walk-forward results and
    build a step-function equity curve (PnL realised on last day of each period).
    """
    df = pd.read_csv(wf_csv)
    df["test_start"] = pd.to_datetime(df["test_start"])
    df["test_end"] = pd.to_datetime(df["test_end"])
    df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0.0)

    # Select non-overlapping slices greedily
    selected = []
    last_end = pd.Timestamp.min
    for _, row in df.sort_values("test_start").iterrows():
        if row["test_start"] >= last_end:
            selected.append(row)
            last_end = row["test_end"]

    print(f"Selected {len(selected)} non-overlapping folds:")
    for r in selected:
        roi = r["net_pnl"] / capital * 100
        print(
            f"  Fold {int(r['fold'])}: {r['test_start'].date()} → {r['test_end'].date()} "
            f"  PnL=${r['net_pnl']:,.0f}  ROI={roi:.1f}%"
        )

    # Step-function: flat within each period, PnL realised on last day
    pnl_events: dict = {}
    for row in selected:
        dt = row["test_end"].normalize()
        pnl_events[dt] = pnl_events.get(dt, 0.0) + row["net_pnl"]

    if not pnl_events:
        raise ValueError("No valid folds")

    start = selected[0]["test_start"].normalize()
    end = selected[-1]["test_end"].normalize()
    date_range = pd.date_range(start, end, freq="D")
    daily_pnl = pd.Series(0.0, index=date_range)
    for dt, pnl in pnl_events.items():
        if dt in daily_pnl.index:
            daily_pnl[dt] = pnl

    equity = capital + daily_pnl.cumsum()
    return equity


def equity_to_returns(equity: pd.Series) -> pd.Series:
    """Convert equity curve to daily percentage returns."""
    returns = equity.pct_change().dropna()
    returns.index = pd.DatetimeIndex(returns.index)
    return returns


def main():
    parser = argparse.ArgumentParser(description="QuantStats report from whale trades or walk-forward folds")
    parser.add_argument(
        "--trades-csv",
        default=str(OUTPUT_DIR / "whale_backtest_trades.csv"),
        help="Primary: path to whale_backtest_trades.csv (trade-level data)",
    )
    parser.add_argument(
        "--wf-csv",
        default=str(OUTPUT_DIR / "walk_forward_results.csv"),
        help="Fallback: path to walk_forward_results.csv",
    )
    parser.add_argument(
        "--use-folds", action="store_true",
        help="Force fold-summary mode even if trades CSV exists",
    )
    parser.add_argument(
        "--capital", type=float, default=1_000_000.0, help="Starting capital"
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_DIR / "quantstats_walk_forward.html"),
        help="Output HTML path",
    )
    args = parser.parse_args()

    try:
        import quantstats as qs
    except ImportError:
        print("ERROR: quantstats not installed. Run: pip install quantstats")
        sys.exit(1)

    trades_csv = Path(args.trades_csv)
    wf_csv = Path(args.wf_csv)

    if not args.use_folds and trades_csv.exists():
        print(f"Building equity curve from trade records: {trades_csv}")
        equity = build_equity_from_trades(trades_csv, capital=args.capital)
        source = "Trade Records"
    elif wf_csv.exists():
        print(f"Building equity curve from walk-forward folds: {wf_csv}")
        equity = build_equity_from_wf(wf_csv, capital=args.capital)
        source = "Walk-Forward Folds (step-function)"
    else:
        print(f"ERROR: neither {trades_csv} nor {wf_csv} found")
        sys.exit(1)

    print(f"\nEquity curve: {equity.index[0].date()} → {equity.index[-1].date()}")
    print(f"Start: ${equity.iloc[0]:,.0f}  End: ${equity.iloc[-1]:,.0f}")
    total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    print(f"Total Return: {total_return:.1f}%")

    returns = equity_to_returns(equity)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    title = f"Whale Following Strategy – {source}"
    print(f"\nGenerating QuantStats tearsheet → {output}")
    qs.reports.html(
        returns,
        output=str(output),
        title=title,
        download_filename=output.name,
    )
    print("Done.")

    def _scalar(v):
        if hasattr(v, "item"):
            return v.item()
        try:
            return float(v)
        except Exception:
            return v

    print("\n--- Quick Stats ---")
    print(f"CAGR:           {_scalar(qs.stats.cagr(returns))*100:.1f}%")
    print(f"Sharpe:         {_scalar(qs.stats.sharpe(returns)):.2f}")
    print(f"Max Drawdown:   {_scalar(qs.stats.max_drawdown(returns))*100:.1f}%")
    print(f"Avg Win/Loss:   {_scalar(qs.stats.avg_win(returns))*100:.2f}% / {_scalar(qs.stats.avg_loss(returns))*100:.2f}%")


if __name__ == "__main__":
    main()

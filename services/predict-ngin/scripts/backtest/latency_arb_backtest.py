"""
latency_arb_backtest.py — Portfolio-level backtest of latency arb strategy

Signal: the 10-min VWAP crosses from <= 75% to >= 85% (buy YES) or from
>= 25% to <= 15% (buy NO). The trade direction is decided from the PRICE PATH
ONLY. (The previous version flipped each series by its known resolution and
only looked for crossings toward the eventual outcome - an outcome oracle that
guaranteed the right side. The resolution is now used only to settle positions
still open when the data ends.)

VWAP buckets are labelled at their END (a 10-min bucket [t, t+10m) is only
known at t+10m); the old code labelled them at the bucket start (look-ahead).

Remaining bias: the market universe comes from systematic_lag.json
``is_fast_jump`` events, which are selected with hindsight. Results are an
upper bound until the universe is built from information available ex-ante.

For each signal crossing the backtest:
  1. Enters at entry_price + ENTRY_SPREAD (simulates taker order)
  2. Exits at the first 10-min VWAP that hits EXIT_TARGET (97%), OR at STOP_HOURS
     whichever comes first — using the actual price path from the parquet file
  3. Applies fee on the notional of each leg

Portfolio rules:
  • Start with INITIAL_CAPITAL
  • Size each trade: Kelly fraction × available_capital, capped at KELLY_CAP
  • Max MAX_POSITIONS open simultaneously
  • If at capacity, skip signals (they would have been skipped live too)

Output:
  • Trade log CSV
  • Equity curve CSV
  • Summary statistics (Sharpe, CAGR, max drawdown, win rate)

Usage:
    PYTHONPATH=.:src venv/bin/python3 scripts/backtest/latency_arb_backtest.py
    PYTHONPATH=.:src venv/bin/python3 scripts/backtest/latency_arb_backtest.py \\
        --capital 10000 --kelly-cap 0.25 --stop-hours 4 --max-positions 5
"""

import argparse
import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
import sys

for _stream in (sys.stdout, sys.stderr):  # cp1252 consoles cannot print box/arrow glyphs
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("lat_arb_bt")

ROOT = Path(__file__).resolve().parent.parent.parent

def _data_dir() -> Path:
    """Per-category Data-API trades (<cat>/trades.parquet) + resolutions.csv.

    Originally data/poly_cat; on this machine only data/research still has them."""
    d = ROOT / "data" / "poly_cat"
    if not (d / "resolutions.csv").exists() and (ROOT / "data" / "research" / "resolutions.csv").exists():
        return ROOT / "data" / "research"
    return d

# ── Cost model ────────────────────────────────────────────────────────────────
ENTRY_SPREAD  = 0.015   # taker spread above mid at entry
EXIT_SPREAD   = 0.005   # market-maker spread below mid at exit
FEE_RATE      = 0.002   # 0.2% of notional per leg (polymarket taker fee)

# ── Signal thresholds ─────────────────────────────────────────────────────────
SIGNAL_LOWER  = 0.75    # price must have been ≤ this before the crossing
SIGNAL_UPPER  = 0.85    # price must cross ≥ this to trigger entry
EXIT_TARGET   = 0.97    # exit when price reaches this
VWAP_BUCKET   = "10min"
MIN_VWAP_POINTS = 3

# ── Categories (in data/poly_cat/) ────────────────────────────────────────────
CATEGORIES = [
    "Art_and_Culture", "Climate_and_Science", "Economy",
    "Finance", "Geopolitics", "Other", "Politics",
]


@dataclass
class Trade:
    condition_id: str
    category: str
    resolution: str
    entry_dt: str
    direction: str            # YES / NO (from price path, not outcome)
    entry_price: float        # actual fill price (mid + spread)
    exit_dt: str
    exit_price: float         # actual fill price (mid - spread)
    exit_reason: str          # "target" | "stop_N h" | "end_of_data"
    gross_pnl: float          # per contract (exit - entry, before fee)
    net_pnl: float            # after fee
    position_usd: float       # dollars deployed
    net_pnl_usd: float        # dollars P&L
    holding_hours: float
    entry_capital: float      # portfolio equity at entry


@dataclass
class PortfolioStats:
    n_trades: int
    n_wins: int
    win_rate: float
    total_net_pnl_usd: float
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    avg_holding_hours: float
    avg_net_roi_per_trade: float
    skipped_at_capacity: int
    date_range: str


# ── Load and build VWAP series per market ─────────────────────────────────────

def load_vwap_series(
    category: str,
    condition_ids: set,
) -> dict[str, pd.Series]:
    """
    Load parquet for one category, filter to condition_ids,
    normalize to YES prices, build 10-min VWAP, return dict[cid → Series].
    """
    path = _data_dir() / category / "trades.parquet"
    if not path.exists():
        return {}

    df = pd.read_parquet(
        path,
        columns=["conditionId", "price", "size", "timestamp", "outcomeIndex"],
    )
    df = df[df["conditionId"].isin(condition_ids)]
    if df.empty:
        return {}

    df["yes_price"] = np.where(df["outcomeIndex"] == 1, 1.0 - df["price"], df["price"])
    df = df[(df["yes_price"] >= 0.001) & (df["yes_price"] <= 0.999)]
    df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)

    result = {}
    for cid, grp in df.groupby("conditionId"):
        grp = grp.set_index("dt").sort_index()
        # Label each bucket [t, t+10m) at its END t+10m: the VWAP is only known then.
        num   = (grp["yes_price"] * grp["size"]).resample(VWAP_BUCKET, label="right", closed="left").sum()
        denom = grp["size"].resample(VWAP_BUCKET, label="right", closed="left").sum()
        vwap  = (num / denom.where(denom > 0)).dropna()
        if len(vwap) >= MIN_VWAP_POINTS:
            result[cid] = vwap
    return result


# ── Find all signal crossings in a price series ───────────────────────────────

def _crossings_one_way(s: pd.Series) -> list[tuple]:
    out = []
    in_episode = False
    for i in range(len(s)):
        v = float(s.iloc[i])
        if v <= SIGNAL_LOWER:
            in_episode = True
        elif in_episode and v >= SIGNAL_UPPER:
            out.append((s.index[i], v))
            in_episode = False   # wait for another dip below SIGNAL_LOWER
    return out


def find_crossings(series: pd.Series) -> list[tuple]:
    """
    Find upward YES crossings (<= SIGNAL_LOWER -> >= SIGNAL_UPPER, buy YES) and
    downward crossings (>= 1-SIGNAL_LOWER -> <= 1-SIGNAL_UPPER, buy NO) using
    the price path only — no knowledge of the eventual outcome.

    Returns list of (crossing_datetime, entry_price_in_trade_direction, direction)
    sorted by time, direction in {"YES", "NO"}.
    """
    yes = [(dt, v, "YES") for dt, v in _crossings_one_way(series)]
    no = [(dt, v, "NO") for dt, v in _crossings_one_way(1.0 - series)]
    return sorted(yes + no, key=lambda x: x[0])


# ── Simulate a single trade's exit on the actual price path ──────────────────

def simulate_exit(
    series: pd.Series,
    direction: str,
    entry_dt: pd.Timestamp,
    stop_hours: float,
    resolution: Optional[str] = None,
) -> tuple[pd.Timestamp, float, str]:
    """
    Given entry at entry_dt in ``direction`` (YES/NO), walk forward and exit:
      - at EXIT_TARGET (97% in the trade direction), or
      - at the stop_hours time limit, or
      - if data ends first: at settlement (1/0 in trade direction) when the
        resolution is known, else at the last observed price.

    Returns (exit_datetime, exit_price_in_trade_direction, reason).
    """
    s = series if direction == "YES" else 1.0 - series

    after = s[s.index > entry_dt]
    stop_dt = entry_dt + pd.Timedelta(hours=stop_hours)

    for dt, price in after.items():
        if price >= EXIT_TARGET:
            return dt, float(price), "target"
        if dt >= stop_dt:
            return dt, float(price), f"stop_{stop_hours:.0f}h"

    last_dt = after.index[-1] if not after.empty else entry_dt
    if resolution in ("YES", "NO"):
        return last_dt, 1.0 if resolution == direction else 0.0, "resolution"
    last_px = float(after.iloc[-1]) if not after.empty else float(s.iloc[-1])
    return last_dt, last_px, "end_of_data"


def build_signals_for_market(
    condition_id: str,
    category: str,
    series: pd.Series,
    resolution: Optional[str],
    stop_hours: float,
) -> list[dict]:
    """Signals for one market. ``resolution`` is used ONLY for settlement at data end."""
    out = []
    for entry_dt, entry_price, direction in find_crossings(series):
        exit_dt, exit_price, reason = simulate_exit(series, direction, entry_dt, stop_hours, resolution)
        out.append({
            "condition_id": condition_id,
            "category": category,
            "resolution": resolution,
            "direction": direction,
            "entry_dt": entry_dt,
            "entry_price": entry_price,
            "exit_dt": exit_dt,
            "exit_price": exit_price,
            "exit_reason": reason,
        })
    return out


# ── Portfolio simulation ───────────────────────────────────────────────────────

def simulate_portfolio(
    all_signals: list[dict],
    initial_capital: float,
    kelly_cap: float,
    max_positions: int,
    stop_hours: float,
) -> tuple[list[Trade], list[dict]]:
    """
    Walk through signals in time order, opening and closing positions.
    Returns (trades, equity_curve_rows).
    """
    # Sort all signals by entry datetime
    all_signals.sort(key=lambda x: x["entry_dt"])

    capital       = initial_capital
    open_positions: list[dict] = []   # list of {exit_dt, exit_price, size_usd, ...}
    trades:         list[Trade] = []
    equity_rows:    list[dict]  = []
    skipped        = 0

    for sig in all_signals:
        entry_dt = sig["entry_dt"]

        # Close any positions that have already exited
        still_open = []
        for pos in open_positions:
            if pos["exit_dt"] <= entry_dt:
                # Realise P&L
                pnl_usd = pos["net_pnl_usd"]
                capital += pos["size_usd"] + pnl_usd
                trades.append(pos["trade"])
                equity_rows.append({
                    "dt": pos["exit_dt"].isoformat(),
                    "equity": capital,
                    "event": "close",
                    "condition_id": pos["trade"].condition_id,
                })
            else:
                still_open.append(pos)
        open_positions = still_open

        # Skip if at max concurrent positions
        if len(open_positions) >= max_positions:
            skipped += 1
            continue

        # Size: kelly × available capital, capped
        residual     = 1.0 - sig["entry_price"]
        kelly_frac   = min(residual / max(1.0 - residual, 0.01), kelly_cap)
        position_usd = kelly_frac * capital

        if position_usd > capital:
            position_usd = capital
        if position_usd < 10:
            continue

        # Actual fill prices (mid ± spread)
        fill_entry = sig["entry_price"] + ENTRY_SPREAD
        fill_entry = min(fill_entry, 0.999)

        fill_exit  = sig["exit_price"] - EXIT_SPREAD
        fill_exit  = max(fill_exit, 0.001)

        # P&L per contract
        gross_pnl   = fill_exit - fill_entry
        fee         = FEE_RATE * (fill_entry + fill_exit)   # both legs
        net_pnl     = gross_pnl - fee

        # Contracts purchased = position_usd / fill_entry
        contracts   = position_usd / fill_entry
        net_pnl_usd = net_pnl * contracts
        holding_h   = (sig["exit_dt"] - entry_dt).total_seconds() / 3600

        trade = Trade(
            condition_id     = sig["condition_id"],
            category         = sig["category"],
            resolution       = sig["resolution"],
            entry_dt         = entry_dt.isoformat(),
            direction        = sig.get("direction", ""),
            entry_price      = round(fill_entry, 4),
            exit_dt          = sig["exit_dt"].isoformat(),
            exit_price       = round(fill_exit, 4),
            exit_reason      = sig["exit_reason"],
            gross_pnl        = round(gross_pnl, 5),
            net_pnl          = round(net_pnl, 5),
            position_usd     = round(position_usd, 2),
            net_pnl_usd      = round(net_pnl_usd, 2),
            holding_hours    = round(holding_h, 2),
            entry_capital    = round(capital, 2),
        )

        # Deduct position from capital immediately (it's deployed)
        capital -= position_usd
        open_positions.append({
            "exit_dt":    sig["exit_dt"],
            "exit_price": fill_exit,
            "size_usd":   position_usd,
            "net_pnl_usd": net_pnl_usd,
            "trade":      trade,
        })

        equity_rows.append({
            "dt":           entry_dt.isoformat(),
            "equity":       capital + sum(p["size_usd"] for p in open_positions),
            "event":        "open",
            "condition_id": sig["condition_id"],
        })

    # Close any remaining positions at their scheduled exit
    for pos in sorted(open_positions, key=lambda x: x["exit_dt"]):
        pnl_usd = pos["net_pnl_usd"]
        capital += pos["size_usd"] + pnl_usd
        trades.append(pos["trade"])
        equity_rows.append({
            "dt": pos["exit_dt"].isoformat(),
            "equity": capital,
            "event": "close",
            "condition_id": pos["trade"].condition_id,
        })

    return trades, equity_rows, skipped


# ── Statistics ────────────────────────────────────────────────────────────────

def compute_stats(
    trades: list[Trade],
    equity_rows: list[dict],
    initial_capital: float,
    skipped: int,
) -> PortfolioStats:
    if not trades:
        return None

    pnls  = [t.net_pnl_usd for t in trades]
    wins  = [p for p in pnls if p > 0]
    rois  = [t.net_pnl / t.entry_price for t in trades]

    eq_df = pd.DataFrame(equity_rows).drop_duplicates("dt").set_index("dt").sort_index()
    equities = eq_df["equity"].values.astype(float)

    # Sharpe (daily returns approximation)
    if len(equities) > 2:
        daily_rets = np.diff(equities) / equities[:-1]
        sharpe = (daily_rets.mean() / (daily_rets.std() + 1e-9)) * np.sqrt(252)
    else:
        sharpe = 0.0

    # Max drawdown
    rolling_max = np.maximum.accumulate(equities)
    drawdowns   = (equities - rolling_max) / rolling_max
    max_dd      = float(drawdowns.min())

    # CAGR
    final_eq = equities[-1]
    # Infer total period from trade dates
    dates = [pd.Timestamp(t.entry_dt) for t in trades]
    period_days = (max(dates) - min(dates)).days if len(dates) > 1 else 365
    years = max(period_days / 365.25, 0.01)
    cagr  = (final_eq / initial_capital) ** (1 / years) - 1

    return PortfolioStats(
        n_trades             = len(trades),
        n_wins               = len(wins),
        win_rate             = len(wins) / len(trades),
        total_net_pnl_usd    = sum(pnls),
        total_return_pct     = (final_eq / initial_capital - 1) * 100,
        cagr_pct             = cagr * 100,
        sharpe               = sharpe,
        max_drawdown_pct     = max_dd * 100,
        avg_holding_hours    = np.mean([t.holding_hours for t in trades]),
        avg_net_roi_per_trade = np.mean(rois) * 100,
        skipped_at_capacity  = skipped,
        date_range           = f"{min(dates).date()} → {max(dates).date()}",
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Latency arb portfolio backtest")
    p.add_argument("--capital",       type=float, default=10_000)
    p.add_argument("--kelly-cap",     type=float, default=0.25,  help="Max Kelly fraction (default 0.25)")
    p.add_argument("--stop-hours",    type=float, default=4.0,   help="Time stop in hours (default 4)")
    p.add_argument("--max-positions", type=int,   default=5,     help="Max concurrent positions (default 5)")
    p.add_argument("--lag-file",      default="backtests/latency_arb/systematic_lag.json")
    p.add_argument("--out-dir",       default="backtests/latency_arb")
    args = p.parse_args()

    # ── Load fast-jump events ────────────────────────────────────────────────
    log.info("Loading fast-jump event list…")
    with open(args.lag_file) as f:
        lag_data = json.load(f)

    fast = [r for r in lag_data if r["is_fast_jump"]]
    log.info(f"  {len(fast)} fast-jump events across "
             f"{len(set(r['category'] for r in fast))} categories")

    # Index by category
    from collections import defaultdict
    by_cat: dict[str, list[str]] = defaultdict(list)
    res_map: dict[str, str] = {}
    for r in fast:
        by_cat[r["category"]].append(r["condition_id"])
        res_map[r["condition_id"]] = r["resolution"]

    # ── Load VWAP series per category ────────────────────────────────────────
    log.info("Loading price paths…")
    vwap_series: dict[str, pd.Series] = {}
    for cat, cids in by_cat.items():
        log.info(f"  {cat}: {len(cids)} markets")
        vwap_series.update(load_vwap_series(cat, set(cids)))
    log.info(f"Loaded price paths for {len(vwap_series)} / {len(fast)} markets")

    # ── Find all signal crossings ─────────────────────────────────────────────
    log.info("Finding signal crossings (both directions, price path only — no outcome oracle)…")
    log.warning("Universe = hindsight-selected fast-jump markets; treat results as an upper bound.")
    cat_map = {r["condition_id"]: r["category"] for r in fast}
    all_signals = []
    for cid, series in vwap_series.items():
        all_signals.extend(build_signals_for_market(
            cid, cat_map.get(cid, "Unknown"), series, res_map.get(cid), args.stop_hours
        ))

    log.info(f"Total crossings found: {len(all_signals)} "
             f"(avg {len(all_signals)/max(len(vwap_series),1):.1f} per market)")

    # ── Portfolio simulation ──────────────────────────────────────────────────
    log.info(f"Running portfolio simulation  capital=${args.capital:,.0f}  "
             f"kelly={args.kelly_cap:.0%}  stop={args.stop_hours:.0f}h  "
             f"max_pos={args.max_positions}")

    trades, equity_rows, skipped = simulate_portfolio(
        all_signals,
        initial_capital = args.capital,
        kelly_cap       = args.kelly_cap,
        max_positions   = args.max_positions,
        stop_hours      = args.stop_hours,
    )

    # ── Statistics ────────────────────────────────────────────────────────────
    stats = compute_stats(trades, equity_rows, args.capital, skipped)

    print(f"\n{'='*72}")
    print("  LATENCY ARB BACKTEST RESULTS")
    print(f"{'='*72}")
    print(f"  Period:               {stats.date_range}")
    print(f"  Capital:              ${args.capital:,.0f}")
    print(f"  Stop:                 {args.stop_hours:.0f} h  |  Max positions: {args.max_positions}")
    print(f"  Total trades:         {stats.n_trades}")
    print(f"  Signals skipped:      {stats.skipped_at_capacity} (at max capacity)")
    print(f"")
    print(f"  Win rate:             {stats.win_rate:.1%}")
    print(f"  Avg net ROI/trade:    {stats.avg_net_roi_per_trade:.2f}%")
    print(f"  Avg holding time:     {stats.avg_holding_hours:.1f} h")
    print(f"")
    print(f"  Total P&L:            ${stats.total_net_pnl_usd:,.2f}")
    print(f"  Total return:         {stats.total_return_pct:.1f}%")
    print(f"  CAGR:                 {stats.cagr_pct:.1f}%")
    print(f"  Sharpe ratio:         {stats.sharpe:.2f}")
    print(f"  Max drawdown:         {stats.max_drawdown_pct:.1f}%")
    print(f"{'='*72}")

    # Per-category breakdown
    from collections import defaultdict as dd
    cat_trades = dd(list)
    for t in trades:
        cat_trades[t.category].append(t)

    print("\n  BY CATEGORY:")
    print(f"  {'Category':<25} {'N':>5}  {'WR':>6}  {'Avg ROI':>8}  {'Total $':>9}  {'Reason: target%':>15}")
    for cat in sorted(cat_trades, key=lambda c: -len(cat_trades[c])):
        ts = cat_trades[cat]
        n  = len(ts)
        wr = sum(1 for t in ts if t.net_pnl_usd > 0) / n
        roi = np.mean([t.net_pnl / t.entry_price for t in ts]) * 100
        total = sum(t.net_pnl_usd for t in ts)
        pct_target = sum(1 for t in ts if t.exit_reason == "target") / n
        print(f"  {cat:<25} {n:>5}  {wr:>5.1%}  {roi:>7.2f}%  ${total:>8,.0f}  {pct_target:>14.1%}")

    # Exit reason breakdown
    from collections import Counter
    reasons = Counter(t.exit_reason for t in trades)
    print(f"\n  EXIT REASONS:")
    for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
        pct = cnt / len(trades)
        avg_roi = np.mean([t.net_pnl / t.entry_price for t in trades
                           if t.exit_reason == reason]) * 100
        print(f"    {reason:<20} {cnt:>4} ({pct:.1%})   avg ROI: {avg_roi:.2f}%")

    # Worst trades
    worst = sorted(trades, key=lambda t: t.net_pnl_usd)[:5]
    print(f"\n  5 WORST TRADES:")
    for t in worst:
        print(f"    {t.condition_id[:20]}  {t.category:<20}  "
              f"entry={t.entry_price:.3f}  exit={t.exit_price:.3f}  "
              f"P&L=${t.net_pnl_usd:+,.0f}  ({t.exit_reason})")

    # ── Save outputs ─────────────────────────────────────────────────────────
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    trades_df = pd.DataFrame([asdict(t) for t in trades])
    trades_df.to_csv(out / "backtest_trades.csv", index=False)

    eq_df = pd.DataFrame(equity_rows)
    eq_df.to_csv(out / "backtest_equity.csv", index=False)

    with open(out / "backtest_stats.json", "w") as f:
        json.dump(asdict(stats), f, indent=2)

    log.info(f"\nSaved to {out}/backtest_trades.csv, backtest_equity.csv, backtest_stats.json")


if __name__ == "__main__":
    main()

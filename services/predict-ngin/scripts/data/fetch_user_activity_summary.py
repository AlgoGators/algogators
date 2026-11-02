#!/usr/bin/env python3
"""
Fetch Polymarket user activity and compute summary statistics.

Uses Data API: leaderboard (to get active users) and /activity per user.
Computes: average holding duration, markets per user, average trade size,
and other profile statistics. Saves raw activity (optional) and a summary report.

Usage:
    # Fetch leaderboard users (up to API limits), get activity for each, write summary
    python scripts/data/fetch_user_activity_summary.py --max-users 1000 --output-dir data/research/users

    # Use a list of proxy wallets from a file (one address per line)
    python scripts/data/fetch_user_activity_summary.py --users-file wallets.txt --output-dir data/research/users

    # Derive users from research trade data (data/research/*/trades.parquet)
    python scripts/data/fetch_user_activity_summary.py --from-research-trades data/research --max-users 500
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

# Project root
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

DATA_API = "https://data-api.polymarket.com"
REQUEST_DELAY = 0.15  # be nice to API
ACTIVITY_PAGE_SIZE = 500
LEADERBOARD_PAGE_SIZE = 50
LEADERBOARD_OFFSET_MAX = 1000  # API limit
ACTIVITY_OFFSET_MAX = 10000    # API limit for activity


def fetch_leaderboard_users(
    category: str = "OVERALL",
    time_period: str = "ALL",
    order_by: str = "VOL",
    limit: int = LEADERBOARD_PAGE_SIZE,
    max_users: int = 2000,
) -> List[str]:
    """Paginate leaderboard and return list of proxy wallet addresses."""
    session = requests.Session()
    users: List[str] = []
    offset = 0

    while len(users) < max_users and offset <= LEADERBOARD_OFFSET_MAX:
        try:
            r = session.get(
                f"{DATA_API}/v1/leaderboard",
                params={
                    "category": category,
                    "timePeriod": time_period,
                    "orderBy": order_by,
                    "limit": limit,
                    "offset": offset,
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for row in data:
                w = (row.get("proxyWallet") or "").strip()
                if w and w.startswith("0x") and w not in users:
                    users.append(w)
            if len(data) < limit:
                break
            offset += len(data)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"  Leaderboard error at offset {offset}: {e}")
            break

    return users[:max_users]


def fetch_user_activity(
    proxy_wallet: str,
    session: requests.Session,
    limit: int = ACTIVITY_PAGE_SIZE,
    max_records: int = 10_000,
    activity_type: str = "TRADE",
) -> List[Dict[str, Any]]:
    """Fetch activity for one user (TRADE only by default). Paginates up to max_records."""
    out: List[Dict[str, Any]] = []
    offset = 0

    while offset < max_records and offset <= ACTIVITY_OFFSET_MAX:
        try:
            r = session.get(
                f"{DATA_API}/activity",
                params={
                    "user": proxy_wallet,
                    "limit": limit,
                    "offset": offset,
                    "type": activity_type,
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            out.extend(data)
            if len(data) < limit:
                break
            offset += len(data)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            break

    return out[:max_records]


def compute_holding_durations(activities: List[Dict[str, Any]]) -> List[float]:
    """
    From TRADE activities (BUY/SELL), approximate holding duration per position.
    Simplified: pair first BUY with first SELL per (conditionId, outcomeIndex) and take duration.
    Returns list of durations in seconds.
    """
    # Group by market (conditionId) and outcome (outcomeIndex); order by timestamp
    by_market = {}
    for a in activities:
        if a.get("type") != "TRADE":
            continue
        cid = a.get("conditionId") or ""
        oidx = a.get("outcomeIndex", 0)
        ts = a.get("timestamp")
        side = (a.get("side") or "").upper()
        if not cid or ts is None:
            continue
        key = (cid, oidx)
        if key not in by_market:
            by_market[key] = []
        by_market[key].append({"ts": int(ts), "side": side})

    durations = []
    for key, events in by_market.items():
        events.sort(key=lambda x: x["ts"])
        buys = [e["ts"] for e in events if e["side"] == "BUY"]
        sells = [e["ts"] for e in events if e["side"] == "SELL"]
        if not buys or not sells:
            continue
        # Simple pairing: first buy to first sell, etc.
        for i, buy_ts in enumerate(buys):
            if i < len(sells) and sells[i] > buy_ts:
                durations.append(sells[i] - buy_ts)
    return durations


def compute_summary_stats(activities: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute summary statistics for one user's activity."""
    trades = [a for a in activities if a.get("type") == "TRADE"]
    if not trades:
        return {
            "num_trades": 0,
            "num_markets": 0,
            "avg_trade_size_usd": None,
            "total_volume_usd": None,
            "holding_duration_avg_seconds": None,
            "holding_duration_median_seconds": None,
        }

    usdc_sizes = []
    for t in trades:
        u = t.get("usdcSize")
        if u is not None:
            try:
                usdc_sizes.append(float(u))
            except (TypeError, ValueError):
                pass
        elif t.get("size") is not None and t.get("price") is not None:
            try:
                usdc_sizes.append(float(t["size"]) * float(t["price"]))
            except (TypeError, ValueError):
                pass

    markets = set()
    for t in trades:
        cid = t.get("conditionId")
        if cid:
            markets.add(cid)

    durations = compute_holding_durations(activities)

    return {
        "num_trades": len(trades),
        "num_markets": len(markets),
        "avg_trade_size_usd": float(sum(usdc_sizes) / len(usdc_sizes)) if usdc_sizes else None,
        "total_volume_usd": sum(usdc_sizes) if usdc_sizes else None,
        "holding_duration_avg_seconds": float(sum(durations) / len(durations)) if durations else None,
        "holding_duration_median_seconds": float(sorted(durations)[len(durations) // 2]) if durations else None,
    }


def users_from_research_trades(research_dir: Path, max_users: int = 10_000) -> List[str]:
    """Collect unique wallet addresses from trades in data/research/*/trades.parquet."""
    wallets: set = set()
    wallet_cols = ("maker", "taker", "proxy_wallet", "proxyWallet", "wallet")
    for cat_dir in research_dir.iterdir():
        if not cat_dir.is_dir():
            continue
        trades_path = cat_dir / "trades.parquet"
        if not trades_path.exists():
            continue
        try:
            df = pd.read_parquet(trades_path)
        except Exception:
            continue
        for col in wallet_cols:
            if col in df.columns:
                for w in df[col].dropna().astype(str).unique():
                    w = w.strip()
                    if w.startswith("0x") and len(w) == 42:
                        wallets.add(w)
        if len(wallets) >= max_users:
            break
    return list(wallets)[:max_users]


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch user activity and compute summary statistics")
    parser.add_argument("--output-dir", type=Path, default=_project_root / "data" / "research" / "users", help="Output directory")
    parser.add_argument("--max-users", type=int, default=500, help="Max users to fetch activity for")
    parser.add_argument("--users-file", type=Path, default=None, help="File with one proxy wallet per line")
    parser.add_argument("--from-research-trades", type=Path, default=None, help="Derive users from data/research/*/trades.parquet")
    parser.add_argument("--leaderboard-category", default="OVERALL", help="Leaderboard category")
    parser.add_argument("--leaderboard-period", default="ALL", help="Leaderboard time period")
    parser.add_argument("--activity-limit-per-user", type=int, default=5000, help="Max activity records per user")
    parser.add_argument("--save-activity", action="store_true", help="Save raw activity JSON per user")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Get user list
    users: List[str] = []
    if args.users_file and args.users_file.exists():
        with open(args.users_file) as f:
            for line in f:
                w = line.strip()
                if w.startswith("0x") and len(w) == 42:
                    users.append(w)
        print(f"Loaded {len(users)} users from {args.users_file}")
    elif args.from_research_trades and args.from_research_trades.exists():
        users = users_from_research_trades(args.from_research_trades, max_users=args.max_users)
        print(f"Derived {len(users)} users from research trades under {args.from_research_trades}")
    else:
        users = fetch_leaderboard_users(
            category=args.leaderboard_category,
            time_period=args.leaderboard_period,
            order_by="VOL",
            max_users=args.max_users,
        )
        print(f"Fetched {len(users)} users from leaderboard")

    if not users:
        print("No users to process.")
        return 1

    users = users[: args.max_users]
    session = requests.Session()
    all_stats: List[Dict[str, Any]] = []

    for i, wallet in enumerate(users):
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  User {i + 1}/{len(users)}: {wallet[:16]}...")
        activity = fetch_user_activity(
            wallet, session, max_records=args.activity_limit_per_user
        )
        stats = compute_summary_stats(activity)
        stats["proxyWallet"] = wallet
        all_stats.append(stats)
        if args.save_activity and activity:
            out_path = args.output_dir / f"activity_{wallet[:10]}.json"
            with open(out_path, "w") as f:
                json.dump(activity[:2000], f)  # cap single file size
        time.sleep(REQUEST_DELAY)

    # Aggregate summary report
    df = pd.DataFrame(all_stats)
    report = {
        "num_users": len(df),
        "avg_holding_duration_seconds": df["holding_duration_avg_seconds"].dropna().mean(),
        "median_holding_duration_seconds": df["holding_duration_median_seconds"].dropna().median(),
        "avg_markets_per_user": df["num_markets"].mean(),
        "median_markets_per_user": df["num_markets"].median(),
        "avg_trade_size_usd": df["avg_trade_size_usd"].dropna().mean(),
        "median_trade_size_usd": df["avg_trade_size_usd"].dropna().median(),
        "total_volume_all_users": df["total_volume_usd"].dropna().sum(),
        "users_with_trades": int((df["num_trades"] > 0).sum()),
    }

    report_path = args.output_dir / "user_activity_summary.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nSummary written to {report_path}")
    for k, v in report.items():
        print(f"  {k}: {v}")

    df.to_csv(args.output_dir / "user_stats.csv", index=False)
    print(f"Per-user stats: {args.output_dir / 'user_stats.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

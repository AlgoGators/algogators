#!/usr/bin/env python3
"""
Find open markets with whale activity using the shared whale detection strategy.

Uses the same WhaleConfig as the backtest (config/default.yaml whale_strategy).
Change volume_percentile, unfavored_only in config to affect both backtest and this scanner.
Default: 95th percentile volume.

Usage:
    python scripts/analysis/find_whale_opportunities.py
    python scripts/analysis/find_whale_opportunities.py --top 20 --min-volume 5000
    python scripts/analysis/find_whale_opportunities.py --verify   # Look up whale historical win rates
    python scripts/analysis/find_whale_opportunities.py --json
"""

import argparse
import json
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "src"))

import pandas as pd
import requests

from src.whale_strategy.research_data_loader import (
    load_research_trades,
    load_resolution_winners,
    get_research_categories,
)
from src.whale_strategy.whale_config import load_whale_config, WhaleConfig
from src.whale_strategy.whale_surprise import (
    calculate_performance_score_with_surprise,
    identify_whales_rolling,
    _filter_unfavored_trades,
)

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
REQUEST_DELAY = 0.15


def fetch_open_markets(
    min_volume: float = 1000,
    limit: int = 200,
    closed: bool = False,
    active: bool = True,
) -> list:
    """Fetch open markets from Gamma API."""
    session = requests.Session()
    session.headers["User-Agent"] = "PredictNgin/1.0"
    markets = []
    offset = 0

    while len(markets) < limit:
        try:
            r = session.get(
                f"{GAMMA_API}/markets",
                params={
                    "limit": min(100, limit - len(markets)),
                    "offset": offset,
                    "closed": str(closed).lower(),
                    "active": str(active).lower(),
                },
                timeout=30,
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for m in batch:
                vol = float(m.get("volume", 0) or m.get("volumeNum", 0) or 0)
                if vol >= min_volume:
                    mid = m.get("conditionId") or m.get("id") or ""
                    if mid:
                        markets.append({
                            "id": mid,
                            "question": (m.get("question") or "")[:120],
                            "slug": m.get("slug", ""),
                            "volume": vol,
                            "liquidity": float(m.get("liquidity", 0) or m.get("liquidityNum", 0) or 0),
                        })
            offset += len(batch)
            if len(batch) < 100:
                break
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"Error fetching markets: {e}", file=sys.stderr)
            break

    markets.sort(key=lambda x: x["volume"], reverse=True)
    return markets[:limit]


def fetch_market_trades(market_id: str, limit: int = 2000) -> list:
    """Fetch recent trades for a market from Data API."""
    session = requests.Session()
    session.headers["User-Agent"] = "PredictNgin/1.0"
    try:
        r = session.get(
            f"{DATA_API}/trades",
            params={"market": market_id, "limit": limit},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            for t in data:
                t["market_id"] = market_id
            return data
    except Exception:
        pass
    return []


def normalize_trades_to_research_format(raw_trades: list, min_usd: float = 100) -> pd.DataFrame:
    """
    Convert Polymarket Data API trades to research format (maker, maker_direction, price, usd_amount, market_id).
    """
    rows = []
    for t in raw_trades:
        wallet = t.get("proxyWallet") or t.get("maker") or ""
        if not wallet or not str(wallet).startswith("0x"):
            continue
        price = float(t.get("price", 0) or 0)
        size = float(t.get("size", 0) or 0)
        usd = price * size
        if usd < min_usd:
            continue
        side = (t.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            continue
        # pmfix: express the print in YES-token terms (BUY NO at p == SELL YES at 1-p). Unknown token -> skip.
        try:
            oi = int(float(t.get("outcomeIndex")))
        except (TypeError, ValueError):
            continue
        if oi == 1:
            price = 1.0 - price
            side = "SELL" if side == "BUY" else "BUY"
        elif oi != 0:
            continue
        ts = t.get("timestamp")
        if ts is not None:
            try:
                ts = int(float(ts))
                if ts > 10_000_000_000:
                    ts = ts // 1000
                dt = pd.to_datetime(ts, unit="s")
            except (TypeError, ValueError):
                dt = pd.NaT
        else:
            dt = pd.NaT
        rows.append({
            "market_id": str(t.get("market_id") or t.get("conditionId", "")).strip(),
            "maker": wallet,
            "maker_direction": side,
            "price": price,
            "usd_amount": usd,
            "datetime": dt,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.dropna(subset=["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def find_whale_opportunities(
    whale_config: WhaleConfig = None,
    min_market_volume: float = 1000,
    max_markets: int = 100,
    trades_per_market: int = 2000,
    top: int = 20,
    research_dir: Path = None,
    filter_by_performance: bool = True,
) -> list:
    """
    Find open markets with recent whale activity.

    By default filters to whales with WR >= 50% and positive surprise (from research data).
    Set filter_by_performance=False to show all volume whales.

    Returns list of dicts: market_id, question, slug, whale_count, whale_trades, latest_whale_trade, etc.
    """
    cfg = whale_config or load_whale_config()
    _project_root = Path(__file__).resolve().parent.parent.parent
    research_dir = research_dir or _project_root / "data" / "research"

    # Surprise-only needs resolutions; open markets don't have them
    if cfg.surprise_only:
        cfg = WhaleConfig(
            mode="volume_only",
            volume_percentile=cfg.volume_percentile,
            unfavored_only=cfg.unfavored_only,
            unfavored_max_price=cfg.unfavored_max_price,
            min_usd=cfg.min_usd,
        )

    markets = fetch_open_markets(min_volume=min_market_volume, limit=max_markets)
    if not markets:
        return []

    all_trades = []
    market_meta = {m["id"]: m for m in markets}

    print(f"Fetching trades for {len(markets)} open markets...")
    for i, m in enumerate(markets):
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(markets)} markets...")
        raw = fetch_market_trades(m["id"], limit=trades_per_market)
        df = normalize_trades_to_research_format(raw, min_usd=cfg.min_usd)
        if df.empty:
            continue
        df["question"] = m.get("question", "")
        df["slug"] = m.get("slug", "")
        all_trades.append(df)
        time.sleep(REQUEST_DELAY)

    if not all_trades:
        return []

    trades_df = pd.concat(all_trades, ignore_index=True)
    if trades_df.empty:
        return []

    if cfg.unfavored_only:
        trades_df = _filter_unfavored_trades(
            trades_df,
            direction_col="maker_direction",
            max_price=cfg.unfavored_max_price,
        )
        if trades_df.empty:
            return []

    # Identify whales (volume_only for live - no resolutions)
    trades_with_whale = identify_whales_rolling(
        trades_df,
        trader_col="maker",
        volume_only=True,
        volume_percentile=cfg.volume_percentile,
    )
    whale_trades = trades_with_whale[trades_with_whale["is_whale"]]

    if whale_trades.empty:
        return []

    # Filter to qualified whales (positive surprise: actual WR > expected WR) when research data available
    if filter_by_performance and cfg.require_positive_surprise:
        all_whales = list(whale_trades["maker"].unique())
        whale_stats = verify_whale_win_rates(all_whales, research_dir, min_trades=cfg.min_trades_for_surprise)
        qualified = {
            addr for addr, s in whale_stats.items()
            if s.get("sample_size", 0) >= cfg.min_trades_for_surprise
            and s.get("surprise_win_rate") is not None
            and s["surprise_win_rate"] > cfg.min_surprise
        }
        if qualified:
            whale_trades = whale_trades[whale_trades["maker"].isin(qualified)]
        # If no qualified whales (e.g. no research data), keep all

    if whale_trades.empty:
        return []

    # Aggregate by market
    agg = whale_trades.groupby("market_id").agg(
        whale_count=("maker", "nunique"),
        whale_trades=("maker", "count"),
        total_usd=("usd_amount", "sum"),
        latest_trade=("datetime", "max"),
        whale_addresses=("maker", lambda x: list(x.unique())),
    ).reset_index()
    agg["question"] = agg["market_id"].map(lambda mid: market_meta.get(mid, {}).get("question", ""))
    agg["slug"] = agg["market_id"].map(lambda mid: market_meta.get(mid, {}).get("slug", ""))
    agg["volume"] = agg["market_id"].map(lambda mid: market_meta.get(mid, {}).get("volume", 0))
    agg["liquidity"] = agg["market_id"].map(lambda mid: market_meta.get(mid, {}).get("liquidity", 0))
    agg = agg.sort_values(["whale_trades", "total_usd"], ascending=[False, False])

    results = []
    for _, row in agg.head(top).iterrows():
        results.append({
            "market_id": row["market_id"],
            "question": row["question"],
            "slug": row["slug"],
            "whale_count": int(row["whale_count"]),
            "whale_trades": int(row["whale_trades"]),
            "total_usd": float(row["total_usd"]),
            "whale_addresses": list(row["whale_addresses"]),
            "latest_trade": str(row["latest_trade"]) if pd.notna(row["latest_trade"]) else None,
            "volume": float(row["volume"]),
            "liquidity": float(row["liquidity"]),
            "url": f"https://polymarket.com/event/{row['slug']}" if row["slug"] else "",
        })

    return results


def verify_whale_win_rates(
    whale_addresses: list,
    research_dir: Path,
    min_trades: int = 5,
) -> dict:
    """
    Look up each whale's historical win rate from research data (resolved trades).

    Returns dict: whale_address -> {actual_win_rate, expected_win_rate, surprise_win_rate, sample_size}
    Whales with no resolved history get sample_size=0.
    """
    research_dir = Path(research_dir)
    resolution_winners = load_resolution_winners(research_dir)
    if not resolution_winners:
        return {addr: {"actual_win_rate": None, "expected_win_rate": None, "surprise_win_rate": None, "sample_size": 0} for addr in whale_addresses}

    categories = get_research_categories(research_dir)
    if not categories:
        return {addr: {"actual_win_rate": None, "expected_win_rate": None, "surprise_win_rate": None, "sample_size": 0} for addr in whale_addresses}

    trades_df = load_research_trades(research_dir, categories=categories, min_usd=10)
    if trades_df.empty:
        return {addr: {"actual_win_rate": None, "expected_win_rate": None, "surprise_win_rate": None, "sample_size": 0} for addr in whale_addresses}

    out = {}
    for addr in whale_addresses:
        result = calculate_performance_score_with_surprise(
            addr,
            trades_df,
            resolution_winners,
            direction_col="maker_direction",
            min_trades=min_trades,
        )
        if result:
            out[addr] = {
                "actual_win_rate": result["actual_win_rate"],
                "expected_win_rate": result["expected_win_rate"],
                "surprise_win_rate": result["surprise_win_rate"],
                "sample_size": result["sample_size"],
            }
        else:
            out[addr] = {"actual_win_rate": None, "expected_win_rate": None, "surprise_win_rate": None, "sample_size": 0}
    return out


def enrich_opportunities_with_whale_history(
    opportunities: list,
    research_dir: Path,
    min_trades: int = 5,
) -> list:
    """Add whale_win_rates to each opportunity from research data."""
    all_whales = set()
    for o in opportunities:
        all_whales.update(o.get("whale_addresses", []))
    if not all_whales:
        return opportunities

    whale_stats = verify_whale_win_rates(list(all_whales), research_dir, min_trades=min_trades)

    for o in opportunities:
        o["whale_win_rates"] = {}
        for addr in o.get("whale_addresses", []):
            s = whale_stats.get(addr, {})
            if s.get("sample_size", 0) >= min_trades:
                o["whale_win_rates"][addr] = s
            else:
                o["whale_win_rates"][addr] = {**s, "sample_size": s.get("sample_size", 0)}

    return opportunities


def format_table(opportunities: list, show_whale_history: bool = False) -> str:
    """Format opportunities as ASCII table. If show_whale_history, append whale win rates."""
    if not opportunities:
        return "No whale opportunities found."

    lines = [
        "Rank | Question (truncated)                    | Whales | Trades | Total $   | Latest",
        "-" * 100,
    ]
    for i, o in enumerate(opportunities, 1):
        q = (o["question"] or "")[:42].ljust(42)
        w = str(o["whale_count"]).rjust(6)
        t = str(o["whale_trades"]).rjust(6)
        usd = f"${o['total_usd']:,.0f}".rjust(9)
        lt = (o.get("latest_trade") or "")[:19]
        lines.append(f"{i:4} | {q} | {w} | {t} | {usd} | {lt}")
        if show_whale_history and o.get("whale_win_rates"):
            for addr, stats in list(o["whale_win_rates"].items())[:3]:
                n = stats.get("sample_size", 0)
                awr = stats.get("actual_win_rate")
                swr = stats.get("surprise_win_rate")
                if n >= 5 and awr is not None:
                    swr_str = f" Surprise:{swr*100:+.0f}%" if swr is not None else ""
                    lines.append(f"       | {addr[:10]}... WR:{awr*100:.0f}% (n={n}){swr_str}")
                else:
                    lines.append(f"       | {addr[:10]}... (no resolved history)")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find open markets with whale activity (uses shared WhaleConfig)"
    )
    parser.add_argument("--top", "-n", type=int, default=15, help="Number of opportunities to show")
    parser.add_argument("--min-volume", type=float, default=1000, help="Min market volume to consider")
    parser.add_argument("--max-markets", type=int, default=100, help="Max open markets to fetch")
    parser.add_argument("--trades-per-market", type=int, default=2000, help="Trades to fetch per market")
    parser.add_argument("--no-filter", action="store_true", help="Don't filter to WR>=50%% and positive surprise (show all volume whales)")
    parser.add_argument("--verify", action="store_true", help="Show whale historical win rates in output (always computed when filtering)")
    parser.add_argument("--research-dir", type=Path, default=_project_root / "data" / "research", help="Research data for filtering and --verify")
    parser.add_argument("--min-verified-trades", type=int, default=5, help="Min resolved trades to show whale WR (--verify)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    cfg = load_whale_config()
    if cfg.surprise_only:
        cfg.mode = "volume_only"  # Live has no resolutions
        print("Note: Using volume_only for live scan (surprise_only needs resolutions).")

    print(f"Whale config: {cfg}")
    print()

    opportunities = find_whale_opportunities(
        whale_config=cfg,
        min_market_volume=args.min_volume,
        max_markets=args.max_markets,
        trades_per_market=args.trades_per_market,
        top=args.top,
        research_dir=args.research_dir,
        filter_by_performance=not args.no_filter,
    )

    if (args.verify or not args.no_filter) and opportunities:
        print("Looking up whale historical win rates from research data...")
        opportunities = enrich_opportunities_with_whale_history(
            opportunities,
            args.research_dir,
            min_trades=args.min_verified_trades,
        )

    if args.json:
        # JSON: serialize numpy floats for whale_win_rates
        def _serialize(obj):
            if isinstance(obj, dict):
                return {k: _serialize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_serialize(x) for x in obj]
            if hasattr(obj, "item") and callable(obj.item):
                return float(obj)
            return obj
        print(json.dumps(_serialize(opportunities), indent=2))
    else:
        print(format_table(opportunities, show_whale_history=args.verify))
        if opportunities:
            print("\nURLs:")
            for o in opportunities[:5]:
                if o.get("url"):
                    print(f"  {o['url']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

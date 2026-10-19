#!/usr/bin/env python3
"""
Fetch recent Polymarket trades from the data API.

These trades use the current market ID format (matching prices/markets tables).

Usage:
    python scripts/fetch_polymarket_trades.py --days 30 --max-markets 500
"""

import argparse
import json
import time
import sqlite3
import requests
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
MAX_WORKERS = 10
REQUEST_DELAY = 0.1


def fetch_active_markets(min_volume: float = 1000, max_markets: int = 500) -> list:
    """Fetch active markets from Gamma API."""
    markets = []
    offset = 0
    session = requests.Session()

    print(f"Fetching active markets with volume >= ${min_volume:,.0f}...")

    while len(markets) < max_markets:
        try:
            response = session.get(
                f"{GAMMA_API}/markets",
                params={"limit": 100, "offset": offset}
            )
            response.raise_for_status()
            batch = response.json()

            if not batch:
                break

            for m in batch:
                vol = float(m.get("volume", 0) or 0)
                if vol >= min_volume:
                    markets.append({
                        "id": m.get("id"),
                        "slug": m.get("slug"),
                        "question": m.get("question", "")[:100],
                        "volume": vol,
                    })

            offset += len(batch)
            if len(batch) < 100:
                break

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            print(f"Error fetching markets: {e}")
            break

    markets.sort(key=lambda x: x["volume"], reverse=True)
    print(f"  Found {len(markets)} markets")
    return markets[:max_markets]


def fetch_market_trades(market_id: str, limit: int = 1000) -> list:
    """Fetch trades for a specific market."""
    session = requests.Session()

    try:
        response = session.get(
            f"{DATA_API}/trades",
            params={"market": market_id, "limit": limit},
            timeout=30,
        )

        if response.status_code == 200:
            return response.json()
        else:
            return []

    except Exception as e:
        return []


def fetch_market_trades_thread(market: dict, progress: dict, lock: threading.Lock) -> list:
    """Thread-safe market trade fetching."""
    trades = fetch_market_trades(market["id"])

    with lock:
        progress["done"] += 1
        progress["trades"] += len(trades)
        if progress["done"] % 50 == 0:
            print(f"  Progress: {progress['done']}/{progress['total']} markets, {progress['trades']:,} trades")

    # Add market info to each trade
    for t in trades:
        t["market_id"] = market["id"]

    return trades


def save_trades_to_db(trades: list, db_path: str) -> int:
    """Save trades to database."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create table if not exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS polymarket_trades_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT,
            timestamp TEXT,
            proxy_wallet TEXT,
            side TEXT,
            outcome TEXT,
            outcome_index INTEGER,
            price REAL,
            size REAL,
            transaction_hash TEXT,
            title TEXT,
            slug TEXT,
            UNIQUE(transaction_hash, market_id, proxy_wallet, timestamp)
        )
    """)

    # Create indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_new_trades_market ON polymarket_trades_new(market_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_new_trades_wallet ON polymarket_trades_new(proxy_wallet)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_new_trades_timestamp ON polymarket_trades_new(timestamp)")

    inserted = 0
    for t in trades:
        try:
            cur.execute("""
                INSERT OR IGNORE INTO polymarket_trades_new
                (market_id, timestamp, proxy_wallet, side, outcome, outcome_index, price, size, transaction_hash, title, slug)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t.get("market_id") or t.get("conditionId"),
                t.get("timestamp"),
                t.get("proxyWallet"),
                t.get("side"),
                t.get("outcome"),
                t.get("outcomeIndex"),
                float(t.get("price", 0) or 0),
                float(t.get("size", 0) or 0),
                t.get("transactionHash"),
                t.get("title", "")[:200] if t.get("title") else None,
                t.get("slug"),
            ))
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            pass

    conn.commit()
    conn.close()

    return inserted


def main():
    parser = argparse.ArgumentParser(description="Fetch Polymarket trades")
    parser.add_argument("--min-volume", type=float, default=10000, help="Minimum market volume")
    parser.add_argument("--max-markets", type=int, default=500, help="Maximum markets to fetch")
    parser.add_argument("--trades-per-market", type=int, default=1000, help="Trades per market")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Concurrent workers")
    parser.add_argument("--db-path", default="data/prediction_markets.db", help="Database path")
    parser.add_argument("--output-json", help="Also save to JSON file")
    args = parser.parse_args()

    print("=" * 60)
    print("POLYMARKET TRADE FETCHER")
    print("=" * 60)

    # Fetch active markets
    markets = fetch_active_markets(args.min_volume, args.max_markets)

    if not markets:
        print("No markets found")
        return

    # Fetch trades concurrently
    print(f"\nFetching trades for {len(markets)} markets with {args.workers} workers...")

    progress = {"done": 0, "total": len(markets), "trades": 0}
    lock = threading.Lock()
    all_trades = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(fetch_market_trades_thread, m, progress, lock): m
            for m in markets
        }

        for future in as_completed(futures):
            trades = future.result()
            all_trades.extend(trades)

    print(f"\n  Total: {len(all_trades):,} trades from {len(markets)} markets")

    # Save to database
    print(f"\nSaving to database: {args.db_path}")
    inserted = save_trades_to_db(all_trades, args.db_path)
    print(f"  Inserted: {inserted:,} new trades")

    # Optionally save to JSON
    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w") as f:
            json.dump(all_trades, f)
        print(f"  Saved to: {args.output_json}")

    # Show stats
    conn = sqlite3.connect(args.db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM polymarket_trades_new")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT market_id) FROM polymarket_trades_new")
    unique_markets = cur.fetchone()[0]
    cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM polymarket_trades_new")
    date_range = cur.fetchone()
    conn.close()

    print(f"\n  Database stats:")
    print(f"    Total trades: {total:,}")
    print(f"    Unique markets: {unique_markets:,}")
    print(f"    Date range: {date_range[0]} to {date_range[1]}")

    print("\n" + "=" * 60)
    print("FETCH COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()

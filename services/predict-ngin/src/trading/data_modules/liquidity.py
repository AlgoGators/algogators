"""
Historical liquidity tracking for Polymarket.

Since Polymarket doesn't provide historical order book data,
this module:
1. Captures periodic order book snapshots (prospective)
2. Estimates historical liquidity from trade impact (retrospective)

Usage:
    # Capture current liquidity snapshot
    python -m src.trading.data_modules.liquidity --snapshot

    # Run continuous capture (every N minutes)
    python -m src.trading.data_modules.liquidity --capture --interval 5

    # Estimate historical liquidity from trades
    python -m src.trading.data_modules.liquidity --estimate
"""

import json
import time
import sqlite3
import requests
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import numpy as np

CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
REQUEST_DELAY = 0.05
MAX_WORKERS = 10


def init_liquidity_schema(db_path: str) -> None:
    """
    Initialize liquidity tracking tables.

    Args:
        db_path: Path to SQLite database
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Liquidity snapshots table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS liquidity_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT,
            token_id TEXT,
            outcome TEXT,
            timestamp INTEGER,
            datetime TEXT,
            bid_depth_1pct REAL,
            bid_depth_5pct REAL,
            bid_depth_10pct REAL,
            ask_depth_1pct REAL,
            ask_depth_5pct REAL,
            ask_depth_10pct REAL,
            spread REAL,
            midpoint REAL,
            best_bid REAL,
            best_ask REAL
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_liq_market_ts
        ON liquidity_snapshots(market_id, timestamp)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_liq_timestamp
        ON liquidity_snapshots(timestamp)
    """)

    # Estimated historical liquidity (from trade impact)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS liquidity_estimates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT,
            date TEXT,
            estimated_liquidity REAL,
            avg_trade_impact REAL,
            trade_count INTEGER,
            total_volume REAL,
            volatility REAL
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_liq_est_market_date
        ON liquidity_estimates(market_id, date)
    """)

    conn.commit()
    conn.close()
    print("Liquidity schema initialized.")


def get_order_book(token_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch order book for a token.

    Args:
        token_id: CLOB token ID

    Returns:
        Order book dict or None
    """
    try:
        response = requests.get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        pass
    return None


def calculate_depth(
    levels: List[Dict[str, str]],
    midpoint: float,
    pct: float,
) -> float:
    """
    Calculate order book depth within pct of midpoint.

    Args:
        levels: List of {"price": str, "size": str}
        midpoint: Current midpoint price
        pct: Percentage from midpoint (e.g., 0.01 = 1%)

    Returns:
        Total size within range
    """
    if not levels or not midpoint:
        return 0.0

    total = 0.0
    for level in levels:
        try:
            price = float(level["price"])
            size = float(level["size"])
            if abs(price - midpoint) / midpoint <= pct:
                total += size
        except (ValueError, KeyError):
            continue
    return total


def parse_order_book(
    book: Dict[str, Any],
    market_id: str,
    outcome: str,
) -> Dict[str, Any]:
    """
    Parse order book into liquidity metrics.

    Args:
        book: Raw order book response
        market_id: Market ID
        outcome: "YES" or "NO"

    Returns:
        Parsed liquidity data
    """
    bids = book.get("bids", [])
    asks = book.get("asks", [])

    # Get best bid/ask
    best_bid = float(bids[0]["price"]) if bids else 0.0
    best_ask = float(asks[0]["price"]) if asks else 1.0

    # Calculate spread and midpoint
    spread = best_ask - best_bid
    midpoint = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.5

    # Calculate depth at various percentages from midpoint
    bid_depth_1pct = calculate_depth(bids, midpoint, 0.01)
    bid_depth_5pct = calculate_depth(bids, midpoint, 0.05)
    bid_depth_10pct = calculate_depth(bids, midpoint, 0.10)

    ask_depth_1pct = calculate_depth(asks, midpoint, 0.01)
    ask_depth_5pct = calculate_depth(asks, midpoint, 0.05)
    ask_depth_10pct = calculate_depth(asks, midpoint, 0.10)

    now = datetime.now()

    return {
        "market_id": market_id,
        "token_id": book.get("asset_id", ""),
        "outcome": outcome,
        "timestamp": int(now.timestamp()),
        "datetime": now.isoformat(),
        "bid_depth_1pct": bid_depth_1pct,
        "bid_depth_5pct": bid_depth_5pct,
        "bid_depth_10pct": bid_depth_10pct,
        "ask_depth_1pct": ask_depth_1pct,
        "ask_depth_5pct": ask_depth_5pct,
        "ask_depth_10pct": ask_depth_10pct,
        "spread": spread,
        "midpoint": midpoint,
        "best_bid": best_bid,
        "best_ask": best_ask,
    }


def get_active_markets(min_volume: float = 1000) -> List[Dict[str, Any]]:
    """
    Get active markets with sufficient volume.

    Args:
        min_volume: Minimum 24hr volume

    Returns:
        List of market dicts with token IDs
    """
    markets = []
    offset = 0

    while True:
        try:
            response = requests.get(
                f"{GAMMA_API}/markets",
                params={
                    "closed": "false",
                    "limit": 100,
                    "offset": offset,
                },
                timeout=30,
            )
            response.raise_for_status()
            batch = response.json()

            if not batch:
                break

            for m in batch:
                vol = float(m.get("volume24hr", 0) or 0)
                tokens = m.get("clobTokenIds", "[]")
                if isinstance(tokens, str):
                    try:
                        tokens = json.loads(tokens)
                    except json.JSONDecodeError:
                        tokens = []

                if vol >= min_volume and tokens:
                    markets.append({
                        "id": m.get("id"),
                        "slug": m.get("slug"),
                        "token_ids": tokens,
                        "volume24hr": vol,
                        "liquidity": float(m.get("liquidity", 0) or 0),
                    })

            offset += len(batch)
            time.sleep(REQUEST_DELAY)

            if len(batch) < 100:
                break

        except requests.RequestException:
            break

    return markets


def capture_liquidity_snapshot(
    db_path: str = "data/prediction_markets.db",
    min_volume: float = 1000,
    max_markets: int = 500,
) -> int:
    """
    Capture current liquidity snapshot for active markets.

    Args:
        db_path: Database path
        min_volume: Minimum 24hr volume
        max_markets: Max markets to capture

    Returns:
        Number of snapshots captured
    """
    print(f"Capturing liquidity snapshot...")

    # Get active markets
    markets = get_active_markets(min_volume)
    markets = sorted(markets, key=lambda x: x["volume24hr"], reverse=True)[:max_markets]
    print(f"  Found {len(markets)} active markets")

    if not markets:
        return 0

    # Initialize schema
    init_liquidity_schema(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    captured = 0
    lock = threading.Lock()

    def fetch_and_store(market: Dict[str, Any]) -> int:
        nonlocal captured
        count = 0
        for i, token_id in enumerate(market["token_ids"][:2]):
            time.sleep(REQUEST_DELAY)
            book = get_order_book(token_id)
            if book:
                outcome = "YES" if i == 0 else "NO"
                data = parse_order_book(book, market["id"], outcome)

                with lock:
                    cursor.execute("""
                        INSERT INTO liquidity_snapshots
                        (market_id, token_id, outcome, timestamp, datetime,
                         bid_depth_1pct, bid_depth_5pct, bid_depth_10pct,
                         ask_depth_1pct, ask_depth_5pct, ask_depth_10pct,
                         spread, midpoint, best_bid, best_ask)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        data["market_id"], data["token_id"], data["outcome"],
                        data["timestamp"], data["datetime"],
                        data["bid_depth_1pct"], data["bid_depth_5pct"], data["bid_depth_10pct"],
                        data["ask_depth_1pct"], data["ask_depth_5pct"], data["ask_depth_10pct"],
                        data["spread"], data["midpoint"], data["best_bid"], data["best_ask"],
                    ))
                    count += 1
        return count

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_and_store, m): m for m in markets}
        for future in as_completed(futures):
            captured += future.result()

    conn.commit()
    conn.close()

    print(f"  Captured {captured} order book snapshots")
    return captured


def estimate_historical_liquidity(
    db_path: str = "data/prediction_markets.db",
) -> int:
    """
    Estimate historical liquidity from trade impact.

    Uses the relationship between trade size and price impact
    to estimate available liquidity at the time of trades.

    Liquidity proxy = trade_volume / price_impact

    Args:
        db_path: Database path

    Returns:
        Number of estimates created
    """
    print("Estimating historical liquidity from trade data...")

    conn = sqlite3.connect(db_path)

    # Check if we have trades
    try:
        count = conn.execute("SELECT COUNT(*) FROM polymarket_trades").fetchone()[0]
        if count == 0:
            print("  No trades found in database")
            conn.close()
            return 0
    except sqlite3.OperationalError:
        print("  polymarket_trades table not found")
        conn.close()
        return 0

    # Initialize schema
    init_liquidity_schema(db_path)

    # Query: group trades by market and date, calculate impact metrics
    query = """
        WITH trade_impacts AS (
            SELECT
                market_id,
                DATE(timestamp) as trade_date,
                price,
                usd_amount,
                LAG(price) OVER (PARTITION BY market_id ORDER BY timestamp) as prev_price
            FROM polymarket_trades
            WHERE usd_amount > 10
        ),
        daily_stats AS (
            SELECT
                market_id,
                trade_date,
                COUNT(*) as trade_count,
                SUM(usd_amount) as total_volume,
                AVG(ABS(price - prev_price)) as avg_impact,
                -- Volatility: std of price changes
                AVG((price - prev_price) * (price - prev_price)) as var_impact
            FROM trade_impacts
            WHERE prev_price IS NOT NULL
            GROUP BY market_id, trade_date
            HAVING COUNT(*) >= 5
        )
        SELECT
            market_id,
            trade_date,
            trade_count,
            total_volume,
            avg_impact,
            SQRT(var_impact) as volatility,
            -- Liquidity estimate: volume / impact (higher = more liquid)
            CASE WHEN avg_impact > 0.001 THEN total_volume / avg_impact ELSE NULL END as est_liquidity
        FROM daily_stats
        ORDER BY market_id, trade_date
    """

    try:
        df = conn.execute(query).fetchall()
    except sqlite3.OperationalError as e:
        print(f"  Error running query: {e}")
        conn.close()
        return 0

    cursor = conn.cursor()
    inserted = 0

    for row in df:
        market_id, trade_date, trade_count, volume, avg_impact, volatility, est_liq = row

        if est_liq is not None:
            cursor.execute("""
                INSERT OR REPLACE INTO liquidity_estimates
                (market_id, date, estimated_liquidity, avg_trade_impact,
                 trade_count, total_volume, volatility)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                market_id, trade_date, est_liq, avg_impact,
                trade_count, volume, volatility,
            ))
            inserted += 1

    conn.commit()
    conn.close()

    print(f"  Created {inserted:,} liquidity estimates")
    return inserted


def get_liquidity_for_backtest(
    db_path: str,
    market_id: str,
    timestamp: int,
) -> Optional[float]:
    """
    Get estimated liquidity for a market at a point in time.

    Used by backtest engine to adjust position sizing.

    Args:
        db_path: Database path
        market_id: Market ID
        timestamp: Unix timestamp

    Returns:
        Estimated liquidity in USD or None
    """
    conn = sqlite3.connect(db_path)

    # First try snapshots (exact data)
    result = conn.execute("""
        SELECT bid_depth_5pct + ask_depth_5pct as total_depth
        FROM liquidity_snapshots
        WHERE market_id = ?
          AND timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (market_id, timestamp)).fetchone()

    if result and result[0]:
        conn.close()
        return result[0]

    # Fall back to estimates
    date_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    result = conn.execute("""
        SELECT estimated_liquidity
        FROM liquidity_estimates
        WHERE market_id = ?
          AND date <= ?
        ORDER BY date DESC
        LIMIT 1
    """, (market_id, date_str)).fetchone()

    conn.close()

    if result and result[0]:
        return result[0]

    return None


def run_continuous_capture(
    db_path: str = "data/prediction_markets.db",
    interval_minutes: int = 5,
    max_iterations: int = 0,  # 0 = infinite
) -> None:
    """
    Run continuous liquidity capture.

    Args:
        db_path: Database path
        interval_minutes: Minutes between captures
        max_iterations: Max captures (0 = run forever)
    """
    print(f"Starting continuous liquidity capture (every {interval_minutes} min)...")
    print("Press Ctrl+C to stop\n")

    iteration = 0
    while max_iterations == 0 or iteration < max_iterations:
        try:
            print(f"\n[{datetime.now().isoformat()}] Capture #{iteration + 1}")
            capture_liquidity_snapshot(db_path)
            iteration += 1

            if max_iterations == 0 or iteration < max_iterations:
                print(f"Sleeping {interval_minutes} minutes...")
                time.sleep(interval_minutes * 60)

        except KeyboardInterrupt:
            print("\nStopped by user")
            break


def get_liquidity_stats(db_path: str = "data/prediction_markets.db") -> Dict[str, Any]:
    """Get liquidity data statistics."""
    conn = sqlite3.connect(db_path)
    stats = {}

    try:
        # Snapshots
        result = conn.execute("SELECT COUNT(*) FROM liquidity_snapshots").fetchone()
        stats["snapshots"] = result[0]

        result = conn.execute(
            "SELECT MIN(datetime), MAX(datetime) FROM liquidity_snapshots"
        ).fetchone()
        stats["snapshot_range"] = (result[0], result[1])

        # Estimates
        result = conn.execute("SELECT COUNT(*) FROM liquidity_estimates").fetchone()
        stats["estimates"] = result[0]

        result = conn.execute(
            "SELECT COUNT(DISTINCT market_id) FROM liquidity_estimates"
        ).fetchone()
        stats["markets_with_estimates"] = result[0]

    except sqlite3.OperationalError:
        stats["error"] = "Tables not initialized"

    conn.close()
    return stats


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m src.trading.data_modules.liquidity --snapshot")
        print("  python -m src.trading.data_modules.liquidity --capture --interval 5")
        print("  python -m src.trading.data_modules.liquidity --estimate")
        print("  python -m src.trading.data_modules.liquidity --stats")
        sys.exit(0)

    if sys.argv[1] == "--snapshot":
        capture_liquidity_snapshot()

    elif sys.argv[1] == "--capture":
        interval = 5
        if len(sys.argv) > 3 and sys.argv[2] == "--interval":
            interval = int(sys.argv[3])
        run_continuous_capture(interval_minutes=interval)

    elif sys.argv[1] == "--estimate":
        estimate_historical_liquidity()

    elif sys.argv[1] == "--stats":
        stats = get_liquidity_stats()
        print("Liquidity Data Statistics:")
        for k, v in stats.items():
            print(f"  {k}: {v}")

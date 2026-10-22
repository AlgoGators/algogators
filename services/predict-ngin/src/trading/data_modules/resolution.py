"""
Market resolution data fetcher for Polymarket.

Fetches resolution status and winner outcomes from the Gamma API.
Run this AFTER the main data load is complete to add resolution info.

Usage:
    python -m src.trading.data_modules.resolution --update
"""

import json
import time
import sqlite3
import requests
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

GAMMA_API = "https://gamma-api.polymarket.com"
REQUEST_DELAY = 0.1


def determine_winner(outcome_prices: List[str]) -> Optional[str]:
    """
    Determine winner from outcome prices.

    When a market resolves:
    - Winner outcome price goes to ~1.0
    - Loser outcome price goes to ~0.0

    Args:
        outcome_prices: List of price strings ["yes_price", "no_price"]

    Returns:
        "YES", "NO", or None if undetermined
    """
    if not outcome_prices or len(outcome_prices) < 2:
        return None

    try:
        yes_price = float(outcome_prices[0])
        no_price = float(outcome_prices[1])
    except (ValueError, TypeError):
        return None

    # Clear winner threshold
    if yes_price > 0.95 and no_price < 0.05:
        return "YES"
    elif no_price > 0.95 and yes_price < 0.05:
        return "NO"
    elif yes_price == 0 and no_price == 0:
        # Market closed without resolution
        return None

    return None


def fetch_resolved_markets(
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Fetch closed/resolved markets from Gamma API.

    Args:
        limit: Batch size
        offset: Pagination offset

    Returns:
        List of market dicts with resolution info
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "PredictionMarketResearch/1.0"})

    try:
        response = session.get(
            f"{GAMMA_API}/markets",
            params={
                "closed": "true",
                "limit": limit,
                "offset": offset,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error fetching resolved markets: {e}")
        return []


def fetch_all_resolved_markets(
    max_markets: int = 10000,
    batch_size: int = 100,
) -> List[Dict[str, Any]]:
    """
    Fetch all resolved markets from Gamma API.

    Args:
        max_markets: Maximum markets to fetch
        batch_size: Markets per request

    Returns:
        List of all resolved market dicts
    """
    all_markets = []
    offset = 0

    print(f"Fetching resolved markets from Polymarket...")

    while len(all_markets) < max_markets:
        time.sleep(REQUEST_DELAY)
        batch = fetch_resolved_markets(limit=batch_size, offset=offset)

        if not batch:
            break

        all_markets.extend(batch)
        offset += len(batch)

        if len(all_markets) % 500 == 0:
            print(f"  Fetched {len(all_markets):,} resolved markets...")

        if len(batch) < batch_size:
            break

    print(f"  Total resolved markets: {len(all_markets):,}")
    return all_markets


def parse_resolution_data(market: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse resolution data from a market response.

    Args:
        market: Market dict from Gamma API

    Returns:
        Dict with resolution fields
    """
    outcome_prices = market.get("outcomePrices", [])
    if isinstance(outcome_prices, str):
        try:
            outcome_prices = json.loads(outcome_prices)
        except json.JSONDecodeError:
            outcome_prices = []

    winner = determine_winner(outcome_prices)

    try:
        yes_price = float(outcome_prices[0]) if outcome_prices else None
        no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else None
    except (ValueError, TypeError, IndexError):
        yes_price = None
        no_price = None

    return {
        "market_id": market.get("id"),
        "slug": market.get("slug"),
        "question": market.get("question", "")[:200],
        "closed": market.get("closed", False),
        "closed_time": market.get("closedTime"),
        "end_date": market.get("endDate"),
        "winner": winner,
        "resolution_price_yes": yes_price,
        "resolution_price_no": no_price,
        "volume": float(market.get("volume", 0) or 0),
    }


def add_resolution_columns(db_path: str) -> bool:
    """
    Add resolution columns to existing polymarket_markets table.

    Args:
        db_path: Path to SQLite database

    Returns:
        True if successful
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check existing columns
    cursor.execute("PRAGMA table_info(polymarket_markets)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    new_cols = [
        ("closed", "INTEGER DEFAULT 0"),
        ("closed_time", "TEXT"),
        ("resolution_outcome", "TEXT"),
        ("resolution_price_yes", "REAL"),
        ("resolution_price_no", "REAL"),
        ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ]

    added = 0
    for col_name, col_def in new_cols:
        if col_name not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE polymarket_markets ADD COLUMN {col_name} {col_def}")
                print(f"  Added column: {col_name}")
                added += 1
            except sqlite3.OperationalError as e:
                print(f"  Column {col_name} already exists or error: {e}")

    conn.commit()
    conn.close()

    print(f"  Added {added} new columns to polymarket_markets")
    return True


def update_resolution_data(
    db_path: str,
    markets: List[Dict[str, Any]],
) -> Tuple[int, int]:
    """
    Update database with resolution data.

    Args:
        db_path: Path to SQLite database
        markets: List of parsed resolution data dicts

    Returns:
        Tuple of (updated_count, skipped_count)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    updated = 0
    skipped = 0

    for market in markets:
        if not market.get("market_id"):
            skipped += 1
            continue

        try:
            cursor.execute("""
                UPDATE polymarket_markets
                SET closed = ?,
                    closed_time = ?,
                    resolution_outcome = ?,
                    resolution_price_yes = ?,
                    resolution_price_no = ?
                WHERE id = ?
            """, (
                1 if market["closed"] else 0,
                market["closed_time"],
                market["winner"],
                market["resolution_price_yes"],
                market["resolution_price_no"],
                market["market_id"],
            ))

            if cursor.rowcount > 0:
                updated += 1
            else:
                skipped += 1

        except sqlite3.Error as e:
            print(f"  Error updating {market['market_id']}: {e}")
            skipped += 1

    conn.commit()
    conn.close()

    return updated, skipped


def save_resolution_cache(
    markets: List[Dict[str, Any]],
    cache_path: str = "data/resolution_cache.json",
) -> None:
    """
    Save resolution data to JSON cache for later use.

    Args:
        markets: List of parsed resolution data
        cache_path: Output path
    """
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({
            "fetched_at": datetime.now().isoformat(),
            "count": len(markets),
            "markets": markets,
        }, f, indent=2)

    print(f"  Saved resolution cache to {cache_path}")


def load_resolution_cache(
    cache_path: str = "data/resolution_cache.json",
) -> Optional[List[Dict[str, Any]]]:
    """
    Load resolution data from cache.

    Args:
        cache_path: Path to cache file

    Returns:
        List of market dicts or None
    """
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"  Loaded {data['count']} markets from cache (fetched {data['fetched_at']})")
        return data["markets"]
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def run_resolution_update(
    db_path: str = "data/prediction_markets.db",
    use_cache: bool = True,
    cache_path: str = "data/resolution_cache.json",
) -> Dict[str, int]:
    """
    Full resolution data update workflow.

    1. Add resolution columns if missing
    2. Fetch resolved markets from API (or cache)
    3. Update database with resolution outcomes

    Args:
        db_path: Path to database
        use_cache: Whether to use/create cache
        cache_path: Path to cache file

    Returns:
        Stats dict
    """
    print("=" * 60)
    print("POLYMARKET RESOLUTION DATA UPDATE")
    print("=" * 60)

    # Step 1: Add columns
    print("\n[1] Adding resolution columns to database...")
    add_resolution_columns(db_path)

    # Step 2: Fetch data
    print("\n[2] Fetching resolution data...")

    markets = None
    if use_cache:
        markets = load_resolution_cache(cache_path)

    if not markets:
        raw_markets = fetch_all_resolved_markets()
        markets = [parse_resolution_data(m) for m in raw_markets]

        if use_cache:
            save_resolution_cache(markets, cache_path)

    # Stats
    with_winner = sum(1 for m in markets if m["winner"])
    yes_wins = sum(1 for m in markets if m["winner"] == "YES")
    no_wins = sum(1 for m in markets if m["winner"] == "NO")

    print(f"\n  Resolved markets: {len(markets):,}")
    print(f"  With clear winner: {with_winner:,}")
    print(f"  YES wins: {yes_wins:,} ({100*yes_wins/max(with_winner,1):.1f}%)")
    print(f"  NO wins: {no_wins:,} ({100*no_wins/max(with_winner,1):.1f}%)")

    # Step 3: Update database
    print("\n[3] Updating database...")
    updated, skipped = update_resolution_data(db_path, markets)

    print(f"\n  Updated: {updated:,}")
    print(f"  Skipped (not in DB): {skipped:,}")

    print("\n" + "=" * 60)
    print("RESOLUTION UPDATE COMPLETE")
    print("=" * 60)

    return {
        "total_markets": len(markets),
        "with_winner": with_winner,
        "updated": updated,
        "skipped": skipped,
    }


def get_resolution_stats(db_path: str = "data/prediction_markets.db") -> Dict[str, Any]:
    """
    Get resolution statistics from database.

    Args:
        db_path: Path to database

    Returns:
        Dict with stats
    """
    conn = sqlite3.connect(db_path)

    try:
        stats = {}

        # Total markets
        result = conn.execute("SELECT COUNT(*) FROM polymarket_markets").fetchone()
        stats["total_markets"] = result[0]

        # Check if resolution columns exist
        cursor = conn.execute("PRAGMA table_info(polymarket_markets)")
        cols = {row[1] for row in cursor.fetchall()}

        if "closed" not in cols:
            stats["resolution_columns_added"] = False
            return stats

        stats["resolution_columns_added"] = True

        # Closed markets
        result = conn.execute("SELECT COUNT(*) FROM polymarket_markets WHERE closed = 1").fetchone()
        stats["closed_markets"] = result[0]

        # With resolution
        result = conn.execute(
            "SELECT COUNT(*) FROM polymarket_markets WHERE resolution_outcome IS NOT NULL"
        ).fetchone()
        stats["with_resolution"] = result[0]

        # By outcome
        result = conn.execute(
            "SELECT resolution_outcome, COUNT(*) FROM polymarket_markets "
            "WHERE resolution_outcome IS NOT NULL GROUP BY resolution_outcome"
        ).fetchall()
        stats["by_outcome"] = dict(result)

        return stats

    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--update":
        run_resolution_update()
    elif len(sys.argv) > 1 and sys.argv[1] == "--stats":
        stats = get_resolution_stats()
        print("Resolution Statistics:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--fetch-only":
        # Just fetch and cache, don't update DB
        raw_markets = fetch_all_resolved_markets()
        markets = [parse_resolution_data(m) for m in raw_markets]
        save_resolution_cache(markets)
    else:
        print("Usage:")
        print("  python -m src.trading.data_modules.resolution --update      # Full update")
        print("  python -m src.trading.data_modules.resolution --fetch-only  # Fetch & cache only")
        print("  python -m src.trading.data_modules.resolution --stats       # Show current stats")

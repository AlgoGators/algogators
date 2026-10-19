#!/usr/bin/env python3
"""
Incrementally update data/poly_cat/resolutions.csv with newly resolved markets.

Fetches markets closed since the latest timestamp in the existing CSV and
appends them.  Safe to re-run at any time; deduplicates by conditionId.

Usage:
    python scripts/data/update_resolutions.py
    python scripts/data/update_resolutions.py --resolutions-dir data/poly_cat
    python scripts/data/update_resolutions.py --lookback-days 30 --dry-run
"""

import argparse
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

GAMMA_API   = "https://gamma-api.polymarket.com"
REQUEST_DELAY = 0.2


def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "update-resolutions/1.0"
    return s


def fetch_closed_markets_since(
    session: requests.Session,
    since_ts: int,
    batch_size: int = 500,
) -> list:
    """
    Fetch markets closed after `since_ts` (unix seconds).
    Returns list of raw market dicts.
    """
    markets = []
    offset = 0
    print(f"  Fetching markets closed after {datetime.fromtimestamp(since_ts).date()}...")

    while True:
        try:
            resp = session.get(
                f"{GAMMA_API}/markets",
                params={
                    "closed": "true",
                    "limit":  batch_size,
                    "offset": offset,
                },
                timeout=20,
            )
            resp.raise_for_status()
            batch = resp.json() or []
        except Exception as e:
            print(f"  Warning: fetch error at offset {offset}: {e}")
            break

        if not batch:
            break

        # Filter to markets closed after since_ts
        for m in batch:
            closed_raw = m.get("closedTime") or m.get("updatedAt") or ""
            try:
                if closed_raw:
                    if isinstance(closed_raw, (int, float)):
                        closed_ts = int(float(closed_raw))
                        if closed_ts > 1e12:
                            closed_ts //= 1000
                    else:
                        dt = datetime.fromisoformat(
                            str(closed_raw).replace("Z", "+00:00")
                        )
                        closed_ts = int(dt.timestamp())
                else:
                    closed_ts = 0
            except Exception:
                closed_ts = 0

            if closed_ts >= since_ts:
                markets.append(m)

        offset += len(batch)
        if len(batch) < batch_size:
            break
        time.sleep(REQUEST_DELAY)

    return markets


def extract_resolution(market: dict) -> dict | None:
    """
    Extract resolution winner from a market dict.

    Returns a flat dict with: market_id, winner, question, slug, closed_time.
    Returns None if we can't determine the winner.
    """
    cid = str(market.get("conditionId", "") or "").strip()
    if not cid:
        return None

    # Determine winner from outcomePrices (1.0 = resolved YES)
    outcome_prices = market.get("outcomePrices")
    outcomes       = market.get("outcomes", "[]")

    try:
        if isinstance(outcome_prices, str):
            import json
            prices = json.loads(outcome_prices)
        elif isinstance(outcome_prices, list):
            prices = outcome_prices
        else:
            prices = []

        if isinstance(outcomes, str):
            import json
            outcomes_list = json.loads(outcomes)
        else:
            outcomes_list = list(outcomes or [])

        if prices and outcomes_list:
            float_prices = [float(p) for p in prices]
            max_idx = float_prices.index(max(float_prices))
            if float_prices[max_idx] >= 0.99:
                winner = str(outcomes_list[max_idx]).upper()
                if winner not in ("YES", "NO") and max_idx == 0:
                    winner = "YES"
                elif winner not in ("YES", "NO"):
                    winner = "NO"
            else:
                return None  # Not definitively resolved yet
        else:
            return None
    except Exception:
        return None

    closed_raw = market.get("closedTime") or market.get("updatedAt") or ""
    try:
        if isinstance(closed_raw, (int, float)):
            closed_ts = int(float(closed_raw))
            if closed_ts > 1e12:
                closed_ts //= 1000
            closed_iso = datetime.fromtimestamp(closed_ts, tz=timezone.utc).isoformat()
        else:
            closed_iso = str(closed_raw)
    except Exception:
        closed_iso = ""

    return {
        "market_id":   cid,
        "winner":      winner,
        "question":    str(market.get("question", "") or "")[:200],
        "slug":        str(market.get("slug", "") or ""),
        "closed_time": closed_iso,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Incrementally update resolutions.csv with newly resolved markets",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--resolutions-dir", type=Path,
        default=_project_root / "data" / "poly_cat",
        help="Directory containing resolutions.csv",
    )
    parser.add_argument(
        "--lookback-days", type=int, default=7,
        help="If no existing CSV: look back this many days for resolved markets",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print new rows without writing",
    )
    args = parser.parse_args()

    csv_path = args.resolutions_dir / "resolutions.csv"

    # Load existing resolutions
    existing_df = pd.DataFrame()
    existing_ids: set = set()
    latest_ts: int = 0

    if csv_path.exists():
        try:
            existing_df = pd.read_csv(csv_path, low_memory=False)
            if "market_id" in existing_df.columns:
                existing_ids = set(existing_df["market_id"].astype(str).unique())
            if "closed_time" in existing_df.columns:
                ts_series = pd.to_datetime(
                    existing_df["closed_time"], errors="coerce", utc=True
                ).dropna()
                if not ts_series.empty:
                    latest_ts = int(ts_series.max().timestamp())
            print(f"Existing resolutions: {len(existing_df):,} markets")
            print(f"Latest resolution:    {datetime.fromtimestamp(latest_ts).date() if latest_ts else 'unknown'}")
        except Exception as e:
            print(f"Warning: could not read existing CSV: {e}")

    if latest_ts == 0:
        latest_ts = int(
            (datetime.now(timezone.utc) - timedelta(days=args.lookback_days)).timestamp()
        )
        print(f"No latest timestamp found — fetching last {args.lookback_days} days")

    session = _get_session()
    markets = fetch_closed_markets_since(session, since_ts=latest_ts)
    print(f"  Found {len(markets):,} closed markets since cutoff")

    new_rows = []
    for m in markets:
        row = extract_resolution(m)
        if row is None:
            continue
        if row["market_id"] in existing_ids:
            continue
        new_rows.append(row)

    if not new_rows:
        print("No new resolutions found.")
        return 0

    new_df = pd.DataFrame(new_rows)
    print(f"New resolutions: {len(new_df):,}")

    if args.dry_run:
        print("\nDry-run — would append:")
        print(new_df[["market_id", "winner", "question"]].to_string(index=False))
        return 0

    combined = pd.concat([existing_df, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["market_id"], keep="last")

    args.resolutions_dir.mkdir(parents=True, exist_ok=True)
    combined.to_csv(csv_path, index=False)
    print(f"Saved {len(combined):,} total resolutions to {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

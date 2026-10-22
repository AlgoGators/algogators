"""
Data loading utilities for Manifold Markets prediction market data.

Supports loading from:
1. SQLite database (preferred, faster)
2. JSON files (fallback)
"""

import json
import glob
import sqlite3
import pandas as pd
from pathlib import Path
from typing import Dict, Any, Optional

# Default database path
DEFAULT_DB_PATH = "data/prediction_markets.db"

def load_manifold_data(
    data_dir: str = "data/manifold",
    db_path: str = DEFAULT_DB_PATH,
    auto_fetch: bool = True,
    use_db: bool = True,
) -> pd.DataFrame:
    """
    Load all Manifold bets.

    Args:
        data_dir: Directory containing bets_*.json files (fallback)
        db_path: Path to SQLite database (preferred)
        auto_fetch: If True, fetch data from API if not found
        use_db: If True, try loading from database first

    Returns:
        DataFrame with all bets, sorted by datetime
    """
    # Try loading from database first
    if use_db and Path(db_path).exists():
        df = _load_manifold_bets_from_db(db_path)
        if len(df) > 0:
            return df

    # Fallback to JSON files
    bets = []
    bet_files = sorted(glob.glob(f"{data_dir}/bets_*.json"))

    # Auto-fetch if no data exists
    if not bet_files and auto_fetch:
        print("No local data found. Fetching from Manifold API...")
        from .fetcher import DataFetcher
        fetcher = DataFetcher(str(Path(data_dir).parent))
        fetcher.fetch_manifold_bets()
        fetcher.fetch_manifold_markets()
        bet_files = sorted(glob.glob(f"{data_dir}/bets_*.json"))

    for f in bet_files:
        try:
            with open(f, encoding="utf-8") as file:
                bets.extend(json.load(file))
        except (json.JSONDecodeError, IOError):
            continue

    if not bets:
        raise ValueError(f"No bets found in {data_dir}. Run with auto_fetch=True or use DataFetcher.")

    df = pd.DataFrame(bets)
    df["datetime"] = pd.to_datetime(df["createdTime"], unit="ms")
    df["date"] = df["datetime"].dt.date
    df["month"] = df["datetime"].dt.to_period("M")
    df["amount_abs"] = df["amount"].abs()
    df = df.sort_values("datetime").reset_index(drop=True)

    return df


def _load_manifold_bets_from_db(db_path: str) -> pd.DataFrame:
    """Load Manifold bets from SQLite database."""
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query("""
            SELECT
                id, user_id as userId, contract_id as contractId,
                amount, outcome, prob_before as probBefore,
                prob_after as probAfter, created_time as createdTime
            FROM manifold_bets
            ORDER BY created_time
        """, conn)

        if len(df) > 0:
            df["datetime"] = pd.to_datetime(df["createdTime"], unit="ms")
            df["date"] = df["datetime"].dt.date
            df["month"] = df["datetime"].dt.to_period("M")
            df["amount_abs"] = df["amount"].abs()
        return df
    finally:
        conn.close()


def load_markets(
    data_dir: str = "data/manifold",
    db_path: str = DEFAULT_DB_PATH,
    use_db: bool = True,
) -> pd.DataFrame:
    """
    Load all Manifold markets.

    Args:
        data_dir: Directory containing markets_*.json files (fallback)
        db_path: Path to SQLite database (preferred)
        use_db: If True, try loading from database first

    Returns:
        DataFrame with all markets
    """
    # Try loading from database first
    if use_db and Path(db_path).exists():
        df = _load_manifold_markets_from_db(db_path)
        if len(df) > 0:
            return df

    # Fallback to JSON files
    markets = []

    for f in glob.glob(f"{data_dir}/markets_*.json"):
        try:
            with open(f, encoding="utf-8") as file:
                markets.extend(json.load(file))
        except (json.JSONDecodeError, IOError):
            continue

    return pd.DataFrame(markets)


def _load_manifold_markets_from_db(db_path: str) -> pd.DataFrame:
    """Load Manifold markets from SQLite database."""
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query("SELECT id, data FROM manifold_markets", conn)

        if len(df) > 0:
            # Parse JSON data column into individual columns
            markets = []
            for _, row in df.iterrows():
                try:
                    market = json.loads(row["data"])
                    markets.append(market)
                except (json.JSONDecodeError, TypeError):
                    continue
            return pd.DataFrame(markets)
        return df
    finally:
        conn.close()


def build_resolution_map(
    markets_df: pd.DataFrame,
    include_open: bool = False
) -> Dict[str, Dict[str, Any]]:
    """
    Build a mapping from market ID to resolution/market data.

    Args:
        markets_df: DataFrame of markets
        include_open: If True, include unresolved markets with current probability

    Returns:
        Dictionary mapping market ID to market info
    """
    resolution_data = {}

    for _, m in markets_df.iterrows():
        mid = m["id"]
        is_resolved = m.get("isResolved", False)
        resolution = m.get("resolution")

        # Resolved YES/NO markets
        if is_resolved and resolution in ["YES", "NO"]:
            resolution_data[mid] = {
                "resolution": 1.0 if resolution == "YES" else 0.0,
                "resolved_time": m.get("resolutionTime", m.get("closeTime", 0)),
                "question": str(m.get("question", "")),
                "liquidity": m.get("totalLiquidity", 1000),
                "volume": m.get("volume", 0),
                "is_resolved": True,
                "status": "resolved",
            }
        # Unresolved markets (only if include_open=True)
        elif include_open and not is_resolved:
            # Use current probability as mark-to-market price
            current_prob = m.get("probability", m.get("prob", 0.5))
            resolution_data[mid] = {
                "resolution": None,  # Unknown
                "current_prob": current_prob,
                "resolved_time": None,
                "question": str(m.get("question", "")),
                "liquidity": m.get("totalLiquidity", 1000),
                "volume": m.get("volume", 0),
                "is_resolved": False,
                "status": "open",
                "close_time": m.get("closeTime", 0),
            }

    return resolution_data


def train_test_split(
    df: pd.DataFrame,
    train_ratio: float = 0.3,
    test_start: Optional[str] = None,
    test_end: Optional[str] = None,
    test_days: Optional[int] = None,
    test_months: Optional[int] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split data temporally into train and test sets.

    Args:
        df: DataFrame with datetime column
        train_ratio: Fraction of data for training (default 30%)
        test_start: Explicit start date for test window (YYYY-MM-DD)
        test_end: Explicit end date for test window (YYYY-MM-DD)
        test_days: Use most recent N days as test window
        test_months: Use most recent N months as test window

    Returns:
        Tuple of (train_df, test_df)
    """
    if df.empty:
        return df.copy(), df.copy()

    use_window = any(
        value is not None for value in (test_start, test_end, test_days, test_months)
    )
    if use_window:
        if test_days is not None and test_days <= 0:
            raise ValueError("test_days must be positive")
        if test_months is not None and test_months <= 0:
            raise ValueError("test_months must be positive")
        if test_days is not None and test_months is not None:
            raise ValueError("Specify only one of test_days or test_months")
        if test_start is not None and (test_days is not None or test_months is not None):
            raise ValueError("Specify test_start or test_days/test_months, not both")

        end = pd.to_datetime(test_end) if test_end else df["datetime"].max()
        include_start = True

        if test_start is not None:
            start = pd.to_datetime(test_start)
            include_start = True
        elif test_days is not None:
            start = end - pd.Timedelta(days=float(test_days))
            include_start = False
        elif test_months is not None:
            start = end - pd.DateOffset(months=int(test_months))
            include_start = False
        else:
            start = df["datetime"].min()

        if start > end:
            raise ValueError("test_start must be <= test_end")

        if include_start:
            test_mask = (df["datetime"] >= start) & (df["datetime"] <= end)
            train_mask = df["datetime"] < start
        else:
            test_mask = (df["datetime"] > start) & (df["datetime"] <= end)
            train_mask = df["datetime"] <= start

        train_df = df[train_mask]
        test_df = df[test_mask]
        return train_df, test_df

    split_date = df["datetime"].quantile(train_ratio)
    train_df = df[df["datetime"] <= split_date]
    test_df = df[df["datetime"] > split_date]

    return train_df, test_df

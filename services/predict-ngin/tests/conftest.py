"""
Shared pytest fixtures for the predict-ngin test suite.

Provides:
- Sample DataFrames for trades, prices, markets
- Mock database connections
- Mock API responses
- Shared test data generators
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List
from unittest.mock import MagicMock, patch
import sys

import pandas as pd
import pytest


# Ensure src is in path
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# =============================================================================
# Sample Data Fixtures
# =============================================================================

@pytest.fixture
def sample_trades_df() -> pd.DataFrame:
    """
    Sample trades DataFrame for testing strategies.

    Contains 20 trades over a 2-hour window with varying sizes and directions.
    """
    base_time = datetime(2026, 1, 15, 12, 0, 0)
    trades = []

    for i in range(20):
        trades.append({
            "trade_id": f"trade_{i:03d}",
            "market_id": "market_001",
            "datetime": base_time + timedelta(minutes=i * 6),
            "taker_direction": "buy" if i % 3 != 0 else "sell",
            "usd_amount": 100 + (i * 50) if i % 2 == 0 else 50 + (i * 25),
            "price": 0.50 + (i * 0.005) - (0.01 if i % 3 == 0 else 0),
            "maker": f"maker_{i % 5}",
            "taker": f"taker_{i % 3}",
            "token_amount": 100,
        })

    return pd.DataFrame(trades)


@pytest.fixture
def sample_prices_df() -> pd.DataFrame:
    """
    Sample price history DataFrame for testing strategies.

    Contains hourly prices over 48 hours for 2 markets.
    """
    base_time = pd.Timestamp("2026-01-14 00:00:00")
    prices = []

    # Market 1: Trending up then consolidating
    for i in range(48):
        price = 0.40 + (i * 0.005) if i < 24 else 0.52 + ((i - 24) % 3 - 1) * 0.002
        prices.append({
            "market_id": "market_001",
            "datetime": base_time + pd.Timedelta(hours=i),
            "price": price,
            "outcome": "YES",
        })

    # Market 2: Mean-reverting around 0.50
    for i in range(48):
        price = 0.50 + (0.08 * ((-1) ** i)) * (0.9 ** (i // 6))
        prices.append({
            "market_id": "market_002",
            "datetime": base_time + pd.Timedelta(hours=i),
            "price": max(0.05, min(0.95, price)),
            "outcome": "YES",
        })

    return pd.DataFrame(prices)


@pytest.fixture
def sample_markets_df() -> pd.DataFrame:
    """
    Sample markets DataFrame for testing.

    Contains 5 markets with varying end dates and volumes.
    """
    base_time = datetime(2026, 1, 15, 12, 0, 0)

    markets = [
        {
            "id": "market_001",
            "slug": "market-one",
            "question": "Will market one resolve YES?",
            "volume": 100000,
            "volume_24hr": 5000,
            "liquidity": 10000,
            "end_date": base_time + timedelta(days=5),
            "outcomes": ["YES", "NO"],
            "outcome_prices": [0.52, 0.48],
        },
        {
            "id": "market_002",
            "slug": "market-two",
            "question": "Will market two resolve YES?",
            "volume": 50000,
            "volume_24hr": 2500,
            "liquidity": 5000,
            "end_date": base_time + timedelta(days=14),
            "outcomes": ["YES", "NO"],
            "outcome_prices": [0.50, 0.50],
        },
        {
            "id": "market_003",
            "slug": "market-three-expiring",
            "question": "Will market three resolve YES?",
            "volume": 75000,
            "volume_24hr": 8000,
            "liquidity": 7500,
            "end_date": base_time + timedelta(days=2),
            "outcomes": ["YES", "NO"],
            "outcome_prices": [0.85, 0.15],
        },
        {
            "id": "market_004",
            "slug": "market-four-low-volume",
            "question": "Will market four resolve YES?",
            "volume": 5000,
            "volume_24hr": 100,
            "liquidity": 500,
            "end_date": base_time + timedelta(days=30),
            "outcomes": ["YES", "NO"],
            "outcome_prices": [0.30, 0.70],
        },
        {
            "id": "market_005",
            "slug": "market-five-high-liquidity",
            "question": "Will market five resolve YES?",
            "volume": 250000,
            "volume_24hr": 15000,
            "liquidity": 50000,
            "end_date": base_time + timedelta(days=10),
            "outcomes": ["YES", "NO"],
            "outcome_prices": [0.65, 0.35],
        },
    ]

    return pd.DataFrame(markets)


@pytest.fixture
def smart_money_trades_df() -> pd.DataFrame:
    """
    Trades DataFrame specifically designed for SmartMoneyStrategy testing.

    Contains large trades showing clear directional flow.
    """
    base_time = datetime(2026, 1, 15, 12, 0, 0)
    trades = []

    # Large buy flow for market_001
    for i in range(5):
        trades.append({
            "market_id": "market_001",
            "datetime": base_time - timedelta(minutes=i * 15),
            "taker_direction": "buy",
            "usd_amount": 6000 + (i * 1000),
            "price": 0.52,
        })

    # Small offsetting sells
    for i in range(2):
        trades.append({
            "market_id": "market_001",
            "datetime": base_time - timedelta(minutes=i * 10 + 5),
            "taker_direction": "sell",
            "usd_amount": 2000,
            "price": 0.51,
        })

    return pd.DataFrame(trades)


@pytest.fixture
def breakout_prices_df() -> pd.DataFrame:
    """
    Price DataFrame designed to trigger breakout detection.

    Shows consolidation followed by breakout.
    """
    base_time = pd.Timestamp("2026-01-10 00:00:00")
    prices = []

    # 24 hours of consolidation (0.48-0.52 range)
    for i in range(24):
        price = 0.50 + ((i % 4) - 2) * 0.01
        prices.append({
            "market_id": "breakout_market",
            "datetime": base_time + pd.Timedelta(hours=i),
            "price": price,
        })

    # Breakout in hour 25-30
    for i in range(6):
        price = 0.52 + (i * 0.03)  # Sharp upward move
        prices.append({
            "market_id": "breakout_market",
            "datetime": base_time + pd.Timedelta(hours=24 + i),
            "price": price,
        })

    return pd.DataFrame(prices)


@pytest.fixture
def sentiment_divergence_data() -> Dict[str, pd.DataFrame]:
    """
    Data designed to show sentiment divergence (bullish flow, bearish price).
    """
    base_time = datetime(2026, 1, 15, 12, 0, 0)

    # Strong buying pressure
    trades = []
    for i in range(15):
        trades.append({
            "market_id": "divergence_market",
            "datetime": base_time - timedelta(minutes=i * 4),
            "taker_direction": "buy",
            "usd_amount": 500 + (i * 100),
        })

    # Few sells
    for i in range(3):
        trades.append({
            "market_id": "divergence_market",
            "datetime": base_time - timedelta(minutes=i * 20 + 10),
            "taker_direction": "sell",
            "usd_amount": 200,
        })

    trades_df = pd.DataFrame(trades)

    # Price going down despite buying
    prices = [
        {"market_id": "divergence_market", "datetime": base_time - timedelta(hours=1), "price": 0.60},
        {"market_id": "divergence_market", "datetime": base_time - timedelta(minutes=30), "price": 0.55},
        {"market_id": "divergence_market", "datetime": base_time, "price": 0.52},
    ]
    prices_df = pd.DataFrame(prices)

    return {"trades": trades_df, "prices": prices_df}


@pytest.fixture
def time_decay_data() -> Dict[str, Any]:
    """
    Data for TimeDecayStrategy testing.
    """
    as_of = datetime(2026, 1, 15, 12, 0, 0)

    # Market expiring in 3 days, stale (no recent trades)
    markets_df = pd.DataFrame([
        {
            "id": "expiring_market",
            "end_date": as_of + timedelta(days=3),
            "question": "Will this resolve?",
        },
        {
            "id": "accelerating_market",
            "end_date": as_of + timedelta(days=5),
            "question": "Will this accelerate?",
        },
    ])

    prices_df = pd.DataFrame([
        {"market_id": "expiring_market", "datetime": as_of - timedelta(hours=2), "price": 0.75},
        {"market_id": "accelerating_market", "datetime": as_of - timedelta(minutes=30), "price": 0.90},
    ])

    trades_df = pd.DataFrame([
        {"market_id": "expiring_market", "datetime": as_of - timedelta(hours=30)},  # Stale
        {"market_id": "accelerating_market", "datetime": as_of - timedelta(minutes=10)},
    ])

    return {
        "markets": markets_df,
        "prices": prices_df,
        "trades": trades_df,
        "as_of": as_of,
    }


# =============================================================================
# Mock Database Fixtures
# =============================================================================

@pytest.fixture
def mock_db(tmp_path: Path) -> Path:
    """
    Create a temporary SQLite database for testing.
    """
    db_path = tmp_path / "test_prediction_markets.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create schema
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS polymarket_markets (
            id TEXT PRIMARY KEY,
            slug TEXT,
            question TEXT,
            volume REAL,
            volume_24hr REAL,
            liquidity REAL,
            end_date TEXT,
            outcomes TEXT,
            outcome_prices TEXT,
            token_id_yes TEXT,
            token_id_no TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS polymarket_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT,
            outcome TEXT,
            timestamp INTEGER,
            price REAL,
            datetime TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS polymarket_trades (
            trade_id TEXT PRIMARY KEY,
            timestamp TEXT,
            timestamp_unix INTEGER,
            market_id TEXT,
            maker TEXT,
            taker TEXT,
            nonusdc_side TEXT,
            maker_direction TEXT,
            taker_direction TEXT,
            price REAL,
            usd_amount REAL,
            token_amount REAL,
            transaction_hash TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS manifold_bets (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            contract_id TEXT,
            amount REAL,
            outcome TEXT,
            prob_before REAL,
            prob_after REAL,
            created_time INTEGER,
            datetime TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS manifold_markets (
            id TEXT PRIMARY KEY,
            data TEXT
        )
    """)

    conn.commit()
    conn.close()

    return db_path


@pytest.fixture
def populated_db(mock_db: Path, sample_markets_df: pd.DataFrame, sample_prices_df: pd.DataFrame) -> Path:
    """
    Create a populated test database with sample data.
    """
    conn = sqlite3.connect(str(mock_db))
    cursor = conn.cursor()

    # Insert markets
    for _, market in sample_markets_df.iterrows():
        cursor.execute("""
            INSERT INTO polymarket_markets
            (id, slug, question, volume, volume_24hr, liquidity, end_date, outcomes, outcome_prices)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            market["id"],
            market["slug"],
            market["question"],
            market["volume"],
            market["volume_24hr"],
            market["liquidity"],
            str(market["end_date"]),
            json.dumps(market["outcomes"]),
            json.dumps(market["outcome_prices"]),
        ))

    # Insert prices
    for _, price in sample_prices_df.iterrows():
        ts = int(pd.Timestamp(price["datetime"]).timestamp())
        cursor.execute("""
            INSERT INTO polymarket_prices
            (market_id, outcome, timestamp, price, datetime)
            VALUES (?, ?, ?, ?, ?)
        """, (
            price["market_id"],
            price["outcome"],
            ts,
            price["price"],
            str(price["datetime"]),
        ))

    conn.commit()
    conn.close()

    return mock_db


# =============================================================================
# Mock API Response Fixtures
# =============================================================================

@pytest.fixture
def mock_order_book_response() -> Dict[str, Any]:
    """
    Mock response for CLOB order book API.
    """
    return {
        "bids": [
            {"price": "0.48", "size": "1000"},
            {"price": "0.47", "size": "2000"},
            {"price": "0.46", "size": "3000"},
            {"price": "0.45", "size": "5000"},
        ],
        "asks": [
            {"price": "0.52", "size": "1000"},
            {"price": "0.53", "size": "2000"},
            {"price": "0.54", "size": "3000"},
            {"price": "0.55", "size": "5000"},
        ],
    }


@pytest.fixture
def mock_market_response() -> Dict[str, Any]:
    """
    Mock response for market info API.
    """
    return {
        "id": "test_market_id",
        "question": "Test Market Question?",
        "outcomePrices": ["0.50", "0.50"],
        "clobTokenIds": ["token_yes_123", "token_no_456"],
        "volume": "100000",
        "liquidity": "10000",
        "closed": False,
    }


# =============================================================================
# Paper Trading Fixtures
# =============================================================================

@pytest.fixture
def mock_paper_trader():
    """
    Create a mock paper trader for testing without real API calls.
    """
    from trading.live.paper_trading import (
        PaperTrader, PaperAccount, CostModel, PriceTracker
    )

    trader = PaperTrader(
        initial_capital=10000.0,
        db_path=":memory:",
        state_path="test_state.json",
        log_path="test_log.jsonl",
        max_position_size=1000.0,
        max_positions=10,
    )

    # Override API calls with mock price
    trader.get_market_price = MagicMock(return_value=0.50)
    trader.get_market_liquidity = MagicMock(return_value=5000)

    return trader


@pytest.fixture
def mock_order_router():
    """
    Create a mock order router for testing without real API calls.
    """
    from trading.live.order_router import OrderRouter

    router = OrderRouter(dry_run=True)
    return router


# =============================================================================
# Utility Functions
# =============================================================================

def create_trade_sequence(
    market_id: str,
    base_time: datetime,
    direction: str,
    count: int,
    base_size: float = 100,
    size_increment: float = 50,
) -> List[Dict[str, Any]]:
    """
    Helper function to create a sequence of trades.

    Args:
        market_id: Market identifier
        base_time: Starting time
        direction: "buy" or "sell"
        count: Number of trades
        base_size: Starting size
        size_increment: Size increase per trade

    Returns:
        List of trade dictionaries
    """
    trades = []
    for i in range(count):
        trades.append({
            "market_id": market_id,
            "datetime": base_time - timedelta(minutes=i * 5),
            "taker_direction": direction,
            "usd_amount": base_size + (i * size_increment),
            "price": 0.50,
        })
    return trades


def create_price_series(
    market_id: str,
    base_time: datetime,
    hours: int,
    start_price: float = 0.50,
    trend: float = 0.0,
    volatility: float = 0.02,
) -> List[Dict[str, Any]]:
    """
    Helper function to create a price series.

    Args:
        market_id: Market identifier
        base_time: Starting time
        hours: Number of hourly prices
        start_price: Initial price
        trend: Price change per hour
        volatility: Random variation

    Returns:
        List of price dictionaries
    """
    import random

    prices = []
    price = start_price

    for i in range(hours):
        prices.append({
            "market_id": market_id,
            "datetime": base_time + timedelta(hours=i),
            "price": max(0.01, min(0.99, price)),
        })
        price += trend + (random.random() - 0.5) * volatility

    return prices


@pytest.fixture(autouse=True)
def _isolated_results_db(tmp_path, monkeypatch):
    """Backtests persist to backtests/results.db by default; never let tests write there."""
    monkeypatch.setenv("PREDICT_NGIN_RESULTS_DB", str(tmp_path / "results_test.db"))


# =============================================================================
# `data` marker: tests that need local files under the data root
# =============================================================================
#
#   @pytest.mark.data("research/Politics/trades.parquet")   # paths relative to the data root
#   @pytest.mark.data                                       # just needs the data root to exist
#
# The data root is $PREDICT_NGIN_DATA, else <repo>/data (predict_ngin.shared.paths.data_root).
# Missing data -> the test is skipped, so `pytest -m "not data"` and a fresh clone both run clean.

def pytest_collection_modifyitems(config, items):
    from predict_ngin.shared.paths import data_root

    root = data_root()
    for item in items:
        for mark in item.iter_markers(name="data"):
            required = [root / p for p in mark.args] or [root]
            missing = [p for p in required if not p.exists()]
            if missing:
                item.add_marker(pytest.mark.skip(
                    reason=f"local data missing: {missing[0]} (set PREDICT_NGIN_DATA)"))
                break

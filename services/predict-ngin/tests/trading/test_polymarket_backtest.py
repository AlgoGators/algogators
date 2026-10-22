import json
import sqlite3
from pathlib import Path

import pandas as pd

from trading.polymarket_backtest import (
    PolymarketBacktestConfig,
    run_polymarket_backtest,
    ClobPriceStore,
)
from trading.data_modules.database import PredictionMarketDB
from trading.data_modules.resolution import add_resolution_columns


def _setup_db(path: Path) -> None:
    db = PredictionMarketDB(str(path))
    cur = db.conn.cursor()

    cur.execute(
        """
        INSERT OR REPLACE INTO polymarket_markets
        (id, slug, question, volume, volume_24hr, liquidity, end_date,
         outcomes, outcome_prices, token_id_yes, token_id_no)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "m1",
            "market-1",
            "Test market?",
            10000,
            5000,
            2000,
            "2026-01-10T00:00:00",
            json.dumps(["YES", "NO"]),
            json.dumps([0.5, 0.5]),
            "yes",
            "no",
        ),
    )

    prices = [
        ("m1", "YES", 1000, 0.55, "2026-01-01T00:16:40"),
        ("m1", "YES", 4600, 0.65, "2026-01-01T01:16:40"),
    ]
    cur.executemany(
        """
        INSERT INTO polymarket_prices
        (market_id, outcome, timestamp, price, datetime)
        VALUES (?, ?, ?, ?, ?)
        """,
        prices,
    )
    db.conn.commit()
    db.close()


def test_polymarket_backtest_executes_trade(tmp_path: Path) -> None:
    db_path = tmp_path / "pm.db"
    _setup_db(db_path)

    signals = pd.DataFrame(
        [
            {
                "market_id": "m1",
                "signal_time": pd.Timestamp(1000, unit="s"),
                "outcome": "YES",
                "side": "BUY",
                "size": 100,
            }
        ]
    )

    config = PolymarketBacktestConfig(
        strategy_name="test",
        position_size=100,
        starting_capital=1000,
        fill_latency_seconds=0,
        max_holding_seconds=3600,
        include_open=False,
    )
    result = run_polymarket_backtest(
        signals=signals,
        config=config,
        price_store=ClobPriceStore(db_path=str(db_path)),
    )

    assert len(result.trades_df) == 1
    assert result.trades_df.iloc[0]["status"] == "closed"


def test_polymarket_backtest_normalizes_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "pm2.db"
    _setup_db(db_path)

    signals = pd.DataFrame(
        [
            {
                "market": "m1",
                "timestamp": pd.Timestamp(1000, unit="s"),
                "side": "BUY",
            }
        ]
    )

    config = PolymarketBacktestConfig(
        strategy_name="test",
        position_size=50,
        starting_capital=1000,
        fill_latency_seconds=0,
        max_holding_seconds=3600,
        include_open=False,
    )
    result = run_polymarket_backtest(
        signals=signals,
        config=config,
        price_store=ClobPriceStore(db_path=str(db_path)),
    )

    assert len(result.trades_df) == 1


def test_polymarket_backtest_uses_resolution_data(tmp_path: Path) -> None:
    db_path = tmp_path / "pm3.db"
    _setup_db(db_path)
    add_resolution_columns(str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        UPDATE polymarket_markets
        SET closed = 1,
            closed_time = ?,
            resolution_outcome = ?,
            resolution_price_yes = ?,
            resolution_price_no = ?
        WHERE id = ?
        """,
        (2000, "YES", 1.0, 0.0, "m1"),
    )
    conn.commit()
    conn.close()

    signals = pd.DataFrame(
        [
            {
                "market_id": "m1",
                "signal_time": pd.Timestamp(1000, unit="s"),
                "outcome": "YES",
                "side": "BUY",
                "size": 100,
            }
        ]
    )

    config = PolymarketBacktestConfig(
        strategy_name="test",
        position_size=100,
        starting_capital=1000,
        fill_latency_seconds=0,
        max_holding_seconds=3600,
        include_open=False,
    )
    result = run_polymarket_backtest(
        signals=signals,
        config=config,
        price_store=ClobPriceStore(db_path=str(db_path)),
    )

    trade = result.trades_df.iloc[0]
    assert trade["exit_reason"] == "resolution"
    assert trade["exit_price"] == 1.0
    assert trade["exit_fill_ts"] == 2000

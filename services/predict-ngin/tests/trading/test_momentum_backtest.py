"""
Tests for momentum signal generation and backtest with QuantStats reporting.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from trading.data_modules.database import PredictionMarketDB
from trading.momentum_signals import (
    generate_momentum_signals,
    generate_momentum_signals_parquet,
    generate_momentum_signals_sqlite,
    signals_dataframe_to_backtest_format,
)
from trading.polymarket_backtest import (
    ClobPriceStore,
    PolymarketBacktestConfig,
    run_polymarket_backtest,
)
from trading.reporting import generate_quantstats_report


def _setup_db_with_24h_move(db_path: Path) -> None:
    """Create SQLite DB with prices that yield a >5%% 24h return for momentum signal."""
    db = PredictionMarketDB(str(db_path))
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
    # Day 0: price 0.50; Day 1 (86400s later): price 0.56 -> return_24h = 0.12
    prices = [
        ("m1", "YES", 1000, 0.50, "2026-01-01T00:16:40"),
        ("m1", "YES", 86400 + 1000, 0.56, "2026-01-02T00:16:40"),
        ("m1", "YES", 86400 * 2 + 1000, 0.54, "2026-01-03T00:16:40"),
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


def test_momentum_signals_sqlite_output_columns_and_threshold(tmp_path: Path) -> None:
    _setup_db_with_24h_move(tmp_path / "pm.db")
    db_path = str(tmp_path / "pm.db")

    signals = generate_momentum_signals_sqlite(
        db_path=db_path,
        start_date=None,
        end_date=None,
        threshold=0.05,
        eval_freq_hours=24,
        outcome="YES",
        position_size=100.0,
    )

    assert not signals.empty
    for col in ("market_id", "signal_time", "outcome", "side"):
        assert col in signals.columns, f"Missing column: {col}"
    assert signals["outcome"].iloc[0] == "YES"
    assert signals["side"].iloc[0] in ("BUY", "SELL")
    # We have one day with return 0.12 (BUY) and one with negative move (SELL possible)
    assert set(signals["side"].unique()).issubset({"BUY", "SELL"})


def test_signals_dataframe_to_backtest_format() -> None:
    df = pd.DataFrame([
        {"market_id": "m1", "signal_time": pd.Timestamp("2026-01-01"), "outcome": "YES", "side": "BUY"},
    ])
    out = signals_dataframe_to_backtest_format(df)
    assert "signal_ts" in out.columns or "signal_time" in out.columns
    assert list(out.columns) >= ["market_id", "signal_time", "outcome", "side"]


def test_momentum_backtest_produces_daily_returns(tmp_path: Path) -> None:
    _setup_db_with_24h_move(tmp_path / "pm.db")
    db_path = str(tmp_path / "pm.db")

    signals = generate_momentum_signals_sqlite(
        db_path=db_path,
        threshold=0.05,
        eval_freq_hours=24,
        position_size=100.0,
    )
    signals = signals_dataframe_to_backtest_format(signals)

    config = PolymarketBacktestConfig(
        strategy_name="Momentum",
        position_size=100,
        starting_capital=1000,
        fill_latency_seconds=0,
        max_holding_seconds=3600,
        include_open=False,
    )
    price_store = ClobPriceStore(db_path=db_path)
    result = run_polymarket_backtest(
        signals=signals,
        config=config,
        price_store=price_store,
    )
    price_store.close()

    assert result.daily_returns is not None
    assert hasattr(result.daily_returns, "index")


def test_generate_quantstats_report_runs_without_error(tmp_path: Path) -> None:
    _setup_db_with_24h_move(tmp_path / "pm.db")
    db_path = str(tmp_path / "pm.db")

    signals = generate_momentum_signals_sqlite(
        db_path=db_path,
        threshold=0.05,
        eval_freq_hours=24,
        position_size=100.0,
    )
    signals = signals_dataframe_to_backtest_format(signals)

    price_store = ClobPriceStore(db_path=db_path)
    result = run_polymarket_backtest(
        signals=signals,
        config=PolymarketBacktestConfig(
            strategy_name="Momentum",
            position_size=100,
            starting_capital=1000,
            fill_latency_seconds=0,
            max_holding_seconds=3600,
            include_open=False,
        ),
        price_store=price_store,
    )
    price_store.close()

    report_path = tmp_path / "quantstats_momentum.html"
    # May return False if too few data points; must not raise
    ok = generate_quantstats_report(
        result.daily_returns,
        str(report_path),
        title="Momentum Backtest Report",
    )
    # With 1–2 days we may get False; with enough days we get True
    assert isinstance(ok, bool)


def test_momentum_signals_parquet_columns_and_threshold(tmp_path: Path) -> None:
    """With a tiny parquet fixture, assert output columns and that side is BUY/SELL."""
    pl = pytest.importorskip("polars")

    pq_dir = tmp_path / "prices"
    pq_dir.mkdir(parents=True)
    # Two days: day0 price 0.50, day1 price 0.56 -> return 0.12 -> BUY
    base_ts = 1704067200  # 2024-01-01 00:00 UTC
    df = pl.DataFrame({
        "market_id": ["m1", "m1", "m1"],
        "outcome": ["YES", "YES", "YES"],
        "timestamp": [base_ts, base_ts + 86400, base_ts + 86400 * 2],
        "price": [0.50, 0.56, 0.54],
    })
    df.write_parquet(pq_dir / "prices_2024-01.parquet")

    signals = generate_momentum_signals_parquet(
        parquet_dir=str(pq_dir),
        start_date="2024-01-01",
        end_date="2024-01-05",
        threshold=0.05,
        eval_freq_hours=24,
        outcome="YES",
        position_size=50.0,
    )

    assert not signals.empty
    for col in ("market_id", "signal_time", "outcome", "side"):
        assert col in signals.columns
    assert signals["side"].iloc[0] in ("BUY", "SELL")

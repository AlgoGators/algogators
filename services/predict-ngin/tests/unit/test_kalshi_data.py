"""Tests for Kalshi data normalisation: timestamps, candles, derived prices, repair."""

import datetime as dt
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.trading.arbitrage.kalshi_data import (
    candle_to_row,
    derive_daily_candles,
    parse_created_time_to_unix,
    recompute_timestamp_unix,
)

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 2025-01-01 12:34:16-05:00 == 17:34:16Z
TS_OFFSET = int(pd.Timestamp("2025-01-01T17:34:16Z").timestamp())


class TestCreatedTimeParsing:
    def test_offset_minus_05(self):
        assert parse_created_time_to_unix("2025-01-01 12:34:16.788024-05") == TS_OFFSET

    def test_offset_minus_04(self):
        assert parse_created_time_to_unix("2025-07-01 08:00:00-04") == int(
            pd.Timestamp("2025-07-01T12:00:00Z").timestamp()
        )

    def test_zulu(self):
        assert parse_created_time_to_unix("2025-01-01T17:34:16.1Z") == TS_OFFSET

    def test_bad_returns_none(self):
        assert parse_created_time_to_unix("") is None
        assert parse_created_time_to_unix(None) is None

    def test_vectorised_recompute_mixed_formats(self):
        df = pd.DataFrame(
            {
                "created_time": [
                    "2025-01-01 12:34:16.788024-05",
                    "2025-01-01T17:34:16.096594Z",
                    "2025-07-01 08:00:00-04",
                ],
                # original buggy values: offset dropped (5h / 4h early)
                "timestamp_unix": [TS_OFFSET - 5 * 3600, TS_OFFSET, 0],
            }
        )
        out = recompute_timestamp_unix(df)
        assert out.tolist() == [
            TS_OFFSET,
            TS_OFFSET,
            int(pd.Timestamp("2025-07-01T12:00:00Z").timestamp()),
        ]


class TestDownloadSqlUsesTimestamptz:
    """download_kalshi_hf must not drop UTC offsets (epoch(x::timestamp) bug)."""

    def test_trades_sql_handles_offsets(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        mod = _load_script("download_kalshi_hf", "scripts/data/download_kalshi_hf.py")
        src = tmp_path / "trades-0.parquet"
        pd.DataFrame(
            {
                "trade_id": ["a", "b"],
                "ticker": ["T", "T"],
                "count": [1, 2],
                "yes_price": [40, 41],
                "no_price": [60, 59],
                "taker_side": ["yes", "no"],
                "created_time": ["2025-01-01 12:34:16.788024-05", "2025-01-01T17:34:16Z"],
            }
        ).to_parquet(src)
        con = duckdb.connect()
        mod._configure_duckdb_utc(con)
        df = con.execute(mod.trades_month_sql(str(src).replace("\\", "/")), ["2025-01"]).fetchdf()
        assert sorted(df["timestamp_unix"].tolist()) == [TS_OFFSET, TS_OFFSET]

    def test_trades_sql_with_native_timestamptz(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        mod = _load_script("download_kalshi_hf", "scripts/data/download_kalshi_hf.py")
        con = duckdb.connect()
        con.execute("SET TimeZone='America/New_York'")  # hostile session tz
        src = str(tmp_path / "t.parquet").replace("\\", "/")
        con.execute(
            f"""copy (select 'a' trade_id, 'T' ticker, 1 count, 40 yes_price, 60 no_price, 'yes' taker_side,
                 TIMESTAMPTZ '2025-01-01 17:34:16+00' created_time) to '{src}' (format parquet)"""
        )
        mod._configure_duckdb_utc(con)
        df = con.execute(mod.trades_month_sql(src), ["2025-01"]).fetchdf()
        assert df["timestamp_unix"].tolist() == [TS_OFFSET]


class TestDeriveDailyCandles:
    def test_timestamp_is_real_epoch_not_one(self):
        day = pd.Timestamp("2025-03-04T00:00:00Z")
        t0 = int(day.timestamp())
        trades = pd.DataFrame(
            {
                "ticker": ["X", "X", "X"],
                "timestamp_unix": [t0 + 100, t0 + 5000, t0 + 86400 + 10],
                "yes_price": [0.40, 0.45, 0.50],
                "count": [10, 20, 5],
            }
        )
        out = derive_daily_candles(trades).sort_values("timestamp").reset_index(drop=True)
        assert (out["timestamp"] > 1_000_000_000).all()
        # Candle stamped at period END (close is only known then)
        assert out.loc[0, "timestamp"] == t0 + 86400
        assert out.loc[0, "period_start_ts"] == t0
        assert out.loc[0, "close"] == pytest.approx(0.45)
        assert out.loc[0, "volume"] == 30
        assert out.loc[1, "timestamp"] == t0 + 2 * 86400

    def test_download_script_uses_fixed_derivation(self, tmp_path):
        mod = _load_script("download_kalshi_hf", "scripts/data/download_kalshi_hf.py")
        tdir = tmp_path / "trades"
        tdir.mkdir()
        t0 = int(pd.Timestamp("2025-03-04T00:00:00Z").timestamp())
        pd.DataFrame(
            {
                "trade_id": ["a"],
                "ticker": ["X"],
                "event_ticker": ["E"],
                "yes_price": [0.4],
                "no_price": [0.6],
                "count": [3],
                "taker_side": ["yes"],
                "created_time": ["2025-03-04T00:01:40Z"],
                "timestamp_unix": [t0 + 100],
            }
        ).to_parquet(tdir / "trades_2025-03.parquet")
        mod._derive_prices_from_trades(tmp_path)
        p = pd.read_parquet(tmp_path / "prices" / "prices_2025-03.parquet")
        assert p["timestamp"].tolist() == [t0 + 86400]


class TestCandleToRow:
    def test_cents_payload(self):
        c = {
            "end_period_ts": 1739336400,
            "price": {"open": 40, "high": 45, "low": 39, "close": 44, "mean": 42},
            "yes_bid": {"close": 43},
            "yes_ask": {"close": 45},
            "volume": 120,
            "open_interest": 999,
        }
        r = candle_to_row(c, "T", "E")
        assert r["timestamp"] == 1739336400
        assert r["close"] == pytest.approx(0.44)
        assert r["yes_bid_close"] == pytest.approx(0.43)
        assert r["yes_ask_close"] == pytest.approx(0.45)
        assert r["volume"] == 120

    def test_empty_candle_is_nan_not_zero(self):
        c = {"end_period_ts": 1739336400, "price": {}, "volume": 0, "open_interest": 5}
        r = candle_to_row(c, "T", "E")
        assert math.isnan(r["close"])
        assert math.isnan(r["open"])

    def test_dollar_string_fields(self):
        c = {
            "end_period_ts": 1739336400,
            "price": {"close_dollars": "0.4400", "open_dollars": "0.4000"},
            "volume": 3,
        }
        r = candle_to_row(c, "T", "E")
        assert r["close"] == pytest.approx(0.44)
        assert r["open"] == pytest.approx(0.40)

    def test_iso_end_period(self):
        c = {"end_period_ts": "2025-02-12T05:00:00Z", "price": {"close": 50}}
        assert candle_to_row(c, "T", "E")["timestamp"] == 1739336400

    def test_missing_end_period_returns_none(self):
        assert candle_to_row({"price": {"close": 50}}, "T", "E") is None
        assert candle_to_row({"end_period_ts": 0, "price": {"close": 50}}, "T", "E") is None


class TestRepairScript:
    def test_repair_writes_new_dir_and_keeps_original(self, tmp_path):
        mod = _load_script("repair_kalshi_trades", "scripts/data/repair_kalshi_trades.py")
        src = tmp_path / "trades"
        dst = tmp_path / "trades_fixed"
        src.mkdir()
        orig = pd.DataFrame(
            {
                "trade_id": ["a", "b"],
                "ticker": ["T", "T"],
                "created_time": ["2025-01-01 12:34:16.788024-05", "2025-01-01T17:34:16Z"],
                "timestamp_unix": [TS_OFFSET - 5 * 3600, TS_OFFSET],
            }
        )
        orig.to_parquet(src / "trades_2025-01.parquet")
        stats = mod.repair_dir(src, dst)
        fixed = pd.read_parquet(dst / "trades_2025-01.parquet")
        assert fixed["timestamp_unix"].tolist() == [TS_OFFSET, TS_OFFSET]
        assert "timestamp_unix_orig" in fixed.columns
        # original untouched
        again = pd.read_parquet(src / "trades_2025-01.parquet")
        assert again["timestamp_unix"].tolist() == orig["timestamp_unix"].tolist()
        assert stats["rows"] == 2 and stats["changed"] == 1

    def test_repair_refuses_in_place(self, tmp_path):
        mod = _load_script("repair_kalshi_trades", "scripts/data/repair_kalshi_trades.py")
        (tmp_path / "trades").mkdir()
        with pytest.raises(ValueError):
            mod.repair_dir(tmp_path / "trades", tmp_path / "trades")


class TestFetcherKalshiWorker:
    """DataFetcher._fetch_single_kalshi_market must parse candles/trades correctly."""

    def _run(self, monkeypatch, candles, trades):
        import threading
        from collections import defaultdict

        from src.trading.data_modules.fetcher import DataFetcher

        f = DataFetcher.__new__(DataFetcher)
        monkeypatch.setattr(f, "fetch_kalshi_candlesticks", lambda **kw: candles, raising=False)
        monkeypatch.setattr(f, "fetch_kalshi_trades", lambda **kw: trades, raising=False)
        mt, mp = defaultdict(list), defaultdict(list)
        progress = {"done": 0, "trades": 0, "candles": 0, "errors": 0, "start_time": 0, "total": 1}
        ok = f._fetch_single_kalshi_market(
            {"ticker": "T", "series_ticker": "S", "event_ticker": "E"},
            progress, threading.Lock(), mt, mp, 0, True, True,
        )
        return ok, mt, mp

    def test_empty_candle_stored_as_nan_and_ts_correct(self, monkeypatch):
        candles = [
            {"end_period_ts": 1739336400, "price": {}, "volume": 0, "open_interest": 1},
            {"end_period_ts": 1739422800, "price": {"close": 55, "open": 50, "high": 56, "low": 49}, "volume": 4},
            {"price": {"close": 55}},  # no end_period_ts -> skipped, not ts=0
        ]
        ok, _, mp = self._run(monkeypatch, candles, [])
        assert ok
        rows = [r for v in mp.values() for r in v]
        assert len(rows) == 2
        rows.sort(key=lambda r: r["timestamp"])
        assert rows[0]["timestamp"] == 1739336400
        assert math.isnan(rows[0]["close"])
        assert rows[1]["close"] == pytest.approx(0.55)
        assert "unknown" not in mp

    def test_trade_timestamp_respects_offset_and_utc_month(self, monkeypatch):
        trades = [{"trade_id": "x", "ticker": "T", "yes_price": 40, "no_price": 60, "count": 1,
                   "taker_side": "yes", "created_time": "2025-01-31 22:30:00-05"}]
        ok, mt, _ = self._run(monkeypatch, [], trades)
        rows = [r for v in mt.values() for r in v]
        assert rows[0]["timestamp_unix"] == int(pd.Timestamp("2025-02-01T03:30:00Z").timestamp())
        assert "2025-02" in mt  # bucketed by UTC month

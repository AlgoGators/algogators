"""Tests for CrossPlatformPriceStore / KalshiPriceStore integrity fixes."""

import logging

import numpy as np
import pandas as pd
import pytest

from src.trading.arbitrage.cross_platform_price_store import (
    CrossPlatformPriceStore,
    KalshiPriceStore,
    resample_last_no_lookahead,
)

DAY = 86400
T0 = int(pd.Timestamp("2025-03-01T00:00:00Z").timestamp())


def _write_kalshi(tmp_path, rows):
    d = tmp_path / "kalshi"
    d.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_parquet(d / "prices_2025-03.parquet")
    return d


class TestKalshiStoreSkipsBadTimestamps:
    def test_rows_with_ts_le_1_are_dropped_and_counted(self, tmp_path, caplog):
        d = _write_kalshi(
            tmp_path,
            {
                "ticker": ["A", "A", "A", "A"],
                "timestamp": [1, 1, T0 + DAY, T0 + 2 * DAY],
                "close": [0.3, 0.4, 0.5, 0.6],
            },
        )
        store = KalshiPriceStore(str(d))
        with caplog.at_level(logging.WARNING):
            hist = store.get_price_history("A")
        assert hist["timestamp"].tolist() == [T0 + DAY, T0 + 2 * DAY]
        assert store.dropped_bad_timestamp == 2
        assert any("timestamp" in r.message for r in caplog.records)

    def test_zero_close_from_empty_candle_not_used_as_price(self, tmp_path):
        d = _write_kalshi(
            tmp_path,
            {"ticker": ["A", "A"], "timestamp": [T0 + DAY, T0 + 2 * DAY], "close": [0.5, np.nan]},
        )
        store = KalshiPriceStore(str(d))
        p = store.price_at_or_before("A", T0 + 2 * DAY + 10)
        assert p["price"] == pytest.approx(0.5)


class TestResampleNoLookahead:
    def test_daily_close_labelled_at_end_of_day(self):
        # Two intraday observations on day 1, one on day 2
        idx = pd.to_datetime([T0 + 3600, T0 + 20 * 3600, T0 + DAY + 3600], unit="s", utc=True)
        s = pd.Series([0.1, 0.2, 0.3], index=idx)
        out = resample_last_no_lookahead(s, "1D", ffill_limit=2)
        # The 0.2 close of day 1 must be labelled at the end of day 1 (start of day 2)
        assert out.loc[pd.Timestamp(T0 + DAY, unit="s", tz="UTC")] == pytest.approx(0.2)
        # Nothing may be known at the start of day 1
        assert pd.Timestamp(T0, unit="s", tz="UTC") not in out.index or np.isnan(
            out.loc[pd.Timestamp(T0, unit="s", tz="UTC")]
        )

    def test_value_at_label_never_from_future(self):
        rng = np.random.default_rng(0)
        ts = np.sort(rng.integers(T0, T0 + 10 * DAY, 200))
        s = pd.Series(rng.random(200), index=pd.to_datetime(ts, unit="s", utc=True))
        out = resample_last_no_lookahead(s, "6h", ffill_limit=4).dropna()
        for label, val in out.items():
            known = s[s.index <= label]
            assert len(known) > 0
            # value must equal some observation at or before the label
            assert val in set(known.values)

    def test_ffill_is_limited(self):
        idx = pd.to_datetime([T0 + 3600, T0 + 10 * DAY + 3600], unit="s", utc=True)
        s = pd.Series([0.4, 0.6], index=idx)
        out = resample_last_no_lookahead(s, "1D", ffill_limit=2)
        filled = out.loc[: pd.Timestamp(T0 + 9 * DAY, unit="s", tz="UTC")]
        assert filled.notna().sum() == 3  # the close + 2 forward fills
        assert filled.isna().sum() >= 5


class TestBuildSpreadSeries:
    def test_spread_series_uses_end_labels(self, tmp_path):
        kd = _write_kalshi(
            tmp_path,
            {"ticker": ["K"] * 2, "timestamp": [T0 + DAY, T0 + 2 * DAY], "close": [0.40, 0.50]},
        )
        store = CrossPlatformPriceStore(
            polymarket_prices_dir=str(tmp_path / "pm"), kalshi_prices_dir=str(kd)
        )
        poly = pd.DataFrame(
            {"timestamp": [T0 + DAY - 60, T0 + 2 * DAY - 60], "price": [0.45, 0.52]}
        )
        store.get_polymarket_history = lambda *_a, **_k: poly.copy()
        out = store.build_spread_series("P", "K", resample_freq="1D")
        # every row's timestamp >= source observation times used
        assert (out["timestamp_unix"] >= T0 + DAY).all()
        assert store.max_ffill_periods >= 1

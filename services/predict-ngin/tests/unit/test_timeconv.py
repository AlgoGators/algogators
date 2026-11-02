"""Unit-safe epoch conversion (pandas 3 defaults to datetime64[us] for parsed strings)."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src.timeconv import to_epoch, to_epoch_seconds

T = 1735689600  # 2025-01-01T00:00:00Z


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_series_any_resolution(unit):
    s = pd.Series(pd.to_datetime([T, T + 61], unit="s", utc=True)).astype(f"datetime64[{unit}, UTC]")
    assert s.dtype.unit == unit
    assert to_epoch_seconds(s).tolist() == [T, T + 61]
    assert to_epoch(s, "ms").tolist() == [T * 1000, (T + 61) * 1000]


def test_old_idiom_is_wrong_for_us_but_helper_is_right():
    s = pd.to_datetime(pd.Series(["2025-01-01T00:00:00Z"]), utc=True, format="ISO8601")
    if s.dtype.unit != "ns":  # pandas >= 3: string parsing gives [us]
        assert (s.astype("int64") // 10**9).iloc[0] == T // 1000
    assert to_epoch_seconds(s).iloc[0] == T


def test_naive_taken_as_utc_and_aware_converted():
    naive = pd.Series([dt.datetime(2025, 1, 1)])
    aware = pd.Series(pd.to_datetime(["2024-12-31T19:00:00-05:00"], format="ISO8601"))
    assert to_epoch_seconds(naive).iloc[0] == T
    assert to_epoch_seconds(aware).iloc[0] == T


def test_strings_and_nat():
    s = pd.Series(["2025-01-01 00:00:01", None])
    out = to_epoch_seconds(s)
    assert str(out.dtype) == "Int64"
    assert out.iloc[0] == T + 1 and pd.isna(out.iloc[1])


def test_floor_of_subsecond():
    s = pd.to_datetime(pd.Series(["2025-01-01T00:00:00.999999Z"]), utc=True, format="ISO8601")
    assert to_epoch_seconds(s).iloc[0] == T
    assert to_epoch(s, "us").iloc[0] == T * 10**6 + 999999


def test_index_and_scalars():
    idx = pd.DatetimeIndex(pd.to_datetime([T], unit="s")).as_unit("ms")
    assert list(to_epoch(idx, "ms")) == [T * 1000]
    assert to_epoch_seconds(pd.Timestamp("2025-01-01", tz="UTC")) == T
    assert to_epoch_seconds("2025-01-01T00:00:00Z") == T
    assert to_epoch_seconds(np.datetime64("2025-01-01T00:00:00", "s")) == T
    assert to_epoch_seconds(pd.NaT) is None


def test_numeric_rejected():
    with pytest.raises(TypeError):
        to_epoch_seconds(pd.Series([T]))
    with pytest.raises(TypeError):
        to_epoch_seconds(T)


# --- regression: call sites that used ``astype("int64") // 10**9`` -------------------------------

def test_unix_seconds_helpers_in_trading_modules():
    # (src/trading/execution.py has the same helper but is shadowed by the src/trading/execution/ package)
    from src.trading.data_modules.costs import _to_unix_seconds as c_conv

    s = pd.Series(pd.to_datetime(["2025-01-01 00:00:00"]))  # pandas 3: datetime64[us]
    assert c_conv(s).iloc[0] == T
    assert c_conv(pd.Series([T * 1000])).iloc[0] == T  # numeric ms path unchanged


def test_tick_store_resample_returns_ms():
    from src.trading.data_modules.tick_store import TickStore

    ticks = pd.DataFrame({"timestamp": [T * 1000, T * 1000 + 30_000, T * 1000 + 90_000],
                          "price": [0.5, 0.6, 0.55], "size": [1.0, 2.0, 3.0]})
    out = TickStore.resample_ohlcv(TickStore.__new__(TickStore), ticks, "1min")
    assert out["timestamp"].tolist() == [T * 1000, T * 1000 + 60_000]

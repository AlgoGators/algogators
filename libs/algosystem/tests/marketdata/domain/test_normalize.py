import pandas as pd
import pytest
from algosystem.marketdata.domain.normalize import normalize_price_series
from algosystem.shared.errors import MarketDataError
from algosystem.shared.values import DateRange

MESSAGES = {
    "index_error": "index message",
    "dtype_error": "dtype message",
    "empty_error": "empty message",
}


def test_normalize_sorts_coerces_and_drops_nans():
    prices = pd.Series(
        ["102", None, "100"],
        index=pd.to_datetime(["2020-01-03", "2020-01-02", "2020-01-01"]),
    )

    result = normalize_price_series(prices, **MESSAGES)

    assert result.tolist() == [100.0, 102.0]
    assert result.index.tolist() == list(pd.to_datetime(["2020-01-01", "2020-01-03"]))
    assert result.dtype == float


def test_normalize_does_not_mutate_the_input():
    prices = pd.Series([102.0, 100.0], index=pd.to_datetime(["2020-01-02", "2020-01-01"]))

    normalize_price_series(prices, **MESSAGES)

    assert prices.tolist() == [102.0, 100.0]


def test_normalize_converts_tz_aware_index_to_naive():
    index = pd.date_range("2020-01-01", periods=3, tz="US/Eastern")
    prices = pd.Series([100.0, 101.0, 102.0], index=index)

    result = normalize_price_series(prices, **MESSAGES)

    assert result.index.tz is None
    assert len(result) == 3


def test_normalize_slices_to_the_requested_date_range():
    prices = pd.Series(
        [100.0, 101.0, 102.0, 103.0],
        index=pd.date_range("2020-01-01", periods=4),
    )
    date_range = DateRange(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03"))

    result = normalize_price_series(prices, date_range=date_range, **MESSAGES)

    assert result.tolist() == [101.0, 102.0]


def test_normalize_rejects_non_datetime_index():
    prices = pd.Series([100.0, 101.0], index=[0, 1])

    with pytest.raises(MarketDataError, match="index message"):
        normalize_price_series(prices, **MESSAGES)


def test_normalize_rejects_non_numeric_prices():
    prices = pd.Series(["abc", "def"], index=pd.date_range("2020-01-01", periods=2))

    with pytest.raises(MarketDataError, match="dtype message"):
        normalize_price_series(prices, **MESSAGES)


def test_normalize_rejects_all_nan_prices():
    prices = pd.Series([None, None], index=pd.date_range("2020-01-01", periods=2))

    with pytest.raises(MarketDataError, match="empty message"):
        normalize_price_series(prices, **MESSAGES)


def test_normalize_rejects_range_with_no_prices():
    prices = pd.Series([100.0, 101.0], index=pd.date_range("2020-01-01", periods=2))
    date_range = DateRange(pd.Timestamp("2021-01-01"), pd.Timestamp("2021-01-02"))

    with pytest.raises(MarketDataError, match="empty message"):
        normalize_price_series(prices, date_range=date_range, **MESSAGES)

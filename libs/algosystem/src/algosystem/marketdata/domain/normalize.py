"""Shared price-series normalization for market-data adapters."""

from __future__ import annotations

import pandas as pd

from algosystem.shared.errors import MarketDataError
from algosystem.shared.values import DateRange


def normalize_price_series(
    prices: pd.Series,
    *,
    date_range: DateRange | None = None,
    index_error: str,
    dtype_error: str,
    empty_error: str,
) -> pd.Series:
    """Return a sorted, tz-naive, float, NaN-free copy of a price series.

    The series is optionally restricted to ``date_range``. A ``MarketDataError``
    is raised with the caller-supplied message when the index is not a
    ``DatetimeIndex`` (``index_error``), when values cannot be coerced to float
    (``dtype_error``), or when no usable prices remain (``empty_error``).
    """
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise MarketDataError(index_error)
    series = prices.copy().sort_index()
    if series.index.tz is not None:
        series.index = series.index.tz_convert(None)
    try:
        series = series.astype(float)
    except (TypeError, ValueError) as exc:
        raise MarketDataError(dtype_error) from exc
    series = series.dropna()
    if date_range is not None:
        series = series.loc[date_range.mask(series.index)]
    if series.empty:
        raise MarketDataError(empty_error)
    return series


__all__ = ["normalize_price_series"]

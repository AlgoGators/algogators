"""YFinance benchmark provider adapter."""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from algosystem.marketdata.domain.benchmark import Ticker
from algosystem.marketdata.domain.normalize import normalize_price_series
from algosystem.shared.errors import MarketDataError
from algosystem.shared.values import DateRange


class YFinanceBenchmarkProvider:
    """Benchmark provider backed by yfinance."""

    def fetch(self, ticker: Ticker, date_range: DateRange) -> pd.Series:
        """Fetch adjusted close prices from yfinance."""
        try:
            data = yf.download(
                ticker.value,
                start=date_range.start,
                end=date_range.end + pd.Timedelta(days=1),
                progress=False,
            )
            prices = _select_price_series(data, ticker)
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(f"failed to fetch benchmark data for {ticker.value}") from exc

        prices = prices.loc[date_range.mask(prices.index)]
        if prices.empty:
            raise MarketDataError(f"no benchmark data returned for {ticker.value}")
        prices.name = ticker.value
        return prices


def _select_price_series(data: pd.DataFrame, ticker: Ticker) -> pd.Series:
    if data.empty:
        raise MarketDataError(f"no benchmark data returned for {ticker.value}")

    if isinstance(data.columns, pd.MultiIndex):
        for field in ("Adj Close", "Close"):
            if field in data.columns.get_level_values(0):
                selected = data[field]
                if isinstance(selected, pd.DataFrame):
                    return _normalize(selected.iloc[:, 0], ticker)
                return _normalize(selected, ticker)
        raise MarketDataError(f"yfinance response has no close prices for {ticker.value}")

    for field in ("Adj Close", "Close"):
        if field in data.columns:
            return _normalize(data[field], ticker)
    raise MarketDataError(f"yfinance response has no close prices for {ticker.value}")


def _normalize(prices: pd.Series, ticker: Ticker) -> pd.Series:
    return normalize_price_series(
        prices,
        index_error=f"yfinance response index is not datetime for {ticker.value}",
        dtype_error=f"yfinance returned non-numeric prices for {ticker.value}",
        empty_error=f"no usable benchmark prices returned for {ticker.value}",
    )


__all__ = ["YFinanceBenchmarkProvider"]

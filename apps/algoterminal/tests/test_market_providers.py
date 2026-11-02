"""Tests for the yfinance and Stooq providers with all network I/O stubbed."""

from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace

import pandas as pd
import requests
from algoterminal.data import cache
from algoterminal.data.provider import AssetClass
from algoterminal.data.stooq_provider import StooqProvider, _to_stooq_symbol
from algoterminal.data.yfinance_provider import YFinanceProvider

from .conftest import StubResponse

START = date(2024, 1, 1)
END = date(2024, 1, 10)


def _yahoo_frame(start: str = "2024-01-01", days: int = 10) -> pd.DataFrame:
    index = pd.date_range(start, periods=days, name="Date")
    return pd.DataFrame(
        {
            "Open": 1.0,
            "High": 2.0,
            "Low": 0.5,
            "Close": 1.5,
            "Adj Close": 1.4,
            "Volume": 100,
        },
        index=index,
    )


def _stub_yf_download(monkeypatch, frames):
    """`frames` is a list of DataFrames returned by successive download calls."""
    calls = []

    def download(symbol, start=None, end=None, **kwargs):
        calls.append((symbol, start, end))
        return frames[min(len(calls) - 1, len(frames) - 1)].copy()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    return calls


class TestYFinanceProvider:
    def test_fetch_normalizes_and_caches(self, monkeypatch):
        calls = _stub_yf_download(monkeypatch, [_yahoo_frame()])
        provider = YFinanceProvider()

        frame = provider.fetch("AAA", AssetClass.EQUITY, START, END)
        assert list(frame.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
        assert frame.index.name == "date"
        assert len(calls) == 1
        assert cache.read("yfinance", "AAA") is not None

        # fully cached range: no second download
        provider.fetch("AAA", AssetClass.EQUITY, START, END)
        assert len(calls) == 1

    def test_fetch_extends_cache_incrementally(self, monkeypatch):
        calls = _stub_yf_download(
            monkeypatch, [_yahoo_frame("2024-01-01", 10), _yahoo_frame("2024-01-11", 5)]
        )
        provider = YFinanceProvider()
        provider.fetch("AAA", AssetClass.EQUITY, START, END)
        extended = provider.fetch("AAA", AssetClass.EQUITY, START, date(2024, 1, 15))
        assert len(calls) == 2
        # the second download only asked for the missing tail
        assert calls[1][1] == date(2024, 1, 11)
        assert len(extended) == 15

    def test_empty_download(self, monkeypatch):
        _stub_yf_download(monkeypatch, [pd.DataFrame()])
        provider = YFinanceProvider()
        assert provider.fetch("NOPE", AssetClass.EQUITY, START, END).empty

    def test_default_date_range(self, monkeypatch):
        calls = _stub_yf_download(monkeypatch, [_yahoo_frame()])
        YFinanceProvider().fetch("AAA", AssetClass.EQUITY)
        (_, start, _end) = calls[0]
        assert (date.today() - start).days >= 3 * 365


_STOOQ_CSV = (
    "Date,Open,High,Low,Close,Volume\n2024-01-02,1,2,0.5,1.5,100\n2024-01-03,1,2,0.5,1.6,110\n"
)


class TestStooqSymbolMapping:
    def test_fx(self):
        assert _to_stooq_symbol("EURUSD=X", AssetClass.FX) == "eurusd"

    def test_crypto(self):
        assert _to_stooq_symbol("BTC-USD", AssetClass.CRYPTO) == "btcusd"

    def test_us_equity(self):
        assert _to_stooq_symbol("AAPL", AssetClass.EQUITY) == "aapl.us"

    def test_equity_with_suffix_passthrough(self):
        assert _to_stooq_symbol("BMW.DE", AssetClass.EQUITY) == "bmw.de"

    def test_future_passthrough(self):
        assert _to_stooq_symbol("CL=F", AssetClass.FUTURE) == "cl=f"


class TestStooqProvider:
    def test_fetch_parses_csv(self, monkeypatch):
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen["params"] = params
            return StubResponse(text=_STOOQ_CSV)

        monkeypatch.setattr(requests, "get", fake_get)
        provider = StooqProvider()
        frame = provider.fetch("AAPL", AssetClass.EQUITY, START, END)
        assert seen["params"]["s"] == "aapl.us"
        assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
        assert len(frame) == 2
        assert cache.read("stooq", "AAPL") is not None

    def test_no_data_response(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **k: StubResponse(text="No data"))
        assert StooqProvider().fetch("AAPL", AssetClass.EQUITY, START, END).empty

    def test_rate_limited_response(self, monkeypatch):
        monkeypatch.setattr(
            requests,
            "get",
            lambda *a, **k: StubResponse(text="Exceeded the daily hits limit"),
        )
        assert StooqProvider()._download("aapl.us", START, END).empty

    def test_http_error(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **k: StubResponse(status_code=503))
        assert StooqProvider()._download("aapl.us", START, END).empty

    def test_request_exception(self, monkeypatch):
        def fake_get(*a, **k):
            raise requests.ConnectionError("nope")

        monkeypatch.setattr(requests, "get", fake_get)
        assert StooqProvider()._download("aapl.us", START, END).empty

    def test_csv_without_date_column(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **k: StubResponse(text="foo,bar\n1,2\n"))
        assert StooqProvider()._download("aapl.us", START, END).empty

    def test_empty_body(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **k: StubResponse(text=""))
        assert StooqProvider()._download("aapl.us", START, END).empty

    def test_cached_range_skips_download(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda *a, **k: StubResponse(text=_STOOQ_CSV))
        provider = StooqProvider()
        provider.fetch("AAPL", AssetClass.EQUITY, date(2024, 1, 2), date(2024, 1, 3))

        def exploding_get(*a, **k):
            raise AssertionError("should have been served from cache")

        monkeypatch.setattr(requests, "get", exploding_get)
        frame = provider.fetch("AAPL", AssetClass.EQUITY, date(2024, 1, 2), date(2024, 1, 3))
        assert len(frame) == 2

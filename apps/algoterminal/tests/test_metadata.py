"""Tests for the instrument metadata cache (yfinance stubbed, never hit)."""

from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace

import pytest
from algoterminal.data import metadata


class _StubTicker:
    def __init__(self, info):
        self.info = info


def _stub_yfinance(monkeypatch, info, calls=None):
    def ticker(symbol):
        if calls is not None:
            calls.append(symbol)
        return _StubTicker(info)

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=ticker))


class TestInstrumentInfo:
    def test_staleness(self):
        fresh = metadata.InstrumentInfo(symbol="AAA", fetched_at=time.time())
        stale = metadata.InstrumentInfo(symbol="AAA", fetched_at=0.0)
        assert not fresh.is_stale
        assert stale.is_stale


class TestPeek:
    def test_peek_missing_returns_none(self):
        assert metadata.peek_metadata("AAA") is None

    def test_peek_returns_cached_even_if_stale(self, monkeypatch):
        _stub_yfinance(monkeypatch, {"shortName": "Aaa Corp"})
        metadata.get_instrument_info("AAA")
        info = metadata.peek_metadata("AAA")
        assert info is not None
        assert info.name == "Aaa Corp"


class TestGetInstrumentInfo:
    def test_fetches_and_caches(self, monkeypatch, isolated_home):
        calls: list[str] = []
        _stub_yfinance(
            monkeypatch,
            {
                "shortName": "Aaa Corp",
                "sector": "Tech",
                "exchange": "NYSE",
                "currency": "USD",
            },
            calls,
        )
        info = metadata.get_instrument_info("AAA")
        assert info.name == "Aaa Corp"
        assert info.sector == "Tech"
        assert info.exchange == "NYSE"
        assert info.currency == "USD"
        assert info.fetched_at > 0
        assert (isolated_home / "instrument_metadata.json").exists()

        # second call is served from cache without touching yfinance
        again = metadata.get_instrument_info("AAA")
        assert again.name == "Aaa Corp"
        assert calls == ["AAA"]

    def test_stale_entry_refetched(self, monkeypatch):
        calls: list[str] = []
        _stub_yfinance(monkeypatch, {"longName": "Aaa Corporation"}, calls)
        stale = metadata.InstrumentInfo(symbol="AAA", name="Old", fetched_at=0.0)
        metadata._save_store({"AAA": stale.__dict__})

        info = metadata.get_instrument_info("AAA")
        assert info.name == "Aaa Corporation"
        assert calls == ["AAA"]

    def test_refresh_forces_refetch(self, monkeypatch):
        calls: list[str] = []
        _stub_yfinance(monkeypatch, {"shortName": "New Name"}, calls)
        fresh = metadata.InstrumentInfo(symbol="AAA", name="Old", fetched_at=time.time())
        metadata._save_store({"AAA": fresh.__dict__})

        info = metadata.get_instrument_info("AAA", refresh=True)
        assert info.name == "New Name"
        assert calls == ["AAA"]

    def test_yfinance_failure_yields_blank_info(self, monkeypatch):
        def broken_ticker(symbol):
            raise RuntimeError("rate limited")

        monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=broken_ticker))
        info = metadata.get_instrument_info("AAA")
        assert info.symbol == "AAA"
        assert info.name == ""
        assert info.fetched_at > 0

    def test_category_fallback_for_sector(self, monkeypatch):
        _stub_yfinance(monkeypatch, {"longName": "A Fund", "category": "Bond Fund"})
        info = metadata.get_instrument_info("AAA")
        assert info.name == "A Fund"
        assert info.sector == "Bond Fund"


class TestStoreIO:
    def test_corrupt_store_treated_as_empty(self, monkeypatch, isolated_home):
        isolated_home.mkdir(parents=True, exist_ok=True)
        (isolated_home / "instrument_metadata.json").write_text("{ not json", encoding="utf-8")
        assert metadata._load_store() == {}

    def test_save_store_writes_json(self, isolated_home):
        metadata._save_store({"AAA": {"symbol": "AAA"}})
        raw = json.loads((isolated_home / "instrument_metadata.json").read_text(encoding="utf-8"))
        assert raw == {"AAA": {"symbol": "AAA"}}


@pytest.mark.parametrize(
    ("raw", "expected_name"),
    [
        ({"shortName": "Short"}, "Short"),
        ({"longName": "Long"}, "Long"),
        ({}, ""),
    ],
)
def test_name_preference(monkeypatch, raw, expected_name):
    _stub_yfinance(monkeypatch, raw)
    assert metadata.get_instrument_info("ZZZ").name == expected_name

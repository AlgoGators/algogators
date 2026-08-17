"""Tests for the alt-data providers and their registry (all HTTP stubbed)."""

from __future__ import annotations

from datetime import date
from typing import ClassVar

import pandas as pd
import pytest
import requests
from algoterminal.data import cache
from algoterminal.data.altdata.fear_greed import FearGreedProvider
from algoterminal.data.altdata.fred import FredProvider
from algoterminal.data.altdata.nasa_power import NasaPowerProvider
from algoterminal.data.altdata.registry import (
    SOURCE_DESCRIPTIONS,
    describe_symbol,
    provider_for_source,
)
from algoterminal.data.altdata.usgs_earthquake import UsgsEarthquakeProvider
from algoterminal.data.altdata.wiki_pageviews import WikiPageviewsProvider
from algoterminal.data.altdata.world_bank import WorldBankProvider
from algoterminal.data.composite_provider import CompositeProvider

from .conftest import StubResponse

START = date(2024, 1, 1)
END = date(2024, 1, 5)


def _get_returning(monkeypatch, response, seen=None):
    def fake_get(url, params=None, timeout=None, headers=None):
        if seen is not None:
            seen.append((url, params, headers))
        return response

    monkeypatch.setattr(requests, "get", fake_get)


def _get_raising(monkeypatch):
    def fake_get(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", fake_get)


class TestFearGreed:
    payload: ClassVar[dict] = {
        "data": [
            {"value": "25", "timestamp": "1704153600", "value_classification": "Fear"},
            {"value": "70", "timestamp": "1704240000", "value_classification": "Greed"},
            {"value": None, "timestamp": "1704326400"},
        ]
    }

    def test_download_and_fetch(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data=self.payload))
        frame = FearGreedProvider().fetch("CRYPTO", end=date(2024, 1, 10))
        assert list(frame.columns) == ["close", "classification"]
        assert len(frame) == 2  # the None-value record is dropped
        assert frame["close"].tolist() == [25.0, 70.0]

    def test_unknown_symbol(self, monkeypatch):
        _get_raising(monkeypatch)  # would fail loudly if it tried the network
        assert FearGreedProvider._download("NOPE").empty

    def test_empty_data(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data={"data": []}))
        assert FearGreedProvider._download("CRYPTO").empty

    def test_all_records_invalid(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data={"data": [{"value": None}]}))
        assert FearGreedProvider._download("CRYPTO").empty

    def test_network_error(self, monkeypatch):
        _get_raising(monkeypatch)
        assert FearGreedProvider().fetch("CRYPTO").empty

    def test_cached_fetch_with_start_slice(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data=self.payload))
        provider = FearGreedProvider()
        provider.fetch("CRYPTO", end=date(2024, 1, 10))
        _get_raising(monkeypatch)
        cached = cache.read("fear-greed", "CRYPTO")
        end = cached.index.max().date()
        sliced = provider.fetch("CRYPTO", start=cached.index.min().date(), end=end)
        assert len(sliced) == 2


class TestFred:
    csv = "observation_date,UNRATE\n2024-01-01,3.7\n2024-01-02,3.9\n2024-01-03,.\n"

    def test_download_parses_csv(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(text=self.csv))
        frame = FredProvider().fetch("UNRATE", start=START, end=END)
        assert list(frame.columns) == ["close"]
        assert frame["close"].iloc[0] == 3.7
        assert pd.isna(frame["close"].iloc[2])  # FRED's "." missing marker coerced to NaN

    def test_legacy_date_column(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(text="DATE,UNRATE\n2024-01-01,3.7\n"))
        frame = FredProvider._download("UNRATE", START, END)
        assert len(frame) == 1

    def test_unknown_series(self):
        assert FredProvider._download("NOT_A_SERIES", START, END).empty

    def test_missing_date_column(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(text="foo,bar\n1,2\n"))
        assert FredProvider._download("UNRATE", START, END).empty

    def test_empty_body(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(text=""))
        assert FredProvider._download("UNRATE", START, END).empty

    def test_network_error(self, monkeypatch):
        _get_raising(monkeypatch)
        assert FredProvider._download("UNRATE", START, END).empty

    def test_cached_range_skips_download(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(text=self.csv))
        provider = FredProvider()
        provider.fetch("UNRATE", start=date(2024, 1, 1), end=date(2024, 1, 3))
        _get_raising(monkeypatch)
        frame = provider.fetch("UNRATE", start=date(2024, 1, 1), end=date(2024, 1, 3))
        assert len(frame) == 3


class TestNasaPower:
    payload: ClassVar[dict] = {
        "properties": {
            "parameter": {
                "T2M": {"20240101": 5.0, "20240102": -999.0},
                "WS10M": {"20240101": 3.0, "20240102": 4.0},
            }
        }
    }

    def test_download_masks_sentinel(self, monkeypatch):
        seen = []
        _get_returning(monkeypatch, StubResponse(json_data=self.payload), seen)
        frame = NasaPowerProvider._download("NYC", START, END)
        assert "close" in frame.columns
        assert "wind_speed" in frame.columns
        assert frame["close"].iloc[0] == 5.0
        assert pd.isna(frame["close"].iloc[1])  # -999 sentinel masked
        params = seen[0][1]
        assert float(params["latitude"]) == pytest.approx(40.7128)

    def test_fetch_uses_cache(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data=self.payload))
        frame = NasaPowerProvider().fetch("NYC", start=START, end=date(2024, 1, 2))
        assert len(frame) == 2

    def test_unknown_location(self):
        assert NasaPowerProvider._download("ATLANTIS", START, END).empty

    def test_missing_parameters(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data={"properties": {}}))
        assert NasaPowerProvider._download("NYC", START, END).empty

    def test_no_matching_columns(self, monkeypatch):
        payload = {"properties": {"parameter": {"UNKNOWN": {"20240101": 1.0}}}}
        _get_returning(monkeypatch, StubResponse(json_data=payload))
        assert NasaPowerProvider._download("NYC", START, END).empty

    def test_network_error(self, monkeypatch):
        _get_raising(monkeypatch)
        assert NasaPowerProvider._download("NYC", START, END).empty


class TestUsgsEarthquake:
    csv = (
        "time,mag\n"
        "2024-01-01T05:00:00.000Z,5.0\n"
        "2024-01-01T07:00:00.000Z,4.6\n"
        "2024-01-03T01:00:00.000Z,6.1\n"
    )

    def test_download_aggregates_daily(self, monkeypatch):
        seen = []
        _get_returning(monkeypatch, StubResponse(text=self.csv), seen)
        frame = UsgsEarthquakeProvider._download("GLOBAL", START, END)
        assert len(frame) == 5  # reindexed over every day in range
        assert frame["close"].tolist() == [2.0, 0.0, 1.0, 0.0, 0.0]
        assert frame["max_magnitude"].iloc[0] == 5.0
        assert frame["max_magnitude"].iloc[2] == 6.1
        assert "latitude" not in seen[0][1]  # GLOBAL is unbounded

    def test_regional_query_bounds(self, monkeypatch):
        seen = []
        _get_returning(monkeypatch, StubResponse(text=self.csv), seen)
        UsgsEarthquakeProvider._download("JAPAN", START, END)
        params = seen[0][1]
        assert float(params["latitude"]) == pytest.approx(36.2048)
        assert float(params["maxradiuskm"]) == 800

    def test_quiet_period_returns_zero_series(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(text="a,b\n"))
        frame = UsgsEarthquakeProvider._download("GLOBAL", START, END)
        assert (frame["close"] == 0).all()
        assert frame["max_magnitude"].isna().all()

    def test_unknown_region(self):
        assert UsgsEarthquakeProvider._download("MOON", START, END).empty

    def test_network_error(self, monkeypatch):
        _get_raising(monkeypatch)
        assert UsgsEarthquakeProvider._download("GLOBAL", START, END).empty

    def test_fetch_slices_cached(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(text=self.csv))
        frame = UsgsEarthquakeProvider().fetch("GLOBAL", start=START, end=END)
        assert len(frame) == 5


class TestWikiPageviews:
    payload: ClassVar[dict] = {
        "items": [
            {"timestamp": "2024010100", "views": 123},
            {"timestamp": "2024010200", "views": 456},
        ]
    }

    def test_download(self, monkeypatch):
        seen = []
        _get_returning(monkeypatch, StubResponse(json_data=self.payload), seen)
        frame = WikiPageviewsProvider._download("BITCOIN", START, END)
        assert frame["close"].tolist() == [123, 456]
        url, _, headers = seen[0]
        assert "Bitcoin" in url
        assert "User-Agent" in headers

    def test_unknown_symbol(self):
        assert WikiPageviewsProvider._download("NOT_A_TOPIC", START, END).empty

    def test_empty_items(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data={"items": []}))
        assert WikiPageviewsProvider._download("BITCOIN", START, END).empty

    def test_network_error(self, monkeypatch):
        _get_raising(monkeypatch)
        assert WikiPageviewsProvider._download("BITCOIN", START, END).empty

    def test_fetch_clamps_start_to_earliest(self, monkeypatch):
        captured = {}

        def fake_download(symbol, start, end):
            captured["start"] = start
            index = pd.date_range(start, end, name="date")
            return pd.DataFrame({"close": 1}, index=index)

        monkeypatch.setattr(WikiPageviewsProvider, "_download", staticmethod(fake_download))
        WikiPageviewsProvider().fetch("BITCOIN", start=date(2014, 1, 1), end=date(2015, 7, 10))
        assert captured["start"] == date(2015, 7, 1)


class TestWorldBank:
    payload: ClassVar[list] = [
        {"page": 1},
        [
            {"date": "2020", "value": 2.3},
            {"date": "2021", "value": None},
            {"date": "2022", "value": 1.1},
        ],
    ]

    def test_download(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data=self.payload))
        frame = WorldBankProvider._download("USA:NY.GDP.MKTP.KD.ZG")
        assert frame["close"].tolist() == [2.3, 1.1]  # None-valued year dropped
        assert frame.index[0] == pd.Timestamp("2020-01-01")

    def test_fetch_slices_by_year(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data=self.payload))
        provider = WorldBankProvider()
        frame = provider.fetch(
            "USA:NY.GDP.MKTP.KD.ZG", start=date(2021, 1, 1), end=date(2023, 1, 1)
        )
        assert frame["close"].tolist() == [1.1]
        _get_raising(monkeypatch)  # second fetch comes from cache
        assert not provider.fetch("USA:NY.GDP.MKTP.KD.ZG").empty

    def test_bad_symbol(self):
        assert WorldBankProvider._download("USA").empty
        assert WorldBankProvider._download("XXX:NY.GDP.MKTP.KD.ZG").empty
        assert WorldBankProvider._download("USA:NOT.AN.INDICATOR").empty

    def test_error_payload(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data={"message": "error"}))
        assert WorldBankProvider._download("USA:NY.GDP.MKTP.KD.ZG").empty

    def test_all_values_null(self, monkeypatch):
        _get_returning(monkeypatch, StubResponse(json_data=[{}, [{"date": "2020", "value": None}]]))
        assert WorldBankProvider._download("USA:NY.GDP.MKTP.KD.ZG").empty

    def test_network_error(self, monkeypatch):
        _get_raising(monkeypatch)
        assert WorldBankProvider._download("USA:NY.GDP.MKTP.KD.ZG").empty


class TestRegistry:
    def test_provider_for_source_mapping(self):
        assert isinstance(provider_for_source("nasa-power"), NasaPowerProvider)
        assert isinstance(provider_for_source("usgs-earthquake"), UsgsEarthquakeProvider)
        assert isinstance(provider_for_source("fred"), FredProvider)
        assert isinstance(provider_for_source("world-bank"), WorldBankProvider)
        assert isinstance(provider_for_source("fear-greed"), FearGreedProvider)
        assert isinstance(provider_for_source("wiki-pageviews"), WikiPageviewsProvider)
        assert isinstance(provider_for_source("market"), CompositeProvider)
        assert isinstance(provider_for_source("anything-else"), CompositeProvider)

    def test_every_source_has_a_description(self):
        for key in (
            "market",
            "nasa-power",
            "usgs-earthquake",
            "fred",
            "world-bank",
            "fear-greed",
            "wiki-pageviews",
        ):
            assert key in SOURCE_DESCRIPTIONS

    def test_describe_symbol(self):
        assert "New York" in describe_symbol("nasa-power", "nyc")
        assert "Japan" in describe_symbol("usgs-earthquake", "japan")
        assert "unemployment" in describe_symbol("fred", "UNRATE")
        assert "United States" in describe_symbol("world-bank", "USA:NY.GDP.MKTP.KD.ZG")
        assert "Fear" in describe_symbol("fear-greed", "crypto")
        assert "Bitcoin" in describe_symbol("wiki-pageviews", "bitcoin")

    def test_describe_symbol_unknowns(self):
        assert describe_symbol("nasa-power", "ATLANTIS") == ""
        assert describe_symbol("usgs-earthquake", "MOON") == ""
        assert describe_symbol("fred", "XXX") == ""
        assert describe_symbol("world-bank", "XXX:YYY") == ""
        assert describe_symbol("fear-greed", "STOCKS") == ""
        assert describe_symbol("wiki-pageviews", "XXX") == ""
        assert describe_symbol("market", "AAPL") == ""

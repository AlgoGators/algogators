"""Tests for the abstract DataProvider interface, quality reports, and
the composite fallback provider."""

from __future__ import annotations

import pandas as pd
import pytest
from algoterminal.data import default_provider
from algoterminal.data.composite_provider import CompositeProvider
from algoterminal.data.provider import AssetClass, DataProvider

from .conftest import FakeProvider, make_ohlcv


class TestAssetClass:
    def test_values(self):
        assert AssetClass("equity") is AssetClass.EQUITY
        assert AssetClass.ALT_DATA.value == "alt_data"


class TestQualityReport:
    def test_empty_frame(self):
        report = DataProvider.quality_report("AAA", pd.DataFrame())
        assert report.issues == ["no data returned"]
        assert not report.ok
        assert report.rows == 0
        assert report.start is None and report.end is None

    def test_clean_frame(self):
        frame = make_ohlcv(n=40)
        report = DataProvider.quality_report("AAA", frame)
        assert report.ok
        assert report.rows == 40
        assert report.start == frame.index.min().date()
        assert report.end == frame.index.max().date()
        assert dict(report.__rich_repr__())["symbol"] == "AAA"

    def test_missing_and_short_history(self):
        frame = make_ohlcv(n=10)
        frame.loc[frame.index[:3], "close"] = float("nan")
        report = DataProvider.quality_report("AAA", frame, min_rows=30)
        assert report.missing_values == 3
        assert "3 missing close values" in report.issues
        assert "insufficient history (10 rows < 30)" in report.issues
        assert not report.ok

    def test_frame_without_close_column(self):
        frame = pd.DataFrame({"foo": [1.0]}, index=pd.date_range("2024-01-01", periods=1))
        report = DataProvider.quality_report("AAA", frame, min_rows=1)
        assert report.missing_values == 0
        assert report.ok


class TestFetchMany:
    def test_empty_symbol_list(self):
        assert FakeProvider().fetch_many([], AssetClass.EQUITY) == {}

    def test_single_symbol_short_circuits(self):
        provider = FakeProvider()
        result = provider.fetch_many(["AAA"], AssetClass.EQUITY)
        assert list(result) == ["AAA"]
        assert not result["AAA"].empty

    def test_concurrent_fetch_with_failure(self):
        provider = FakeProvider(fail=("BAD",))
        result = provider.fetch_many(["AAA", "BAD", "CCC"], AssetClass.EQUITY)
        assert set(result) == {"AAA", "BAD", "CCC"}
        assert result["BAD"].empty
        assert not result["AAA"].empty
        assert not result["CCC"].empty


class TestCompositeProvider:
    def test_requires_at_least_one_provider(self):
        with pytest.raises(ValueError):
            CompositeProvider([])

    def test_first_non_empty_wins(self):
        first = FakeProvider(frames={"AAA": make_ohlcv(n=5, seed=1)})
        second = FakeProvider(frames={"AAA": make_ohlcv(n=9, seed=2)})
        composite = CompositeProvider([first, second])
        result = composite.fetch("AAA", AssetClass.EQUITY)
        assert len(result) == 5
        assert second.calls == []

    def test_falls_through_on_exception_and_empty(self):
        failing = FakeProvider(fail=("AAA",))
        empty = FakeProvider(frames={"AAA": pd.DataFrame()})
        good = FakeProvider(frames={"AAA": make_ohlcv(n=7)})
        composite = CompositeProvider([failing, empty, good])
        result = composite.fetch("AAA", AssetClass.EQUITY)
        assert len(result) == 7

    def test_all_fail_returns_empty(self):
        composite = CompositeProvider([FakeProvider(fail=("AAA",))])
        assert composite.fetch("AAA", AssetClass.EQUITY).empty


def test_default_provider_is_yfinance_then_stooq():
    provider = default_provider()
    assert isinstance(provider, CompositeProvider)
    assert [p.name for p in provider.providers] == ["yfinance", "stooq"]

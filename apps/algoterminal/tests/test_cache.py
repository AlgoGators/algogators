"""Tests for the local parquet cache (algoterminal.data.cache)."""

from __future__ import annotations

from datetime import date

import pandas as pd
from algoterminal.data import cache

from .conftest import make_ohlcv


def _frame(start: str = "2024-01-01", days: int = 10) -> pd.DataFrame:
    index = pd.date_range(start, periods=days, name="date")
    frame = pd.DataFrame({"close": range(days)}, index=index, dtype=float)
    return frame


class TestSafeSymbol:
    def test_replaces_awkward_characters(self):
        assert cache._safe_symbol("EURUSD=X") == "EURUSD-X"
        assert cache._safe_symbol("BRK/B") == "BRK-B"
        assert cache._safe_symbol("^GSPC") == "GSPC"
        assert cache._safe_symbol("a:b") == "a-b"


class TestReadWrite:
    def test_roundtrip(self):
        frame = make_ohlcv(n=5)
        cache.write("prov", "AAA", frame)
        loaded = cache.read("prov", "AAA")
        pd.testing.assert_frame_equal(loaded, frame, check_freq=False)

    def test_read_missing_returns_none(self):
        assert cache.read("prov", "NOPE") is None

    def test_read_corrupt_returns_none(self, isolated_home):
        path = isolated_home / "cache" / "prov__BAD.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not parquet at all")
        assert cache.read("prov", "BAD") is None

    def test_write_empty_is_noop(self, isolated_home):
        cache.write("prov", "EMPTY", pd.DataFrame())
        assert not (isolated_home / "cache" / "prov__EMPTY.parquet").exists()


class TestMergeAndWrite:
    def test_merges_dedupes_and_persists(self):
        existing = _frame("2024-01-01", 5)
        fresh = _frame("2024-01-04", 5)  # overlaps existing on the 4th and 5th
        merged = cache.merge_and_write("prov", "AAA", existing, fresh)
        assert len(merged) == 8
        assert not merged.index.duplicated().any()
        assert merged.index.is_monotonic_increasing
        # overlapping rows keep the fresh values
        assert merged.loc["2024-01-04", "close"] == fresh.loc["2024-01-04", "close"]
        reloaded = cache.read("prov", "AAA")
        pd.testing.assert_frame_equal(reloaded, merged)

    def test_no_frames_returns_empty(self):
        assert cache.merge_and_write("prov", "AAA", None, pd.DataFrame()).empty

    def test_existing_none(self):
        fresh = _frame()
        merged = cache.merge_and_write("prov", "AAA", None, fresh)
        pd.testing.assert_frame_equal(merged, fresh)


class TestCoversRange:
    def test_none_or_empty_never_covers(self):
        assert not cache.covers_range(None, None, None)
        assert not cache.covers_range(pd.DataFrame(), None, None)

    def test_full_coverage(self):
        frame = _frame("2024-01-01", 10)
        assert cache.covers_range(frame, date(2024, 1, 2), date(2024, 1, 9))
        assert cache.covers_range(frame, None, None)

    def test_uncovered_edges(self):
        frame = _frame("2024-01-05", 5)  # covers 5th..9th
        assert not cache.covers_range(frame, date(2024, 1, 1), date(2024, 1, 8))
        assert not cache.covers_range(frame, date(2024, 1, 6), date(2024, 1, 20))


class TestMissingRanges:
    def test_no_cache_needs_everything(self):
        assert cache.missing_ranges(None, date(2024, 1, 1), date(2024, 1, 31)) == [
            (date(2024, 1, 1), date(2024, 1, 31))
        ]

    def test_fully_covered(self):
        frame = _frame("2024-01-01", 31)
        assert cache.missing_ranges(frame, date(2024, 1, 5), date(2024, 1, 20)) == []

    def test_gap_before(self):
        frame = _frame("2024-01-10", 10)  # 10th..19th
        assert cache.missing_ranges(frame, date(2024, 1, 1), date(2024, 1, 15)) == [
            (date(2024, 1, 1), date(2024, 1, 9))
        ]

    def test_gap_after(self):
        frame = _frame("2024-01-01", 10)  # 1st..10th
        assert cache.missing_ranges(frame, date(2024, 1, 5), date(2024, 1, 20)) == [
            (date(2024, 1, 11), date(2024, 1, 20))
        ]

    def test_gaps_both_sides(self):
        frame = _frame("2024-01-10", 5)  # 10th..14th
        assert cache.missing_ranges(frame, date(2024, 1, 1), date(2024, 1, 31)) == [
            (date(2024, 1, 1), date(2024, 1, 9)),
            (date(2024, 1, 15), date(2024, 1, 31)),
        ]


class TestInventory:
    def test_list_cached_reports_entries(self, isolated_home):
        cache.write("provA", "AAA", _frame("2024-01-01", 4))
        cache.write("provB", "BBB", make_ohlcv(n=6))
        corrupt = isolated_home / "cache" / "provC__CCC.parquet"
        corrupt.write_bytes(b"junk")  # skipped, not fatal

        entries = cache.list_cached()
        assert [(e.provider, e.symbol) for e in entries] == [("provA", "AAA"), ("provB", "BBB")]
        first = entries[0]
        assert first.rows == 4
        assert first.start == date(2024, 1, 1)
        assert first.end == date(2024, 1, 4)
        assert first.size_bytes > 0

    def test_clear_filters(self):
        cache.write("p1", "AAA", _frame())
        cache.write("p1", "B/B", _frame())
        cache.write("p2", "AAA", _frame())

        assert cache.clear(provider_name="p2") == 1
        assert cache.clear(symbol="B/B") == 1  # matched through _safe_symbol
        assert cache.clear() == 1
        assert cache.list_cached() == []

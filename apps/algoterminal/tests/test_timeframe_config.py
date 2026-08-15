"""Tests for timeframe resolution, config paths, console theme, and stats table."""

from __future__ import annotations

from datetime import date

from algoterminal.analytics.stats import PerformanceStats
from algoterminal.config import ensure_dirs
from algoterminal.console import RICH_THEME, console
from algoterminal.theme import ALGOGATORS_THEME, BANNER, ORANGE, PROMPT, TAGLINE
from algoterminal.timeframe import (
    DEFAULT_TIMEFRAME,
    TIMEFRAME_OPTIONS,
    resolve_timeframe,
)
from algoterminal.tui.widgets.stats_table import build_stats_table

TODAY = date(2026, 8, 15)


class TestResolveTimeframe:
    def test_offset_keys(self):
        assert resolve_timeframe("1m", TODAY) == (date(2026, 7, 15), TODAY)
        assert resolve_timeframe("3m", TODAY) == (date(2026, 5, 15), TODAY)
        assert resolve_timeframe("6m", TODAY) == (date(2026, 2, 15), TODAY)
        assert resolve_timeframe("1y", TODAY) == (date(2025, 8, 15), TODAY)
        assert resolve_timeframe("3y", TODAY) == (date(2023, 8, 15), TODAY)
        assert resolve_timeframe("5y", TODAY) == (date(2021, 8, 15), TODAY)
        assert resolve_timeframe("10y", TODAY) == (date(2016, 8, 15), TODAY)

    def test_ytd(self):
        assert resolve_timeframe("ytd", TODAY) == (date(2026, 1, 1), TODAY)

    def test_max(self):
        assert resolve_timeframe("max", TODAY) == (date(1990, 1, 1), TODAY)

    def test_unknown_key_falls_back_to_max(self):
        assert resolve_timeframe("bogus", TODAY) == (date(1990, 1, 1), TODAY)

    def test_defaults_to_today(self):
        start, end = resolve_timeframe("1y")
        assert end == date.today()
        assert start < end

    def test_default_key_is_offered(self):
        keys = [key for _, key in TIMEFRAME_OPTIONS]
        assert DEFAULT_TIMEFRAME in keys


class TestConfig:
    def test_ensure_dirs_creates_tree(self, isolated_home):
        assert not isolated_home.exists()
        ensure_dirs()
        assert (isolated_home / "cache").is_dir()
        assert (isolated_home / "universes").is_dir()
        assert (isolated_home / "research").is_dir()
        ensure_dirs()  # idempotent


class TestBranding:
    def test_console_theme_styles(self):
        for style in ("brand", "heading", "success", "warning", "error"):
            assert style in RICH_THEME.styles
        assert console is not None

    def test_theme_constants(self):
        assert ALGOGATORS_THEME.name == "algoterminal"
        assert ALGOGATORS_THEME.primary == ORANGE
        assert "AlgoGators".upper()[:4] != ""  # sanity
        assert BANNER
        assert TAGLINE
        assert PROMPT.startswith(">")


class TestStatsTable:
    def test_includes_optional_rows(self):
        stats = PerformanceStats(
            cagr=0.1,
            sharpe=1.2,
            sortino=1.5,
            max_drawdown=-0.2,
            total_return=0.3,
            win_rate=0.55,
            n_trades=12,
        )
        table = build_stats_table(stats, title="My Stats")
        assert table.row_count == 7

    def test_skips_optional_rows_when_none(self):
        stats = PerformanceStats(
            cagr=0.1, sharpe=1.2, sortino=1.5, max_drawdown=-0.2, total_return=0.3
        )
        assert build_stats_table(stats).row_count == 5

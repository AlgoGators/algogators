"""Tests for algoterminal.analytics.stats."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from algoterminal.analytics.stats import (
    PerformanceStats,
    drawdown_periods,
    drawdown_series,
    monthly_returns_table,
    performance_stats,
    rolling_sharpe,
)


def _equity(values, start="2024-01-01"):
    index = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=index, name="equity")


class TestDrawdownSeries:
    def test_known_values(self):
        equity = _equity([100.0, 110.0, 99.0, 121.0])
        dd = drawdown_series(equity)
        assert dd.iloc[0] == 0.0
        assert dd.iloc[1] == 0.0
        assert dd.iloc[2] == pytest.approx((99 - 110) / 110)
        assert dd.iloc[3] == 0.0

    def test_never_positive(self):
        equity = _equity(list(100 + np.sin(np.linspace(0, 10, 50)) * 20))
        assert (drawdown_series(equity) <= 0).all()


class TestRollingSharpe:
    def test_window_and_name(self):
        rng = np.random.default_rng(1)
        returns = pd.Series(
            rng.normal(0.001, 0.01, 100), index=pd.date_range("2024-01-01", periods=100)
        )
        sharpe = rolling_sharpe(returns, window=10)
        assert len(sharpe) == 91  # first window-1 rows dropped
        assert sharpe.name == "Rolling Sharpe (10d)"

    def test_constant_positive_returns_high_sharpe(self):
        returns = pd.Series(0.01, index=pd.date_range("2024-01-01", periods=30))
        noise = pd.Series(np.tile([0.0005, -0.0005], 15), index=returns.index)  # avoid zero std dev
        sharpe = rolling_sharpe(returns + noise, window=10)
        assert (sharpe > 0).all()


class TestMonthlyReturnsTable:
    def test_compounds_by_month_and_year(self):
        index = pd.date_range("2024-01-01", "2024-02-29", freq="D")
        returns = pd.Series(0.01, index=index)
        table = monthly_returns_table(returns)
        assert list(table.columns) == [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
            "Year",
        ]
        assert table.loc[2024, "Jan"] == pytest.approx(1.01**31 - 1)
        assert table.loc[2024, "Feb"] == pytest.approx(1.01**29 - 1)
        assert table.loc[2024, "Year"] == pytest.approx(1.01 ** len(index) - 1)
        assert np.isnan(table.loc[2024, "Mar"])


class TestDrawdownPeriods:
    def test_recovered_and_open_episodes(self):
        equity = _equity([100, 110, 99, 112, 108, 90])
        periods = drawdown_periods(equity)
        assert len(periods) == 2
        deepest = periods.iloc[0]
        assert deepest["depth"] == pytest.approx((90 - 112) / 112)
        assert not deepest["recovered"]
        assert deepest["end"] == equity.index[-1]
        recovered = periods.iloc[1]
        assert recovered["recovered"]
        assert recovered["depth"] == pytest.approx((99 - 110) / 110)
        assert recovered["start"] == equity.index[1]
        assert recovered["trough"] == equity.index[2]
        assert recovered["end"] == equity.index[3]
        assert recovered["length_days"] == 2

    def test_top_n_limits_output(self):
        equity = _equity([100, 90, 101, 80, 102, 70, 103])
        assert len(drawdown_periods(equity, top_n=2)) == 2

    def test_monotonic_equity_has_no_periods(self):
        equity = _equity([100, 101, 102, 103])
        assert drawdown_periods(equity).empty


class TestPerformanceStats:
    def test_basic_fields(self):
        rng = np.random.default_rng(3)
        returns = pd.Series(
            rng.normal(0.001, 0.01, 252), index=pd.date_range("2024-01-01", periods=252)
        )
        equity = (1 + returns).cumprod() * 100
        stats = performance_stats(returns, equity)
        assert isinstance(stats, PerformanceStats)
        assert stats.total_return == pytest.approx(equity.iloc[-1] / equity.iloc[0] - 1)
        # exactly 252 observations -> one "year", so CAGR == total return
        assert stats.cagr == pytest.approx(stats.total_return)
        assert stats.max_drawdown <= 0
        assert stats.win_rate is None
        assert stats.n_trades is None

    def test_with_positions(self):
        returns = pd.Series(
            [0.01, 0.02, 0.03, -0.01, 0.02], index=pd.date_range("2024-01-01", periods=5)
        )
        equity = (1 + returns).cumprod() * 100
        positions = pd.Series([0, 1, 1, 0, 1], index=returns.index, dtype=float)
        stats = performance_stats(returns, equity, positions)
        # active periods are those where yesterday's position was non-zero
        assert stats.win_rate == pytest.approx(0.5)
        assert stats.n_trades == 3

    def test_empty_returns(self):
        empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        stats = performance_stats(empty, empty)
        assert stats.total_return == 0.0
        assert stats.cagr == 0.0
        # an all-NaN std propagates: sharpe is NaN rather than 0 for empty input
        assert np.isnan(stats.sharpe)

    def test_flat_positions_give_zero_win_rate(self):
        returns = pd.Series([0.0, 0.0, 0.0], index=pd.date_range("2024-01-01", periods=3))
        equity = pd.Series([100.0, 100.0, 100.0], index=returns.index)
        positions = pd.Series([0.0, 0.0, 0.0], index=returns.index)
        stats = performance_stats(returns, equity, positions)
        assert stats.win_rate == 0.0
        assert stats.n_trades == 0
        assert stats.sharpe == 0.0

"""Tests for panel alignment, correlation, beta, spread, relative performance,
and cointegration analytics on synthetic series."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from algoterminal.analytics.beta import rolling_beta
from algoterminal.analytics.cointegration import CointegrationResult, engle_granger_test
from algoterminal.analytics.correlation import correlation_matrix, rolling_correlation
from algoterminal.analytics.panel import close_panel, series_panel
from algoterminal.analytics.relative_performance import relative_performance
from algoterminal.analytics.spread import ratio_series, spread_series, zscore_spread

from .conftest import make_close_series, make_ohlcv


def _pair(n: int = 250) -> dict[str, pd.Series]:
    a = make_close_series(n=n, seed=11)
    return {"A": a, "B": a * 2.0}


class TestSeriesPanel:
    def test_aligns_named_series(self):
        data = _pair()
        panel = series_panel(data)
        assert list(panel.columns) == ["A", "B"]
        assert len(panel) == len(data["A"])

    def test_filters_none_and_empty(self):
        a = make_close_series(n=10)
        data = {"A": a, "B": None, "C": pd.Series(dtype=float)}
        panel = series_panel(data)
        assert list(panel.columns) == ["A"]

    def test_all_empty_returns_empty_frame(self):
        assert series_panel({}).empty
        assert series_panel({"A": None}).empty

    def test_close_panel_uses_close_column(self):
        data = {"X": make_ohlcv(n=20), "Y": pd.DataFrame()}
        panel = close_panel(data)
        assert list(panel.columns) == ["X"]
        assert panel["X"].iloc[0] == data["X"]["close"].iloc[0]


class TestCorrelation:
    def test_matrix_of_scaled_copy_is_one(self):
        corr = correlation_matrix(_pair())
        assert corr.loc["A", "A"] == pytest.approx(1.0)
        assert corr.loc["A", "B"] == pytest.approx(1.0)
        assert corr.shape == (2, 2)

    def test_matrix_alternative_method(self):
        corr = correlation_matrix(_pair(), method="spearman")
        assert corr.loc["A", "B"] == pytest.approx(1.0)

    def test_rolling_correlation(self):
        corr = rolling_correlation(_pair(), "A", "B", window=20)
        assert corr.name == "A vs B (20d)"
        assert not corr.empty
        assert corr.dropna().min() == pytest.approx(1.0)


class TestRollingBeta:
    def test_beta_of_squared_series_is_two(self):
        a = make_close_series(n=250, seed=5)
        data = {"A": (a / a.iloc[0]) ** 2, "B": a}
        beta = rolling_beta(data, "A", "B", window=60)
        assert beta.name == "A beta vs B (60d)"
        assert beta.mean() == pytest.approx(2.0, abs=0.1)


class TestSpread:
    def test_spread_values_and_name(self):
        data = _pair()
        spread = spread_series(data, "A", "B", hedge_ratio=0.5)
        assert spread.name == "A - 0.5xB"
        # B == 2A, so A - 0.5 * B == 0 everywhere
        assert np.allclose(spread.values, 0.0)

    def test_ratio_values_and_name(self):
        data = _pair()
        ratio = ratio_series(data, "A", "B")
        assert ratio.name == "A/B"
        assert np.allclose(ratio.values, 0.5)

    def test_zscore_spread(self):
        a = make_close_series(n=100, seed=1)
        b = make_close_series(n=100, seed=2)
        z = zscore_spread({"A": a, "B": b}, "A", "B", window=20)
        assert z.name == "Z-score spread: A - 1xB (20d)"
        assert len(z) == 100 - 19
        assert abs(z.mean()) < 3.0


class TestRelativePerformance:
    def test_rebased_to_common_start(self):
        rebased = relative_performance(_pair())
        assert rebased.iloc[0].tolist() == [100.0, 100.0]
        # scaling out, both columns follow the same path
        assert np.allclose(rebased["A"].values, rebased["B"].values)

    def test_custom_base(self):
        rebased = relative_performance(_pair(), base=1)
        assert rebased.iloc[0].tolist() == [1.0, 1.0]

    def test_empty_input(self):
        assert relative_performance({}).empty


class TestCointegration:
    def test_cointegrated_pair_detected(self):
        rng = np.random.default_rng(42)
        n = 300
        index = pd.bdate_range("2023-01-02", periods=n)
        a = pd.Series(np.cumsum(rng.normal(0, 1.0, n)) + 100, index=index)
        b = a + rng.normal(0, 0.5, n)
        result = engle_granger_test({"A": a, "B": b}, "A", "B")
        assert isinstance(result, CointegrationResult)
        assert result.sym_a == "A"
        assert result.sym_b == "B"
        assert result.p_value < 0.05
        assert result.is_cointegrated()
        assert set(result.critical_values) == {"1%", "5%", "10%"}

    def test_independent_walks_not_cointegrated(self):
        rng = np.random.default_rng(0)
        n = 300
        index = pd.bdate_range("2023-01-02", periods=n)
        a = pd.Series(np.cumsum(rng.normal(0, 1.0, n)) + 100, index=index)
        b = pd.Series(np.cumsum(rng.normal(0, 1.0, n)) + 100, index=index)
        result = engle_granger_test({"A": a, "B": b}, "A", "B")
        assert not result.is_cointegrated(alpha=0.01)

    def test_alignment_drops_mismatched_dates(self):
        rng = np.random.default_rng(9)
        n = 200
        index = pd.bdate_range("2023-01-02", periods=n)
        a = pd.Series(np.cumsum(rng.normal(0, 1.0, n)) + 100, index=index)
        b = (a + rng.normal(0, 0.5, n)).iloc[20:]  # shorter series
        result = engle_granger_test({"A": a, "B": b}, "A", "B")
        assert result.p_value < 0.05

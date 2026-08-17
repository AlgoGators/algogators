"""Tests for the quantstats-backed tearsheet renderer adapter."""

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import matplotlib
import pandas as pd
import pytest
from algosystem.backtesting.domain.equity_curve import EquityCurve
from algosystem.backtesting.infrastructure.quantstats_tearsheet import (
    QuantStatsTearsheetRenderer,
    _benchmark_returns,
    _patched_resampler_sum_axis,
    _returns,
)
from algosystem.shared.errors import CalculationError
from pandas.core.resample import Resampler


def _curve(name=None, periods=10, start=100.0, step=1.0):
    dates = pd.date_range("2020-01-01", periods=periods, freq="D")
    values = [start + step * i for i in range(periods)]
    return EquityCurve.from_series(pd.Series(values, index=dates, name=name))


def _result(with_benchmark=True, name=None, benchmark_name=None):
    return SimpleNamespace(
        equity_curve=_curve(name=name),
        benchmark_curve=(
            _curve(name=benchmark_name, start=50.0, step=0.5) if with_benchmark else None
        ),
    )


class _FakeReports:
    """Records quantstats report calls instead of rendering figures."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def html(self, returns, benchmark=None, title=None, output=None, rf=0.0, periods_per_year=252):
        self._record("html", returns, benchmark, rf, periods_per_year, title=title, output=output)
        Path(output).write_text("<html>tearsheet</html>", encoding="utf-8")

    def full(self, returns, benchmark=None, rf=0.0, periods_per_year=252):
        self._record("full", returns, benchmark, rf, periods_per_year)

    def basic(self, returns, benchmark=None, rf=0.0, periods_per_year=252):
        self._record("basic", returns, benchmark, rf, periods_per_year)

    def _record(self, mode, returns, benchmark, rf, periods_per_year, **extra):
        if self.fail:
            raise RuntimeError("quantstats exploded")
        self.calls.append(
            {
                "mode": mode,
                "returns": returns,
                "benchmark": benchmark,
                "rf": rf,
                "periods_per_year": periods_per_year,
                **extra,
            }
        )


@pytest.fixture
def fake_reports(monkeypatch):
    module = types.ModuleType("quantstats")
    reports = _FakeReports()
    module.reports = reports
    monkeypatch.setitem(sys.modules, "quantstats", module)
    return reports


def test_render_html_writes_report_and_passes_arguments(fake_reports, tmp_path):
    result = _result()
    output = tmp_path / "report.html"

    returned = QuantStatsTearsheetRenderer().render(
        result, output, title="My Strategy", rf=0.01, periods_per_year=126
    )

    assert returned == output
    assert output.read_text(encoding="utf-8") == "<html>tearsheet</html>"
    assert len(fake_reports.calls) == 1
    call = fake_reports.calls[0]
    assert call["mode"] == "html"
    assert call["title"] == "My Strategy"
    assert call["output"] == str(output)
    assert call["rf"] == 0.01
    assert call["periods_per_year"] == 126
    pd.testing.assert_series_equal(
        call["returns"], result.equity_curve.returns(), check_names=False
    )
    pd.testing.assert_series_equal(
        call["benchmark"], result.benchmark_curve.returns(), check_names=False
    )


def test_render_names_unnamed_series_strategy_and_benchmark(fake_reports, tmp_path):
    QuantStatsTearsheetRenderer().render(_result(), tmp_path / "r.html", title="t")

    call = fake_reports.calls[0]
    assert call["returns"].name == "strategy"
    assert call["benchmark"].name == "benchmark"


def test_render_creates_missing_parent_directories(fake_reports, tmp_path):
    output = tmp_path / "deeply" / "nested" / "report.html"

    QuantStatsTearsheetRenderer().render(_result(), output, title="t")

    assert output.parent.is_dir()
    assert output.exists()


def test_render_full_mode_calls_full_report(fake_reports, tmp_path):
    output = tmp_path / "full.html"

    returned = QuantStatsTearsheetRenderer().render(_result(), output, title="t", mode="full")

    assert returned == output
    assert [call["mode"] for call in fake_reports.calls] == ["full"]


def test_render_basic_mode_without_benchmark_passes_none(fake_reports, tmp_path):
    QuantStatsTearsheetRenderer().render(
        _result(with_benchmark=False), tmp_path / "basic.html", title="t", mode="basic"
    )

    call = fake_reports.calls[0]
    assert call["mode"] == "basic"
    assert call["benchmark"] is None


def test_render_mode_is_case_insensitive(fake_reports, tmp_path):
    QuantStatsTearsheetRenderer().render(_result(), tmp_path / "r.html", title="t", mode="HTML")

    assert fake_reports.calls[0]["mode"] == "html"


def test_render_rejects_unknown_mode(tmp_path):
    with pytest.raises(CalculationError, match="unsupported tearsheet mode: pdf"):
        QuantStatsTearsheetRenderer().render(_result(), tmp_path / "r.pdf", title="t", mode="pdf")


def test_render_wraps_report_failures_in_calculation_error(monkeypatch, tmp_path):
    module = types.ModuleType("quantstats")
    module.reports = _FakeReports(fail=True)
    monkeypatch.setitem(sys.modules, "quantstats", module)

    with pytest.raises(CalculationError, match="failed to render quantstats tearsheet") as excinfo:
        QuantStatsTearsheetRenderer().render(_result(), tmp_path / "r.html", title="t")

    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_render_restores_matplotlib_backend(fake_reports, tmp_path):
    original = matplotlib.get_backend()
    matplotlib.use("svg", force=True)
    try:
        QuantStatsTearsheetRenderer().render(_result(), tmp_path / "r.html", title="t")
        assert matplotlib.get_backend().lower() == "svg"
    finally:
        matplotlib.use(original, force=True)


def test_returns_helper_preserves_existing_name():
    named = _result(name="alpha")

    returns = _returns(named)

    assert returns.name == "alpha"
    pd.testing.assert_series_equal(returns, named.equity_curve.returns(), check_names=False)


def test_benchmark_returns_helper_handles_missing_and_named_curves():
    assert _benchmark_returns(_result(with_benchmark=False)) is None

    named = _benchmark_returns(_result(benchmark_name="spx"))
    assert named is not None
    assert named.name == "spx"


def test_patched_resampler_sum_accepts_axis_and_restores():
    original = Resampler.sum
    series = pd.Series([1.0, 2.0, 3.0, 4.0], index=pd.date_range("2020-01-01", periods=4))

    with _patched_resampler_sum_axis():
        assert Resampler.sum is not original
        patched = series.resample("2D").sum(axis=0)
        assert patched.tolist() == [3.0, 7.0]

    assert Resampler.sum is original
    assert series.resample("2D").sum().tolist() == [3.0, 7.0]


def test_patched_resampler_sum_restores_after_exception():
    original = Resampler.sum

    with pytest.raises(RuntimeError, match="boom"), _patched_resampler_sum_axis():
        raise RuntimeError("boom")

    assert Resampler.sum is original

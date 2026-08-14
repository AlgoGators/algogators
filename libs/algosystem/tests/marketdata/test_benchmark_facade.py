"""Tests for the public benchmark market-data facade."""

import numpy as np
import pandas as pd
import pytest
from algosystem.marketdata import benchmark as benchmark_module
from algosystem.marketdata.benchmark import (
    BENCHMARK_ALIASES,
    compare_benchmarks,
    fetch_all_benchmarks,
    fetch_benchmark_data,
    get_benchmark_description,
    get_benchmark_info,
    get_benchmark_list,
    get_benchmark_metrics,
    get_benchmark_path,
    get_benchmark_returns,
    yield_to_price_index,
)
from algosystem.marketdata.domain.benchmark import STANDARD_CATALOG
from algosystem.shared.errors import MarketDataError, UnknownBenchmarkError
from algosystem.shared.metric_key import MetricKey


def _prices(values, start="2020-01-01"):
    index = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series([float(value) for value in values], index=index)


def _patch_fetch(monkeypatch, prices_by_alias):
    calls = []

    def fake_fetch(alias, start_date=None, end_date=None, force_refresh=False):
        calls.append((alias, start_date, end_date, force_refresh))
        if alias not in prices_by_alias:
            raise MarketDataError(f"no data for {alias}")
        return prices_by_alias[alias].copy()

    monkeypatch.setattr(benchmark_module, "fetch_benchmark_data", fake_fetch)
    return calls


def test_get_benchmark_list_matches_catalog():
    aliases = get_benchmark_list()

    assert aliases == STANDARD_CATALOG.aliases()
    assert aliases == sorted(aliases)
    assert {"sp500", "nasdaq", "gold"} <= set(aliases)


def test_get_benchmark_info_returns_display_frame():
    info = get_benchmark_info()

    assert list(info.columns) == ["Alias", "Category", "Ticker/Symbol", "Description"]
    assert len(info) == len(get_benchmark_list())
    assert "sp500" in set(info["Alias"])


def test_get_benchmark_description_known_alias():
    assert "S&P 500" in get_benchmark_description("sp500")


def test_get_benchmark_description_unknown_alias_raises():
    with pytest.raises(UnknownBenchmarkError, match="unknown benchmark alias"):
        get_benchmark_description("definitely_not_a_benchmark")


def test_benchmark_aliases_map_alias_to_ticker():
    assert BENCHMARK_ALIASES["sp500"] == "^GSPC"
    assert BENCHMARK_ALIASES["gold"] == "GLD"


def test_get_benchmark_path_uses_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(benchmark_module, "default_cache_dir", lambda: tmp_path)

    assert get_benchmark_path("sp500") == str(tmp_path / "sp500.parquet")


def test_get_benchmark_path_unknown_alias_raises():
    with pytest.raises(UnknownBenchmarkError):
        get_benchmark_path("nope")


def test_fetch_benchmark_data_downloads_once_then_uses_cache(monkeypatch, tmp_path):
    from algosystem.marketdata.infrastructure import parquet_cache, yfinance_provider

    cache_dir = tmp_path / "bench-cache"
    monkeypatch.setattr(parquet_cache, "default_cache_dir", lambda: cache_dir)

    downloads = []
    dates = pd.date_range("2020-01-01", periods=5, freq="D")

    def fake_download(ticker, start=None, end=None, progress=False):
        downloads.append((ticker, start, end))
        return pd.DataFrame({"Close": [100.0, 101.0, 102.0, 103.0, 104.0]}, index=dates)

    monkeypatch.setattr(yfinance_provider.yf, "download", fake_download)

    prices = fetch_benchmark_data("sp500", "2020-01-01", "2020-01-05")

    assert downloads[0][0] == "^GSPC"
    assert prices.tolist() == [100.0, 101.0, 102.0, 103.0, 104.0]
    assert (cache_dir / "sp500.parquet").exists()

    cached = fetch_benchmark_data("sp500", "2020-01-01", "2020-01-05")
    assert len(downloads) == 1
    assert cached.tolist() == prices.tolist()

    fetch_benchmark_data("sp500", "2020-01-01", "2020-01-05", force_refresh=True)
    assert len(downloads) == 2


def test_fetch_benchmark_data_unknown_alias_raises():
    with pytest.raises(UnknownBenchmarkError):
        fetch_benchmark_data("not_a_real_alias")


def test_fetch_all_benchmarks_skips_failing_aliases(monkeypatch):
    calls = _patch_fetch(
        monkeypatch,
        {"sp500": _prices([100, 101]), "gold": _prices([50, 51])},
    )

    results = fetch_all_benchmarks("2020-01-01", "2020-01-02", force_refresh=True)

    assert set(results) == {"sp500", "gold"}
    assert results["sp500"].tolist() == [100.0, 101.0]
    assert len(calls) == len(get_benchmark_list())
    assert all(call[1:] == ("2020-01-01", "2020-01-02", True) for call in calls)


def test_get_benchmark_returns_computes_simple_returns(monkeypatch):
    _patch_fetch(monkeypatch, {"sp500": _prices([100, 110, 121])})

    returns = get_benchmark_returns("sp500")

    assert len(returns) == 2
    assert returns.tolist() == pytest.approx([0.1, 0.1])


def test_compare_benchmarks_normalizes_each_series_to_100(monkeypatch):
    _patch_fetch(
        monkeypatch,
        {"sp500": _prices([100, 110, 121]), "gold": _prices([50, 55, 60])},
    )

    frame = compare_benchmarks(["sp500", "gold"])

    assert list(frame.columns) == ["sp500", "gold"]
    assert frame["sp500"].tolist() == pytest.approx([100.0, 110.0, 121.0])
    assert frame["gold"].tolist() == pytest.approx([100.0, 110.0, 120.0])


def test_compare_benchmarks_requires_at_least_one_alias():
    with pytest.raises(MarketDataError, match="at least one benchmark alias"):
        compare_benchmarks([])


def test_compare_benchmarks_rejects_empty_price_series(monkeypatch):
    _patch_fetch(monkeypatch, {"sp500": pd.Series(dtype=float)})

    with pytest.raises(MarketDataError, match="no benchmark prices available"):
        compare_benchmarks(["sp500"])


def test_get_benchmark_metrics_zero_volatility_gives_zero_sharpe(monkeypatch):
    _patch_fetch(monkeypatch, {"sp500": _prices([100, 110, 121])})

    metrics = get_benchmark_metrics("sp500")

    assert metrics[MetricKey.TOTAL_RETURN.value] == pytest.approx(0.21)
    assert metrics[MetricKey.ANNUALIZED_RETURN.value] == pytest.approx(1.21 ** (252 / 2) - 1)
    assert metrics[MetricKey.ANNUALIZED_VOLATILITY.value] == pytest.approx(0.0)
    assert metrics[MetricKey.SHARPE_RATIO.value] == 0.0
    assert metrics[MetricKey.MAX_DRAWDOWN.value] == pytest.approx(0.0)


def test_get_benchmark_metrics_with_drawdown(monkeypatch):
    _patch_fetch(monkeypatch, {"gold": _prices([100, 120, 90, 105])})

    metrics = get_benchmark_metrics("gold")

    assert metrics[MetricKey.TOTAL_RETURN.value] == pytest.approx(0.05)
    assert metrics[MetricKey.MAX_DRAWDOWN.value] == pytest.approx(-0.25)
    assert metrics[MetricKey.ANNUALIZED_VOLATILITY.value] > 0
    assert metrics[MetricKey.SHARPE_RATIO.value] == pytest.approx(
        metrics[MetricKey.ANNUALIZED_RETURN.value] / metrics[MetricKey.ANNUALIZED_VOLATILITY.value]
    )


def test_get_benchmark_metrics_requires_enough_data(monkeypatch):
    _patch_fetch(monkeypatch, {"sp500": _prices([100])})

    with pytest.raises(MarketDataError, match="not enough benchmark data"):
        get_benchmark_metrics("sp500")


def test_yield_to_price_index_inverts_yields():
    yields = _prices([2.0, 4.0, 2.0])

    index = yield_to_price_index(yields)

    assert index.tolist() == pytest.approx([100.0, 50.0, 100.0])
    assert isinstance(index, pd.Series)
    assert np.isfinite(index).all()

"""Round-trip, listing, search, and error-path tests for the in-memory run repository."""

from datetime import date, datetime, timedelta

import algosystem.backtesting.infrastructure.persistence.in_memory_repository as imr
import numpy as np
import pandas as pd
import pytest
from algosystem.backtesting.domain.backtest import BacktestResult
from algosystem.backtesting.domain.equity_curve import EquityCurve
from algosystem.backtesting.domain.metrics import PerformanceMetrics
from algosystem.backtesting.domain.ports import RunSummary
from algosystem.shared.errors import DuplicateRunError, RepositoryError, RunNotFoundError
from algosystem.shared.metric_key import MetricKey
from algosystem.shared.values import Money, Percent, RunId


def make_result(
    run_id: str | None = "run_a",
    start: str = "2020-01-01",
    periods: int = 4,
    start_value: float = 100.0,
    end_value: float = 120.0,
    metrics: dict | None = None,
) -> BacktestResult:
    """Build a small, valid BacktestResult for repository tests."""
    dates = pd.date_range(start, periods=periods, freq="D")
    series = pd.Series(np.linspace(start_value, end_value, periods), index=dates)
    curve = EquityCurve.from_series(series)
    if metrics is None:
        metrics = {
            MetricKey.TOTAL_RETURN: (end_value - start_value) / start_value,
            MetricKey.SHARPE_RATIO: 1.5,
            MetricKey.MAX_DRAWDOWN: 0.1,
            MetricKey.BETA: 0.9,
        }
    return BacktestResult(
        equity_curve=curve,
        benchmark_curve=None,
        metrics=PerformanceMetrics.from_dict(metrics),
        date_range=curve.date_range,
        initial_capital=Money(start_value),
        final_capital=Money(end_value),
        total_return=Percent((end_value - start_value) / start_value),
        run_id=RunId(run_id) if run_id is not None else None,
    )


@pytest.fixture
def repo(monkeypatch):
    """Repository whose insertion timestamps tick forward one day per save."""

    class _TickingDateTime:
        _now = datetime(2024, 1, 1, 12, 0, 0)

        @classmethod
        def utcnow(cls):
            cls._now = cls._now + timedelta(days=1)
            return cls._now

    monkeypatch.setattr(imr, "datetime", _TickingDateTime)
    return imr.InMemoryBacktestRunRepository()


class TestSaveAndGet:
    def test_save_and_get_round_trip(self, repo):
        assigned = repo.save(make_result("run_a"))
        assert isinstance(assigned, RunId)
        assert assigned.value == "run_a"

        loaded = repo.get(assigned)
        assert loaded.run_id == assigned
        assert loaded.initial_capital == Money(100.0)
        assert loaded.final_capital == Money(120.0)
        assert loaded.total_return.as_fraction == pytest.approx(0.2)
        assert loaded.metrics.get(MetricKey.SHARPE_RATIO) == pytest.approx(1.5)
        pd.testing.assert_series_equal(
            loaded.equity_curve.values, make_result("run_a").equity_curve.values
        )

    def test_save_generates_run_id_when_missing(self, repo):
        assigned = repo.save(make_result(run_id=None))
        assert isinstance(assigned, RunId)
        assert assigned.value
        assert repo.get(assigned).run_id == assigned

    def test_duplicate_save_raises(self, repo):
        repo.save(make_result("dup"))
        with pytest.raises(DuplicateRunError) as excinfo:
            repo.save(make_result("dup"))
        assert excinfo.value.run_id == "dup"

    def test_overwrite_replaces_existing(self, repo):
        repo.save(make_result("dup", end_value=120.0), name="old")
        repo.save(make_result("dup", end_value=140.0), overwrite=True, name="new")

        loaded = repo.get(RunId("dup"))
        assert loaded.final_capital.amount == pytest.approx(140.0)
        assert repo.get_backtest_summary("dup")["name"] == "new"
        assert len(repo.list_runs(limit=10, offset=0)) == 1

    def test_get_missing_raises_run_not_found(self, repo):
        with pytest.raises(RunNotFoundError) as excinfo:
            repo.get(RunId("missing"))
        assert excinfo.value.run_id == "missing"

    def test_get_accepts_plain_string_run_id(self, repo):
        repo.save(make_result("stringy"))
        assert repo.get("stringy").run_id == RunId("stringy")

    def test_save_stores_a_defensive_copy(self, repo):
        result = make_result("iso")
        repo.save(result)
        result.equity_curve.values.iloc[0] = 999.0
        assert repo.get("iso").equity_curve.values.iloc[0] == pytest.approx(100.0)

    def test_get_returns_a_defensive_copy(self, repo):
        repo.save(make_result("iso"))
        first = repo.get("iso")
        first.equity_curve.values.iloc[0] = 555.0
        assert repo.get("iso").equity_curve.values.iloc[0] == pytest.approx(100.0)


class TestDelete:
    def test_delete_removes_run(self, repo):
        repo.save(make_result("gone"))
        repo.delete("gone")
        assert repo.list_runs(limit=10, offset=0) == []
        with pytest.raises(RunNotFoundError):
            repo.get("gone")

    def test_delete_missing_raises(self, repo):
        with pytest.raises(RunNotFoundError) as excinfo:
            repo.delete(RunId("missing"))
        assert excinfo.value.run_id == "missing"

    def test_delete_then_delete_again_raises(self, repo):
        repo.save(make_result("twice"))
        repo.delete(RunId("twice"))
        with pytest.raises(RunNotFoundError):
            repo.delete(RunId("twice"))


class TestListRuns:
    def test_newest_first_with_paging(self, repo):
        repo.save(make_result("first"))
        repo.save(make_result("second"))
        repo.save(make_result("third"))

        page_one = repo.list_runs(limit=2, offset=0)
        assert [summary.run_id.value for summary in page_one] == ["third", "second"]

        page_two = repo.list_runs(limit=2, offset=2)
        assert [summary.run_id.value for summary in page_two] == ["first"]

        assert repo.list_runs(limit=5, offset=3) == []

    def test_summary_fields(self, repo):
        repo.save(make_result("summ", start_value=100.0, end_value=130.0))
        (summary,) = repo.list_runs(limit=1, offset=0)
        assert isinstance(summary, RunSummary)
        assert summary.run_id == RunId("summ")
        assert summary.initial_capital == Money(100.0)
        assert summary.final_capital == Money(130.0)
        assert summary.total_return.as_fraction == pytest.approx(0.3)
        assert summary.date_range.start == pd.Timestamp("2020-01-01")

    @pytest.mark.parametrize(("limit", "offset"), [(0, 0), (-1, 0), (1, -1)])
    def test_invalid_paging_raises(self, repo, limit, offset):
        with pytest.raises(RepositoryError):
            repo.list_runs(limit=limit, offset=offset)


class TestFindBest:
    def test_descending_for_sharpe(self, repo):
        repo.save(make_result("low", metrics={MetricKey.SHARPE_RATIO: 0.5}))
        repo.save(make_result("high", metrics={MetricKey.SHARPE_RATIO: 2.5}))
        repo.save(make_result("mid", metrics={MetricKey.SHARPE_RATIO: 1.5}))

        best = repo.find_best(MetricKey.SHARPE_RATIO, limit=3)
        assert [summary.run_id.value for summary in best] == ["high", "mid", "low"]

    def test_ascending_for_drawdown(self, repo):
        repo.save(make_result("deep", metrics={MetricKey.MAX_DRAWDOWN: 0.4}))
        repo.save(make_result("shallow", metrics={MetricKey.MAX_DRAWDOWN: 0.05}))

        best = repo.find_best(MetricKey.MAX_DRAWDOWN, limit=2)
        assert [summary.run_id.value for summary in best] == ["shallow", "deep"]

    def test_skips_runs_missing_the_metric(self, repo):
        repo.save(make_result("scored", metrics={MetricKey.SHARPE_RATIO: 1.0}))
        repo.save(make_result("unscored", metrics={MetricKey.TOTAL_RETURN: 0.2}))

        best = repo.find_best(MetricKey.SHARPE_RATIO, limit=5)
        assert [summary.run_id.value for summary in best] == ["scored"]

    def test_limit_truncates(self, repo):
        for index in range(3):
            repo.save(make_result(f"run_{index}", metrics={MetricKey.SHARPE_RATIO: index}))
        assert len(repo.find_best(MetricKey.SHARPE_RATIO, limit=2)) == 2

    def test_invalid_limit_raises(self, repo):
        with pytest.raises(RepositoryError):
            repo.find_best(MetricKey.SHARPE_RATIO, limit=0)

    def test_requires_metric_key_instance(self, repo):
        with pytest.raises(RepositoryError):
            repo.find_best("sharpe_ratio", limit=1)


class TestSearch:
    def test_by_name_case_insensitive(self, repo):
        repo.save(make_result("a"), name="Momentum Alpha")
        repo.save(make_result("b"), name="Mean Reversion")

        matches = repo.search("momentum", "name")
        assert [summary.run_id.value for summary in matches] == ["a"]

    def test_by_run_id_and_description(self, repo):
        repo.save(make_result("alpha_run"), description="uses vol targeting")
        assert [s.run_id.value for s in repo.search("ALPHA", "run_id")] == ["alpha_run"]
        assert [s.run_id.value for s in repo.search("vol", "description")] == ["alpha_run"]

    @pytest.mark.parametrize(
        ("field", "query"),
        [
            ("start_date", "2020-01-01"),
            ("end_date", "2020-01-04"),
            ("initial_capital", "100"),
            ("final_capital", "120"),
            ("total_return", "0.2"),
        ],
    )
    def test_by_date_and_capital_fields(self, repo, field, query):
        repo.save(make_result("target"))
        assert [summary.run_id.value for summary in repo.search(query, field)] == ["target"]

    def test_by_metric_field_ignores_runs_missing_it(self, repo):
        repo.save(make_result("scored", metrics={MetricKey.SHARPE_RATIO: 1.5}))
        repo.save(make_result("unscored", metrics={MetricKey.TOTAL_RETURN: 0.2}))

        matches = repo.search("1.5", "sharpe_ratio")
        assert [summary.run_id.value for summary in matches] == ["scored"]

    def test_unsupported_field_raises(self, repo):
        repo.save(make_result("a"))
        with pytest.raises(RepositoryError):
            repo.search("anything", "not_a_field")

    def test_orders_matches_newest_first(self, repo):
        repo.save(make_result("older"), name="strategy one")
        repo.save(make_result("newer"), name="strategy two")

        matches = repo.search("strategy", "name")
        assert [summary.run_id.value for summary in matches] == ["newer", "older"]

    def test_no_matches_returns_empty(self, repo):
        repo.save(make_result("a"), name="alpha")
        assert repo.search("zzz", "name") == []


class TestBacktestStats:
    def test_empty_repository(self, repo):
        stats = repo.get_backtest_stats()
        assert stats["total_backtests"] == 0
        assert stats["unique_names"] == 0
        assert stats["equity_curve_records"] == 0
        assert "first_backtest" not in stats
        assert "avg_return" not in stats

    def test_populated_repository(self, repo):
        repo.save(
            make_result(
                "a",
                metrics={
                    MetricKey.TOTAL_RETURN: 0.1,
                    MetricKey.SHARPE_RATIO: 1.0,
                    MetricKey.MAX_DRAWDOWN: 0.2,
                },
            ),
            name="shared",
        )
        repo.save(
            make_result(
                "b",
                metrics={
                    MetricKey.TOTAL_RETURN: 0.3,
                    MetricKey.SHARPE_RATIO: 2.0,
                    MetricKey.MAX_DRAWDOWN: 0.4,
                },
            ),
            name="shared",
        )
        repo.save(make_result("c", metrics={MetricKey.TOTAL_RETURN: 0.2}), name="solo")

        stats = repo.get_backtest_stats()
        assert stats["total_backtests"] == 3
        assert stats["unique_names"] == 2
        assert stats["equity_curve_records"] == 12
        assert stats["days_span"] == 2
        assert stats["first_backtest"] < stats["last_backtest"]
        assert stats["avg_return"] == pytest.approx(0.2)
        assert stats["min_return"] == pytest.approx(0.1)
        assert stats["max_return"] == pytest.approx(0.3)
        assert stats["avg_sharpe"] == pytest.approx(1.5)
        assert stats["avg_drawdown"] == pytest.approx(0.3)

    def test_avg_sharpe_none_when_no_run_has_it(self, repo):
        repo.save(make_result("a", metrics={MetricKey.TOTAL_RETURN: 0.1}))
        stats = repo.get_backtest_stats()
        assert stats["avg_return"] == pytest.approx(0.1)
        assert stats["avg_sharpe"] is None
        assert stats["avg_drawdown"] is None


class TestCompareBacktests:
    def test_empty_ids_raise(self, repo):
        with pytest.raises(RepositoryError):
            repo.compare_backtests([])

    def test_only_unknown_ids_raise(self, repo):
        repo.save(make_result("known"))
        with pytest.raises(RepositoryError):
            repo.compare_backtests(["ghost_one", "ghost_two"])

    def test_skips_unknown_and_sorts_newest_first(self, repo):
        repo.save(make_result("older", metrics={MetricKey.SHARPE_RATIO: 1.0}), name="one")
        repo.save(make_result("newer", metrics={MetricKey.SHARPE_RATIO: 2.0}), name="two")

        comparison = repo.compare_backtests(["older", "ghost", RunId("newer")])
        rows = comparison["backtests"]
        assert [row["run_id"] for row in rows] == ["newer", "older"]
        assert rows[0]["name"] == "two"
        assert rows[0][MetricKey.SHARPE_RATIO.value] == pytest.approx(2.0)
        assert set(comparison["equity_curves"]) == {"older", "newer"}

    def test_equity_curves_are_copies(self, repo):
        repo.save(make_result("copyme"))
        comparison = repo.compare_backtests(["copyme"])
        comparison["equity_curves"]["copyme"].iloc[0] = -1.0
        assert repo.get("copyme").equity_curve.values.iloc[0] == pytest.approx(100.0)


class TestSummaryAndEquityCurve:
    def test_summary_missing_returns_none(self, repo):
        assert repo.get_backtest_summary("missing") is None

    def test_summary_contents(self, repo):
        hyperparameters = {"lookback": 20, "threshold": 0.5}
        repo.save(
            make_result("summ"),
            name="my strategy",
            description="a description",
            hyperparameters=hyperparameters,
        )
        summary = repo.get_backtest_summary(RunId("summ"))
        assert summary["run_id"] == "summ"
        assert summary["name"] == "my strategy"
        assert summary["description"] == "a description"
        assert summary["hyperparameters"] == hyperparameters
        assert summary["hyperparameters"] is not hyperparameters
        assert summary["start_date"] == date(2020, 1, 1)
        assert summary["end_date"] == date(2020, 1, 4)
        assert summary["initial_capital"] == pytest.approx(100.0)
        assert summary["final_capital"] == pytest.approx(120.0)
        assert summary["equity_points"] == 4
        assert summary[MetricKey.SHARPE_RATIO.value] == pytest.approx(1.5)

    def test_summary_default_name_is_run_id(self, repo):
        repo.save(make_result("unnamed"))
        assert repo.get_backtest_summary("unnamed")["name"] == "unnamed"

    def test_equity_curve_missing_returns_none(self, repo):
        assert repo.get_equity_curve("missing") is None

    def test_equity_curve_is_a_copy(self, repo):
        repo.save(make_result("curve"))
        curve = repo.get_equity_curve("curve")
        assert isinstance(curve, pd.Series)
        assert curve.iloc[0] == pytest.approx(100.0)
        curve.iloc[0] = -5.0
        assert repo.get_equity_curve("curve").iloc[0] == pytest.approx(100.0)

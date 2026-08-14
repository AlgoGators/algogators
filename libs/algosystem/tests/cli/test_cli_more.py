"""CLI coverage tests for algosystem.interfaces.cli.main and .loaders.

Everything that would touch a database or the network is monkeypatched; the
backtest happy paths run the real engine against tmp_path CSV fixtures.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pandas as pd
import pytest
from algosystem.interfaces.cli import loaders
from algosystem.interfaces.cli import main as cli_main
from algosystem.interfaces.cli.main import (
    _format_param_grid,
    _format_percent,
    _parse_param_options,
    _select_price_series,
    cli,
)
from algosystem.shared.errors import (
    ConfigurationError,
    InvalidPriceSeriesError,
    RepositoryError,
    RunNotFoundError,
)
from algosystem.shared.values import RunId
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _price(i: int) -> float:
    return 100.0 + 10.0 * math.sin(i / 5.0) + 0.3 * i


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def price_csv(tmp_path: Path) -> Path:
    dates = pd.date_range("2022-01-03", periods=60, freq="B")
    lines = ["date,price"]
    lines += [f"{d.date()},{_price(i):.4f}" for i, d in enumerate(dates)]
    path = tmp_path / "prices.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def multi_csv(tmp_path: Path) -> Path:
    dates = pd.date_range("2022-01-03", periods=40, freq="B")
    lines = ["date,alpha,beta"]
    lines += [f"{d.date()},{_price(i):.4f},{_price(i + 3):.4f}" for i, d in enumerate(dates)]
    path = tmp_path / "multi.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def benchmark_csv(tmp_path: Path) -> Path:
    dates = pd.date_range("2022-01-03", periods=60, freq="B")
    lines = ["date,bench"]
    lines += [f"{d.date()},{100.0 + 0.2 * i:.4f}" for i, d in enumerate(dates)]
    path = tmp_path / "bench.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# loaders.load_prices
# ---------------------------------------------------------------------------


class TestLoadPrices:
    def test_loads_csv_with_date_column(self, price_csv: Path) -> None:
        frame = loaders.load_prices(price_csv)

        assert isinstance(frame.index, pd.DatetimeIndex)
        assert frame.index.name == "date"
        assert list(frame.columns) == ["price"]
        assert len(frame) == 60

    def test_sorts_unsorted_dates(self, tmp_path: Path) -> None:
        path = tmp_path / "unsorted.csv"
        path.write_text("date,price\n2022-01-05,102\n2022-01-03,100\n2022-01-04,101\n")

        frame = loaders.load_prices(path)

        assert frame.index.is_monotonic_increasing
        assert frame["price"].tolist() == [100, 101, 102]

    def test_accepts_unnamed_index_column(self, tmp_path: Path) -> None:
        path = tmp_path / "unnamed.csv"
        path.write_text(",price\n2022-01-03,100\n2022-01-04,101\n")

        frame = loaders.load_prices(path)

        assert isinstance(frame.index, pd.DatetimeIndex)
        assert list(frame.columns) == ["price"]

    def test_accepts_timestamp_column_name(self, tmp_path: Path) -> None:
        path = tmp_path / "ts.csv"
        path.write_text("Timestamp,price\n2022-01-03,100\n2022-01-04,101\n")

        frame = loaders.load_prices(path)

        assert frame.index.name == "Timestamp"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidPriceSeriesError, match="not found"):
            loaders.load_prices(tmp_path / "nope.csv")

    def test_header_only_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.csv"
        path.write_text("date,price\n")

        with pytest.raises(InvalidPriceSeriesError, match="empty"):
            loaders.load_prices(path)

    def test_no_date_column_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "nodate.csv"
        path.write_text("name,city\nalice,gainesville\nbob,orlando\n")

        with pytest.raises(InvalidPriceSeriesError, match="date column"):
            loaders.load_prices(path)


# ---------------------------------------------------------------------------
# loaders.load_benchmark_input
# ---------------------------------------------------------------------------


class TestLoadBenchmarkInput:
    def test_none_and_blank_return_none(self) -> None:
        assert loaders.load_benchmark_input(None) is None
        assert loaders.load_benchmark_input("   ") is None

    def test_csv_path_returns_series(self, benchmark_csv: Path) -> None:
        series = loaders.load_benchmark_input(str(benchmark_csv))

        assert isinstance(series, pd.Series)
        assert series.name == "bench"
        assert isinstance(series.index, pd.DatetimeIndex)
        assert len(series) == 60

    def test_csv_without_numeric_column_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "text_bench.csv"
        path.write_text("date,label\n2022-01-03,aa\n2022-01-04,bb\n")

        with pytest.raises(InvalidPriceSeriesError, match="no numeric price column"):
            loaders.load_benchmark_input(str(path))

    def test_alias_falls_back_to_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict[str, object] = {}
        expected = pd.Series([1.0, 2.0], name="sp500")

        def fake_fetch(value: str, start_date: object = None, end_date: object = None) -> pd.Series:
            calls["value"] = value
            calls["start"] = start_date
            calls["end"] = end_date
            return expected

        monkeypatch.setattr("algosystem.marketdata.benchmark.fetch_benchmark_data", fake_fetch)

        result = loaders.load_benchmark_input("sp500", start="2022-01-01", end="2022-06-30")

        assert result is expected
        assert calls == {"value": "sp500", "start": "2022-01-01", "end": "2022-06-30"}


# ---------------------------------------------------------------------------
# backtest command (real engine, offline)
# ---------------------------------------------------------------------------


class TestBacktestCommand:
    def test_happy_path(self, runner: CliRunner, price_csv: Path) -> None:
        result = runner.invoke(cli, ["backtest", str(price_csv)])

        assert result.exit_code == 0, result.output
        assert "Backtest Summary" in result.output
        assert "Total Return" in result.output

    def test_detailed_with_benchmark_csv_and_options(
        self, runner: CliRunner, price_csv: Path, benchmark_csv: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "backtest",
                str(price_csv),
                "--benchmark",
                str(benchmark_csv),
                "--start",
                "2022-01-04",
                "--end",
                "2022-03-01",
                "--initial-capital",
                "10000",
                "--detailed",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Detailed Metrics" in result.output

    def test_multi_column_with_price_column(self, runner: CliRunner, multi_csv: Path) -> None:
        result = runner.invoke(cli, ["backtest", str(multi_csv), "--price-column", "alpha"])

        assert result.exit_code == 0, result.output
        assert "Backtest Summary" in result.output

    def test_benchmark_alias_uses_fetcher(
        self, runner: CliRunner, price_csv: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dates = pd.date_range("2022-01-03", periods=60, freq="B")
        bench = pd.Series([100.0 + 0.1 * i for i in range(60)], index=dates, name="sp500")
        seen: dict[str, object] = {}

        def fake_fetch(value: str, start_date: object = None, end_date: object = None) -> pd.Series:
            seen["value"] = value
            return bench

        monkeypatch.setattr("algosystem.marketdata.benchmark.fetch_benchmark_data", fake_fetch)

        result = runner.invoke(cli, ["backtest", str(price_csv), "--benchmark", "sp500"])

        assert result.exit_code == 0, result.output
        assert seen["value"] == "sp500"

    def test_missing_input_file_is_usage_error(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(cli, ["backtest", str(tmp_path / "missing.csv")])

        assert result.exit_code == 2
        assert "does not exist" in result.output

    def test_empty_csv_becomes_click_error(self, runner: CliRunner, tmp_path: Path) -> None:
        path = tmp_path / "empty.csv"
        path.write_text("date,price\n")

        result = runner.invoke(cli, ["backtest", str(path)])

        assert result.exit_code == 1
        assert "input file is empty" in result.output

    def test_undetectable_dates_become_click_error(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        path = tmp_path / "nodate.csv"
        path.write_text("name,city\nalice,gainesville\nbob,orlando\n")

        result = runner.invoke(cli, ["backtest", str(path)])

        assert result.exit_code == 1
        assert "could not detect a date column" in result.output

    def test_non_numeric_initial_capital_is_usage_error(
        self, runner: CliRunner, price_csv: Path
    ) -> None:
        result = runner.invoke(
            cli, ["backtest", str(price_csv), "--initial-capital", "lots-of-money"]
        )

        assert result.exit_code == 2
        assert "Invalid value" in result.output


# ---------------------------------------------------------------------------
# tearsheet command
# ---------------------------------------------------------------------------


class _FakeTearsheetAlgo:
    calls: ClassVar[dict[str, object]] = {}

    def __init__(self, **kwargs: object) -> None:  # accepts repository=... too
        pass

    def backtest(self, prices: object, **kwargs: object) -> object:
        type(self).calls["backtest_kwargs"] = kwargs
        return "RESULT"

    def tearsheet(
        self, result: object, output: object, title: object = None, mode: str = "html"
    ) -> str:
        type(self).calls["tearsheet"] = {"result": result, "title": title, "mode": mode}
        Path(str(output)).write_text("<html></html>")
        return str(output)


class TestTearsheetCommand:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _FakeTearsheetAlgo.calls = {}
        monkeypatch.setattr(cli_main, "AlgoSystem", _FakeTearsheetAlgo)

    def test_renders_to_output(self, runner: CliRunner, price_csv: Path, tmp_path: Path) -> None:
        out = tmp_path / "sheet.html"

        result = runner.invoke(
            cli,
            ["tearsheet", str(price_csv), "-o", str(out), "--title", "My Sheet", "--mode", "full"],
        )

        assert result.exit_code == 0, result.output
        assert "Rendered tearsheet:" in result.output
        assert out.exists()
        assert _FakeTearsheetAlgo.calls["tearsheet"] == {
            "result": "RESULT",
            "title": "My Sheet",
            "mode": "full",
        }

    def test_open_flag_opens_browser(
        self, runner: CliRunner, price_csv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[str] = []
        monkeypatch.setattr(cli_main, "webbrowser", SimpleNamespace(open=opened.append))
        out = tmp_path / "sheet.html"

        result = runner.invoke(cli, ["tearsheet", str(price_csv), "-o", str(out), "--open"])

        assert result.exit_code == 0, result.output
        assert len(opened) == 1
        assert opened[0].startswith("file://")
        assert opened[0].endswith("sheet.html")

    def test_invalid_mode_is_usage_error(self, runner: CliRunner, price_csv: Path) -> None:
        result = runner.invoke(cli, ["tearsheet", str(price_csv), "--mode", "pdf"])

        assert result.exit_code == 2
        assert "Invalid value" in result.output

    def test_backtest_failure_becomes_click_error(
        self, runner: CliRunner, price_csv: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(self: object, prices: object, **kwargs: object) -> object:
            raise InvalidPriceSeriesError("series exploded")

        monkeypatch.setattr(_FakeTearsheetAlgo, "backtest", boom)

        result = runner.invoke(cli, ["tearsheet", str(price_csv)])

        assert result.exit_code == 1
        assert "series exploded" in result.output


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------


class _FakeOverfitResults:
    n_params = 4
    n_reps = 25
    best_sharpe = 1.234567
    unbiased_pvalue = 0.04
    prob_overfit = 0.12
    deflated_sharpe = 0.987654

    def summary(self) -> list[str]:
        return ["permutation line one", "permutation line two"]


class _FakeValidateAlgo:
    calls: ClassVar[dict[str, object]] = {}

    def __init__(self, **kwargs: object) -> None:
        pass

    def detect_overfitting(self, **kwargs: object) -> _FakeOverfitResults:
        type(self).calls["detect"] = kwargs
        return _FakeOverfitResults()

    def validation_report(
        self, results: object, output: object, open_browser: bool = False
    ) -> str:
        type(self).calls["report"] = {"output": Path(str(output)), "open_browser": open_browser}
        Path(str(output)).write_text("<html></html>")
        return str(output)


class TestValidateCommand:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _FakeValidateAlgo.calls = {}
        monkeypatch.setattr(cli_main, "AlgoSystem", _FakeValidateAlgo)

    def test_happy_path_with_report(
        self, runner: CliRunner, price_csv: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "overfit.html"

        result = runner.invoke(
            cli,
            [
                "validate",
                str(price_csv),
                "--strategy",
                "momentum",
                "--param",
                "window=5,10",
                "--param",
                "mode=fast",
                "--reps",
                "25",
                "--seed",
                "7",
                "--shuffle",
                "cyclic",
                "--output",
                str(out),
            ],
        )

        assert result.exit_code == 0, result.output
        detect = _FakeValidateAlgo.calls["detect"]
        assert detect["strategy"] == "momentum"
        assert detect["param_grid"] == {"window": [5, 10], "mode": ["fast"]}
        assert detect["n_reps"] == 25
        assert detect["seed"] == 7
        assert detect["shuffle_method"] == "cyclic"
        assert detect["n_workers"] == 1
        assert "Validation Results" in result.output
        assert "1.234567" in result.output
        assert "permutation line one" in result.output
        assert "Rendered validation report:" in result.output
        assert _FakeValidateAlgo.calls["report"] == {"output": out, "open_browser": False}

    def test_no_output_skips_report(self, runner: CliRunner, price_csv: Path) -> None:
        result = runner.invoke(cli, ["validate", str(price_csv), "--reps", "10"])

        assert result.exit_code == 0, result.output
        assert _FakeValidateAlgo.calls["detect"]["param_grid"] is None
        assert "report" not in _FakeValidateAlgo.calls
        assert "Rendered validation report:" not in result.output

    def test_invalid_param_becomes_click_error(self, runner: CliRunner, price_csv: Path) -> None:
        result = runner.invoke(cli, ["validate", str(price_csv), "--param", "window"])

        assert result.exit_code == 1
        assert "invalid --param value" in result.output

    def test_unknown_price_column_becomes_click_error(
        self, runner: CliRunner, multi_csv: Path
    ) -> None:
        result = runner.invoke(cli, ["validate", str(multi_csv), "--price-column", "gamma"])

        assert result.exit_code == 1
        assert "price column not found: gamma" in result.output

    def test_multi_numeric_columns_require_price_column(
        self, runner: CliRunner, multi_csv: Path
    ) -> None:
        result = runner.invoke(cli, ["validate", str(multi_csv)])

        assert result.exit_code == 1
        assert "use --price-column" in result.output

    def test_empty_date_range_becomes_click_error(
        self, runner: CliRunner, price_csv: Path
    ) -> None:
        result = runner.invoke(cli, ["validate", str(price_csv), "--start", "2030-01-01"])

        assert result.exit_code == 1
        assert "selected validation date range is empty" in result.output

    def test_non_integer_reps_is_usage_error(self, runner: CliRunner, price_csv: Path) -> None:
        result = runner.invoke(cli, ["validate", str(price_csv), "--reps", "many"])

        assert result.exit_code == 2
        assert "Invalid value" in result.output


# ---------------------------------------------------------------------------
# db subcommands (repository fully faked; no database)
# ---------------------------------------------------------------------------


class _FakeDbAlgo:
    calls: ClassVar[dict[str, object]] = {}

    def __init__(self, repository: object = None, **kwargs: object) -> None:
        type(self).calls.setdefault("repositories", []).append(repository)  # type: ignore[union-attr]

    def backtest(self, prices: object, **kwargs: object) -> str:
        type(self).calls["backtest_kwargs"] = kwargs
        return "RESULT"

    def save(self, result: object, **kwargs: object) -> SimpleNamespace:
        type(self).calls["save"] = {"result": result, **kwargs}
        return SimpleNamespace(value="run-123")

    def load(self, run_id: object) -> str:
        type(self).calls["load"] = run_id
        return "LOADED"

    def compare(self, run_ids: list[str]) -> pd.DataFrame:
        type(self).calls["compare"] = run_ids
        return pd.DataFrame({"run-a": [1000.0, 1234.5], "run-b": [1000.0, 987.6]})

    def print_summary(self, result: object, detailed: bool = False) -> None:
        type(self).calls["summary"] = (result, detailed)


class TestDbCommands:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _FakeDbAlgo.calls = {}
        monkeypatch.setattr(cli_main, "AlgoSystem", _FakeDbAlgo)
        self.repo = SimpleNamespace(
            list_runs=self._list_runs,
            delete=self._delete,
        )
        monkeypatch.setattr(cli_main, "_repository", lambda: self.repo)
        self.repo_calls: dict[str, object] = {}

    def _list_runs(self, limit: int = 20, offset: int = 0) -> list[SimpleNamespace]:
        self.repo_calls["list"] = {"limit": limit, "offset": offset}
        return [
            SimpleNamespace(
                run_id=SimpleNamespace(value="abc"),
                date_range=SimpleNamespace(
                    start=dt.datetime(2022, 1, 3), end=dt.datetime(2022, 3, 25)
                ),
                total_return=SimpleNamespace(as_fraction=0.1234),
            )
        ]

    def _delete(self, run_id: RunId) -> None:
        self.repo_calls["delete"] = run_id

    def test_save(self, runner: CliRunner, price_csv: Path) -> None:
        result = runner.invoke(
            cli,
            [
                "db",
                "save",
                str(price_csv),
                "--name",
                "myrun",
                "--description",
                "a test run",
                "--overwrite",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Saved run: run-123" in result.output
        save = _FakeDbAlgo.calls["save"]
        assert save["result"] == "RESULT"
        assert save["name"] == "myrun"
        assert save["description"] == "a test run"
        assert save["overwrite"] is True
        assert _FakeDbAlgo.calls["repositories"] == [self.repo]

    def test_save_requires_name(self, runner: CliRunner, price_csv: Path) -> None:
        result = runner.invoke(cli, ["db", "save", str(price_csv)])

        assert result.exit_code == 2
        assert "Missing option" in result.output

    def test_list(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["db", "list", "--limit", "5", "--offset", "2"])

        assert result.exit_code == 0, result.output
        assert self.repo_calls["list"] == {"limit": 5, "offset": 2}
        assert "abc" in result.output
        assert "2022-01-03" in result.output
        assert "12.34%" in result.output

    def test_list_repository_error(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(limit: int = 20, offset: int = 0) -> list[SimpleNamespace]:
            raise RepositoryError("database is down")

        monkeypatch.setattr(self.repo, "list_runs", boom)

        result = runner.invoke(cli, ["db", "list"])

        assert result.exit_code == 1
        assert "database is down" in result.output

    def test_show(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["db", "show", "myrun"])

        assert result.exit_code == 0, result.output
        loaded = _FakeDbAlgo.calls["load"]
        assert isinstance(loaded, RunId)
        assert loaded.value == "myrun"
        assert _FakeDbAlgo.calls["summary"] == ("LOADED", True)

    def test_show_missing_run(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(self: object, run_id: object) -> str:
            raise RunNotFoundError(str(getattr(run_id, "value", run_id)))

        monkeypatch.setattr(_FakeDbAlgo, "load", boom)

        result = runner.invoke(cli, ["db", "show", "ghost"])

        assert result.exit_code == 1
        assert "Backtest run not found: ghost" in result.output

    def test_compare(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["db", "compare", "run-a", "run-b"])

        assert result.exit_code == 0, result.output
        assert _FakeDbAlgo.calls["compare"] == ["run-a", "run-b"]
        assert "run-a" in result.output
        assert "1,234.50" in result.output
        assert "987.60" in result.output

    def test_compare_requires_run_ids(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["db", "compare"])

        assert result.exit_code == 2
        assert "Missing argument" in result.output

    def test_delete(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["db", "delete", "xyz"])

        assert result.exit_code == 0, result.output
        assert "Deleted run: xyz" in result.output
        assert self.repo_calls["delete"] == RunId("xyz")

    def test_delete_repository_error(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(run_id: object) -> None:
            raise RepositoryError("delete failed")

        monkeypatch.setattr(self.repo, "delete", boom)

        result = runner.invoke(cli, ["db", "delete", "xyz"])

        assert result.exit_code == 1
        assert "delete failed" in result.output


class TestDbInit:
    def test_init_creates_schema(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlalchemy
        from algosystem.backtesting.infrastructure import persistence

        created: dict[str, object] = {}
        config = SimpleNamespace(
            url=lambda: "postgresql://unit/test", pool_size=3, schema="unit_test_schema"
        )
        monkeypatch.setattr(
            persistence, "DatabaseConfig", SimpleNamespace(from_env=lambda: config)
        )

        def fake_create_engine(url: str, pool_size: int = 5) -> str:
            created["engine"] = (url, pool_size)
            return "ENGINE"

        monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)

        def fake_create_all(engine: object, schema: str) -> None:
            created["create_all"] = (engine, schema)

        monkeypatch.setattr(
            persistence, "schema", SimpleNamespace(create_all=fake_create_all), raising=False
        )

        result = runner.invoke(cli, ["db", "init"])

        assert result.exit_code == 0, result.output
        assert created["engine"] == ("postgresql://unit/test", 3)
        assert created["create_all"] == ("ENGINE", "unit_test_schema")
        assert "Initialized database schema: unit_test_schema" in result.output

    def test_init_configuration_error(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from algosystem.backtesting.infrastructure import persistence

        def boom() -> SimpleNamespace:
            raise ConfigurationError("DB_HOST is not set")

        monkeypatch.setattr(persistence, "DatabaseConfig", SimpleNamespace(from_env=boom))

        result = runner.invoke(cli, ["db", "init"])

        assert result.exit_code == 1
        assert "DB_HOST is not set" in result.output


def test_repository_factory_builds_postgres_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from algosystem.backtesting.infrastructure import persistence

    config = SimpleNamespace(name="fake-config")
    monkeypatch.setattr(persistence, "DatabaseConfig", SimpleNamespace(from_env=lambda: config))

    built: dict[str, object] = {}

    class FakeRepo:
        def __init__(self, cfg: object) -> None:
            built["config"] = cfg

    monkeypatch.setattr(persistence, "PostgresBacktestRunRepository", FakeRepo)

    repo = cli_main._repository()

    assert isinstance(repo, FakeRepo)
    assert built["config"] is config


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_format_percent(self) -> None:
        assert _format_percent(0.1234) == "12.34%"
        assert _format_percent(-0.05) == "-5.00%"

    def test_parse_param_options_types(self) -> None:
        parsed = _parse_param_options(["window=10,20", "threshold=0.5", "mode=fast,slow"])

        assert parsed == {
            "window": [10, 20],
            "threshold": [0.5],
            "mode": ["fast", "slow"],
        }

    @pytest.mark.parametrize("bad", ["window", "=5", "window=", " =1,2"])
    def test_parse_param_options_rejects_malformed(self, bad: str) -> None:
        with pytest.raises(ValueError, match="invalid --param value"):
            _parse_param_options([bad])

    def test_format_param_grid(self) -> None:
        text = _format_param_grid({"window": [5, 10], "mode": ["fast"]})

        assert text == "window=[5, 10]; mode=['fast']"

    def test_select_price_series_single_numeric(self) -> None:
        dates = pd.date_range("2022-01-03", periods=4, freq="D")
        frame = pd.DataFrame({"price": [1.0, 2.0, 3.0, 4.0], "note": list("abcd")}, index=dates)

        series = _select_price_series(frame, price_column=None, start=None, end=None)

        assert series.tolist() == [1.0, 2.0, 3.0, 4.0]

    def test_select_price_series_slices_dates(self) -> None:
        dates = pd.date_range("2022-01-03", periods=4, freq="D")
        frame = pd.DataFrame({"price": [1.0, 2.0, 3.0, 4.0]}, index=dates)

        series = _select_price_series(
            frame, price_column="price", start="2022-01-04", end="2022-01-05"
        )

        assert series.tolist() == [2.0, 3.0]

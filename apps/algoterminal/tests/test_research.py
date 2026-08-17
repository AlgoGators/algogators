"""Tests for the research cycle: models, storage, data stage, methodology,
backtest, and writeup — all against a temporary research directory."""

from __future__ import annotations

import pandas as pd
import pytest
from algoterminal.data.provider import AssetClass, DataQualityReport
from algoterminal.research import storage
from algoterminal.research.backtest import (
    has_backtest_result,
    load_backtest_result,
    run_backtest,
    save_backtest_result,
)
from algoterminal.research.data_stage import (
    load_quality_reports,
    pull_and_validate,
    save_quality_reports,
)
from algoterminal.research.methodology import load_strategy_module, scaffold_strategy
from algoterminal.research.models import Hypothesis, slugify
from algoterminal.research.writeup import _data_section, generate_writeup

from .conftest import SMA_STRATEGY, FakeProvider, make_close_series, make_hypothesis


class TestSlugify:
    def test_basic(self):
        assert slugify("FX Carry Momentum!") == "fx-carry-momentum"

    def test_collapses_runs_and_strips(self):
        assert slugify("  --Weird   ##Title--  ") == "weird-title"

    def test_empty_falls_back(self):
        assert slugify("!!!") == "hypothesis"


class TestHypothesisModel:
    def test_roundtrip(self):
        hyp = make_hypothesis()
        again = Hypothesis.from_dict(hyp.to_dict())
        assert again == hyp

    def test_slug_property(self):
        assert make_hypothesis(title="Test Momentum").slug == "test-momentum"

    def test_from_dict_defaults(self):
        hyp = Hypothesis.from_dict(
            {
                "title": "T",
                "thesis": "th",
                "universe": "u",
                "symbols": ["A"],
                "expected_edge": "e",
            }
        )
        assert hyp.asset_class is AssetClass.EQUITY
        assert hyp.risk_notes == ""
        assert hyp.author == ""


class TestStorage:
    def test_create_record_persists_hypothesis(self):
        hyp = make_hypothesis()
        record = storage.create_record(hyp)
        assert record.slug == "test-momentum"
        assert record.hypothesis_path.exists()
        assert record.load_hypothesis() == hyp

    def test_record_paths(self):
        record = storage.create_record(make_hypothesis())
        assert record.strategy_path.name == "strategy.py"
        assert record.data_quality_path.name == "data_quality.yaml"
        assert record.backtest_results_path.name == "backtest_results.json"
        assert record.equity_curve_path.name == "equity_curve.parquet"
        assert record.writeup_path.name == "writeup.md"

    def test_listing_and_lookup(self):
        record = storage.create_record(make_hypothesis())
        assert storage.list_slugs() == ["test-momentum"]
        versions = storage.list_versions("test-momentum")
        assert [v.version for v in versions] == [record.version]
        assert storage.latest_record("test-momentum").version == record.version
        assert storage.get_record("test-momentum", record.version).path == record.path

    def test_missing_lookups(self):
        assert storage.list_versions("nope") == []
        assert storage.latest_record("nope") is None
        with pytest.raises(KeyError):
            storage.get_record("nope", "20240101-000000")


class TestDataStage:
    def test_pull_and_validate(self):
        hyp = make_hypothesis(symbols=["AAA", "BBB"])
        data, reports = pull_and_validate(hyp, FakeProvider())
        assert set(data) == {"AAA", "BBB"}
        assert all(r.ok for r in reports)

    def test_quality_report_roundtrip(self):
        record = storage.create_record(make_hypothesis())
        hyp = make_hypothesis(symbols=["AAA", "EMPTY"])
        provider = FakeProvider(frames={"EMPTY": pd.DataFrame()})
        _, reports = pull_and_validate(hyp, provider)
        save_quality_reports(record, reports)

        loaded = load_quality_reports(record)
        by_symbol = {r.symbol: r for r in loaded}
        assert by_symbol["AAA"].ok
        assert by_symbol["AAA"].start is not None
        assert not by_symbol["EMPTY"].ok
        assert by_symbol["EMPTY"].issues == ["no data returned"]
        assert by_symbol["EMPTY"].start is None

    def test_load_reports_missing_file(self):
        record = storage.create_record(make_hypothesis())
        assert load_quality_reports(record) == []


class TestMethodology:
    def test_scaffold_writes_template(self):
        hyp = make_hypothesis()
        record = storage.create_record(hyp)
        path = scaffold_strategy(record, hyp)
        content = path.read_text(encoding="utf-8")
        assert hyp.title in content
        assert "def generate_signals" in content
        assert "def size_positions" in content
        assert "def apply_risk_rules" in content

    def test_scaffolded_module_loads(self):
        hyp = make_hypothesis()
        record = storage.create_record(hyp)
        scaffold_strategy(record, hyp)
        module = load_strategy_module(record.strategy_path)
        prices = make_close_series(n=30)
        assert (module.generate_signals(prices) == 0).all()

    def test_missing_required_function_rejected(self, tmp_path):
        bad = tmp_path / "strategy.py"
        bad.write_text("def generate_signals(p):\n    return p\n", encoding="utf-8")
        with pytest.raises(AttributeError, match="size_positions"):
            load_strategy_module(bad)


class TestBacktest:
    def _record_and_result(self):
        hyp = make_hypothesis()
        record = storage.create_record(hyp)
        record.strategy_path.write_text(SMA_STRATEGY, encoding="utf-8")
        strategy = load_strategy_module(record.strategy_path)
        prices = make_close_series(n=250, seed=3, drift=0.001)
        return record, run_backtest(strategy, prices, initial_capital=50_000.0)

    def test_run_backtest_shapes_and_stats(self):
        _, result = self._record_and_result()
        assert len(result.equity_curve) == 250
        assert result.equity_curve.iloc[0] == pytest.approx(50_000.0)
        assert (result.drawdown <= 0).all()
        assert result.positions.abs().max() <= 1.0
        assert result.stats.n_trades is not None and result.stats.n_trades > 0
        assert result.stats.win_rate is not None

    def test_save_load_roundtrip(self):
        record, result = self._record_and_result()
        assert not has_backtest_result(record)
        save_backtest_result(record, result)
        assert has_backtest_result(record)

        loaded = load_backtest_result(record)
        assert loaded.stats == result.stats
        pd.testing.assert_series_equal(
            loaded.equity_curve, result.equity_curve, check_names=False, check_freq=False
        )
        pd.testing.assert_series_equal(
            loaded.positions, result.positions, check_names=False, check_freq=False
        )


class TestWriteup:
    def test_data_section_empty(self):
        assert _data_section([]) == "No data quality reports available."

    def test_data_section_table(self):
        reports = [
            DataQualityReport("AAA", 100, 0, None, None, []),
            DataQualityReport("BBB", 5, 1, None, None, ["too short"]),
        ]
        section = _data_section(reports)
        assert "| AAA | 100 | n/a | none |" in section
        assert "too short" in section

    def test_generate_writeup(self):
        hyp = make_hypothesis()
        record = storage.create_record(hyp)
        record.strategy_path.write_text(SMA_STRATEGY, encoding="utf-8")
        strategy = load_strategy_module(record.strategy_path)
        result = run_backtest(strategy, make_close_series(n=250))
        _, reports = pull_and_validate(hyp, FakeProvider())

        content = generate_writeup(record, hyp, reports, result, methodology_notes="my notes")
        assert record.writeup_path.exists()
        assert hyp.title in content
        assert "my notes" in content
        assert record.slug in content
        assert f"{result.stats.sharpe:.2f}" in content

    def test_generate_writeup_defaults(self):
        hyp = make_hypothesis()
        hyp.risk_notes = ""
        record = storage.create_record(hyp)
        record.strategy_path.write_text(SMA_STRATEGY, encoding="utf-8")
        strategy = load_strategy_module(record.strategy_path)
        result = run_backtest(strategy, make_close_series(n=250))

        content = generate_writeup(record, hyp, [], result)
        assert "None recorded." in content
        assert "No additional methodology notes." in content
        assert "No data quality reports available." in content

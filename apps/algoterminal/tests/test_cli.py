"""Tests for the Typer CLI (network and TUI stubbed out)."""

from __future__ import annotations

import algoterminal.data as data_pkg
import numpy as np
import pandas as pd
import pytest
from algoterminal.cli import app
from algoterminal.data import cache
from algoterminal.data.universe import UniverseStore
from algoterminal.research import storage
from typer.testing import CliRunner

from .conftest import FakeProvider, make_hypothesis, make_ohlcv

runner = CliRunner()


@pytest.fixture
def fake_provider(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(data_pkg, "default_provider", lambda: provider)
    return provider


class TestUniverseCommands:
    def test_create_show_list(self):
        result = runner.invoke(
            app,
            [
                "universe",
                "create",
                "my-uni",
                "--symbols",
                "AAA, BBB",
                "--asset-class",
                "crypto",
                "--description",
                "demo",
            ],
        )
        assert result.exit_code == 0
        saved = UniverseStore().load("my-uni")
        assert saved.symbols == ["AAA", "BBB"]
        assert saved.asset_class.value == "crypto"

        result = runner.invoke(app, ["universe", "show", "my-uni"])
        assert result.exit_code == 0
        assert "my-uni" in result.output

        result = runner.invoke(app, ["universe", "list"])
        assert result.exit_code == 0
        assert "my-uni" in result.output

    def test_add_and_remove_symbol(self):
        runner.invoke(app, ["universe", "create", "my-uni", "--symbols", "AAA"])
        result = runner.invoke(app, ["universe", "add-symbol", "my-uni", "BBB"])
        assert result.exit_code == 0
        assert UniverseStore().load("my-uni").symbols == ["AAA", "BBB"]

        # adding again is a no-op
        runner.invoke(app, ["universe", "add-symbol", "my-uni", "BBB"])
        assert UniverseStore().load("my-uni").symbols == ["AAA", "BBB"]

        result = runner.invoke(app, ["universe", "remove-symbol", "my-uni", "AAA"])
        assert result.exit_code == 0
        assert UniverseStore().load("my-uni").symbols == ["BBB"]

    def test_delete(self):
        runner.invoke(app, ["universe", "create", "my-uni", "--symbols", "AAA"])
        result = runner.invoke(app, ["universe", "delete", "my-uni"])
        assert result.exit_code == 0
        assert "my-uni" not in {u.name for u in UniverseStore().list()}


class TestCacheCommands:
    def test_status_and_clear(self):
        cache.write("fake", "AAA", make_ohlcv(n=5))
        result = runner.invoke(app, ["cache", "status"])
        assert result.exit_code == 0
        assert "1 cached symbol(s)." in result.output

        result = runner.invoke(app, ["cache", "clear", "--provider", "fake"])
        assert result.exit_code == 0
        assert "Removed 1 cached file(s)." in result.output
        assert cache.list_cached() == []


class TestResearchCommands:
    def test_hypothesis_wizard(self):
        UniverseStore()  # seed builtins so the wizard can print known universes
        result = runner.invoke(
            app,
            ["hypothesis"],
            input="My CLI Hypo\nBecause reasons\nAAA,BBB\n\nSome edge\n\n\n",
        )
        assert result.exit_code == 0
        assert storage.list_slugs() == ["my-cli-hypo"]
        record = storage.latest_record("my-cli-hypo")
        hyp = record.load_hypothesis()
        assert hyp.symbols == ["AAA", "BBB"]
        assert hyp.asset_class.value == "equity"

    def test_data_command(self, fake_provider):
        storage.create_record(make_hypothesis())
        result = runner.invoke(app, ["data", "test-momentum"])
        assert result.exit_code == 0
        assert "AAA" in result.output
        record = storage.latest_record("test-momentum")
        assert record.data_quality_path.exists()

    def test_data_unknown_slug_exits(self):
        result = runner.invoke(app, ["data", "nope"])
        assert result.exit_code == 1

    def test_backtest_and_writeup(self, fake_provider):
        record = storage.create_record(make_hypothesis())
        result = runner.invoke(app, ["backtest", "test-momentum"])
        assert result.exit_code == 0
        assert record.strategy_path.exists()
        assert record.backtest_results_path.exists()

        result = runner.invoke(app, ["writeup", "test-momentum", "--version", record.version])
        assert result.exit_code == 0
        assert record.writeup_path.exists()

    def test_backtest_without_data_exits(self, monkeypatch):
        storage.create_record(make_hypothesis())
        provider = FakeProvider(frames={"AAA": pd.DataFrame()})
        monkeypatch.setattr(data_pkg, "default_provider", lambda: provider)
        result = runner.invoke(app, ["backtest", "test-momentum"])
        assert result.exit_code == 1

    def test_writeup_before_backtest_exits(self):
        storage.create_record(make_hypothesis())
        result = runner.invoke(app, ["writeup", "test-momentum"])
        assert result.exit_code == 1


class TestCompareCommands:
    def test_matrix_with_symbol_list(self, fake_provider):
        result = runner.invoke(app, ["compare", "matrix", "AAA,BBB"])
        assert result.exit_code == 0
        assert "Correlation Matrix" in result.output

    def test_matrix_with_universe_name(self, fake_provider):
        UniverseStore()
        result = runner.invoke(app, ["compare", "matrix", "crypto-majors"])
        assert result.exit_code == 0
        assert set(fake_provider.calls) == {"BTC-USD", "ETH-USD", "SOL-USD"}

    def test_relative(self, fake_provider):
        result = runner.invoke(app, ["compare", "relative", "AAA,BBB"])
        assert result.exit_code == 0
        assert "Relative Performance" in result.output

    def test_cointegration(self, monkeypatch):
        rng = np.random.default_rng(21)
        n = 300
        index = pd.bdate_range("2023-01-02", periods=n, name="date")
        walk = np.cumsum(rng.normal(0, 1.0, n)) + 100
        frame_a = pd.DataFrame({"close": walk}, index=index)
        frame_b = pd.DataFrame({"close": walk + rng.normal(0, 0.5, n)}, index=index)
        provider = FakeProvider(frames={"CA": frame_a, "CB": frame_b})
        monkeypatch.setattr(data_pkg, "default_provider", lambda: provider)

        result = runner.invoke(app, ["compare", "cointegration", "CA", "CB"])
        assert result.exit_code == 0
        assert "cointegrated (5%): True" in result.output


class TestTuiLaunch:
    def test_tui_command_invokes_app(self, monkeypatch):
        import algoterminal.tui.app as tui_app

        launched = []
        monkeypatch.setattr(tui_app, "run", lambda: launched.append(True))
        result = runner.invoke(app, ["tui"])
        assert result.exit_code == 0
        assert launched == [True]

    def test_no_subcommand_launches_tui(self, monkeypatch):
        import algoterminal.tui.app as tui_app

        launched = []
        monkeypatch.setattr(tui_app, "run", lambda: launched.append(True))
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert launched == [True]

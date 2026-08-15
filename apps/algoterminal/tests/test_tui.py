"""Textual TUI tests, driven through App.run_test() (headless, no network).

pytest-asyncio isn't part of this workspace, so each test wraps its async
scenario in asyncio.run() around Textual's own test harness.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import algoterminal.tui.app as app_module
import pytest
from algoterminal.data import cache
from algoterminal.data.provider import AssetClass
from algoterminal.data.universe import Universe, UniverseStore
from algoterminal.research import storage
from algoterminal.research.backtest import has_backtest_result, run_backtest, save_backtest_result
from algoterminal.research.design_agent import Engine
from algoterminal.research.methodology import load_strategy_module
from algoterminal.tui.app import AlgoTerminalApp
from algoterminal.tui.screens import compare_screen, data_screen, design_screen, research_screen
from algoterminal.tui.screens.compare_screen import (
    ComparePane,
    _comparable_items,
    _resolve_series,
)
from algoterminal.tui.screens.data_screen import DataPane
from algoterminal.tui.screens.design_screen import DesignPane
from algoterminal.tui.screens.hypothesis_modal import HypothesisModal
from algoterminal.tui.screens.research_screen import ResearchPane
from algoterminal.tui.screens.splash_screen import SplashScreen
from algoterminal.tui.screens.universe_modal import UniverseModal
from algoterminal.tui.widgets import plot_view
from algoterminal.tui.widgets.plot_view import PlotView
from PIL import Image
from textual.widgets import Button, Input, Select, TabbedContent, TextArea

from .conftest import SMA_STRATEGY, FakeProvider, make_close_series, make_hypothesis

SIZE = (120, 40)


def run_scenario(scenario):
    """Boot the app headless and run an async scenario against it."""

    async def _runner():
        app = AlgoTerminalApp()
        async with app.run_test(size=SIZE) as pilot:
            await scenario(app, pilot)

    asyncio.run(_runner())


async def _dismiss_splash(app, pilot):
    assert isinstance(app.screen, SplashScreen)
    await pilot.press("space")
    await pilot.pause()
    assert not isinstance(app.screen, SplashScreen)


def _press(pane, button_id: str) -> None:
    button = pane.query_one(button_id, Button)
    pane.on_button_pressed(Button.Pressed(button))


@pytest.fixture
def stub_cell_size(monkeypatch):
    monkeypatch.setattr(plot_view, "get_cell_size", lambda: SimpleNamespace(width=8, height=16))


@pytest.fixture
def backtested_record(isolated_home):
    """A research record with a saved SMA backtest, created before app boot."""
    hyp = make_hypothesis(symbols=["AAA"])
    record = storage.create_record(hyp)
    record.strategy_path.write_text(SMA_STRATEGY, encoding="utf-8")
    strategy = load_strategy_module(record.strategy_path)
    result = run_backtest(strategy, make_close_series(n=250, seed=3))
    save_backtest_result(record, result)
    return record


class TestAppSmoke:
    def test_boot_splash_and_tabs(self):
        async def scenario(app, pilot):
            splash = app.screen
            await _dismiss_splash(app, pilot)
            assert app.theme == "algoterminal"
            tabs = app.query_one("#main-tabs", TabbedContent)
            assert tabs.active == "tab-research"
            app.query_one(ResearchPane)
            app.query_one(DataPane)
            app.query_one(ComparePane)
            app.query_one(DesignPane)
            # a stale auto-dismiss timer firing after dismissal is a no-op
            splash._dismiss_if_active()

        run_scenario(scenario)

    def test_splash_auto_dismiss_callback(self):
        async def scenario(app, pilot):
            splash = app.screen
            splash._dismiss_if_active()
            await pilot.pause()
            assert not isinstance(app.screen, SplashScreen)

        run_scenario(scenario)

    def test_command_bar_dispatch(self):
        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            tabs = app.query_one("#main-tabs", TabbedContent)

            command_input = app.query_one("#command-input", Input)
            command_input.focus()
            command_input.value = "/compare"
            await pilot.press("enter")
            await pilot.pause()
            assert tabs.active == "tab-compare"
            assert command_input.value == ""

            app._dispatch_command("/bogus nonsense")  # unknown command -> warning toast
            app._dispatch_command("/writeup")  # no record selected -> warning toast
            app._dispatch_command("/data")
            await pilot.pause()
            assert tabs.active == "tab-research"

            app._dispatch_command("/hypothesis")
            await pilot.pause()
            assert isinstance(app.screen, HypothesisModal)
            modal = app.screen
            _press(modal, "#cancel")
            await pilot.pause()
            assert not isinstance(app.screen, HypothesisModal)

            # submissions from other inputs are ignored by the app handler
            app.on_input_submitted(
                SimpleNamespace(input=SimpleNamespace(id="other", value="x"), value="x")
            )

        run_scenario(scenario)

    def test_tab_activation_refreshes_panes(self):
        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            tabs = app.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-design"
            await pilot.pause()
            tabs.active = "tab-compare"
            await pilot.pause()
            assert tabs.active == "tab-compare"

        run_scenario(scenario)

    def test_run_helper_invokes_app(self, monkeypatch):
        launched = []
        monkeypatch.setattr(AlgoTerminalApp, "run", lambda self: launched.append(True))
        app_module.run()
        assert launched == [True]


class TestHypothesisModal:
    def test_validation_then_save(self):
        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            app._dispatch_command("/hypothesis")
            await pilot.pause()
            modal = app.screen

            _press(modal, "#save")  # everything empty -> error toast, modal stays up
            await pilot.pause()
            assert isinstance(app.screen, HypothesisModal)

            modal.query_one("#title", Input).value = "TUI Test Strategy"
            modal.query_one("#thesis", TextArea).text = "A thesis."
            modal.query_one("#universe", Input).value = "AAA,BBB"
            modal.query_one("#expected_edge", Input).value = "An edge."
            modal.query_one("#risk_notes", Input).value = "Risky."
            _press(modal, "#save")
            await pilot.pause()
            assert not isinstance(app.screen, HypothesisModal)
            assert "tui-test-strategy" in storage.list_slugs()
            hyp = storage.latest_record("tui-test-strategy").load_hypothesis()
            assert hyp.symbols == ["AAA", "BBB"]

        run_scenario(scenario)


class TestUniverseModal:
    def test_create_edit_and_cancel(self):
        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)

            app.push_screen(UniverseModal())
            await pilot.pause()
            modal = app.screen
            _press(modal, "#save")  # empty form -> error toast
            await pilot.pause()
            assert isinstance(app.screen, UniverseModal)

            modal.query_one("#name", Input).value = "tui-made"
            modal.query_one("#symbols", Input).value = "AAA, BBB"
            modal.query_one("#description", Input).value = "made in a test"
            _press(modal, "#save")
            await pilot.pause()
            saved = UniverseStore().load("tui-made")
            assert saved.symbols == ["AAA", "BBB"]
            assert saved.source == "market"

            app.push_screen(UniverseModal(saved))
            await pilot.pause()
            modal = app.screen
            assert modal.query_one("#name", Input).disabled
            modal.query_one("#symbols", Input).value = "CCC"
            _press(modal, "#save")
            await pilot.pause()
            assert UniverseStore().load("tui-made").symbols == ["CCC"]

            app.push_screen(UniverseModal())
            await pilot.pause()
            _press(app.screen, "#cancel")
            await pilot.pause()
            assert not isinstance(app.screen, UniverseModal)

        run_scenario(scenario)


class TestResearchPane:
    def test_full_cycle(self, monkeypatch, stub_cell_size):
        provider = FakeProvider()
        monkeypatch.setattr(research_screen, "default_provider", lambda: provider)
        record = storage.create_record(make_hypothesis(symbols=["AAA"]))

        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            pane = app.query_one(ResearchPane)
            assert [r.slug for r in pane._records] == ["test-momentum"]

            pane.action_run_cycle()  # nothing selected yet -> warning toast
            pane.action_generate_writeup()  # ditto

            pane.on_data_table_row_selected(SimpleNamespace(cursor_row=0))
            await pilot.pause()
            assert pane._selected is not None

            pane.action_generate_writeup()  # no backtest yet -> warning toast

            pane.action_run_cycle()  # scaffolds the zero-signal template, then backtests
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert has_backtest_result(record)

            # swap in a real strategy and re-run: charts now have non-zero data
            record.strategy_path.write_text(SMA_STRATEGY, encoding="utf-8")
            pane.action_run_cycle()
            await app.workers.wait_for_complete()
            await pilot.pause()

            pane.action_generate_writeup()
            await pilot.pause()
            assert record.writeup_path.exists()

        run_scenario(scenario)

    def test_run_cycle_with_no_data(self, monkeypatch, stub_cell_size):
        import pandas as pd

        provider = FakeProvider(frames={"AAA": pd.DataFrame()})
        monkeypatch.setattr(research_screen, "default_provider", lambda: provider)
        record = storage.create_record(make_hypothesis(symbols=["AAA"]))

        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            pane = app.query_one(ResearchPane)
            pane.on_data_table_row_selected(SimpleNamespace(cursor_row=0))
            pane.action_run_cycle()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not has_backtest_result(record)

        run_scenario(scenario)


class TestDataPane:
    def test_universe_browsing_and_cache_actions(self, monkeypatch):
        recorded_info = []
        monkeypatch.setattr(data_screen, "provider_for_source", lambda source: FakeProvider())
        monkeypatch.setattr(
            data_screen, "get_instrument_info", lambda sym: recorded_info.append(sym)
        )

        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            pane = app.query_one(DataPane)
            assert pane._universes

            _press(pane, "#universe-edit")  # nothing selected -> warning toast
            _press(pane, "#universe-delete")
            _press(pane, "#universe-refresh")
            _press(pane, "#fetch-info")
            _press(pane, "#clear-universe-cache")

            market_index = next(i for i, u in enumerate(pane._universes) if u.source == "market")
            table = pane.query_one("#universe-table")
            pane.on_data_table_row_selected(
                SimpleNamespace(data_table=table, cursor_row=market_index)
            )
            selected = pane._selected
            assert selected is not None

            # a cached symbol shows its row count in the detail pane
            cache.write("yfinance", selected.symbols[0], make_close_series(n=5).to_frame())
            pane.refresh_cache_table()
            pane._render_detail()

            _press(pane, "#universe-refresh")  # pull via the stubbed provider
            await app.workers.wait_for_complete()
            await pilot.pause()

            _press(pane, "#fetch-info")  # market source -> metadata worker
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert set(recorded_info) == set(selected.symbols)

            _press(pane, "#clear-universe-cache")
            await pilot.pause()
            assert cache.list_cached() == []

            # alt-data universe: detail uses describe_symbol, fetch-info refuses
            alt_index = next(i for i, u in enumerate(pane._universes) if u.source == "nasa-power")
            pane.on_data_table_row_selected(SimpleNamespace(data_table=table, cursor_row=alt_index))
            _press(pane, "#fetch-info")
            await pilot.pause()

            # rows selected on other tables are ignored
            pane.on_data_table_row_selected(
                SimpleNamespace(data_table=SimpleNamespace(id="cache-table"), cursor_row=0)
            )

            _press(pane, "#universe-new")
            await pilot.pause()
            assert isinstance(app.screen, UniverseModal)
            _press(app.screen, "#cancel")
            await pilot.pause()

            _press(pane, "#universe-edit")
            await pilot.pause()
            assert isinstance(app.screen, UniverseModal)
            _press(app.screen, "#cancel")
            await pilot.pause()

            # deleting only sticks for non-builtin universes (builtins re-seed)
            store = UniverseStore()
            scratch = Universe(name="tui-scratch", asset_class=AssetClass.EQUITY, symbols=["AAA"])
            store.save(scratch)
            pane.refresh_universes()
            scratch_index = next(
                i for i, u in enumerate(pane._universes) if u.name == "tui-scratch"
            )
            pane.on_data_table_row_selected(
                SimpleNamespace(data_table=table, cursor_row=scratch_index)
            )
            _press(pane, "#universe-delete")
            await pilot.pause()
            assert "tui-scratch" not in {u.name for u in UniverseStore().list()}

            cache.write("yfinance", "ZZZ", make_close_series(n=5).to_frame())
            _press(pane, "#clear-all-cache")
            await pilot.pause()
            assert cache.list_cached() == []

        run_scenario(scenario)


class TestDesignPane:
    def test_editing_and_agent_flow(self, monkeypatch, backtested_record):
        edits = []

        def fake_run_edit(engine, record, instruction):
            edits.append((engine, instruction))
            return (len(edits) == 1, f"attempt {len(edits)}")

        monkeypatch.setattr(design_screen, "available_engines", lambda: [Engine.CLAUDE])
        monkeypatch.setattr(design_screen, "find_vscode", lambda: "C:/bin/code")
        monkeypatch.setattr(design_screen, "can_open_file_location", lambda: True)
        monkeypatch.setattr(design_screen, "run_edit", fake_run_edit)
        opened = []
        monkeypatch.setattr(design_screen, "open_file_location", opened.append)
        monkeypatch.setattr(design_screen, "open_in_vscode", opened.append)

        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            tabs = app.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-design"
            await pilot.pause()

            pane = app.query_one(DesignPane)
            assert pane._engine is Engine.CLAUDE
            assert len(pane._records) == 1

            # every action refuses politely before a strategy is selected
            _press(pane, "#design-send")
            _press(pane, "#design-save")
            _press(pane, "#design-open-folder")
            _press(pane, "#design-open-vscode")
            assert opened == []

            table = pane.query_one("#design-records-table")
            pane.on_data_table_row_selected(SimpleNamespace(data_table=table, cursor_row=0))
            await pilot.pause()
            assert pane._selected is not None
            code = pane.query_one("#design-code", TextArea)
            assert "SMA crossover" in code.text

            code.text += "\n# edited in test\n"
            _press(pane, "#design-save")
            assert "# edited in test" in pane._selected.strategy_path.read_text(encoding="utf-8")
            _press(pane, "#design-reload")

            _press(pane, "#design-open-folder")
            _press(pane, "#design-open-vscode")
            assert len(opened) == 2

            _press(pane, "#design-send")  # no instruction typed -> warning toast

            pane.query_one("#design-prompt", Input).value = "make it mean revert"
            _press(pane, "#design-send")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert edits == [(Engine.CLAUDE, "make it mean revert")]
            assert pane.query_one("#design-prompt", Input).value == ""

            pane.query_one("#design-prompt", Input).value = "again"
            _press(pane, "#design-send")  # this attempt reports failure
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(edits) == 2

            pane.on_select_changed(
                SimpleNamespace(select=SimpleNamespace(id="design-engine"), value=Engine.CLAUDE)
            )
            assert pane._engine is Engine.CLAUDE
            pane.on_select_changed(
                SimpleNamespace(select=SimpleNamespace(id="design-engine"), value=None)
            )
            assert pane._engine is None
            pane.query_one("#design-prompt", Input).value = "no engine now"
            _press(pane, "#design-send")  # engine deselected -> warning toast
            assert len(edits) == 2

            # ignore row-selection events from other tables
            pane.on_data_table_row_selected(
                SimpleNamespace(data_table=SimpleNamespace(id="not-ours"), cursor_row=0)
            )

        run_scenario(scenario)

    def test_no_engines_disables_prompt(self, monkeypatch, backtested_record):
        monkeypatch.setattr(design_screen, "available_engines", lambda: [])
        monkeypatch.setattr(design_screen, "find_vscode", lambda: None)
        monkeypatch.setattr(design_screen, "can_open_file_location", lambda: False)

        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            tabs = app.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-design"
            await pilot.pause()
            pane = app.query_one(DesignPane)
            assert pane._engine is None

            table = pane.query_one("#design-records-table")
            pane.on_data_table_row_selected(SimpleNamespace(data_table=table, cursor_row=0))
            await pilot.pause()
            assert pane.query_one("#design-prompt", Input).disabled
            assert pane.query_one("#design-open-vscode", Button).disabled
            pane._send_to_agent()  # engine gate -> warning toast

        run_scenario(scenario)


class TestComparePane:
    def test_analyses(self, monkeypatch, stub_cell_size):
        import numpy as np
        import pandas as pd

        rng = np.random.default_rng(21)
        n = 300
        index = pd.bdate_range("2024-01-02", periods=n, name="date")
        walk = np.cumsum(rng.normal(0, 1.0, n)) + 100
        frame_a = pd.DataFrame({"close": walk}, index=index)
        frame_b = pd.DataFrame({"close": walk + rng.normal(0, 0.5, n)}, index=index)
        provider = FakeProvider(frames={"AAPL": frame_a, "MSFT": frame_b})
        monkeypatch.setattr(compare_screen, "default_provider", lambda: provider)

        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            tabs = app.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-compare"
            await pilot.pause()
            pane = app.query_one(ComparePane)

            _press(pane, "#compare-refresh")
            await pilot.pause()

            pane._run()  # nothing picked -> warning toast

            select_a = pane.query_one("#compare-item-a", Select)
            select_b = pane.query_one("#compare-item-b", Select)
            analysis = pane.query_one("#compare-analysis", Select)
            select_a.value = "symbol::AAPL::equity"
            select_b.value = "symbol::AAPL::equity"
            analysis.value = "correlation-matrix"
            pane._run()  # same item twice -> warning toast

            select_b.value = "symbol::MSFT::equity"
            chart = pane.query_one("#compare-chart", PlotView)
            for key in (
                "correlation-matrix",
                "cointegration",
                "rolling-correlation",
                "relative-performance",
                "spread",
                "ratio",
                "zscore-spread",
                "rolling-beta",
            ):
                analysis.value = key
                pane._run()
                await app.workers.wait_for_complete()
                await pilot.pause()
                if key in ("correlation-matrix", "cointegration"):
                    assert chart._renderer is None
                else:
                    assert chart._renderer is not None

        run_scenario(scenario)

    def test_unresolvable_selection(self, monkeypatch, stub_cell_size):
        import pandas as pd

        provider = FakeProvider(frames={"AAPL": pd.DataFrame(), "MSFT": pd.DataFrame()})
        monkeypatch.setattr(compare_screen, "default_provider", lambda: provider)

        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            pane = app.query_one(ComparePane)
            pane.query_one("#compare-item-a", Select).value = "symbol::AAPL::equity"
            pane.query_one("#compare-item-b", Select).value = "symbol::MSFT::equity"
            pane.query_one("#compare-analysis", Select).value = "spread"
            pane._run()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert pane.query_one("#compare-chart", PlotView)._renderer is None

        run_scenario(scenario)


class TestComparableItems:
    def test_items_and_resolution(self, backtested_record):
        from datetime import date

        items = dict(_comparable_items())
        strategy_id = f"strategy::{backtested_record.slug}::{backtested_record.version}"
        assert strategy_id in items.values()
        assert "symbol::AAPL::equity" in items.values()

        provider = FakeProvider()
        start, end = date(2024, 1, 1), date(2024, 12, 31)
        label, series = _resolve_series(strategy_id, provider, start, end)
        assert label == f"{backtested_record.slug}/{backtested_record.version}"
        assert not series.empty

        label, series = _resolve_series("symbol::AAA::equity", provider, start, end)
        assert label == "AAA"
        assert not series.empty

        assert _resolve_series("bogus::x", provider, start, end) is None

        import pandas as pd

        empty_provider = FakeProvider(frames={"AAA": pd.DataFrame()})
        assert _resolve_series("symbol::AAA::equity", empty_provider, start, end) is None

    def test_strategy_without_backtest_skipped(self):
        storage.create_record(make_hypothesis(title="No Backtest Yet"))
        items = [item_id for _, item_id in _comparable_items()]
        assert not any(item.startswith("strategy::no-backtest-yet") for item in items)

    def test_resolve_strategy_without_backtest(self):
        from datetime import date

        record = storage.create_record(make_hypothesis(title="No Backtest Yet"))
        item = f"strategy::{record.slug}::{record.version}"
        assert _resolve_series(item, FakeProvider(), date(2024, 1, 1), date(2024, 12, 31)) is None


class TestPlotView:
    def test_redraw_paths(self, stub_cell_size):
        async def scenario(app, pilot):
            await _dismiss_splash(app, pilot)
            view = app.query_one("#equity-chart", PlotView)

            view.show_message("nothing here yet")
            assert view._renderer is None

            calls = []

            def fake_render(width, height):
                calls.append((width, height))
                return Image.new("RGB", (max(width, 1), max(height, 1)))

            view.show_chart(fake_render)
            assert len(calls) == 1
            width, height = calls[0]
            assert width >= 200 and height >= 120

            view._redraw()  # same size again -> skipped
            assert len(calls) == 1

            view.on_resize(None)
            view.on_resize(None)  # second resize cancels the first timer
            await pilot.pause(0.2)
            assert len(calls) == 1  # size unchanged, so the debounce redraw no-ops

        run_scenario(scenario)

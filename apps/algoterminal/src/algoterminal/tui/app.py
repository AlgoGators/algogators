"""AlgoTerminal — full-screen Textual application.

Tabs (Strategies / Data / Compare) plus a slash-command bar for quick actions:
/hypothesis, /data, /backtest, /compare, /writeup.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, Static, TabbedContent, TabPane

from algoterminal.config import ensure_dirs
from algoterminal.theme import ALGOGATORS_THEME
from algoterminal.tui.screens.compare_screen import ComparePane
from algoterminal.tui.screens.data_screen import DataPane
from algoterminal.tui.screens.design_screen import DesignPane
from algoterminal.tui.screens.hypothesis_modal import HypothesisModal
from algoterminal.tui.screens.research_screen import ResearchPane
from algoterminal.tui.screens.splash_screen import SplashScreen

_COMMANDS = {"/hypothesis", "/data", "/backtest", "/compare", "/writeup"}


class AlgoTerminalApp(App):
    """AlgoTerminal — the QR team's terminal research workbench."""

    TITLE = "AlgoTerminal"
    SUB_TITLE = "Data Driven, Student Run."

    CSS = """
    #command-bar {
        dock: top;
        height: 3;
        padding: 0 1;
        background: $panel;
        border-bottom: solid $primary;
    }
    #command-prompt {
        width: auto;
        color: $primary;
        text-style: bold;
        content-align: left middle;
        padding-right: 1;
    }
    #command-input {
        border: none;
        background: $panel;
    }
    #records-col {
        width: 45%;
        border-right: solid $primary;
    }
    #detail-col {
        width: 1fr;
        padding: 0 1;
    }
    #record-detail-scroll {
        height: 12;
        border-bottom: solid $primary;
    }
    #charts-tabs {
        height: 1fr;
    }
    #research-buttons {
        height: auto;
        padding: 1 0;
    }
    #research-buttons Select {
        width: 22;
        margin-left: 1;
    }
    #compare-controls {
        height: auto;
        padding: 1;
    }
    #compare-controls Select {
        width: 1fr;
        margin-right: 1;
    }
    #compare-controls #compare-timeframe {
        width: 20;
    }
    #universe-col {
        width: 45%;
        border-right: solid $primary;
    }
    #universe-buttons, #cache-buttons {
        height: auto;
        padding: 1 0;
    }
    #data-detail-col {
        width: 1fr;
        padding: 0 1;
    }
    #universe-detail {
        height: auto;
        padding: 1 0;
    }
    #data-sources-scroll, #analysis-toolkit-scroll {
        padding: 1 2;
    }
    #design-records-col {
        width: 40%;
        border-right: solid $primary;
    }
    #design-detail {
        height: auto;
        padding: 1 0;
    }
    #design-code-col {
        width: 1fr;
        padding: 0 1;
    }
    #design-prompt-row {
        height: auto;
        padding-top: 1;
    }
    #design-prompt-row Select {
        width: 16;
        margin-right: 1;
    }
    #design-prompt-row Input {
        width: 1fr;
        margin-right: 1;
    }
    #design-status {
        height: auto;
        padding: 0 0 1 0;
    }
    #design-code {
        height: 1fr;
        border: solid $primary;
    }
    #design-code-buttons {
        height: auto;
        padding: 1 0;
    }
    """

    BINDINGS: ClassVar = [
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(ALGOGATORS_THEME)
        self.theme = "algoterminal"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="command-bar"):
            yield Static(">", id="command-prompt")
            yield Input(
                placeholder="/hypothesis  /data  /backtest  /compare  /writeup", id="command-input"
            )
        with TabbedContent(id="main-tabs"):
            with TabPane("Strategies", id="tab-research"):
                yield ResearchPane()
            with TabPane("Data", id="tab-data"):
                yield DataPane()
            with TabPane("Compare", id="tab-compare"):
                yield ComparePane()
            with TabPane("Design", id="tab-design"):
                yield DesignPane()
        yield Footer()

    def on_mount(self) -> None:
        ensure_dirs()
        self.push_screen(SplashScreen())

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "tab-design":
            self.query_one(DesignPane).refresh_records()
        elif event.pane.id == "tab-compare":
            self.query_one(ComparePane).refresh_items()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command-input":
            return
        command = event.value.strip()
        event.input.value = ""
        self._dispatch_command(command)

    def _dispatch_command(self, command: str) -> None:
        head, *_rest = ([*command.split(maxsplit=1), ""])[:2]
        if head not in _COMMANDS:
            self.notify(f"Unknown command: {command!r}", severity="warning")
            return

        tabs = self.query_one("#main-tabs", TabbedContent)
        research = self.query_one(ResearchPane)

        if head == "/hypothesis":
            tabs.active = "tab-research"
            self.push_screen(HypothesisModal(), research._on_hypothesis_created)
        elif head in ("/data", "/backtest"):
            tabs.active = "tab-research"
            research.action_run_cycle()
        elif head == "/writeup":
            tabs.active = "tab-research"
            research.action_generate_writeup()
        elif head == "/compare":
            tabs.active = "tab-compare"


def run() -> None:
    ensure_dirs()
    AlgoTerminalApp().run()

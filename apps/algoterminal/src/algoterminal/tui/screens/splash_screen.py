"""Branded startup splash — echoes algogators.com's hero: a bold wordmark,
the "Data Driven, Student Run." tagline, and a terminal-style prompt line."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Middle
from textual.screen import ModalScreen
from textual.widgets import Static

from algoterminal.theme import BANNER, PROMPT, TAGLINE

_AUTO_DISMISS_SECONDS = 1.4


class SplashScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    SplashScreen {
        align: center middle;
        background: $background;
    }
    #splash-body {
        width: auto;
        height: auto;
        align: center middle;
    }
    #splash-banner {
        color: $primary;
        text-style: bold;
        content-align: center middle;
        width: auto;
    }
    #splash-tagline {
        color: $foreground;
        text-style: bold;
        content-align: center middle;
        width: 100%;
        margin-top: 1;
    }
    #splash-prompt {
        color: $text-muted;
        content-align: center middle;
        width: 100%;
        margin-top: 1;
    }
    #splash-hint {
        color: $text-muted;
        text-style: italic;
        content-align: center middle;
        width: 100%;
        margin-top: 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Middle(id="splash-body"):
            yield Static(BANNER, id="splash-banner")
            yield Static(TAGLINE, id="splash-tagline")
            yield Static(PROMPT, id="splash-prompt")
            yield Static("press any key to continue", id="splash-hint")

    def on_mount(self) -> None:
        self.set_timer(_AUTO_DISMISS_SECONDS, self._dismiss_if_active)

    def _dismiss_if_active(self) -> None:
        if self.is_current:
            self.dismiss(None)

    def on_key(self, event) -> None:
        self.dismiss(None)

    def on_click(self, event) -> None:
        self.dismiss(None)

"""A Static widget that displays a plotext chart (an ANSI string) inside Textual."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static


class PlotView(Static):
    """Renders plotext chart output (which carries ANSI colour codes) as Rich Text."""

    def show_chart(self, chart_text: str) -> None:
        self.update(Text.from_ansi(chart_text))

    def show_message(self, message: str) -> None:
        self.update(Text(message, style="dim italic"))

"""Modal form for the "Hypothesis" stage inside the TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, TextArea

from algoterminal.data.provider import AssetClass
from algoterminal.data.universe import UniverseStore
from algoterminal.research.models import Hypothesis
from algoterminal.research.storage import ResearchRecord, create_record


class HypothesisModal(ModalScreen[tuple[Hypothesis, ResearchRecord] | None]):
    """Captures thesis, target universe, expected edge, and risk notes."""

    DEFAULT_CSS = """
    HypothesisModal {
        align: center middle;
    }
    #hyp-form {
        width: 80;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #hyp-form Label {
        margin-top: 1;
    }
    #hyp-buttons {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._universes = [u.name for u in UniverseStore().list()]

    def compose(self) -> ComposeResult:
        with Vertical(id="hyp-form"):
            yield Label("[bold]New Research Hypothesis[/bold]")
            yield Label("Title")
            yield Input(placeholder="e.g. FX carry momentum overlay", id="title")
            yield Label("Thesis")
            yield TextArea(id="thesis")
            yield Label(f"Universe (known: {', '.join(self._universes)})")
            yield Input(placeholder="g10-fx or AAPL,MSFT,...", id="universe")
            yield Label("Asset class")
            yield Select(
                [(c.value, c) for c in AssetClass],
                value=AssetClass.EQUITY,
                id="asset_class",
            )
            yield Label("Expected edge")
            yield Input(placeholder="Why should this be profitable?", id="expected_edge")
            yield Label("Risk notes")
            yield Input(placeholder="Optional", id="risk_notes")
            with Grid(id="hyp-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return

        title = self.query_one("#title", Input).value.strip()
        thesis = self.query_one("#thesis", TextArea).text.strip()
        universe = self.query_one("#universe", Input).value.strip()
        asset_class = self.query_one("#asset_class", Select).value
        expected_edge = self.query_one("#expected_edge", Input).value.strip()
        risk_notes = self.query_one("#risk_notes", Input).value.strip()

        if not (title and thesis and universe and expected_edge):
            self.notify("Title, thesis, universe, and expected edge are required.", severity="error")
            return

        symbols = UniverseStore().resolve(universe)
        hypothesis = Hypothesis(
            title=title,
            thesis=thesis,
            universe=universe,
            symbols=symbols,
            expected_edge=expected_edge,
            asset_class=asset_class,
            risk_notes=risk_notes,
        )
        record = create_record(hypothesis)
        self.dismiss((hypothesis, record))

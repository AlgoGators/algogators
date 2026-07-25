"""Modal form for creating or editing a named universe."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from algogators.data.altdata.registry import SOURCE_DESCRIPTIONS
from algogators.data.provider import AssetClass
from algogators.data.universe import Universe, UniverseStore

_SOURCES = list(SOURCE_DESCRIPTIONS)


class UniverseModal(ModalScreen[Universe | None]):
    """Create a new universe, or edit an existing one if `universe` is passed in."""

    DEFAULT_CSS = """
    UniverseModal {
        align: center middle;
    }
    #universe-form {
        width: 70;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    #universe-form Label {
        margin-top: 1;
    }
    #universe-buttons {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    """

    def __init__(self, universe: Universe | None = None) -> None:
        super().__init__()
        self._editing = universe

    def compose(self) -> ComposeResult:
        u = self._editing
        with Vertical(id="universe-form"):
            yield Label("[bold]Edit Universe[/bold]" if u else "[bold]New Universe[/bold]")
            yield Label("Name")
            yield Input(value=u.name if u else "", placeholder="e.g. my-basket", id="name", disabled=bool(u))
            yield Label("Symbols (comma-separated)")
            yield Input(value=", ".join(u.symbols) if u else "", placeholder="AAPL, MSFT, GOOGL", id="symbols")
            yield Label("Asset class")
            yield Select(
                [(c.value, c) for c in AssetClass],
                value=u.asset_class if u else AssetClass.EQUITY,
                id="asset_class",
            )
            yield Label("Source (data provider)")
            yield Select(
                [(s, s) for s in _SOURCES],
                value=u.source if u else "market",
                id="source",
            )
            yield Label("Description")
            yield Input(value=u.description if u else "", placeholder="Optional", id="description")
            with Grid(id="universe-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return

        name = self.query_one("#name", Input).value.strip()
        symbols_raw = self.query_one("#symbols", Input).value.strip()
        asset_class = self.query_one("#asset_class", Select).value
        source = self.query_one("#source", Select).value
        description = self.query_one("#description", Input).value.strip()

        if not (name and symbols_raw):
            self.notify("Name and symbols are required.", severity="error")
            return

        universe = Universe(
            name=name,
            asset_class=asset_class,
            symbols=[s.strip() for s in symbols_raw.split(",") if s.strip()],
            description=description,
            source=source,
        )
        UniverseStore().save(universe)
        self.dismiss(universe)

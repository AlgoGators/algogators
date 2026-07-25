"""Data tab: browse/edit universes, inspect the local cache, pull fresh data.

This is the "especially the data side" tab — the place to see, at a glance,
what's defined, what's cached, and how stale it is, without leaving the TUI.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Static

from algogators.data import cache, default_provider
from algogators.data.metadata import get_instrument_info, peek_metadata
from algogators.data.universe import Universe, UniverseStore
from algogators.tui.screens.universe_modal import UniverseModal
from algogators.theme import ORANGE


class DataPane(Horizontal):
    def __init__(self) -> None:
        super().__init__()
        self._universes: list[Universe] = []
        self._selected: Universe | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="universe-col"):
            yield DataTable(id="universe-table", cursor_type="row")
            with Horizontal(id="universe-buttons"):
                yield Button("New", id="universe-new")
                yield Button("Edit", id="universe-edit")
                yield Button("Delete", id="universe-delete")
                yield Button("Refresh Data", id="universe-refresh", variant="primary")
        with VerticalScroll(id="data-detail-col"):
            yield Static(id="universe-detail")
            with Horizontal(id="cache-buttons"):
                yield Button("Fetch Info", id="fetch-info")
                yield Button("Clear Cache (universe)", id="clear-universe-cache")
                yield Button("Clear All Cache", id="clear-all-cache")
            yield DataTable(id="cache-table")

    def on_mount(self) -> None:
        table = self.query_one("#universe-table", DataTable)
        table.add_columns("Name", "Asset Class", "Symbols", "Description")
        cache_table = self.query_one("#cache-table", DataTable)
        cache_table.add_columns("Provider", "Symbol", "Rows", "Start", "End", "Size (KB)")
        self.refresh_universes()
        self.refresh_cache_table()

    def refresh_universes(self) -> None:
        self._universes = UniverseStore().list()
        table = self.query_one("#universe-table", DataTable)
        table.clear()
        for u in self._universes:
            table.add_row(u.name, u.asset_class.value, str(len(u.symbols)), u.description or "")

    def refresh_cache_table(self) -> None:
        cache_table = self.query_one("#cache-table", DataTable)
        cache_table.clear()
        for entry in cache.list_cached():
            cache_table.add_row(
                entry.provider,
                entry.symbol,
                str(entry.rows),
                str(entry.start) if entry.start else "-",
                str(entry.end) if entry.end else "-",
                f"{entry.size_bytes / 1024:.1f}",
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "universe-table":
            return
        row_index = event.cursor_row
        if 0 <= row_index < len(self._universes):
            self._selected = self._universes[row_index]
            self._render_detail()

    def _render_detail(self) -> None:
        detail = self.query_one("#universe-detail", Static)
        u = self._selected
        if u is None:
            detail.update("No universe selected.")
            return

        cached_by_symbol = {e.symbol: e for e in cache.list_cached()}
        lines = [f"[bold {ORANGE}]{u.name}[/] ({u.asset_class.value}) — {u.description or 'no description'}", ""]
        for sym in u.symbols:
            info = peek_metadata(sym)
            name = f" — {info.name}" if info and info.name else ""
            entry = cached_by_symbol.get(sym.replace("/", "-").replace("=", "-").replace("^", ""))
            if entry:
                lines.append(f"  {sym}{name}: {entry.rows} rows, {entry.start} -> {entry.end} [{entry.provider}]")
            else:
                lines.append(f"  {sym}{name}: [dim]not cached yet[/dim]")
        detail.update("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "universe-new":
            self.app.push_screen(UniverseModal(), self._on_universe_saved)
        elif button_id == "universe-edit":
            if self._selected is None:
                self.notify("Select a universe first.", severity="warning")
                return
            self.app.push_screen(UniverseModal(self._selected), self._on_universe_saved)
        elif button_id == "universe-delete":
            self._delete_selected()
        elif button_id == "universe-refresh":
            self._refresh_selected()
        elif button_id == "fetch-info":
            self._fetch_metadata_selected()
        elif button_id == "clear-universe-cache":
            self._clear_cache_selected()
        elif button_id == "clear-all-cache":
            removed = cache.clear()
            self.refresh_cache_table()
            self._render_detail()
            self.notify(f"Cleared {removed} cached file(s).")

    def _on_universe_saved(self, universe: Universe | None) -> None:
        if universe is None:
            return
        self.refresh_universes()
        self.notify(f"Saved universe {universe.name!r}.")

    def _delete_selected(self) -> None:
        if self._selected is None:
            self.notify("Select a universe first.", severity="warning")
            return
        name = self._selected.name
        UniverseStore().delete(name)
        self._selected = None
        self.refresh_universes()
        self.query_one("#universe-detail", Static).update("No universe selected.")
        self.notify(f"Deleted universe {name!r}.")

    def _refresh_selected(self) -> None:
        if self._selected is None:
            self.notify("Select a universe first.", severity="warning")
            return
        self.notify(f"Pulling fresh data for {self._selected.name}...")
        self._refresh_worker(self._selected)

    @work(exclusive=True, thread=True)
    def _refresh_worker(self, universe: Universe) -> None:
        provider = default_provider()
        data = provider.fetch_many(universe.symbols, universe.asset_class)
        ok = sum(1 for df in data.values() if not df.empty)
        self.app.call_from_thread(self._after_refresh, universe.name, ok, len(universe.symbols))

    def _after_refresh(self, name: str, ok: int, total: int) -> None:
        self.refresh_cache_table()
        self._render_detail()
        self.notify(f"{name}: refreshed {ok}/{total} symbols.")

    def _fetch_metadata_selected(self) -> None:
        if self._selected is None:
            self.notify("Select a universe first.", severity="warning")
            return
        self.notify(f"Fetching instrument info for {self._selected.name}...")
        self._fetch_metadata_worker(self._selected)

    @work(exclusive=True, thread=True)
    def _fetch_metadata_worker(self, universe: Universe) -> None:
        for sym in universe.symbols:
            get_instrument_info(sym)
        self.app.call_from_thread(self._render_detail)

    def _clear_cache_selected(self) -> None:
        if self._selected is None:
            self.notify("Select a universe first.", severity="warning")
            return
        removed = 0
        for sym in self._selected.symbols:
            removed += cache.clear(symbol=sym)
        self.refresh_cache_table()
        self._render_detail()
        self.notify(f"Cleared {removed} cached file(s) for {self._selected.name}.")

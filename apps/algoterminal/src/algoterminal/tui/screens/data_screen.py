"""Data tab: browse/edit universes, inspect the local cache, pull fresh data —
plus two reference sub-tabs (Data Sources, Analysis Toolkit) cataloging what's
integrated and what you can run on it. "Universes" is the only working
sub-tab; the other two are read-only reference material, not a pipeline step.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Static, TabbedContent, TabPane

from algoterminal.data import SOURCE_DESCRIPTIONS, cache, describe_symbol, provider_for_source
from algoterminal.data.metadata import get_instrument_info, peek_metadata
from algoterminal.data.universe import Universe, UniverseStore
from algoterminal.theme import ORANGE
from algoterminal.tui.screens.universe_modal import UniverseModal

_DATA_SOURCES_REFERENCE = f"""\
[bold {ORANGE}]Data Sources[/]
[dim]What's actually wired up and pulling real data — reference only. To use one, \
create a universe (Universes tab) with its source key below.[/dim]

[bold {ORANGE}]market[/]  [dim](default source)[/]
{SOURCE_DESCRIPTIONS["market"]}
Covers: equities, futures, FX, crypto, custom baskets — anything with a Yahoo/Stooq ticker.

[bold {ORANGE}]nasa-power[/]
{SOURCE_DESCRIPTIONS["nasa-power"]}
Covers: temperature, solar irradiance, precipitation by lat/lon location, no key needed.

[bold {ORANGE}]usgs-earthquake[/]
{SOURCE_DESCRIPTIONS["usgs-earthquake"]}
Covers: daily aggregated seismic event counts/magnitudes by region.

[bold {ORANGE}]fred[/]
{SOURCE_DESCRIPTIONS["fred"]}
Covers: rates, inflation, employment, and other official US macro series.

[bold {ORANGE}]world-bank[/]
{SOURCE_DESCRIPTIONS["world-bank"]}
Covers: annual macro/development indicators (GDP, population, etc.) by country.

[bold {ORANGE}]fear-greed[/]
{SOURCE_DESCRIPTIONS["fear-greed"]}
Covers: one daily sentiment index (0-100) for the crypto market as a whole.

[bold {ORANGE}]wiki-pageviews[/]
{SOURCE_DESCRIPTIONS["wiki-pageviews"]}
Covers: daily human traffic to a company/asset/macro-topic Wikipedia article — a free \
proxy for retail search attention.

[dim]Every source implements the same `DataProvider` interface, so the analytics, \
Compare, and backtest layers don't care which one a symbol came from.[/dim]
"""

_ANALYSIS_TOOLKIT_REFERENCE = f"""\
[bold {ORANGE}]Analysis Toolkit[/]
[dim]What you can run once data exists — reference only. Cross-asset tools live in \
the Compare tab; backtest stats appear automatically after running a backtest in \
Strategies.[/dim]

[bold {ORANGE}]Cross-asset comparison[/]  [dim](Compare tab — pick any two things)[/]
  [bold]Correlation matrix[/] — Pearson correlation of daily returns across a set of series.
  [bold]Rolling correlation[/] — 60-day-windowed correlation between two series, over time.
  [bold]Cointegration (Engle-Granger)[/] — OLS regression + unit-root test on the residual; \
checks whether two price series share a long-run equilibrium, the standard pairs-trading \
validity check.
  [bold]Relative performance[/] — rebases series to a common start (=100) for visual \
cross-asset comparison.
  [bold]Spread (A - B)[/] — hedge-ratio-adjusted difference between two series.
  [bold]Ratio (A / B)[/] — cross-asset relative value, e.g. gold/silver.
  [bold]Z-score spread[/] — the spread expressed in rolling std-dev units; the standard \
pairs-trading entry/exit signal.
  [bold]Rolling beta[/] — rolling-window OLS beta of one series' returns against the other's.

[bold {ORANGE}]Backtest statistics[/]  [dim](Strategies tab, after Data + Backtest)[/]
  [bold]CAGR[/] / [bold]Total Return[/] — annualized and cumulative growth of the equity curve.
  [bold]Sharpe Ratio[/] — return per unit of total volatility.
  [bold]Sortino Ratio[/] — return per unit of downside volatility only.
  [bold]Max Drawdown[/] — largest peak-to-trough decline.
  [bold]Win Rate[/] — share of periods with positive returns.
  [bold]Rolling Sharpe[/] — trailing-window Sharpe over time (chart).
  [bold]Drawdown periods[/] — the deepest drawdown episodes, tabulated (depth, length, recovery).
  [bold]Monthly returns[/] — calendar heatmap of month-by-month performance.

[dim]None of this is generated for you — Methodology still scaffolds three empty \
functions. This is a menu of what's available to reach for, not a black box.[/dim]
"""


class DataPane(Vertical):
    def __init__(self) -> None:
        super().__init__()
        self._universes: list[Universe] = []
        self._selected: Universe | None = None

    def compose(self) -> ComposeResult:
        with TabbedContent(id="data-subtabs"):
            with TabPane("Universes", id="data-sub-universes"):
                with Horizontal(id="universes-pane"):
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
            with TabPane("Data Sources", id="data-sub-sources"):
                with VerticalScroll(id="data-sources-scroll"):
                    yield Static(_DATA_SOURCES_REFERENCE)
            with TabPane("Analysis Toolkit", id="data-sub-models"):
                with VerticalScroll(id="analysis-toolkit-scroll"):
                    yield Static(_ANALYSIS_TOOLKIT_REFERENCE)

    def on_mount(self) -> None:
        table = self.query_one("#universe-table", DataTable)
        table.add_columns("Name", "Asset Class", "Source", "Symbols", "Description")
        cache_table = self.query_one("#cache-table", DataTable)
        cache_table.add_columns("Provider", "Symbol", "Rows", "Start", "End", "Size (KB)")
        self.refresh_universes()
        self.refresh_cache_table()

    def refresh_universes(self) -> None:
        self._universes = UniverseStore().list()
        table = self.query_one("#universe-table", DataTable)
        table.clear()
        for u in self._universes:
            table.add_row(
                u.name, u.asset_class.value, u.source, str(len(u.symbols)), u.description or ""
            )

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

        source_desc = SOURCE_DESCRIPTIONS.get(u.source, "")
        cached_by_symbol = {e.symbol: e for e in cache.list_cached()}
        lines = [
            f"[bold {ORANGE}]{u.name}[/] ({u.asset_class.value}) — {u.description or 'no description'}",
        ]
        if source_desc:
            lines.append(f"[dim]{source_desc}[/dim]")
        lines.append("")

        for sym in u.symbols:
            if u.source == "market":
                info = peek_metadata(sym)
                label = f" — {info.name}" if info and info.name else ""
            else:
                blurb = describe_symbol(u.source, sym)
                label = f" — {blurb}" if blurb else ""
            safe_sym = sym.replace("/", "-").replace("=", "-").replace("^", "").replace(":", "-")
            entry = cached_by_symbol.get(safe_sym)
            if entry:
                lines.append(
                    f"  {sym}{label}: {entry.rows} rows, {entry.start} -> {entry.end} [{entry.provider}]"
                )
            else:
                lines.append(f"  {sym}{label}: [dim]not cached yet[/dim]")
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
        provider = provider_for_source(universe.source)
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
        if self._selected.source != "market":
            self.notify(
                "Fetch Info only applies to market universes — alt-data symbol "
                "descriptions are already shown above.",
                severity="information",
            )
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

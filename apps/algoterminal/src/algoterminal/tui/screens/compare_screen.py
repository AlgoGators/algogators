"""Compare tab: compare any two things against each other — backtested
strategies (their equity curves) or raw instruments (any symbol across every
saved universe) — using the same six analyses either way.
"""

from __future__ import annotations

from datetime import date

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Select, Static

import pandas as pd

from algoterminal.analytics.beta import rolling_beta
from algoterminal.analytics.cointegration import engle_granger_test
from algoterminal.analytics.correlation import correlation_matrix, rolling_correlation
from algoterminal.analytics.relative_performance import relative_performance
from algoterminal.analytics.spread import ratio_series, spread_series, zscore_spread
from algoterminal.charts.heatmap import correlation_heatmap
from algoterminal.charts.mpl_charts import line_chart, multi_line_chart
from algoterminal.data import default_provider
from algoterminal.data.provider import AssetClass, DataProvider
from algoterminal.data.universe import UniverseStore
from algoterminal.research.backtest import has_backtest_result, load_backtest_result
from algoterminal.research.storage import get_record, list_slugs, list_versions
from algoterminal.timeframe import DEFAULT_TIMEFRAME, TIMEFRAME_OPTIONS, resolve_timeframe
from algoterminal.tui.widgets.plot_view import PlotView

_ANALYSES = [
    ("Correlation matrix", "correlation-matrix"),
    ("Rolling correlation", "rolling-correlation"),
    ("Cointegration (Engle-Granger)", "cointegration"),
    ("Relative performance", "relative-performance"),
    ("Spread (A - B)", "spread"),
    ("Ratio (A / B)", "ratio"),
    ("Z-score spread", "zscore-spread"),
    ("Rolling beta", "rolling-beta"),
]


def _comparable_items() -> list[tuple[str, str]]:
    """(display label, item id) pairs for every backtested strategy + known instrument."""
    items: list[tuple[str, str]] = []

    for slug in list_slugs():
        for record in list_versions(slug):
            if not has_backtest_result(record):
                continue
            try:
                title = record.load_hypothesis().title
            except Exception:
                title = slug
            item_id = f"strategy::{slug}::{record.version}"
            items.append((f"[strategy] {title} ({slug}/{record.version})", item_id))

    asset_class_by_symbol: dict[str, AssetClass] = {}
    for universe in UniverseStore().list():
        for symbol in universe.symbols:
            asset_class_by_symbol.setdefault(symbol, universe.asset_class)

    for symbol, asset_class in sorted(asset_class_by_symbol.items()):
        items.append((f"[data] {symbol}", f"symbol::{symbol}::{asset_class.value}"))

    return items


def _resolve_series(item_id: str, provider: DataProvider, start: date, end: date) -> tuple[str, pd.Series] | None:
    """Turn a selected item id into (label, series) — an equity curve for a
    strategy, or a close-price series for a raw instrument — sliced to
    [start, end].
    """
    kind, _, rest = item_id.partition("::")

    if kind == "strategy":
        slug, _, version = rest.partition("::")
        record = get_record(slug, version)
        if not has_backtest_result(record):
            return None
        result = load_backtest_result(record)
        series = result.equity_curve.loc[str(start) : str(end)]
        return f"{slug}/{version}", series

    if kind == "symbol":
        symbol, _, asset_class_value = rest.partition("::")
        df = provider.fetch(symbol, AssetClass(asset_class_value), start, end)
        if df.empty:
            return None
        return symbol, df["close"]

    return None


class ComparePane(Vertical):
    def compose(self) -> ComposeResult:
        items = _comparable_items()
        with Horizontal(id="compare-controls"):
            yield Select(items, prompt="Compare this...", id="compare-item-a")
            yield Select(items, prompt="...against this", id="compare-item-b")
            yield Select(_ANALYSES, id="compare-analysis")
            yield Select(TIMEFRAME_OPTIONS, value=DEFAULT_TIMEFRAME, allow_blank=False, id="compare-timeframe")
            yield Button("Run", id="compare-run", variant="primary")
            yield Button("Refresh list", id="compare-refresh")
        with VerticalScroll(id="compare-output"):
            yield Static(id="compare-table")
            yield PlotView(id="compare-chart")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "compare-run":
            self._run()
        elif event.button.id == "compare-refresh":
            self.refresh_items()
            self.notify("Refreshed comparison list.")

    def refresh_items(self) -> None:
        items = _comparable_items()
        self.query_one("#compare-item-a", Select).set_options(items)
        self.query_one("#compare-item-b", Select).set_options(items)

    def _run(self) -> None:
        item_a = self.query_one("#compare-item-a", Select).value
        item_b = self.query_one("#compare-item-b", Select).value
        analysis = self.query_one("#compare-analysis", Select).value
        timeframe = self.query_one("#compare-timeframe", Select).value
        if item_a in (None, Select.BLANK) or item_b in (None, Select.BLANK) or analysis in (None, Select.BLANK):
            self.notify("Pick two things to compare and an analysis type.", severity="warning")
            return
        if item_a == item_b:
            self.notify("Pick two different things to compare.", severity="warning")
            return
        start, end = resolve_timeframe(timeframe)
        self.notify(f"Running {analysis} ({start} to {end})...")
        self._run_analysis(item_a, item_b, analysis, start, end)

    @work(exclusive=True, thread=True)
    def _run_analysis(self, item_a: str, item_b: str, analysis: str, start: date, end: date) -> None:
        provider = default_provider()
        resolved_a = _resolve_series(item_a, provider, start, end)
        resolved_b = _resolve_series(item_b, provider, start, end)
        if resolved_a is None or resolved_b is None:
            self.app.call_from_thread(
                self.notify, "Couldn't load data for one of the selections.", severity="error"
            )
            return

        label_a, series_a = resolved_a
        label_b, series_b = resolved_b
        data = {label_a: series_a, label_b: series_b}

        table_renderable = None
        chart_render = None

        if analysis == "correlation-matrix":
            table_renderable = correlation_heatmap(correlation_matrix(data))
        elif analysis == "rolling-correlation":
            chart_render = (line_chart, (rolling_correlation(data, label_a, label_b),), {"title": f"Rolling corr: {label_a} vs {label_b}"})
        elif analysis == "cointegration":
            result = engle_granger_test(data, label_a, label_b)
            from rich.table import Table

            t = Table(title=f"Engle-Granger: {label_a} vs {label_b}")
            t.add_column("metric")
            t.add_column("value")
            t.add_row("t-statistic", f"{result.t_statistic:.4f}")
            t.add_row("p-value", f"{result.p_value:.4f}")
            t.add_row("cointegrated (5%)", str(result.is_cointegrated()))
            table_renderable = t
        elif analysis == "relative-performance":
            chart_render = (multi_line_chart, (relative_performance(data),), {"title": "Relative Performance (rebased=100)"})
        elif analysis == "spread":
            chart_render = (line_chart, (spread_series(data, label_a, label_b),), {"title": f"Spread: {label_a} - {label_b}"})
        elif analysis == "ratio":
            chart_render = (line_chart, (ratio_series(data, label_a, label_b),), {"title": f"Ratio: {label_a} / {label_b}"})
        elif analysis == "zscore-spread":
            chart_render = (
                line_chart,
                (zscore_spread(data, label_a, label_b),),
                {"title": f"Z-score spread: {label_a} - {label_b}"},
            )
        elif analysis == "rolling-beta":
            chart_render = (
                line_chart,
                (rolling_beta(data, label_a, label_b),),
                {"title": f"Rolling beta: {label_a} vs {label_b}"},
            )

        self.app.call_from_thread(self._render_result, table_renderable, chart_render)

    def _render_result(self, table_renderable, chart_render) -> None:
        table = self.query_one("#compare-table", Static)
        chart = self.query_one("#compare-chart", PlotView)
        table.update(table_renderable if table_renderable is not None else "")
        if chart_render:
            render, args, kwargs = chart_render
            chart.show_chart(render, *args, **kwargs)
        else:
            chart.show_message("")
        self.notify("Done.")

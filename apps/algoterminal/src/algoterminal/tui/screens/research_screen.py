"""Research tab: walks a hypothesis through Data -> Methodology -> Backtest -> Writeup."""

from __future__ import annotations

from datetime import date

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Markdown, Select, Static, TabbedContent, TabPane

from algoterminal.analytics.stats import drawdown_periods, monthly_returns_table, rolling_sharpe
from algoterminal.charts.heatmap import drawdown_periods_table, monthly_returns_heatmap
from algoterminal.charts.mpl_charts import (
    drawdown_chart,
    equity_curve_chart,
    histogram_chart,
    line_chart,
    step_chart,
)
from algoterminal.data import default_provider
from algoterminal.research.backtest import (
    BacktestResult,
    has_backtest_result,
    load_backtest_result,
    run_backtest,
    save_backtest_result,
)
from algoterminal.research.data_stage import (
    load_quality_reports,
    pull_and_validate,
    save_quality_reports,
)
from algoterminal.research.methodology import load_strategy_module, scaffold_strategy
from algoterminal.research.storage import ResearchRecord, list_versions
from algoterminal.research.writeup import generate_writeup
from algoterminal.timeframe import DEFAULT_TIMEFRAME, TIMEFRAME_OPTIONS, resolve_timeframe
from algoterminal.tui.screens.hypothesis_modal import HypothesisModal
from algoterminal.tui.widgets.plot_view import PlotView

_NO_BACKTEST_MESSAGE = "No backtest run yet. Select a record and press Run Data+Backtest."


class ResearchPane(Horizontal):
    """Left: table of research records. Right: detail + a tabbed Charts section."""

    def __init__(self) -> None:
        super().__init__()
        self._records: list[ResearchRecord] = []
        self._selected: ResearchRecord | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="records-col"):
            yield DataTable(id="records-table", cursor_type="row")
            with Horizontal(id="research-buttons"):
                yield Button("New (n)", id="new-hyp")
                yield Button("Run Data+Backtest (r)", id="run-cycle")
                yield Button("Writeup (w)", id="gen-writeup")
                yield Select(
                    TIMEFRAME_OPTIONS,
                    value=DEFAULT_TIMEFRAME,
                    allow_blank=False,
                    id="research-timeframe",
                )
        with Vertical(id="detail-col"):
            yield VerticalScroll(Markdown(id="record-detail"), id="record-detail-scroll")
            with TabbedContent(id="charts-tabs"):
                with TabPane("Equity", id="chart-equity"):
                    yield PlotView(id="equity-chart")
                with TabPane("Drawdown", id="chart-drawdown"):
                    yield PlotView(id="drawdown-chart")
                with TabPane("Monthly Returns", id="chart-monthly"):
                    yield VerticalScroll(Static(id="monthly-returns-table"))
                with TabPane("Rolling Sharpe", id="chart-rolling-sharpe"):
                    yield PlotView(id="rolling-sharpe-chart")
                with TabPane("Distribution", id="chart-distribution"):
                    yield PlotView(id="distribution-chart")
                with TabPane("Exposure", id="chart-exposure"):
                    yield PlotView(id="exposure-chart")
                with TabPane("Worst Drawdowns", id="chart-dd-periods"):
                    yield VerticalScroll(Static(id="dd-periods-table"))

    def on_mount(self) -> None:
        table = self.query_one("#records-table", DataTable)
        table.add_columns("Nickname", "Version", "Title", "Backtested")
        self.refresh_records()

    def refresh_records(self) -> None:
        from algoterminal.research.storage import list_slugs

        self._records = []
        table = self.query_one("#records-table", DataTable)
        table.clear()
        for slug in list_slugs():
            for record in list_versions(slug):
                try:
                    hyp = record.load_hypothesis()
                    title = hyp.title
                except Exception:
                    title = "(unreadable)"
                self._records.append(record)
                table.add_row(
                    record.slug,
                    record.version,
                    title,
                    "yes" if has_backtest_result(record) else "no",
                )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_index = event.cursor_row
        if 0 <= row_index < len(self._records):
            self._selected = self._records[row_index]
            self._render_detail()

    def _render_detail(self) -> None:
        record = self._selected
        detail = self.query_one("#record-detail", Markdown)
        equity_chart = self.query_one("#equity-chart", PlotView)
        dd_chart = self.query_one("#drawdown-chart", PlotView)
        monthly_table = self.query_one("#monthly-returns-table", Static)
        sharpe_chart = self.query_one("#rolling-sharpe-chart", PlotView)
        dist_chart = self.query_one("#distribution-chart", PlotView)
        exposure_chart = self.query_one("#exposure-chart", PlotView)
        dd_periods_widget = self.query_one("#dd-periods-table", Static)

        chart_widgets = [equity_chart, dd_chart, sharpe_chart, dist_chart, exposure_chart]

        if record is None:
            detail.update("*No record selected.*")
            for widget in chart_widgets:
                widget.show_message("")
            monthly_table.update("")
            dd_periods_widget.update("")
            return

        hyp = record.load_hypothesis()
        lines = [
            f"# {hyp.title}",
            f"`{record.slug}/{record.version}`",
            "",
            f"**Thesis:** {hyp.thesis}",
            "",
            f"**Universe:** {hyp.universe} ({', '.join(hyp.symbols)})",
            f"**Asset class:** {hyp.asset_class.value}",
            f"**Expected edge:** {hyp.expected_edge}",
        ]

        has_result = has_backtest_result(record)
        if has_result:
            result = load_backtest_result(record)
            stats = result.stats
            lines += [
                "",
                "## Backtest",
                "",
                f"- CAGR: {stats.cagr:.2%}",
                f"- Sharpe: {stats.sharpe:.2f}",
                f"- Sortino: {stats.sortino:.2f}",
                f"- Max Drawdown: {stats.max_drawdown:.2%}",
                f"- Total Return: {stats.total_return:.2%}",
            ]
        else:
            lines += ["", f"*{_NO_BACKTEST_MESSAGE}*"]

        if record.writeup_path.exists():
            lines += [
                "",
                "## Writeup",
                "",
                "*Generated — see `writeup.md` in the research record directory.*",
            ]

        # Update the Markdown detail panel *before* drawing the charts:
        # Markdown.update() reflows content asynchronously, block by block,
        # which fires resize events on the chart panes as it settles. Drawing
        # charts first meant that reflow raced the chart render, so the
        # chart could get momentarily redrawn at an intermediate layout size
        # before settling — updating the text first lets that reflow finish
        # before the charts ever measure their size.
        detail.update("\n".join(lines))

        if has_result:
            self._render_charts(result, hyp.title)
        else:
            for widget in chart_widgets:
                widget.show_message(_NO_BACKTEST_MESSAGE)
            monthly_table.update(_NO_BACKTEST_MESSAGE)
            dd_periods_widget.update(_NO_BACKTEST_MESSAGE)

    def _render_charts(self, result: BacktestResult, title: str) -> None:
        equity_chart = self.query_one("#equity-chart", PlotView)
        dd_chart = self.query_one("#drawdown-chart", PlotView)
        monthly_table = self.query_one("#monthly-returns-table", Static)
        sharpe_chart = self.query_one("#rolling-sharpe-chart", PlotView)
        dist_chart = self.query_one("#distribution-chart", PlotView)
        exposure_chart = self.query_one("#exposure-chart", PlotView)
        dd_periods_widget = self.query_one("#dd-periods-table", Static)

        equity_chart.show_chart(equity_curve_chart, result.equity_curve, title=f"{title} — Equity")
        dd_chart.show_chart(drawdown_chart, result.equity_curve)

        returns = result.returns.dropna()
        if len(returns) >= 21:
            monthly_table.update(monthly_returns_heatmap(monthly_returns_table(returns)))
        else:
            monthly_table.update("Not enough history for a monthly breakdown yet.")

        sharpe = rolling_sharpe(returns)
        if not sharpe.empty:
            sharpe_chart.show_chart(line_chart, sharpe, title="Rolling Sharpe (63d)")
        else:
            sharpe_chart.show_message("Not enough history for a rolling Sharpe yet.")

        nonzero_returns = returns[returns != 0]
        if not nonzero_returns.empty:
            dist_chart.show_chart(
                histogram_chart, nonzero_returns * 100, title="Daily Return Distribution (%)"
            )
        else:
            dist_chart.show_message("No non-zero returns to plot yet.")

        exposure_chart.show_chart(step_chart, result.positions, title="Position Exposure")
        dd_periods_widget.update(drawdown_periods_table(drawdown_periods(result.equity_curve)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-hyp":
            self.action_new_hypothesis()
        elif event.button.id == "run-cycle":
            self.action_run_cycle()
        elif event.button.id == "gen-writeup":
            self.action_generate_writeup()

    def action_new_hypothesis(self) -> None:
        self.app.push_screen(HypothesisModal(), self._on_hypothesis_created)

    def _on_hypothesis_created(self, result) -> None:
        if result is None:
            return
        _, record = result
        self.refresh_records()
        self.notify(f"Saved research record {record.slug}/{record.version}")

    def action_run_cycle(self) -> None:
        if self._selected is None:
            self.notify("Select a research record first.", severity="warning")
            return
        timeframe = self.query_one("#research-timeframe", Select).value
        if not isinstance(timeframe, str):
            # Unreachable while the Select has allow_blank=False; narrows NoSelection.
            self.notify("Select a timeframe first.", severity="warning")
            return
        start, end = resolve_timeframe(timeframe)
        self.notify(
            f"Pulling data ({start} to {end}) and running backtest for {self._selected.slug}..."
        )
        self._run_cycle(self._selected, start, end)

    @work(exclusive=True, thread=True)
    def _run_cycle(self, record: ResearchRecord, start: date, end: date) -> None:
        hypothesis = record.load_hypothesis()
        provider = default_provider()

        data, reports = pull_and_validate(hypothesis, provider, start, end)
        save_quality_reports(record, reports)

        primary = hypothesis.symbols[0]
        if primary not in data or data[primary].empty:
            self.app.call_from_thread(
                self.notify, f"No data returned for {primary}.", severity="error"
            )
            return

        if not record.strategy_path.exists():
            scaffold_strategy(record, hypothesis)

        strategy = load_strategy_module(record.strategy_path)
        result = run_backtest(strategy, data[primary]["close"])
        save_backtest_result(record, result)

        self.app.call_from_thread(self._after_run_cycle, record)

    def _after_run_cycle(self, record: ResearchRecord) -> None:
        self.refresh_records()
        self._selected = record
        self._render_detail()
        self.notify(f"Backtest complete for {record.slug}/{record.version}.")

    def action_generate_writeup(self) -> None:
        if self._selected is None:
            self.notify("Select a research record first.", severity="warning")
            return
        record = self._selected
        if not has_backtest_result(record):
            self.notify("Run Data+Backtest before generating a writeup.", severity="warning")
            return

        hypothesis = record.load_hypothesis()
        reports = load_quality_reports(record)
        result = load_backtest_result(record)
        generate_writeup(record, hypothesis, reports, result)
        self._render_detail()
        self.notify(f"Writeup written to {record.writeup_path}")

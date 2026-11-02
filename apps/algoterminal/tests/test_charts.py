"""Tests for terminal (plotext), Rich heatmap, and matplotlib chart builders."""

from __future__ import annotations

import numpy as np
import pandas as pd
from algoterminal.analytics.stats import drawdown_periods, monthly_returns_table
from algoterminal.charts import heatmap, mpl_charts, terminal_charts
from PIL import Image
from rich.table import Table

from .conftest import make_close_series


def _returns(n=100):
    rng = np.random.default_rng(2)
    return pd.Series(rng.normal(0.001, 0.01, n), index=pd.date_range("2024-01-01", periods=n))


class TestTerminalCharts:
    def test_line_chart_builds_string(self):
        series = make_close_series(n=60)
        out = terminal_charts.line_chart(series, title="My Line", width=60, height=15)
        assert isinstance(out, str)
        assert "My Line" in out

    def test_line_chart_title_falls_back_to_name(self):
        series = make_close_series(n=30)
        series.name = "close"
        assert "close" in terminal_charts.line_chart(series, width=60, height=15)

    def test_multi_line_chart(self):
        series = make_close_series(n=40)
        frame = pd.DataFrame({"AAA": series, "BBB": series * 2})
        out = terminal_charts.multi_line_chart(frame, title="Multi", width=70, height=15)
        assert "Multi" in out
        assert "AAA" in out
        assert "BBB" in out

    def test_equity_and_drawdown_charts(self):
        equity = make_close_series(n=50)
        assert "Equity Curve" in terminal_charts.equity_curve_chart(equity, width=60, height=15)
        assert "Drawdown" in terminal_charts.drawdown_chart(equity, width=60, height=10)

    def test_rolling_correlation_chart_uses_series_name(self):
        corr = make_close_series(n=50) / 200
        corr.name = "A vs B (60d)"
        assert "A vs B (60d)" in terminal_charts.rolling_correlation_chart(corr, width=60)

    def test_histogram_chart(self):
        out = terminal_charts.histogram_chart(_returns(), title="Dist", width=60, height=10)
        assert "Dist" in out

    def test_step_chart(self):
        series = pd.Series(
            [0.0, 1.0, 1.0, -1.0], index=pd.date_range("2024-01-01", periods=4), name="pos"
        )
        assert "pos" in terminal_charts.step_chart(series, width=60, height=10)


class TestHeatmapColors:
    def test_color_for_extremes(self):
        assert heatmap._color_for(1.0) == "rgb(255,92,0)"
        assert heatmap._color_for(0.0) == "rgb(0,0,0)"
        grey = heatmap._color_for(-1.0)
        assert grey.startswith("rgb(")
        assert len(set(grey[4:-1].split(","))) == 1  # all channels equal -> grey

    def test_color_for_clamps_out_of_range(self):
        assert heatmap._color_for(5.0) == heatmap._color_for(1.0)
        assert heatmap._color_for(-5.0) == heatmap._color_for(-1.0)

    def test_return_color_handles_nan_and_sign(self):
        assert heatmap._return_color(float("nan")) == "grey15"
        assert heatmap._return_color(0.10) == "rgb(255,92,0)"  # saturates at +scale
        negative = heatmap._return_color(-0.10)
        assert len(set(negative[4:-1].split(","))) == 1


class TestHeatmapTables:
    def test_correlation_heatmap(self):
        corr = pd.DataFrame([[1.0, 0.5], [0.5, 1.0]], index=["AAA", "BBB"], columns=["AAA", "BBB"])
        table = heatmap.correlation_heatmap(corr)
        assert isinstance(table, Table)
        assert len(table.columns) == 3  # row-label column + one per symbol
        assert table.row_count == 2

    def test_monthly_returns_heatmap_with_gaps(self):
        index = pd.date_range("2024-01-01", "2024-02-15", freq="D")
        monthly = monthly_returns_table(pd.Series(0.001, index=index))
        table = heatmap.monthly_returns_heatmap(monthly)
        assert table.row_count == 1
        assert len(table.columns) == 14  # Year label + 12 months + Year total

    def test_drawdown_periods_table_empty(self):
        table = heatmap.drawdown_periods_table(pd.DataFrame())
        assert table.row_count == 1  # placeholder dash row

    def test_drawdown_periods_table_filled(self):
        equity = pd.Series([100, 110, 99, 112, 90], index=pd.date_range("2024-01-01", periods=5))
        table = heatmap.drawdown_periods_table(drawdown_periods(equity))
        assert table.row_count == 2


class TestMplCharts:
    def test_line_chart_returns_image(self):
        image = mpl_charts.line_chart(make_close_series(n=30), title="T", width=300, height=200)
        assert isinstance(image, Image.Image)
        assert image.size == (300, 200)

    def test_multi_line_chart(self):
        series = make_close_series(n=30)
        frame = pd.DataFrame({"AAA": series, "BBB": series * 2})
        image = mpl_charts.multi_line_chart(frame, title="Multi", width=320, height=200)
        assert image.size == (320, 200)

    def test_equity_drawdown_and_rolling(self):
        equity = make_close_series(n=30)
        assert mpl_charts.equity_curve_chart(equity, width=300, height=150).size == (300, 150)
        assert mpl_charts.drawdown_chart(equity, width=300, height=150).size == (300, 150)
        corr = equity / 200
        corr.name = None
        assert mpl_charts.rolling_correlation_chart(corr, width=300, height=150).size == (
            300,
            150,
        )

    def test_histogram_chart(self):
        image = mpl_charts.histogram_chart(_returns(), width=300, height=150)
        assert image.size == (300, 150)

    def test_step_chart(self):
        series = pd.Series(
            [0.0, 1.0, -1.0], index=pd.date_range("2024-01-01", periods=3), name="pos"
        )
        image = mpl_charts.step_chart(series, width=300, height=150)
        assert image.size == (300, 150)

    def test_message_image(self):
        image = mpl_charts.message_image("nothing to see", width=200, height=100)
        assert image.size == (200, 100)
        # the text must actually be drawn: not a flat single-color image
        assert len(image.getcolors(maxcolors=10_000)) > 1

    def test_message_image_clamps_degenerate_size(self):
        image = mpl_charts.message_image("x", width=0, height=0)
        assert image.size == (1, 1)

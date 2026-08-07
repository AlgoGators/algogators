"""plotext-based line/bar charts rendered as plain strings, so they can be
dropped into any Rich/Textual widget without pulling in a plotting backend
that needs a display.

Colors match the AlgoTerminal brand (see `algoterminal.theme`): a near-black
canvas, cream axes/ticks, and a hot-orange primary line.
"""

from __future__ import annotations

import pandas as pd
import plotext as plt

CANVAS = (19, 19, 22)
AXES = (19, 19, 22)
TICKS = (246, 244, 239)
ORANGE = (255, 92, 0)
CREAM = (246, 244, 239)
AMBER = (255, 176, 32)
RED = (255, 65, 54)
GREEN = (46, 204, 113)
GREY = (140, 140, 148)

PALETTE = [ORANGE, CREAM, AMBER, GREEN, RED, GREY]


def _reset(width: int, height: int) -> None:
    plt.clear_data()
    plt.clear_figure()
    # plotext clamps plotsize() to the OS-detected terminal size by default,
    # which can silently shrink charts to a stale/fallback size (e.g. inside
    # a Textual pane narrower or wider than the real terminal). We always
    # want the exact size the widget asked for.
    plt.limit_size(False, False)
    plt.plotsize(width, height)
    plt.canvas_color(CANVAS)
    plt.axes_color(AXES)
    plt.ticks_color(TICKS)


def line_chart(series: pd.Series, title: str = "", width: int = 90, height: int = 20, color=ORANGE) -> str:
    _reset(width, height)
    dates = [d.strftime("%Y-%m-%d") for d in series.index]
    plt.date_form("Y-m-d")
    plt.plot(dates, series.values.tolist(), marker="fhd", color=color)
    plt.title(title or series.name or "")
    return plt.build()


def multi_line_chart(frame: pd.DataFrame, title: str = "", width: int = 90, height: int = 20) -> str:
    _reset(width, height)
    dates = [d.strftime("%Y-%m-%d") for d in frame.index]
    plt.date_form("Y-m-d")
    for i, col in enumerate(frame.columns):
        plt.plot(dates, frame[col].values.tolist(), marker="fhd", label=str(col), color=PALETTE[i % len(PALETTE)])
    plt.title(title)
    return plt.build()


def equity_curve_chart(equity: pd.Series, title: str = "Equity Curve", width: int = 90, height: int = 20) -> str:
    return line_chart(equity, title=title, width=width, height=height, color=ORANGE)


def drawdown_chart(equity: pd.Series, title: str = "Drawdown", width: int = 90, height: int = 14) -> str:
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    drawdown.name = "drawdown %"
    _reset(width, height)
    dates = [d.strftime("%Y-%m-%d") for d in drawdown.index]
    plt.date_form("Y-m-d")
    plt.plot(dates, drawdown.values.tolist(), marker="fhd", fillx=True, color=RED)
    plt.title(title)
    return plt.build()


def rolling_correlation_chart(corr: pd.Series, width: int = 90, height: int = 16) -> str:
    return line_chart(corr, title=str(corr.name or "Rolling Correlation"), width=width, height=height, color=ORANGE)


def histogram_chart(series: pd.Series, title: str = "Distribution", bins: int = 40, width: int = 90, height: int = 16) -> str:
    _reset(width, height)
    plt.hist(series.dropna().values.tolist(), bins=bins, color=ORANGE)
    plt.title(title)
    return plt.build()


def step_chart(series: pd.Series, title: str = "", width: int = 90, height: int = 14, color=AMBER) -> str:
    """A step-style line chart — used for discrete signals like position exposure."""
    _reset(width, height)
    dates = [d.strftime("%Y-%m-%d") for d in series.index]
    plt.date_form("Y-m-d")
    plt.plot(dates, series.values.tolist(), marker="fhd", fillx=True, color=color)
    plt.title(title or series.name or "")
    return plt.build()

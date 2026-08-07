"""matplotlib-based charts rendered to raster images for the Textual TUI.

`terminal_charts.py` draws with plotext (Unicode glyphs approximating a line)
because that's what a plain ANSI-text CLI command can print. Inside the TUI
we can do better: render a real anti-aliased matplotlib figure and display it
as an image via `textual-image`, which picks the best protocol the terminal
supports (Sixel, Kitty's graphics protocol, or a truecolor half-block
fallback that still shows genuine pixel data). Same brand theme as
`terminal_charts.py`/`theme.py`, just pixels instead of glyphs.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image, ImageDraw, ImageFont

from algoterminal.theme import CREAM, ERROR, ORANGE, SUCCESS, SURFACE, WARNING

GREY = "#8C8C94"
PALETTE = [ORANGE, CREAM, WARNING, SUCCESS, ERROR, GREY]

DPI = 100

# Every chart here uses the same fonts, tick label sizes, and title style, so
# the margins fig.tight_layout() would compute are the same every time too —
# but tight_layout doesn't know that, and recomputes it via a full extra
# render pass (measuring every tick label's rendered extent) on every single
# call. That measurement pass alone was costing as much as the real draw,
# roughly doubling render time. Fixed margins, sized generously enough for
# this app's actual label lengths (short price/percentage/date values),
# skip that pass entirely.
_MARGINS = {"left": 0.09, "right": 0.985, "top": 0.88, "bottom": 0.18}


def _figure(width: int, height: int) -> tuple[Figure, plt.Axes]:
    # Built directly with Figure + FigureCanvasAgg instead of plt.subplots():
    # pyplot's figure registry adds bookkeeping overhead we don't need since
    # nothing here is ever shown interactively, and skipping it means we
    # never have to plt.close() to avoid leaking figures into that registry.
    fig = Figure(figsize=(max(width, 1) / DPI, max(height, 1) / DPI), dpi=DPI)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    fig.subplots_adjust(**_MARGINS)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for spine in ax.spines.values():
        spine.set_color(CREAM)
        spine.set_alpha(0.25)
    ax.tick_params(colors=CREAM, labelsize=8)
    ax.grid(True, color=CREAM, alpha=0.08, linewidth=0.6)
    return fig, ax


def _finish(fig: Figure, ax: plt.Axes, title: str) -> Image.Image:
    if title:
        ax.set_title(title, color=CREAM, fontsize=10, loc="left", pad=8)
    canvas = fig.canvas
    canvas.draw()
    width, height = canvas.get_width_height()
    # Read pixels straight off the Agg canvas instead of round-tripping
    # through fig.savefig(png)/Image.open(): that path re-encodes and then
    # decodes a PNG on every render, which dominated render time for charts
    # this size — the raw RGBA buffer is already sitting in memory.
    image = Image.frombuffer("RGBA", (width, height), canvas.buffer_rgba(), "raw", "RGBA", 0, 1)
    return image.convert("RGB")


def _date_axis(ax: plt.Axes) -> None:
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))


def line_chart(series: pd.Series, title: str = "", width: int = 900, height: int = 400, color: str = ORANGE) -> Image.Image:
    fig, ax = _figure(width, height)
    ax.plot(series.index, series.values, color=color, linewidth=1.8, antialiased=True)
    _date_axis(ax)
    return _finish(fig, ax, title or str(series.name or ""))


def multi_line_chart(frame: pd.DataFrame, title: str = "", width: int = 900, height: int = 400) -> Image.Image:
    fig, ax = _figure(width, height)
    for i, col in enumerate(frame.columns):
        ax.plot(frame.index, frame[col].values, color=PALETTE[i % len(PALETTE)], linewidth=1.6, label=str(col), antialiased=True)
    _date_axis(ax)
    legend = ax.legend(loc="upper left", fontsize=8, facecolor=SURFACE, edgecolor=CREAM, framealpha=0.6)
    for text in legend.get_texts():
        text.set_color(CREAM)
    return _finish(fig, ax, title)


def equity_curve_chart(equity: pd.Series, title: str = "Equity Curve", width: int = 900, height: int = 400) -> Image.Image:
    return line_chart(equity, title=title, width=width, height=height, color=ORANGE)


def drawdown_chart(equity: pd.Series, title: str = "Drawdown", width: int = 900, height: int = 280) -> Image.Image:
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    fig, ax = _figure(width, height)
    ax.fill_between(drawdown.index, drawdown.values, 0, color=ERROR, alpha=0.35, antialiased=True)
    ax.plot(drawdown.index, drawdown.values, color=ERROR, linewidth=1.4, antialiased=True)
    ax.axhline(0, color=CREAM, alpha=0.3, linewidth=0.8)
    _date_axis(ax)
    return _finish(fig, ax, title)


def rolling_correlation_chart(corr: pd.Series, width: int = 900, height: int = 320) -> Image.Image:
    return line_chart(corr, title=str(corr.name or "Rolling Correlation"), width=width, height=height, color=ORANGE)


def histogram_chart(series: pd.Series, title: str = "Distribution", bins: int = 40, width: int = 900, height: int = 320) -> Image.Image:
    fig, ax = _figure(width, height)
    ax.hist(series.dropna().values, bins=bins, color=ORANGE, alpha=0.85, edgecolor=SURFACE, linewidth=0.5)
    return _finish(fig, ax, title)


def step_chart(series: pd.Series, title: str = "", width: int = 900, height: int = 280, color: str = WARNING) -> Image.Image:
    """A step-style line chart — used for discrete signals like position exposure."""
    fig, ax = _figure(width, height)
    ax.step(series.index, series.values, where="post", color=color, linewidth=1.4, antialiased=True)
    ax.fill_between(series.index, series.values, 0, step="post", color=color, alpha=0.25, antialiased=True)
    ax.axhline(0, color=CREAM, alpha=0.3, linewidth=0.8)
    _date_axis(ax)
    return _finish(fig, ax, title or str(series.name or ""))


def message_image(message: str, width: int = 900, height: int = 280) -> Image.Image:
    """A themed placeholder image for empty/loading states, so PlotView never
    has to mix text widgets with image widgets."""
    image = Image.new("RGB", (max(width, 1), max(height, 1)), SURFACE)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), message, font=font)
    x = (image.width - (bbox[2] - bbox[0])) / 2
    y = (image.height - (bbox[3] - bbox[1])) / 2
    draw.text((x, y), message, fill=GREY, font=font)
    return image

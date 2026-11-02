"""A widget that displays a matplotlib-rendered chart inside Textual.

Uses `textual-image`'s Sixel renderer directly — Windows Terminal has
supported Sixel by default since v1.22, no config needed. We deliberately
don't use `textual_image.widget.AutoImage`: its runtime protocol detection
sends a query escape sequence and reads the terminal's response, but
`textual_image`'s own source notes this "will not work anymore once Textual
is started" because Textual's stdin-reading thread grabs the response first.
That race most likely explains an earlier blank/black render — auto-detect
probably misfired and picked Kitty's graphics protocol, which Windows
Terminal has never implemented, so the escape codes were silently dropped.
Forcing Sixel skips that detection entirely.

The chart is rebuilt at the widget's actual pixel size whenever it resizes,
so it always fills its pane cleanly.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PIL import Image as PILImage
from textual import events
from textual.app import ComposeResult
from textual.timer import Timer
from textual.widget import Widget
from textual_image._terminal import get_cell_size
from textual_image.widget import SixelImage

from algoterminal.charts.mpl_charts import message_image


class PlotView(Widget):
    """Renders a matplotlib chart as a real image, resized to fill the widget."""

    DEFAULT_CSS = """
    PlotView {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._renderer: Callable[..., PILImage.Image] | None = None
        self._redraw_timer: Timer | None = None
        self._last_render_size: tuple[int, int] | None = None

    def compose(self) -> ComposeResult:
        yield SixelImage()

    def show_chart(self, render: Callable[..., PILImage.Image], *args, **kwargs) -> None:
        """`render` is one of the chart-building functions from
        `algoterminal.charts.mpl_charts` (e.g. `line_chart`); any args/kwargs it
        needs besides `width`/`height` are bound now, and width/height (in
        pixels) are filled in from the widget's current size, recomputed on
        every resize.
        """
        self._renderer = partial(render, *args, **kwargs)
        self._last_render_size = None  # force a redraw even if the size hasn't changed
        self._redraw()

    def show_message(self, message: str) -> None:
        self._renderer = None
        self._last_render_size = None
        self.query_one(SixelImage).image = message_image(message)

    def on_resize(self, event: events.Resize) -> None:
        # Tab switches and terminal reflow fire a burst of resize events in
        # quick succession; redrawing (matplotlib render + Sixel write) on
        # every one of them is what made switching tabs feel glitchy.
        # Collapse the burst into a single redraw once sizing settles.
        if self._redraw_timer is not None:
            self._redraw_timer.stop()
        self._redraw_timer = self.set_timer(0.08, self._redraw)

    def _redraw(self) -> None:
        if self._renderer is None:
            return
        if self.size.width <= 0 or self.size.height <= 0:
            return
        if not self.is_on_screen:
            # A hidden ancestor (e.g. an inactive TabPane) keeps this widget's
            # last real size instead of zeroing it, so the size check above
            # doesn't catch it — without this, switching away from a tab with
            # a chart still queues a full matplotlib render + Sixel encode for
            # a widget nobody can see, which is what caused the stutter.
            return
        if (self.size.width, self.size.height) == self._last_render_size:
            # Clicking anywhere in the pane (opening/closing a Select, a
            # Button press) makes Textual re-run layout, which fires a Resize
            # event here even when our actual pixel size hasn't changed.
            # Re-encoding and re-writing the Sixel image on every one of
            # those was the "flash" on every click — only redraw when the
            # size actually moved.
            return
        image_widget = self.query_one(SixelImage)
        # Reserve a margin (in cells) smaller than the container on purpose.
        # textual-image's Sixel renderer re-derives the drawn pixel size as
        # (reserved cells) x (terminal's own reported cell-pixel size), not
        # from our source image's resolution — so the overflow comes from
        # how many *cells* we ask it to reserve, not from the image we hand
        # it. A tiny per-cell measurement error in the terminal's reported
        # cell size, compounded across dozens of columns/rows, bleeds into
        # neighboring widgets once a pane gets large (e.g. a full-width
        # chart shoving the docked command bar around) — a flat one-cell
        # margin is only enough for small panes, so scale it with size.
        margin_w = max(1, self.size.width // 40 + 1)
        margin_h = max(1, self.size.height // 40 + 1)
        cols = max(1, self.size.width - margin_w)
        rows = max(1, self.size.height - margin_h)
        image_widget.styles.width = cols
        image_widget.styles.height = rows
        cell = get_cell_size()
        width_px = max(200, cols * cell.width)
        height_px = max(120, rows * cell.height)
        image_widget.image = self._renderer(width=width_px, height=height_px)
        self._last_render_size = (self.size.width, self.size.height)

"""Correlation heatmap rendered as a Rich Table with color-coded cells.

plotext doesn't have a labeled matrix/heatmap primitive, so this uses Rich
directly, per the brief's "plotext (or Rich's own charting utilities)" allowance.
"""

from __future__ import annotations

import pandas as pd
from rich.table import Table
from rich.text import Text

from algogators.theme import ORANGE


def _color_for(value: float) -> str:
    """Diverging grey (-1) -> near-black (0) -> orange (+1) scale, matching the brand accent."""
    value = max(-1.0, min(1.0, value))
    if value >= 0:
        intensity = int(value * 255)
        return f"rgb({(255 * intensity) // 255},{(92 * intensity) // 255},0)"
    intensity = int(-value * 255)
    grey = 40 + intensity // 3
    return f"rgb({grey},{grey},{grey})"


def correlation_heatmap(corr: pd.DataFrame, title: str = "Correlation Matrix") -> Table:
    table = Table(title=f"[bold {ORANGE}]{title}[/]", show_lines=False, header_style=f"bold {ORANGE}", border_style="grey50")
    table.add_column("")
    for col in corr.columns:
        table.add_column(str(col), justify="right")

    for row_name, row in corr.iterrows():
        cells = [Text(str(row_name), style="bold")]
        for col_name in corr.columns:
            value = float(row[col_name])
            cells.append(Text(f"{value:+.2f}", style=f"bold on {_color_for(value)}"))
        table.add_row(*cells)

    return table


def _return_color(value: float, scale: float = 0.10) -> str:
    """Diverging grey (negative) -> orange (positive) scale for a return value,
    saturating at +/- `scale` (default 10%)."""
    if pd.isna(value):
        return "grey15"
    magnitude = max(-1.0, min(1.0, value / scale))
    if magnitude >= 0:
        intensity = int(magnitude * 255)
        return f"rgb({intensity},{(92 * intensity) // 255},0)"
    intensity = int(-magnitude * 255)
    grey = 40 + intensity // 3
    return f"rgb({grey},{grey},{grey})"


def monthly_returns_heatmap(table: pd.DataFrame, title: str = "Monthly Returns") -> Table:
    """Calendar-style table of monthly returns, color-coded by magnitude."""
    rich_table = Table(title=f"[bold {ORANGE}]{title}[/]", show_lines=False, header_style=f"bold {ORANGE}", border_style="grey50")
    rich_table.add_column("Year")
    for col in table.columns:
        rich_table.add_column(str(col), justify="right")

    for year, row in table.iterrows():
        cells = [Text(str(year), style="bold")]
        for col in table.columns:
            value = row[col]
            if pd.isna(value):
                cells.append(Text("-", style="dim"))
            else:
                style = f"bold on {_return_color(float(value))}" if col != "Year" else "bold"
                cells.append(Text(f"{float(value):+.1%}", style=style))
        rich_table.add_row(*cells)

    return rich_table


def drawdown_periods_table(periods: pd.DataFrame, title: str = "Worst Drawdowns") -> Table:
    rich_table = Table(title=f"[bold {ORANGE}]{title}[/]", header_style=f"bold {ORANGE}", border_style="grey50")
    for col in ("Start", "Trough", "End", "Depth", "Length (days)", "Recovered"):
        rich_table.add_column(col, justify="right" if col not in ("Start", "Trough", "End") else "left")

    if periods.empty:
        rich_table.add_row("-", "-", "-", "-", "-", "-")
        return rich_table

    for _, row in periods.iterrows():
        rich_table.add_row(
            str(row["start"].date()) if hasattr(row["start"], "date") else str(row["start"]),
            str(row["trough"].date()) if hasattr(row["trough"], "date") else str(row["trough"]),
            str(row["end"].date()) if hasattr(row["end"], "date") else str(row["end"]),
            f"{row['depth']:.2%}",
            str(row["length_days"]),
            "yes" if row["recovered"] else "no",
        )
    return rich_table

"""Rich Table builder for backtest / fund performance stats."""

from __future__ import annotations

from rich.table import Table

from algogators.analytics.stats import PerformanceStats
from algogators.theme import ORANGE


def build_stats_table(stats: PerformanceStats, title: str = "Stats") -> Table:
    table = Table(title=f"[bold {ORANGE}]{title}[/]", show_header=False, border_style="grey50")
    table.add_column("metric", style="bold")
    table.add_column("value", justify="right")

    table.add_row("CAGR", f"{stats.cagr:.2%}")
    table.add_row("Sharpe", f"{stats.sharpe:.2f}")
    table.add_row("Sortino", f"{stats.sortino:.2f}")
    table.add_row("Max Drawdown", f"{stats.max_drawdown:.2%}")
    table.add_row("Total Return", f"{stats.total_return:.2%}")
    if stats.win_rate is not None:
        table.add_row("Win Rate", f"{stats.win_rate:.2%}")
    if stats.n_trades is not None:
        table.add_row("Trades", str(stats.n_trades))

    return table

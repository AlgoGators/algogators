"""End-to-end example: universe -> hypothesis -> data -> methodology -> backtest -> writeup.

Run with:

    python examples/end_to_end.py

This exercises every stage of the research cycle against real (delayed,
free) data pulled through the default provider chain (yfinance, falling
back to Stooq per-symbol), so the tool is demonstrably functional out of
the box. The strategy logic below (a simple moving-average crossover) is
filled in by hand, exactly the way a QR team member would fill in the
scaffolded strategy.py themselves — nothing here is AI-generated.
"""

from __future__ import annotations

from algoterminal.charts.terminal_charts import drawdown_chart, equity_curve_chart
from algoterminal.console import console
from algoterminal.data import default_provider
from algoterminal.data.provider import AssetClass
from algoterminal.data.universe import Universe, UniverseStore
from algoterminal.research.backtest import run_backtest, save_backtest_result
from algoterminal.research.data_stage import pull_and_validate, save_quality_reports
from algoterminal.research.methodology import load_strategy_module, scaffold_strategy
from algoterminal.research.models import Hypothesis
from algoterminal.research.storage import create_record
from algoterminal.research.writeup import generate_writeup
from algoterminal.tui.widgets.stats_table import build_stats_table

SMA_CROSSOVER_STRATEGY = '''"""SMA crossover strategy — filled in by hand for the end-to-end example."""

from __future__ import annotations

import pandas as pd

FAST, SLOW = 20, 100


def generate_signals(prices: pd.Series) -> pd.Series:
    fast_ma = prices.rolling(FAST).mean()
    slow_ma = prices.rolling(SLOW).mean()
    signal = (fast_ma > slow_ma).astype(float) - (fast_ma < slow_ma).astype(float)
    return signal.fillna(0.0)


def size_positions(signal: pd.Series, prices: pd.Series) -> pd.Series:
    return signal.astype(float)


def apply_risk_rules(positions: pd.Series, prices: pd.Series) -> pd.Series:
    return positions.clip(-1.0, 1.0)
'''


def main() -> None:
    # 1. Universe — define once, reference by name.
    store = UniverseStore()
    universe = Universe(
        name="demo-tech",
        asset_class=AssetClass.EQUITY,
        symbols=["AAPL"],
        description="Single-name demo universe for the end-to-end example",
    )
    store.save(universe)
    console.print(f"[brand]1. Universe[/brand] saved: {universe.name} -> {universe.symbols}")

    # 2. Hypothesis
    hypothesis = Hypothesis(
        title="AAPL SMA crossover momentum",
        thesis="A 20/100-day SMA crossover captures medium-term trend in AAPL.",
        universe=universe.name,
        symbols=universe.symbols,
        expected_edge="Trend persistence following moving-average crossovers.",
        asset_class=universe.asset_class,
        risk_notes="No stop-loss in this demo; single-name, undiversified.",
        author="AlgoGators QR (example)",
    )
    record = create_record(hypothesis)
    console.print(f"[brand]2. Hypothesis[/brand] saved: {record.slug}/{record.version}")

    # 3. Data
    data, reports = pull_and_validate(hypothesis, default_provider())
    save_quality_reports(record, reports)
    for r in reports:
        console.print(
            f"   {r.symbol}: {r.rows} rows, {r.start} -> {r.end}, issues={r.issues or 'none'}"
        )

    primary = hypothesis.symbols[0]
    prices = data[primary]["close"]

    # 4. Methodology — scaffold, then fill in (this is what a QR analyst does by hand).
    scaffold_strategy(record, hypothesis)
    record.strategy_path.write_text(SMA_CROSSOVER_STRATEGY, encoding="utf-8")
    console.print(f"[brand]3. Methodology[/brand] strategy written: {record.strategy_path}")

    strategy = load_strategy_module(record.strategy_path)

    # 5. Backtest
    result = run_backtest(strategy, prices)
    save_backtest_result(record, result)
    console.print("[brand]4. Backtest[/brand]")
    console.print(build_stats_table(result.stats, title=f"{hypothesis.title} — Backtest"))
    console.print(equity_curve_chart(result.equity_curve, title="Equity Curve"))
    console.print(drawdown_chart(result.equity_curve, title="Drawdown"))

    # 6. Writeup
    writeup_md = generate_writeup(record, hypothesis, reports, result)
    console.print(f"[brand]5. Writeup[/brand] written: {record.writeup_path}")
    console.print(writeup_md)


if __name__ == "__main__":
    main()

# Portfolio report

Last revised: 2026-10-17

`predict-ngin report` writes one self-contained HTML page, `data/reports/portfolio.html`, and opens it.
The page has the data and the chart library inlined, so it works offline and can be sent as a single
file. Charts use [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/)
4.2.3 (Apache 2.0, vendored in `predict_ngin/viz/report/vendor/`).

```
uv run --no-sync predict-ngin report               # build and open
uv run --no-sync predict-ngin report --no-open --out some/where.html
uv run --no-sync predict-ngin report --sources paper_runs
```

## Levels

Portfolio → book → strategy. A **book** is one capital pool (a paper or forward run). A **strategy**
is a slice of a book (the run's `group` column, e.g. `base`, `rule`). The portfolio is the sum of
the books. Every level has the same page:

- **Metrics:** net and gross P&L; return and annualised return on the level's capital (annualised
  only after 7 days); Sharpe with its standard error; Sortino; P(Sharpe > 0) and the t statistic;
  max drawdown in $ and %; volatility; hit rate; profit factor; costs and their share of gross;
  best and worst day.
- **Diversification** (for levels with children): the sum of the children's P&L volatilities over
  the level's volatility.
- **Charts** (linked time axes): equity with the benchmark; cumulative net P&L of each child;
  drawdown; daily net P&L; gross vs net vs costs (the transaction-cost drag); and a 30-day rolling
  Sharpe once there are 40 days.
- **Tables:** children with all metrics (sortable; click a row to open it), correlation of
  children's daily P&L, and returns by month.

Pages under 30 days carry a warning: ratios at that length are noise.

## Conventions

Daily UTC periods with 365 periods a year. Returns are daily P&L over equity at the start of the
day. Drawdowns count from the starting capital, so a first-day loss is a drawdown. A strategy has
returns only when it has its own capital (`strategy_capital`); otherwise its ratios use dollar
P&L. Formulas: `predict_ngin/viz/performance.py`.

## Adding data

Built-in source `paper_runs`: every run in `config/paper_monitor.local.yaml` with a
`performance:` spec (see `config/paper_monitor.example.yaml`), or a standard `performance.jsonl`
(`predict_ngin.paper.runner.append_performance`). Spec keys for the report:

- `group`: the column that splits the book into strategies;
- `capital`: the book's capital;
- `strategy_capital: {name: dollars}`;
- `portfolio: false` to leave a run out (e.g. a study whose P&L is not capital-limited);
- `note`: text shown on the book's page.

Another source is a function returning `Book` objects (`predict_ngin.viz.report.model`):

```python
from predict_ngin.viz.report import Book, register_source

@register_source("my_backtests")
def my_backtests():
    daily = ...  # DataFrame: date, strategy, net, gross, fees, slippage (costs positive dollars)
    return [Book("my book", daily, capital=10_000, cost_cols=["fees", "slippage"])]
```

Import the module that registers it before building the report (or add it to
`predict_ngin/viz/report/sources_builtin.py`). Metrics, aggregation and charts need nothing else.

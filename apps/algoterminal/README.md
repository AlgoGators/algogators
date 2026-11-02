# AlgoTerminal

A terminal research workbench for the AlgoGators Quantitative Research (QR) team.

Everything here is deterministic and code-driven — there is no AI/LLM component
anywhere in this tool. It exists to structure and speed up the QR team's own
research process, not to replace it.

This tool is intentionally standalone from AlgoGators' internal trading
infrastructure — it will never connect to the fund's real data feed. Its
data layer is built entirely on free, public sources (yfinance today, more
over time) and is meant to keep growing on that basis, not be swapped out
for something internal later.

Themed after [algogators.com](https://www.algogators.com/): near-black
background, a hot-orange accent, and a terminal-native, monospace-forward look.

## What it does

- **Data layer** — a unified `DataProvider` interface across equities, futures,
  FX, crypto, custom baskets, and non-price alt-data. Ships with 28 built-in
  "universes" (reusable instrument baskets) out of the box, spanning market
  data via yfinance/Stooq (indices, sector ETFs, rates, credit, volatility,
  metals, shipping/freight equities, and more) as well as real alt-data
  integrations with **NASA POWER** (satellite weather/solar by location),
  **USGS** (earthquake activity by region), **FRED**, and the **World Bank**
  (macroeconomic indicators) — all free, keyless public APIs. Concurrent
  multi-symbol fetching, an incremental local parquet cache (only missing
  date ranges are re-fetched), and cached instrument/source metadata are
  built in. More free/public sources can be added the same way over time —
  this layer is not a placeholder for AlgoGators' internal feed.
- **Comparison engine** — compare anything against anything: a backtested
  strategy's equity curve, a raw instrument, or a mix of the two. Correlation
  matrices, rolling correlation, Engle-Granger cointegration, relative
  performance, and spread/ratio analysis, rendered as terminal tables and
  charts.
- **Research cycle** — a structured Hypothesis → Data → Methodology →
  Backtest → Writeup pipeline. Every run is saved as a versioned research
  record so a strategy's iteration history is browsable later. Backtests get
  a full **Charts section**: equity curve, drawdown, a monthly-returns
  calendar, rolling Sharpe, return distribution, position exposure, and a
  worst-drawdowns table.

It runs as a single full-screen terminal app — no web server, no browser.

## Install

```sh
curl -fsSL <url-to-this-repo>/install.sh | sh
```

This installs [pipx](https://pypa.github.io/pipx/) if it isn't already
present, then installs `algoterminal-cli` as an isolated, globally-available
`algoterminal` command.

To install from a local checkout instead:

```sh
git clone <this-repo>
cd AlgoGatorsCLI
pipx install .
```

Or, for local development:

```sh
python3 -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e .
```

Requires Python 3.11+.

## Usage

Launch the full-screen workbench:

```sh
algoterminal
```

A brief branded splash appears, then the app opens on three tabs — **Research**,
**Data**, **Compare** — plus a slash-command bar at the top for quick actions:

| Command       | Effect                                              |
| ------------- | ---------------------------------------------------- |
| `/hypothesis` | Open the new-hypothesis form                          |
| `/data`       | Pull + validate data and run the backtest for the selected record |
| `/backtest`   | Same as `/data` — runs the full data + backtest cycle  |
| `/compare`    | Jump to the Compare tab                                |
| `/writeup`    | Generate the markdown writeup for the selected record   |

The **Data** tab is where universes are browsed, created, and edited, and
where the local cache can be inspected (rows/date range/size per symbol) or
cleared, without leaving the TUI.

The **Compare** tab has two dropdowns — pick anything for each side (any
backtested strategy's equity curve, or any instrument from any saved
universe) and any of the six analyses, then Run. Strategies and raw
instruments can be freely mixed, e.g. a strategy's equity curve against the
instrument it trades. "Refresh list" picks up strategies/universes created
after the tab was opened.

Both the Research tab's "Run Data+Backtest" and the Compare tab's "Run" have
a timeframe dropdown (1M up to 10Y, or Max) controlling how much history is
pulled/compared — Research re-pulls data for that window before running the
backtest; Compare slices whatever it resolved (price series or equity curve)
to that window.

### Non-interactive CLI

Every stage is also available as a scriptable subcommand:

```sh
algoterminal universe list
algoterminal universe show g10-fx
algoterminal universe create my-basket --symbols AAPL,MSFT,GOOGL --asset-class equity
algoterminal universe add-symbol my-basket TSLA
algoterminal universe remove-symbol my-basket TSLA
algoterminal universe delete my-basket

algoterminal hypothesis                       # interactive wizard
algoterminal data <slug> [--version VERSION]
algoterminal backtest <slug> [--version VERSION]
algoterminal writeup <slug> [--version VERSION]

algoterminal compare matrix g10-fx
algoterminal compare relative spx-tech
algoterminal compare cointegration EURUSD=X GBPUSD=X

algoterminal cache status                     # what's cached locally
algoterminal cache clear --symbol AAPL        # or --provider, or both, or neither (clears everything)
```

Research records live under `~/.algoterminal/research/<slug>/<version>/` and
contain `hypothesis.yaml`, `data_quality.yaml`, `strategy.py` (your scaffolded
strategy — edit this yourself), `backtest_results.json`, `equity_curve.parquet`,
and `writeup.md`. Universes live under `~/.algoterminal/universes/`, and the data
cache under `~/.algoterminal/cache/`.

## The research cycle, end to end

1. **Hypothesis** — a guided form captures thesis, target universe, expected
   edge, and risk notes, and saves it as a versioned record.
2. **Data** — pulls data for the hypothesis's symbols (yfinance, falling back
   to Stooq per-symbol) and flags basic quality issues (missing values,
   insufficient history).
3. **Methodology** — scaffolds a `strategy.py` with three stub functions
   (`generate_signals`, `size_positions`, `apply_risk_rules`) for you to fill
   in. Nothing is generated for you here beyond boilerplate — the actual
   strategy logic is yours.
4. **Backtest** — runs your strategy against the pulled data through a small
   built-in vectorized backtest engine, producing a full charts section
   (equity curve, drawdown, monthly-returns calendar, rolling Sharpe, return
   distribution, position exposure, worst-drawdowns table) and a
   CAGR/Sharpe/Sortino/max-drawdown/win-rate stats table.
5. **Writeup** — renders the hypothesis + backtest results into a submittable
   markdown writeup, following the same Hypothesis/Data/Methodology/Backtest
   structure.

See [`examples/end_to_end.py`](examples/end_to_end.py) for a scripted
walkthrough of all five stages using real (delayed, free) data.

## Architecture

```
src/algoterminal/
  theme.py       AlgoTerminal brand theme (colors, Textual Theme, splash banner)
  console.py     Shared branded Rich console for CLI output
  data/          DataProvider interface + yfinance/Stooq/composite impls, cache, universes, metadata
  analytics/     correlation, cointegration, relative performance, spread/ratio, backtest stats
  charts/        plotext/Rich terminal chart + heatmap/table helpers
  research/      hypothesis/data/methodology/backtest/writeup pipeline + versioned storage
  tui/           Textual app: tabs, screens, widgets
  cli.py         Typer entry point
```

`DataProvider` is an abstract interface. `YFinanceProvider` and `StooqProvider`
are its current implementations, composed by `CompositeProvider` (first
non-empty result per symbol wins) via `data.default_provider()`. This tool
is deliberately standalone from AlgoGators' internal trading infrastructure
— it is never meant to connect to the fund's real data feed. Instead, the
data layer keeps being built out against more free/public sources over
time, added the same way (implement `DataProvider`, add it to the
composite chain). The analytics, research, and TUI layers only ever talk
to the `DataProvider` interface, so adding a source doesn't touch them.

## Data quality note

`yfinance` and Stooq are free, delayed data sources — the tool's real,
permanent data layer, not stand-ins for something else. Stooq in particular
now fronts its CSV endpoint with a bot check and may legitimately return
nothing in some environments — `CompositeProvider` handles that by falling
through, and no attempt is made to solve the challenge. Given the delayed,
free nature of these sources, don't use them for anything beyond research
scaffolding and demos.

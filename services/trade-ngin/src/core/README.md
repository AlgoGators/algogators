# Core

Types, errors, logging, configuration loading, calendars and the small rules every other module
shares. Headers are in `include/trade_ngin/core/`, sources in `src/core/`. The module map of the
whole engine is in [docs/SYSTEM_ARCHITECTURE.md](../../docs/SYSTEM_ARCHITECTURE.md), section 4.2;
this file covers the parts of `core/` that change what is stored or what a reader sees.

## Configuration

The runners load their configuration through `config_loader` (`include/trade_ngin/core/config_loader.hpp`):
`config/defaults.json` plus the portfolio's directory, and `require_loop_keys`, which refuses a
futures book that lacks a required key. Every file and key is in
[docs/CONFIG_GUIDE.md](../../docs/CONFIG_GUIDE.md).

`config_manager` and `config_version` (environments, validators and versioned configuration
migrations) are compiled into the library but only their own tests use them: no runner reads its
configuration through them. Do not take them for the loading path.

## A statistic with no value

A ratio whose denominator is zero has no value: a Sharpe ratio with no volatility, a Sortino ratio
with no downside, a Calmar ratio with no drawdown, a profit factor with no loss. The calculators
return `kNoRatio`, a NaN, for it (`include/trade_ngin/core/no_value.hpp`), never 0 and never a
large stand-in such as 999. A 0 would read as a measured zero and 999 as an excellent figure; a
missing value says what is true, that the ratio is not defined on that sample.

- In storage the value is NULL: the results writers never put a non-finite number into a
  statement (`include/trade_ngin/data/finite_statement.hpp`).
- On the console `ratio_or_na` prints "n/a" for it.
- `has_value` is the test: a statistic has a value when it is finite.

The backtest metrics calculator and the live historical metrics use it
(`include/trade_ngin/backtest/backtest_metrics_calculator.hpp`,
`include/trade_ngin/live/live_historical_metrics.hpp`). A query over stored statistics must treat
NULL as "no value", not as zero.

## Report text helpers

The book e-mail is rendered by the Python report module from the stored rows
([docs/SYSTEM_ARCHITECTURE.md](../../docs/SYSTEM_ARCHITECTURE.md), section 4.5). These header-only
helpers make what the C++ side prints, stores or hands to that module, so the console, the CSV
files and the e-mail show the same figures:

| Header | What it makes | Who uses it |
|---|---|---|
| `report_price_text.hpp` | a price with every decimal it has, at most eight and never fewer than two (0.0062995, 103.73828125, 677.00); "n/a" when it is not finite. Two fixed decimals printed a yen future as 0.01, so a row's quantity, price and notional did not multiply out | the live CSV exporter, the settled-day prices |
| `report_settled_marks.hpp` | the two prices a settled (T-1) row was booked from and at, taken from the settle's own arithmetic and never looked up again; a symbol with no bar that day gets no latest price. `stamp_settled_row` writes them to the row with its mark, `NO_BAR`, `ROLL` (a roll bar) or `WITHHELD` (a rejected price), into the columns of migration 041 | the three live runners, the CSV exporter |
| `report_risk_lines.hpp` | the risk lines: the overlay's expected risk of the stored book as a percent of the sizing capital beside the risk target, and the one-day 95 percent VaR in dollars. A figure with no value is left out. They never print `trading.live_results.portfolio_var`, a price-weighted figure without the contract multiplier that is not a value at risk | the futures runners' console |
| `report_rolls.hpp` | with `per_contract_rolls` on: one line per roll (the two contract months, the market move booked on the roll bar, the price difference between the two contracts, the roll's cost), the rollover warning for each held contract inside its roll window, and the front month and contract months of each root. Built from the per-contract rule's outlook, never from a hand-written calendar | `include/trade_ngin/live/report_rolls_json.hpp`, which hands the texts to the report module as `--rolls` |
| `report_leverage_console.hpp` | console leverage lines that name what they divide by ("on the sizing capital", "on the account value"), and "n/a" with the reason when that capital is missing or not positive | the three live runners' console |

## Smaller helpers

The rest of `core/` explains itself in each header's opening comment: `config_base.hpp` (save and
load of a configuration object as JSON), `portability_win.hpp` (POSIX time functions for MSVC,
force-included on Windows only), `stat_quality.hpp` (INFO lines naming the held symbols that
printed no bar on a settled date; nothing reads them back), `run_id_generator.hpp` (a backtest's
run id is its sorted strategy ids joined by `&` and the window's end instant in UTC, so two runs of
the same strategies to the same end date share one id; the second is refused, see
[src/backtest/README.md](../backtest/README.md)), `record_file.hpp` (the reference-check record
files written only when `TRADE_NGIN_SERIES_DUMP_DIR` is set; a production run never sets it) and
`resolved_sleeves.hpp` (the JSON a run stores about its own configuration). `timed_process.hpp`
runs a child process with a time limit; the live runners use it for the book e-mail
(`include/trade_ngin/live/python_mail.hpp`).

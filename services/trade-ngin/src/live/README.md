# Live Trading Module

## Overview

The live module (`src/live/`, headers in `include/trade_ngin/live/`) holds the parts a live runner uses to run one
date for one book: loading the stored book and the bars, pricing, P&L, margin, executions, the stored metrics, the
positions files, and for the equity book the corporate actions. The runners themselves are in `apps/strategies/`.

Where the detail lives:

| Topic | Document |
|---|---|
| What a run does on a date, the P&L frame, what each table and column holds | [docs/LIVE_RUN_CYCLE.md](../../docs/LIVE_RUN_CYCLE.md) |
| The container, the schedule, the wrapper and its exit codes, the watchdog, manual and catch-up rules, migrations | [docs/performance_upkeep.md](../../docs/performance_upkeep.md) |
| Which table or series is the source of truth for each fact | [docs/DATA_SOURCES_OF_TRUTH.md](../../docs/DATA_SOURCES_OF_TRUTH.md) |
| `average_price` on a stored position | [docs/AVERAGE_PRICE_LIFECYCLE.md](../../docs/AVERAGE_PRICE_LIFECYCLE.md) |
| The futures sizing loop and the risk modules | [docs/OPTIMIZER_AND_RISK_DESIGN.md](../../docs/OPTIMIZER_AND_RISK_DESIGN.md), [docs/RISK_MODULES.md](../../docs/RISK_MODULES.md) |
| Rolls and costs | [docs/FUTURES_ROLLS.md](../../docs/FUTURES_ROLLS.md), [docs/COST_MODEL.md](../../docs/COST_MODEL.md) |
| Equity corporate actions and the broker's basis | [docs/CORP_ACTIONS_DATA_BOUNDARY.md](../../docs/CORP_ACTIONS_DATA_BOUNDARY.md), [docs/BROKER_BASIS_RECONCILIATION.md](../../docs/BROKER_BASIS_RECONCILIATION.md) |
| Configuration files and keys | [docs/CONFIG_GUIDE.md](../../docs/CONFIG_GUIDE.md) |

---

## Files

Compiled sources in `src/live/`:

| File | Class | Role |
|---|---|---|
| `live_trading_coordinator.cpp` | `LiveTradingCoordinator` | owns and wires the live components for one book |
| `live_data_loader.cpp` | `LiveDataLoader` | the reads of a book's stored results, positions and history that the live components share (the runners also query the database directly for their guards) |
| `live_price_manager.cpp` | `LivePriceManager` | the closes a run prices with |
| `live_pnl_manager.cpp` | `LivePnLManager` | the day's P&L per position |
| `live_metrics_calculator.cpp` | `LiveMetricsCalculator` | the daily figures of a results row (pure calculations) |
| `live_historical_metrics.cpp` | `LiveHistoricalMetricsCalculator` | the since-inception figures |
| `execution_manager.cpp` | `ExecutionManager` | builds the day's executions from the change in the book |
| `execution_price_resolver.cpp` | `ExecutionPriceResolver` | the price a fill is booked at |
| `margin_manager.cpp` | `MarginManager` | posted and maintenance margin, notional and leverage |
| `csv_exporter.cpp` | `CSVExporter` | the positions files attached to the report |
| `python_mail.cpp` | (functions) | builds and runs the Python report command that sends the book e-mail (`python_mail_command`, `send_via_python`) |
| `report_roll_query.cpp` | (functions) | the held symbols and roll states the e-mail's roll view is asked for (`report_roll_query`) |
| `live_statistics_columns.cpp` | (functions) | the live statistics of migration 036 on the results row: the worst day, the monthly skew and tail ratio, the calendar-year returns and the fill counts |
| `live_settings.cpp` | (functions) | a run's settings: the config files with the portfolio's active `trading.strategy_config` row merged in, refusing a saved change that cannot be applied (`resolve_live_settings`) |
| `qt_desk.cpp` | (functions, namespace `qt`) | the QT desk's reads and writes around the three books and `trading.position_overrides` |
| `broker_frame.cpp` | (functions) | relates the book's adjusted cost basis to the broker's |
| `corporate_actions_applier.cpp`, `corporate_actions_classification.cpp`, `corporate_actions_lifecycle.cpp`, `corporate_actions_audit_log.cpp` | `CorporateActionsApplier`, `CorporateActionsAuditLog` | equity corporate actions: classification, application, the applied record |

Most of the other headers in `include/trade_ngin/live/` are header-only rules a runner calls, each with its design
comment at the top (`pnl_manager_base.hpp` and `price_manager_base.hpp` are base classes). The ones an operator
meets by name in a log:

| Header | Rule |
|---|---|
| `risk_module_failure.hpp` | the risk step refused the book or the sizing read failed: the book is held, the day is stored and marked, the run exits 3 |
| `run_metadata_marks.hpp` | the `risk_refusal` and `strict_assertion` marks on the day's `trading.live_run_metadata` row |
| `live_sizing_read.hpp` | the read of the account's settled equity that sizes the futures book |
| `late_bar_warning.hpp` | the one-line warning for a bar that arrived after the run that should have consumed it |
| `stored_book_ownership.hpp` | refuses a run that would not load positions the portfolio has stored |
| `live_listing_guard.hpp` | refuses a run whose book or symbol list disagrees with the declared listing dates |
| `data_freshness.hpp` | the feed staleness checks |
| `python_mail.hpp` | the book e-mail: runs the Python report module after the day is stored; a send that fails exits 4 |

---

## Running Live Trading

### Binaries

| Binary | Source | Book | Configuration it loads |
|---|---|---|---|
| `live_portfolio_conservative` | `apps/strategies/live_portfolio_conservative.cpp` | CONSERVATIVE, the futures trend book (the scheduled one) | `./config`, portfolio `conservative` |
| `live_portfolio` | `apps/strategies/live_portfolio.cpp` | BASE, the two-sleeve futures test book | `./config`, portfolio `base` |
| `live_equity_mr` | `apps/strategies/live_equity_mean_reversion.cpp` | the equity mean reversion book | `./config`, portfolio `equity_mr` |

A Release build puts them in `build/bin/Release/`. Each resolves `./config` and `logs/` from the current directory,
so start it from the directory that holds `config/`.

### Command Line

A live run always carries a date.

```bash
# Production (what the scheduled wrapper runs): the date and --send-email
./build/bin/Release/live_portfolio_conservative 2026-04-28 --send-email

# A test or a replay: the date, no --send-email (nothing is mailed)
./build/bin/Release/live_portfolio_conservative 2026-04-28

# The stored day's report written to a directory instead of mailed (from services/rpc)
python -m algogators_rpc.services.report --portfolio-id CONSERVATIVE_PORTFOLIO --date 2026-04-28 \
    --preview-dir /path/to/dir
```

### Arguments

All three binaries take the date and `--send-email`, in either order. The equity runner takes nothing else: an
argument that is neither a date nor `--send-email` prints the usage line and exits 1. It accepts only an exact
`YYYY-MM-DD` (`core::parse_utc_date`, `include/trade_ngin/core/time_utils.hpp`), and a second date on its line
replaces the first with no message. The two futures runners also take the date as `--date` and the QT desk's
arguments listed below the table; any other argument prints the usage line and exits 1. They test only that a date
could be read from the argument (`run_date_at_local_midnight`, `include/trade_ngin/live/session_book_gate.hpp`) and
refuse two different dates with exit 1 (`include/trade_ngin/live/run_args.hpp`). Type the date exactly as
`YYYY-MM-DD`, once.

| Argument | Format | Description |
|---|---|---|
| Date | `YYYY-MM-DD` | The date to run. Always given. A run started without a date prints the usage and exits 2: it runs, stores and sends nothing (`kDatelessRunExitCode`, `include/trade_ngin/live/run_args.hpp`). The futures runners read the date as midnight in the host's local time and the equity runner as midnight UTC; the two run modes are in [docs/LIVE_RUN_CYCLE.md](../../docs/LIVE_RUN_CYCLE.md), section 1.2. |
| `--send-email` | Flag | After the day is stored, run the report module, which builds the report and mails it. Production passes it; a test or a replay never does. |

The two futures binaries also take the QT desk's arguments (`--desk`, `--override`, `--publish`, `--fallback`,
`--send`, `--finalize-system`, `--send-now`, `--portfolio-config`, `--audit-id`), which the desk service passes;
[docs/design/desk-service.md](../../docs/design/desk-service.md) describes them.

### Environment

| Variable | Value | Description |
|----------|-------|-------------|
| QT_MAIL_PYTHON | A Python interpreter | The interpreter that runs the report module. Default `/opt/rpc/venv/bin/python`. |
| QT_MAIL_APP | A directory | The directory put on `PYTHONPATH` for the module. Default `/opt/rpc/app`. |
| QT_MAIL_TIMEOUT | Seconds | The time limit of one send. Default 300 (`PythonMailSettings`, `python_mail.hpp`); a value that cannot be read as a number, or is not above 0, keeps the default. When the limit passes, the module's whole process group is killed and the run exits 4. |
| QT_MAIL_TEST_TO | An address | On a QT `--send` run, mail only this address (no Cc) and record nothing: `sent_at` is left as it was, so a day already sent can be sent again as a preview. |
| QT_MAIL_CC | Addresses, comma separated | Added to the Cc of the book e-mail (read by the module). |

### Rules

- One runner at a time: never start two live or backtest runners together.
- A replay runs every date forward in order. A lone re-run of an older date is not supported, and the runner does
  not refuse it.
- The futures book runs every calendar day; a run refuses to start when the previous day's run is missing.

A futures live run exits 0 when it reaches the end of the run with the book not refused, 3 when it reaches the
end with the book held because the risk step refused it or the sizing read failed, 4 when the day is stored but
the book e-mail was not sent (a run that exits 3 keeps 3), and 1 when it stops early.
Exit 0 covers a completed day, a carried day, and a run in which a store failed, was logged and was passed over;
the evidence of a completed day is the date's `trading.live_results` row, not the exit code. The wrapper also
returns 0, without starting the binary, when it finds its lock held (nothing stored). A refusal by the risk step never ends at 0: the
`live_run_metadata` row is marked `risk_refusal` and the run ends at 3, whether a module could not answer or a
sleeve module decided to refuse, or at 1 when the refused book has no stored previous positions to be held at.
Exit 1 stores nothing when the run refuses before its `live_run_metadata` row is written, and can leave that
row, the signals or a half-written day when it stops later. Every live runner exits 2, having done nothing, when
it is started without a date. The equity runner returns 0, 1, 2 or 4, the backtest runners only 0 or 1.

The reasons, the full exit-code table (0, 1, 2, 3, 4, and the wrapper's 127), the log locations and the scheduled job are
in [docs/performance_upkeep.md](../../docs/performance_upkeep.md).

---

## What a run stores

A live run writes the `trading` schema: `trading.positions`, `trading.executions`, `trading.signals`,
`trading.live_results`, `trading.equity_curve` and `trading.live_run_metadata`, and for the equity book
`trading.corp_action_applied`. It reads its start date from `trading.strategy_trading_days_metadata`. There is no
`live` schema. What one
row of each table is, its key, when it is final and the meaning of each P&L column are in
[docs/LIVE_RUN_CYCLE.md](../../docs/LIVE_RUN_CYCLE.md): a row dated D is final only after the run of D+1, and
the P&L columns are not a single quantity times price change formula.

---

## Email Report

The book e-mail is sent by the Python report module `services/rpc/algogators_rpc/services/report`, not by the
runner. A run with `--send-email`, after storing the day, runs it through `send_via_python` (`python_mail.hpp`):

```bash
python -m algogators_rpc.services.report --portfolio-id ID --date YYYY-MM-DD [--strategy-id ID] [--no-session] \
    [--equities] [--alert TEXT] [--rolls JSON] [--attach FILE ...] [--to ADDR] [--preview-dir DIR]
```

A futures runner passes `--strategy-id` with the combined id the day is stored under, `--no-session` when the run
carried its book (no session the day before), `--alert` with the risk-module failure text (`risk_module_failure_text`,
`risk_module_failure.hpp`), and `--to` with `QT_MAIL_TEST_TO` when that is set. The equity runner passes
`--strategy-id` with its id (`LIVE_EQUITY_MEAN_REVERSION`), `--equities` (no futures notes, margin lines or symbols
reference) and `--to` with `QT_MAIL_TEST_TO` when that is set. Both pass their positions files from `CSVExporter` as
`--attach` (`python_mail_command`, `src/live/python_mail.cpp`).

With `per_contract_rolls` on, a futures runner also passes `--rolls`: a JSON object of the roll texts the e-mail
shows, made in C++ so the module only lays them out and computes none of them (`report_rolls_json`,
`include/trade_ngin/live/report_rolls_json.hpp`). The runner asks the database's roll view
(`PostgresDatabase::report_roll_view`) for the settled day's held symbols, the contract each holds and the run's
`ROLL` closing legs (`report_roll_query`, `include/trade_ngin/live/report_roll_query.hpp`), and the object carries:
one line per roll from its closing leg's split and both legs' costs; the held contracts inside their roll window,
with the target, the date to roll by and the deadline's kind (first notice or last trading day); the contract held
per root (the Symbols Reference's Front Month column); each root's cycle months; the footer's futures sentence; and
the note and symbol list for a settled row whose bar was a roll bar. With the switch off the runner passes no
`--rolls`. The module reads the stored day from the database (the date's `live_results` row and
the one before, the positions, the executions with the roll legs and the netting, the equity curve, the start
date), renders the HTML (a Jinja template, matplotlib charts) with a plain-text part, and sends it over SMTP.
On a day the book was held, the e-mail opens with the alert and the subject begins
`[RISK MODULE FAILED - BOOK HELD] `. The module exits 0 when the server accepted the message; otherwise the
runner logs `EMAIL_FAILED` and exits 4 (3 on a held day). The To recipients are `to_emails` of the portfolio's
`email.json`, the QT approvers and `QT_MAIL_CC` go in Cc; SMTP settings are in the same file
([docs/CONFIG_GUIDE.md](../../docs/CONFIG_GUIDE.md)). `--preview-dir` writes the report there and sends nothing;
`--to` sends to that one address only.

---

## Testing

The live tests are in `tests/live/` and are part of `trade_ngin_tests`:

```bash
ctest --test-dir build --output-on-failure
```

---

## References

- [Strategy Development Guide](../strategy/README.md)
- [Portfolio Module](../portfolio/README.md)
- [Backtest Module](../backtest/README.md)
- [Transaction Cost Module](../transaction_cost/README.md)
- [Data Module](../data/README.md)

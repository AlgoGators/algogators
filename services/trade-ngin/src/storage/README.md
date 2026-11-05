# Storage

The results managers map a run's output onto the database: `backtest_results_manager` onto
`backtest.*`, `live_results_manager` onto `trading.*`. Headers are in
`include/trade_ngin/storage/`, sources in `src/storage/`. The SQL itself is in
`src/data/postgres_database.cpp` and `postgres_database_extensions.cpp`; what one row of each
table is, its key and when it is final are in
[docs/LIVE_RUN_CYCLE.md](../../docs/LIVE_RUN_CYCLE.md) and
[src/backtest/README.md](../backtest/README.md).

## The shared base

`ResultsManagerBase` (`include/trade_ngin/storage/results_manager_base.hpp`) holds what every
write names: the schema (`backtest` or `trading`), the strategy id and name, the portfolio id, one
switch that turns all storage on or off, and the book. The book is the `portfolio_type` of every
live row, `system` unless `set_book` sets `qt_proposal` or `qt` (migration 021); any other value is
refused and the book is left as it was. A query over `trading.*` that does not filter on
`portfolio_type` can therefore add the QT desk's books to the system book.

## The equity curve refuses a value it cannot hold

`LiveResultsManager::save_equity_curve` refuses an equity value that is not finite or is under
1,000 (zero and negative included): it writes no `trading.equity_curve` row for that date, logs
`EQUITY_CURVE REFUSED` with the value, the date, the strategy and the portfolio, and returns the
error (`storable_equity_curve_value`, `include/trade_ngin/storage/live_results_manager.hpp`). Such a
value is a broken or uninitialised figure, not an account: the books start at 100,000 and 500,000.
Writing the latest stored equity in its place would put a number no run produced into the table
the statistics read, so the date is left without a point instead. The same rule guards the
rewrite of the previous day's point.

`save_all_results` writes every table even after one fails, and returns an error that names the
tables that failed, so a refused curve point is reported as a failed store. What a failed store
does to a run's exit code is in [docs/SYSTEM_ARCHITECTURE.md](../../docs/SYSTEM_ARCHITECTURE.md),
section 4.5.

Two neighbouring rules are in `core/` and `data/`: a statistic with no value is stored as NULL,
never as 0 or 999 ([src/core/README.md](../core/README.md)), and the writers put no non-finite
number into a statement (`include/trade_ngin/data/finite_statement.hpp`).

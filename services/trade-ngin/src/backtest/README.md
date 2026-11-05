# Backtest Module

## Overview

`src/backtest/` replays stored daily bars through the same `PortfolioManager` the live runners use,
books the fills and their costs, builds the equity curve, computes the run's metrics and stores the
results. `BacktestCoordinator` (`include/trade_ngin/backtest/backtest_coordinator.hpp`) owns the
run; the four runners that run a book (`bt_portfolio`,
`bt_portfolio_conservative`, `bt_equity_mr`, `bt_equity_validation`) call `BacktestCoordinator::run_portfolio`
(`src/backtest/backtest_coordinator.cpp`).

How a rebalance works is described in
[docs/OPTIMIZER_AND_RISK_DESIGN.md](../../docs/OPTIMIZER_AND_RISK_DESIGN.md); what a fill costs in
[docs/COST_MODEL.md](../../docs/COST_MODEL.md); rolls in
[docs/FUTURES_ROLLS.md](../../docs/FUTURES_ROLLS.md); the stored tables in
[docs/LIVE_RUN_CYCLE.md](../../docs/LIVE_RUN_CYCLE.md).

---

## File layout

```
src/backtest/
├── backtest_coordinator.cpp          # the run: load, cycle by cycle, metrics, storage
├── backtest_data_loader.cpp          # bars from the database
├── backtest_execution_manager.cpp    # fills and their costs
├── backtest_pnl_manager.cpp          # daily P&L
├── backtest_price_manager.cpp        # price history per symbol
├── backtest_metrics_calculator.cpp   # the metrics below
├── backtest_statistics_columns.cpp   # the statistics columns of migration 037
├── backtest_csv_exporter.cpp         # CSV output under apps/backtest/results/<portfolio_id>
├── equity_cost_warmup.cpp            # equity cost model warm-up
├── slippage_model.cpp                # slippage model classes; built and unit-tested, called by no run
└── transaction_cost_analysis.cpp     # cost analysis helpers; built and unit-tested, called by no run

include/trade_ngin/backtest/
├── backtest_coordinator.hpp, backtest_data_loader.hpp, backtest_execution_manager.hpp,
│   backtest_pnl_manager.hpp, backtest_price_manager.hpp, backtest_metrics_calculator.hpp,
│   backtest_csv_exporter.hpp, backtest_types.hpp
├── junk_signal_feed.hpp              # which bars a cycle feeds and which it withholds
├── backtest_statistics_columns.hpp   # the statistics columns of migration 037
├── stored_run_refusal.hpp            # refuses a run id that already has stored rows
├── consumed_series_record.hpp        # optional record of the consumed series
├── equity_cost_retier.hpp, equity_cost_warmup.hpp
└── slippage_models.hpp, transaction_cost_analysis.hpp
```

Two parts of the module are not on any run's path. `SlippageModel` and its two subclasses
(`slippage_models.hpp`) are used by their unit test only: a fill's slippage is priced by the cost
model ([docs/COST_MODEL.md](../../docs/COST_MODEL.md)). `TransactionCostAnalyzer`
(`transaction_cost_analysis.hpp`) is included by `bt_portfolio` and `bt_portfolio_conservative` but
never called; the run's cost totals come from `transaction_cost::run_cost_totals` (below).

The runners are in `apps/backtest/`:

| Binary | Source | Book |
|---|---|---|
| `bt_portfolio` | `bt_portfolio.cpp` | BASE (config `base`) |
| `bt_portfolio_conservative` | `bt_portfolio_conservative.cpp` | CONSERVATIVE (config `conservative`) |
| `bt_equity_mr` | `bt_equity_mean_reversion.cpp` | the equity book (config `equity_mr`) |
| `bt_equity_validation` | `bt_equity_validation.cpp` | equity validation runs |

Each loads `./config` through `ConfigLoader::load`, so it is run from the engine's folder, `services/trade-ngin/`, where `config/` is. Run one
runner at a time: the runners share tables and process-wide singletons.

```bash
./build/bin/Release/bt_portfolio_conservative
```

---

## The window and the warm-up

- **Window.** The window is `backtest.lookback_years` (in `config/defaults.json`) ending at the end
  date: `start = end - lookback_years`. The end date is the wall-clock time of the run, so the
  window slides with the clock, unless `backtest.frozen_end_date` is set, a test-only key that pins
  the end date and logs a warning (`ConfigLoader::resolve_backtest_window`,
  `src/core/config_loader.cpp`; the key's comment is in
  `include/trade_ngin/core/config_loader.hpp`). An end that is not frozen is the wall-clock
  instant; a frozen end is local midnight of the key's date on the host; the start is
  `lookback_years` earlier on the host's local calendar (`src/core/config_loader.cpp`).
- **Warm-up.** The first rows of the window are warm-up: the sleeves are fed and no fill is
  generated. The length is the longest EMA window of the book's trend sleeves, 256 rows with the
  shipped configuration (`BacktestCoordinator::calculate_warmup_days`,
  `src/backtest/backtest_coordinator.cpp`). A window of `lookback_years = 3` therefore trades
  about two years. A book with no trend sleeve has no warm-up.
- **Rows.** The equity curve has one row for the start and one per bar date. A futures book has a
  bar date for every session the data carries, Sunday sessions included, which is about 312 rows a
  year.

---

## Metrics calculation

`BacktestMetricsCalculator::calculate_all_metrics`
(`src/backtest/backtest_metrics_calculator.cpp`) computes everything below, on the same definitions
as the live statistics (`include/trade_ngin/live/live_historical_metrics.hpp`), so a backtest's
figures and a live book's are comparable.

- **The curve.** A UTC date the equity curve carries more than once counts once, by its last row.
  The first warm-up rows are then dropped (`filter_warmup_period`): the curve kept starts at the
  last flat warm-up row, which is the base and not a return. A curve with no more rows than the
  warm-up is not filtered.
- **The statistics grid.** Every statistic except the total return, the drawdown curve and the
  monthly returns is taken on the run's statistics grid (`statistics_grid_curve`). A futures run's
  grid is the Sunday-to-Friday dates on which at least 9 distinct stored symbols of the universe
  printed a bar (`BacktestCoordinator::statistics_grid`, `futures_statistics_grid_dates`). A
  predecessor contract that a listing date adds to the run (ES for MES before its listing) is not
  counted: its bars are copies of the listed contract's rows. An equity run's grid is its own rows,
  the NYSE sessions. A row
  dated off the grid (a Saturday point, a thin date) is no observation of its own: its P&L lands in
  the next grid return, and a grid date with no row repeats the level before it. Returns are the
  changes between the grid's levels.
- **K.** Every annualised figure uses `statistics.sessions_per_year` from `config/defaults.json`,
  the row frequency of the grid: 311.0574 for futures and 252 for equities. The in-memory result
  (`BacktestResults`, `include/trade_ngin/backtest/backtest_types.hpp`) records K and the number of
  grid returns (`statistics_sessions_per_year`, `statistics_days`); neither is a column of
  `backtest.results`.
- **No value.** A ratio whose denominator is 0 has no value (`kNoRatio`,
  `include/trade_ngin/core/no_value.hpp`): it prints as `n/a` and is stored as NULL, never as 0 or
  999.

| Metric | Formula as computed |
|---|---|
| Total return | `last / first - 1` of the post-warm-up curve, every row counted |
| Annualised return | the compound rate on the grid, `(1 + R)^(K / n) - 1`, R from the base to the last grid level and n the number of grid returns |
| Volatility | the sample standard deviation of the grid returns (divide by `n - 1`) `x sqrt(K)`; 0 under two returns |
| Sharpe ratio | `(mean grid return x K - risk-free rate) / volatility`, risk-free rate 0; no value with no volatility |
| Downside volatility | `sqrt(sum over every grid return of the squared shortfall below 0 / n) x sqrt(K)` (a return at or above 0 counts as a 0 shortfall) |
| Sortino ratio | `mean grid return x K / downside volatility`; no value when no return is below 0 |
| Max drawdown | the largest `(peak - level) / peak` over the grid's levels, the peak being the running maximum. The stored drawdown curve is taken on every row |
| Calmar ratio | annualised return over max drawdown; no value with no drawdown |
| VaR 95 | minus the grid return at index `floor(n x 0.05)` of the sorted returns |
| CVaR 95 | minus the mean of the `floor(n x 0.05) + 1` worst grid returns, the VaR observation included, so it is never below VaR |
| Beta, correlation | lag-1 autocorrelation of the book's own grid returns: `beta` is the slope of today's return on yesterday's, `correlation` the lag-1 correlation. Neither is measured against a market benchmark |
| Monthly returns | the sum of the row-to-row returns per calendar month (the month of the row's timestamp in the host's local time), over the whole curve (warm-up rows are flat) |

The statistics columns of migration 037 (the worst day, the sizing-capital figures, the monthly
skew and tail ratio, the calendar years) are taken on the same grid series with the same K
(`backtest_statistics_columns`, `src/backtest/backtest_statistics_columns.cpp`).

### Trade statistics

`calculate_trade_statistics` first nets the executions into the account's fills (`account_fills`):
the `STRATEGY` rows of one contract on one bar are the account's one order, so a full cross between
sleeves is no fill, a partial offset is one fill of the net size, and rows on one side are one
fill. It then walks those fills in order and tracks one net position per symbol.

- **A trade** is an account fill that reduces, closes or flips the tracked position of its symbol.
  A fill that opens or adds is not a trade. `total_trades` is the count of those fills, so one
  position closed in three steps counts as three trades.
- **Its P&L** is in dollars: `closed quantity x (fill price - average entry price) x the symbol's
  point value`, signed by the side of the position, less the fill's cost after netting
  (`transaction_cost::net_cost`). The point value is the one the equity curve books with. A fill
  through zero is one fill, and its whole cost goes to the trade it closes.
- **Win rate** is `winning trades / total trades`, a winning trade being one with P&L above 0.
  It is not a count of positive days.
- **Profit factor** is total profit over total loss; no value when there is no loss.
- **Average holding period** is the mean number of days from the position's open (or its last
  closing fill) to each closing fill.
- **Rolls.** A `ROLL` leg moves no tracked position and scores no trade; the open trade's entry
  price is carried across the roll by the gap between the two legs, and the leg's cost goes to the
  roll total. A `BORROW` row is a cost and not a trade. `total_strategy_fills` counts the account's
  fills, and `total_roll_fills` the roll legs.

The run's cost totals are computed separately, after netting, by
`transaction_cost::run_cost_totals` (`src/backtest/backtest_coordinator.cpp`).

---

## Data loading

`BacktestDataLoader` reads daily bars for the run's symbols and window from the database. A futures
run also loads history before the window to seed the trend estimators
(`trend_estimator::kHistoryCalendarDays`, `include/trade_ngin/strategy/trend_estimator.hpp`), so
the estimators do not start cold at the window's first row; the warm-up above still applies. Which
bars a cycle consumes and which it withholds is stated in
`include/trade_ngin/backtest/junk_signal_feed.hpp`. With `per_contract_rolls` on, the futures bars are the held
contract's own, from `futures_data.contract_ohlcv_1d`, and the rolls follow the engine's rule
([docs/FUTURES_ROLLS.md](../../docs/FUTURES_ROLLS.md)). The roll bar then books the old contract's
own move to its close, and the two `ROLL` legs (`RL-<sleeve>-<n>`, the closing leg's number even)
are priced at the roll bar's two closes and costed by the cost model; a leg with no usable close
fails the run (`ROLL_LEG STOP`). A micro's change of price source books the move with no legs and
no cost (`PRICE_SOURCE_CHANGE` in the log). The cases are in section 5 of
[docs/FUTURES_ROLLS.md](../../docs/FUTURES_ROLLS.md) (`BacktestCoordinator`,
`src/backtest/backtest_coordinator.cpp`). With the switch off, the vendor's change bar books 0. Where each table comes from and how it must be
read is in [docs/DATA_SOURCES_OF_TRUTH.md](../../docs/DATA_SOURCES_OF_TRUTH.md).

---

## What is stored

A run is stored in the `backtest` schema: `results`, `equity_curve`, `executions` and `run_metadata`
at the end of the run (`BacktestResultsManager`, `src/storage/backtest_results_manager.cpp`, called
from `BacktestCoordinator::save_portfolio_results_to_db`), and `final_positions` during the run, once
per bar date after the warm-up (`BacktestCoordinator::save_daily_positions`,
`src/backtest/backtest_coordinator.cpp`). `backtest.signals` exists and no run writes it. With `per_contract_rolls` on, a roll's closing
leg carries the roll split of migration 040 and `backtest.results` its two totals. What one row of each table is, its key and who writes it are in
[docs/LIVE_RUN_CYCLE.md](../../docs/LIVE_RUN_CYCLE.md).

---

## Testing

Every test is built into one binary, `trade_ngin_tests`, and `ctest` lists each case by its suite
name, so filter on suite names:

```bash
cd build
ctest -R "^Backtest" --output-on-failure
```

---

## References

- [docs/OPTIMIZER_AND_RISK_DESIGN.md](../../docs/OPTIMIZER_AND_RISK_DESIGN.md)
- [docs/COST_MODEL.md](../../docs/COST_MODEL.md)
- [docs/FUTURES_ROLLS.md](../../docs/FUTURES_ROLLS.md)
- [docs/LIVE_RUN_CYCLE.md](../../docs/LIVE_RUN_CYCLE.md)
- [docs/CONFIG_GUIDE.md](../../docs/CONFIG_GUIDE.md)
- [Strategy Module](../strategy/README.md)
- [Portfolio Module](../portfolio/README.md)
- [Optimization Module](../optimization/README.md)
- [Transaction Cost Module](../transaction_cost/README.md)
- [Data Module](../data/README.md)

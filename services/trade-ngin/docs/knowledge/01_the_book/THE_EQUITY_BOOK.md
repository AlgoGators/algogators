# The equity book

The equity book is a 100,000 account that holds US-listed stocks (mostly large caps; the template also holds an ADR,
ABEV, and a mid cap, ABM), long only, traded by one mean-reversion
strategy. Cash only is the account's stance: every position is meant to be paid for in full, with nothing borrowed and
no margin posted beyond what was paid. The code holds the long-only half (the book cannot short), but no code caps the
book's gross exposure (section 2.1). Prices are rebuilt each day from the vendor's per-bar dividend and split columns; corporate actions
restate the cost basis once each, through a record of the events already applied. Each run names its date, finds the
previous trading day on the NYSE calendar, carries the book over closed days, and stores its day in the same `trading`
tables the futures book uses. A dated live runner (`live_equity_mr`) and a separate backtest (`bt_equity_mr`) run it;
the repository's schedule starts only the futures runner, so the equity runner is run by hand with a date. The book was
built in early 2026, finished in September and merged on 2026-09-04 (AlgoGators/trade-ngin#76 to
AlgoGators/trade-ngin#82). Shorting, margin, several strategies in one book and a book that holds futures and equities
together are planned in AlgoGators/algogators#139 to AlgoGators/algogators#142.

This file is the book as a system: its account rules, its data, its calendar, its day, what is planned, and what changed
in the engine to make it run. The mechanics live in the tracked docs and are not repeated here:

| Topic | Tracked doc |
|---|---|
| What `average_price` means at each step of a live day, and the rules that keep a cost basis apart from a mark | [AVERAGE_PRICE_LIFECYCLE.md](../../AVERAGE_PRICE_LIFECYCLE.md) |
| Where each class of corporate action is read, the de-duplication table, the replay guards and the reset rule | [CORP_ACTIONS_DATA_BOUNDARY.md](../../CORP_ACTIONS_DATA_BOUNDARY.md) |
| How the book's adjusted basis relates to a broker statement | [BROKER_BASIS_RECONCILIATION.md](../../BROKER_BASIS_RECONCILIATION.md) |
| Which equity tables and columns to read, and which never to read | [DATA_SOURCES_OF_TRUTH.md](../../DATA_SOURCES_OF_TRUTH.md), sections 2 and 3 |
| The run date, closed days, the P&L frame, the stored columns, annualisation, refusals and exit codes | [LIVE_RUN_CYCLE.md](../../LIVE_RUN_CYCLE.md), sections 1, 2, 4.4, 4.5, 6, 7 and 10 |
| How to write and wire a new equity strategy | [EQUITY_STRATEGY_GUIDE.md](../../EQUITY_STRATEGY_GUIDE.md) |
| The config files and their keys | [CONFIG_GUIDE.md](../../CONFIG_GUIDE.md) |

Code locations were read on AlgoGators/algogators main, under `services/trade-ngin/`. They name the file and the symbol, because line numbers in the runner move. Short forms:
the live runner is `services/trade-ngin/apps/strategies/live_equity_mean_reversion.cpp`, the backtest runner
`services/trade-ngin/apps/backtest/bt_equity_mean_reversion.cpp`, the strategy
`services/trade-ngin/src/strategy/mean_reversion.cpp`, the daily cycle
`services/trade-ngin/include/trade_ngin/live/live_daily_cycle.hpp`.

## 1. The book at a glance

| Item | Value | Where |
|---|---|---|
| Portfolio | `EQUITY_MR_PORTFOLIO`, initial capital 100,000 | `services/trade-ngin/config_template/portfolios/equity_mr/portfolio.json` |
| Ids stored on every row | strategy id `LIVE_EQUITY_MEAN_REVERSION`, name `EQUITY_MEAN_REVERSION` | the live runner, `kEquityStrategyId`, `kEquityStrategyName` |
| Config directory | `equity_mr`, fixed in both runners; no portfolio argument | both runners' `ConfigLoader::load` call |
| Symbols in the template | AAPL, MSFT, AMZN, GOOGL, META, TMUS, NSC, ABT, ABEV, ABM | `portfolio.json` |
| Strategies | exactly one enabled; the live runner refuses more; one strategy type is registered, `MeanReversionStrategy` | the live runner's strategy-count check; `services/trade-ngin/include/trade_ngin/strategy/equity_strategy_builder.hpp` |
| Capital given to the strategy | live: the whole initial capital; backtest: capital times the strategy's weight | the live runner, `capital_allocation = initial_capital`; the backtest runner |

A symbol is traded only once the equity table holds its bars; the template's `_symbols_note` in `portfolio.json`
records the two symbols the live runtime config holds in place of ABEV and ABM for that reason.

The strategy, in one paragraph (parameters in `MeanReversionConfig`,
`services/trade-ngin/include/trade_ngin/strategy/mean_reversion.hpp`, the same values in the template): z is the close
less its 20-day mean, over the 20-day standard deviation. A flat symbol is bought when z falls below -2.0 and would be
sold short above 2.0; a long closes when z rises back above -0.5 or the close is 5% below the position's cost basis.
The target is capital x 0.1 x a volatility scalar, the scalar being 0.15 over the annualised volatility of the log
returns of the last 20 closes (19 returns), held between 0.25 and 2.0, so one name takes at most a fifth of the book.
Shares are fractional (to a millionth) above 1.00 dollar and 50,000 shares of average daily volume, whole otherwise.
The 5% stop reads the cost basis that fills and corporate actions maintain, never a mark. The live runner stores one `trading.signals` row per symbol per day from
the z-score; the backtest stores none.

## 2. The account and its rules

### 2.1 Cash only

What it does to the book: cash only is the account's stance. Every position is meant to be paid for in full from the
account's cash, so the book pays no interest and has no margin call or forced sale to meet.

Why: the engine does not model a margin account: it has no maintenance margin, no margin call or forced sale, and no
interest on borrowed cash, and shorting is not built. Cash only keeps the book inside what the engine models; Reg T is
the intended later mode (AlgoGators/algogators#140). The record supports the stance: the book never asked for more than
its cash. A name gets at most 20% of capital, and the strategy holds only names whose price has moved two standard deviations
from its mean. Over the measured live chain the gross-leverage limit of 1.0 never cut the book on any of 14,632
evaluations, and 2 of 19 dated rows held two or more names (the reason recorded in
`services/trade-ngin/config_template/portfolios/equity_mr/risk.json`). That the book's exposure stayed under 1.0 is a
measurement of that record, not a bound. Against that, a margin account carries a borrowing cost, a maintenance rule
and a margin-call path. Margin is planned for when a book needs it (section 6).

How the code holds it: `EquityAccountMode` has `CASH` and `REG_T`, every equity is `CASH` by default and nothing in
config changes it (`services/trade-ngin/include/trade_ngin/instruments/equity.hpp`); the live runner fixes the book as a
cash account by constant (`kBookIsCashAccount`). A cash-account equity in a book whose
`risk_reporting.max_gross_leverage` is above 1.0 stops both runners at start, because a cash account cannot borrow and
a cap above 1.0 promises leverage it cannot have (the leverage guard at the start of each runner). The guard reads the
configured value only, never the book's exposure.

What the code does not hold: no code caps the book's gross exposure. The strategy sizes each name on its own, up to
capital x 0.1 x 2.0 (`MeanReversionStrategy::calculate_position_size`). The only cap is a per-symbol share count,
`position_limits`, filled from `execution.position_limit_live` (500 shares) in the live runner and
`position_limit_backtest` (1,000) in the backtest runner (`services/trade-ngin/config_template/defaults.json`). No
portfolio-risk module runs (section 2.5). So a full set of the ten template symbols can size up to 2.0 times capital at
once, and the stored `cash_available` would then read below zero. Anyone who adds symbols or widens the sizing adds to
that ceiling, and nothing refuses it.

### 2.2 Margin priced at the cost basis

What it does to the book: `margin_posted` is the cash the open positions cost, not what they are worth today.

Why: in a cash account the margin a position posts is the money paid for it, and that does not move with the price.
Pricing it at the close made the posted margin rise with every gain, as if the account had paid more. The notional
(the book's exposure) stays at the previous close, because exposure is what the shares are worth now.

How: the margin line is priced at the row's `average_price`, the previous close only when no basis is known
(`services/trade-ngin/src/live/margin_manager.cpp`, `calculate_margin_requirements`; adopted 2026-09-27). Margin is 100%
of value in a cash account, and Reg T would be 50% on a long and 150% on a short
(`services/trade-ngin/src/instruments/equity.cpp`). Maintenance in a cash account is zero: nothing can be called.

### 2.3 cash_available

What it does to the book: `cash_available` is the cash the account actually holds, the money a new position could use.

Why: an unrealized gain cannot be spent until the shares are sold (the comment beside `equity_cash_available`). With
margin at the cost basis, value less margin is cash plus the unrealized gain, as the table below shows, so the gain
has to come out. So on a cash account `cash_available` = value -
unrealized - margin (`equity_cash_available`, `services/trade-ngin/include/trade_ngin/live/margin_columns.hpp`; adopted
2026-10-14). A margin account would keep value - margin; that is decided when margin is built.

Worked example (costs left out): the book buys 100 shares for 15,000 and sells them two days later at 160.

| Day | Close | Value | Unrealized | `margin_posted` (cost) | value - margin | `cash_available` | `equity_to_margin_ratio` | `margin_cushion` |
|---|---|---|---|---|---|---|---|---|
| 1, bought at 150.00 | 152 | 100,200 | 200 | 15,000 | 85,200 | 85,000 | 6.68 | 1.0 |
| 2 | 160 | 101,000 | 1,000 | 15,000 | 86,000 | 85,000 | 6.73 | 1.0 |
| 3, sold at 160.00 | flat | 101,000 | 0 | 0 | 101,000 | 101,000 | NULL | NULL |

The cash never moved while the shares were held (85,000 is what buying them left); value - margin would have shown
86,000 on day 2. The ratio is value over posted margin and the cushion is (value - maintenance) / value; both are NULL
on a flat book (`margin_columns.hpp`). The column comments are in `services/trade-ngin/migrations/034_column_comments.sql`.

### 2.4 No shorts

What it does to the book: the short half of the signal never trades. The book earns only on names that fell too far
and came back, and sits in cash otherwise.

Why: live accrues no borrow cost, so a live short would report a return it would not earn; rather than store that,
live refuses (the reason the runner's refusal gives). A short also changes the account: it needs a margin account (Reg
T posts 150% of a short's value, `services/trade-ngin/src/instruments/equity.cpp`), it pays a borrow fee every day the
short is held, and under the terms of a stock loan it owes the lender any dividend paid on the borrowed shares. The
backtest charges the borrow fee (an annual 25 to 500 basis points by liquidity flags, times a volatility multiplier of 1
to 3, per day over 365: `services/trade-ngin/src/transaction_cost/transaction_cost_manager.cpp`, called only from
`services/trade-ngin/src/backtest/backtest_coordinator.cpp`).

How: the strategy sets a short target to zero with a warning for any equity whose account does not allow shorts, which
is every equity on main (the short check in `on_data`); and the live runner refuses to store a short (the persist check
after the day's positions). Shorting is AlgoGators/algogators#140 (section 6).

### 2.5 No optimiser and no risk gate

The optimiser is off on both equity runners since 2026-09-01, and both refuse to start when `use_optimization` is true
(`apps::refuse_if_optimizer_requested`, `equity_strategy_builder.hpp`). Carver's dynamic optimisation exists to turn a
small account's fractional futures positions into whole contracts while keeping the portfolio close to its ideal; an
equity book trades fractional shares, so there is no rounding problem for it to solve, and the futures search cannot
see an equity strategy.

No portfolio-risk module gates the book: `risk.json` holds one module of type `none` (adopted 2026-09-18). Carver's
risk overlay is built for a leveraged futures book; on this book its leverage and jump terms never bound, and its
correlation term could fire only on the two rows of nineteen that held two or more names. What remained was a VaR
limit of 0.25, an equity calibration of its own, not one of Carver's parameters. It was set at 0.25 on 2026-09-04 while a
VaR gate still scaled this book, because that gate does not depend on leverage: a ten-name long-only book runs at about
0.29 annual volatility, so a limit of 0.10 could never be met. That gate no longer runs here; the limit now acts only on
the reporter, whose readings of the stored book's annual volatility ran from 0.19 to 0.31 on the 2026-06-15 to 2026-07-09
chain (the `_var_limit_rationale` in `risk.json`).
The Carver reporter still measures the book: portfolio VaR, maximum correlation and jump risk go to `live_results` and
the e-mail (`risk_reporting`; `var_limit` 0.25 kept on 2026-09-27).

## 3. Prices and corporate actions

What it does to the book: the strategy sees one continuous price series per stock in today's units, so a split or a
dividend never looks like a move; and every corporate action changes the stored position exactly once.

Why: the strategy trades on a 20-day z-score and a 5% stop. An unadjusted 4-for-1 split would read as a 75% crash and
fire both; a dividend drop would bring the stop closer by the dividend. Adjusting the series removes these false moves.

- **Prices.** Bars come from `equities_data.ohlcv_1d`. Each window is back-adjusted in SQL from the per-bar `div_cash`
  and `split_factor` columns, with factor 1 on the last bar, so the newest close is the raw close and the history is
  expressed in today's units (`services/trade-ngin/src/data/market_data_utils.cpp`, `build_equity_adjusted_query`;
  `compute_backward_adjustment_factors` in `market_data_utils.hpp` is its C++ mirror, used by the tests and
  `bt_equity_validation`). The vendor's adjusted columns are not read: they depend on a restating job,
  and they are stale after 2026-08-05, while `div_cash` and `split_factor` are facts about one bar that need no upkeep. Which
  columns to read and which never to read is in [DATA_SOURCES_OF_TRUTH.md](../../DATA_SOURCES_OF_TRUTH.md).
- **Three classes**, by what an event does to a position, each from the source that covers it
  (`services/trade-ngin/include/trade_ngin/live/corporate_actions_classification.hpp`):

| Class | Events | Source |
|---|---|---|
| Price restating | splits, ADR ratio changes, spin-offs, dividends | the per-bar columns |
| Series continuity | renames | `equities_data.ticker_aliases` |
| Termination | mergers, acquisitions, delistings | timed by `ohlcv_1d.delisting_date`; deal terms only from the frozen `equities_data.corporate_action` table |

- **The frozen deal-terms feed.** `corporate_action` holds no termination after 2025-08-29. The freeze date is a
  compiled constant (`kCorpActionTableFrozenAfter`) and the runner also measures the feed and warns. What it costs: a
  stock-for-stock merger after that date exits at the final close instead of rolling into the acquirer. A source of
  deal terms is AlgoGators/algogators#27; the engine's side of reading them is AlgoGators/algogators#147.
- **Dividends move the basis, not the cash.** On the ex-date the cost basis is divided by 1 + dividend / raw close of
  the ex-date (`services/trade-ngin/src/live/corporate_actions_applier.cpp`). The price series is total return, so the
  dividend is already in the P&L through the price; crediting cash too would count it twice.
  `live_results.total_dividend_income` is a record only. If the raw close cannot be loaded the dividend is skipped and
  left for the next run, with a warning.
- **Each event applies once.** `trading.corp_action_applied` records every event that changed the book, with the run
  date and the ratio the basis was divided by. Reset the book and this table together or not at all.
- **Spin-offs.** A parent without a positive basis is refused; the child is sold at its first close by default
  (`live.spinoff_child_policy` `liquidate_at_first_close`; `services/trade-ngin/src/live/corporate_actions_lifecycle.cpp`,
  `apply_spinoffs`): a child outside the configured universe has no bars loaded, so a held child could never be priced
  or traded and every later run would roll its target back (the note beside the key in
  `services/trade-ngin/config_template/defaults.json`); `hold` is safe only for a child already in the universe. Coincident reverse splits and splits
  folded into spin-off bars are handled case by case in the runner (cases met in testing: HLT, DD 2019-06-03,
  FTV 2025-06-30, LEN).
- **Terminations** synthesise an exit whose realized P&L reaches `live_results` through the corporate-action realized
  total, not through an execution row.
- **The backtest** has no corporate-action applier: it trades the adjusted series only, which is already total return.

## 4. Dates and the trading-days anchor

What it does to the book: each run books one named trading day, priced at that day's previous close, and closed days
add no P&L of their own.

Why: a run booked on the wrong date prices fills at the wrong close and lands P&L on the wrong day; every number after
it inherits the error.

- **A run names its date.** `live_equity_mr YYYY-MM-DD [--send-email]`; with no date the runner prints a message and
  exits 2 without running or sending. The date is parsed as midnight UTC (`core::parse_utc_date`), because a local-time
  parse moved the whole run to the previous day on a host east of UTC.
- **The calendar must load.** The holiday calendar must cover the run date and load in full, or the run stops. The
  previous trading day is found by walking back up to 14 days, and the run stops if none is found
  (`services/trade-ngin/include/trade_ngin/core/holiday_checker.hpp`, `max_lookback_days`). The resolved day is logged.
- **Closed days carry the book.** On a weekend or holiday the positions, `live_results` and `equity_curve` are written
  from the previous day's book, with no signals and no executions (`LiveDailyCycle::is_non_trading_day`,
  `carry_forward`). Where the weekend move lands is in [LIVE_RUN_CYCLE.md](../../LIVE_RUN_CYCLE.md) section 4.4.
- **The anchor.** `trading.get_trading_days()` counts the days that annualise the book's return from
  `trading.strategy_trading_days_metadata.live_start_date`, and falls back to the book's first `live_results` date only
  when no row exists (`services/trade-ngin/migrations/004_get_trading_days_portfolio_scope.sql`). A row later than the
  book's first day is worse than none: an anchor of 07-24 on a book that began 04-01 gave an annualised return of
  -1,674% on 07-27. The metadata row is authoritative: the runner warns when it disagrees with the book's first stored
  day and does not move it, because the first stored day can be wrong too: a stray backdated row would make the runner
  annualise the book over a span it never traded. Bad data is fixed in the data, not by the runner (adopted 2026-09-10;
  `services/trade-ngin/include/trade_ngin/live/trading_days_anchor.hpp`). A new live strategy needs its row seeded by hand
  ([RUNBOOKS.md](../05_operations/RUNBOOKS.md)).
- **Outside the US** exchanges have no holiday calendar and are treated as open, with one warning
  (`services/trade-ngin/src/instruments/equity.cpp`).

## 5. The daily cycle

A live run, in order. Each step's reason is beside it; the full cycle and what each step reads and writes is in
[AVERAGE_PRICE_LIFECYCLE.md](../../AVERAGE_PRICE_LIFECYCLE.md) section 2 and [LIVE_RUN_CYCLE.md](../../LIVE_RUN_CYCLE.md).

| Step | What happens | Why |
|---|---|---|
| 1 | Resolve the run date and the previous trading day; load the calendar | Every price and row is keyed on these two days |
| 2 | Anchor check (it warns and never moves the anchor); the deal-terms feed check; then the data freshness guard over the requested universe, where a symbol with no bar counts as stale (`services/trade-ngin/include/trade_ngin/live/data_freshness.hpp`) | The annualised return must count from the book's first day; a stale feed would trade on old prices |
| 3 | Load the previous day's book and set closed rows aside; run-gap guard | The day must continue the stored series, not skip a day |
| 4 | Build the run's `trading.live_run_metadata` row, apply corporate actions, seed the strategy; the row is written at the first point the run stores anything, so a refusal before that leaves none | Corporate actions change the book before the strategy reads it |
| 5 | One bar feed through the portfolio manager, after seeding; the cost model is fed from the same bars | Each bar must reach the strategy once, as in the backtest |
| 6 | Finalise the previous day's P&L; set the day's positions. Fills are priced only from a real close (the previous close, or the most recent one within `live.execution_price_max_staleness_days`, 5): a symbol that cannot be priced does not trade and keeps its carried quantity (`LiveDailyCycle::execute_day_t`); a fill the strategy cannot book stops the run. Then costs | A fill at an invented price would invent P&L |
| 7 | In-run identities: the position rows' realized must sum to the day's total, and the unrealized identity must hold; either failing stops the run | Stored rows and the book total cannot disagree |
| 8 | Finalise the previous day's `live_results` and equity curve; statistics through the last settled row; the `settled_at` stamp; the day's cumulative values | A row is final only after the next day's run |
| 9 | E-mail only with `--send-email`: the runner hands the stored day to the Python report (`services/trade-ngin/include/trade_ngin/live/python_mail.hpp`, the only book e-mail path since AlgoGators/trade-ngin#261) | The report reads what was stored, not what the run held in memory |

Tables written: `trading.positions` (delete then insert, keyed on strategy, portfolio, book type and date), `trading.executions`,
`trading.live_results`, `trading.equity_curve`, `trading.corp_action_applied`, `trading.live_run_metadata`,
`trading.signals`, and a record of the settings used. Read: `trading.strategy_config`,
`trading.strategy_trading_days_metadata`, `trading.get_trading_days()` and the three equity data tables.

**P&L columns.** `trading.positions.average_price` on an equity row is the weighted cost basis per share, restated on
the ex-date by the recorded ratio, and 0 on a closed row. One rare path can still write a previous close into it, when
a stored average price overflows the Decimal limit, against the rule that a basis never comes from a mark. Realized
columns are the realized P&L of closing fills, gross of costs; unrealized is the open position at the row's close.
Do not add realized or unrealized across asset classes; total P&L, daily P&L and portfolio value do add. An equity's
point value is 1.0 (`services/trade-ngin/src/live/live_pnl_manager.cpp`, `get_point_value`).

**Costs.** Tiered equity cost configs by average daily volume; a symbol the model was never fed falls back to 100,000
shares (`services/trade-ngin/src/transaction_cost/transaction_cost_manager.cpp`). SEC and TAF fees exist in the code
but `apply_regulatory_fees` is false wherever it is set. The model is in [COST_MODEL.md](../../COST_MODEL.md).

**The backtest.** `bt_equity_mr` writes `backtest.*` (results, equity curve, positions). The cost tiers warm up over
the 30 calendar days before the start (adopted 2026-09-21), so the first fills get a tier set from bars that existed
before the backtest began rather than the fallback. Unlike live, a
dropped fill only warns, and every fill is given to every strategy
(`services/trade-ngin/src/backtest/backtest_coordinator.cpp`, the `on_execution` loop); both are harmless with one
strategy and both are in AlgoGators/algogators#138. A test-only `backtest.frozen_end_date` exists. The trade count
snaps quantity residues below 1e-9 to zero (`TradePairing::kFlatBelow`,
`services/trade-ngin/src/backtest/backtest_metrics_calculator.cpp`); residues of 1e-6 exist, so the threshold may be
too small (unmeasured).

## 6. What is scheduled, and what is planned

**Scheduled.** The container's one cron entry starts the futures conservative runner
(`services/trade-ngin/live_portfolio.cron`, `services/trade-ngin/scripts/run_live_portfolio.sh`). Nothing in the
repository schedules the equity runner: it is built and run by hand with a date.

**Planned.** Some items in these issues were done on main after the issues were written (the margin price order and the
cash maintenance rule), so each item is checked against main before work starts.

| Issue | What | What it would do to the book, and why |
|---|---|---|
| AlgoGators/algogators#138 | Equities leftovers | Backtest-to-live agreement: a backtest that drops a fill with only a warning, or hands a fill to a strategy that did not make it, reports a book it never held. Harmless with one strategy, wrong with two |
| AlgoGators/algogators#139 | Equity margin: the leverage guard on a book holding futures and equities, the price the margin manager falls back to, an equity maintenance rule | Nothing changes for a cash book. It matters once a book mixes asset classes: on main the cash guard refuses any cash-account equity in a book whose gross cap is above 1.0, and every futures book's is |
| AlgoGators/algogators#140 | Reg T margin and equity shorting, with live borrow accrual | Opens the short half of the signal: selling names two standard deviations above their mean. The cost is real: borrow every day, 150% margin on a short, the dividends paid out, and a loss with no ceiling, which leans on the 5% stop. Live must accrue borrow before it may store a short. The issue is a plan; building it is its own decision |
| AlgoGators/algogators#141 | Several strategies in one equity book; two portfolios on one strategy | A second rule that moves differently from mean reversion would smooth the book's returns for the same risk, the diversification across rules at the heart of Carver's design. It carries the futures runner's multi-strategy pattern across and proves it with a trivial test strategy before any new strategy is judged |
| AlgoGators/algogators#142 | A hybrid book: futures and equity sleeves in one portfolio | A futures position posts only its margin, so a futures account holds most of its capital as cash; one portfolio could put part of that cash to work in equities, at the price of less cash to meet margin calls. The two paths differ in calendars, sizing, risk, pricing, P&L and margin, and the issue lists the design questions that follow |
| AlgoGators/algogators#163 | The held-symbol feed check does not refuse in the production time zone | A held stock halted or dropped by the feed for more than four days should stop a run for the day itself; in the New York time zone the check sees a past date and only warns. It matters once the book runs on a production host in that zone |

Known gaps in the code: four local-time bar-load sites in `services/trade-ngin/src/data/postgres_database.cpp`, a stale
comment in the dividend applier that still says the previous close, and no tracked script for resetting, replaying or
comparing an equity book.

## 7. What changed in the engine for equities, and why

The equity code first ran end to end, against the database, in late August 2026. The September finishing work changed
the shared engine and the equity runner in the ways below; each is in force on main. The failure modes it found
and the rules that prevent them are in [WARNINGS.md](../04_lessons/WARNINGS.md), section 7; how it was verified is in
[HOW_WE_VERIFY.md](../04_lessons/HOW_WE_VERIFY.md).

**The date frame.** What it does: the run date is midnight UTC, the previous trading day is found in UTC, and order ids
and stored rows carry the UTC date (`services/trade-ngin/include/trade_ngin/core/holiday_checker.hpp`,
`services/trade-ngin/src/live/execution_manager.cpp`). Why: a run must price its fills at the close of the day it
names. Before, the date was parsed in the host's local time: on a host east of UTC, 2026-08-06 parsed as 2026-08-05.
Moving the parse alone was not enough: a local-time lookup of the previous trading day then returned a closed day for
every Monday (the 06-15 run resolved to Saturday 06-13), so the book was marked at the wrong close and the first day's
unrealized P&L was off by 112, 153 and 193 on AAPL, GOOGL and META. All consumers now use one frame, and tests pin both
frames under a New York time zone.

**A single bar feed.** What it does: seeding no longer feeds bars; the portfolio manager feeds each bar to the strategy
once (`LiveDailyCycle`, the comment on seeding). Why: the strategy appends every bar it sees. Fed twice, its 20-day
history held 10 days twice over and its average-volume estimate ran over a series twice as long, which moves the
fractional-share test; live and backtest could not agree on the same data.

**Prices adjusted per bar.** What it does: prices are back-adjusted from the per-bar dividend and split columns, and
price-restating events come from the same columns. Why: the two sources used before both failed silently. The
corporate-action table stopped receiving events on 2025-08-29, so the earlier applier applied nothing after it, and
the vendor's adjusted columns are stale after 2026-08-05. An unapplied split leaves the basis in old units: 100 shares
bought at 400 before a 4-for-1 split show a loss of 30,000 that never happened.

**The dividend basis.** What it does: a dividend divides the cost basis by 1 + dividend / raw ex-date close and credits
no cash. Why: the adjusted series already carries the dividend as total return; cash on top would count it twice, and
a basis left unadjusted falls out of the adjusted frame after every dividend (a 25 cent dividend on a 100 entry would
report a 25 dollar loss on 100 shares). The raw ex-date close is the one the series' own adjustment uses, so the basis
and the marks move by the same factor. The book's basis therefore differs from a broker's by a known factor, set out in
[BROKER_BASIS_RECONCILIATION.md](../../BROKER_BASIS_RECONCILIATION.md).

**De-duplication.** What it does: each corporate action is recorded once it changes the book, with the run date and
the ratio; the window of events looked at reaches back to the oldest held position; a run refuses if the record holds
a row from a later run. Why: a missed run must not lose an event, and a re-run must not apply one twice (a split
applied twice halves the basis again and books a gain that never happened). A replay that reset the book but kept the
record showed a false unrealized loss of 4,824. The rules are in [CORP_ACTIONS_DATA_BOUNDARY.md](../../CORP_ACTIONS_DATA_BOUNDARY.md).

**Statistics on the trading-day grid.** What it does: one annualisation constant per book (equities 252), statistics
taken on the grid of NYSE trading days through the last settled day, sample standard deviation, win rate as wins over
wins plus losses, drawdown measured from initial capital; the futures and equity books never run different
conventions (`services/trade-ngin/config_template/defaults.json`, `statistics.sessions_per_year`). Why: both books
wrote a row every calendar day and counted calendar days, so weekend rows of zero return diluted the volatility (0.798
against 0.950 on one measured row), the annualised return came out about a third too small, and the win rate counted
idle days (10% against 20%). A book's statistics must be comparable with its backtest and with the other book. The
backtest's `backtest.results` statistics are fractions (live stores percent) taken on the same kind of grid: for an
equity run the grid is its own rows, the NYSE trading days, with K = 252 (migration
`services/trade-ngin/migrations/037_backtest_statistics.sql`, which restated migration 034's table comment; the backtest
side is in [src/backtest/README.md](../../../src/backtest/README.md)). Annualisation is in
[LIVE_RUN_CYCLE.md](../../LIVE_RUN_CYCLE.md) section 7.

**The cash rule.** What it does: margin is priced at the cost basis, maintenance is zero, and `cash_available` is
value - unrealized - margin; flat-book ratio and cushion are NULL. Why: sections 2.2 and 2.3.

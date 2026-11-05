# How rolls work

What the engine does when a futures position passes from one contract to the next: where the bars
come from, which contract the book holds on each date, when it rolls and into what, what is booked
on the roll bar, what is stored and what the book e-mail says. It applies to every futures book
(CONSERVATIVE, BASE and the QT books), in the backtest and in the live runners, which share the
code cited here.

The engine has two modes, chosen by `per_contract_rolls` in `portfolio.json`. With it on (every
futures template ships with it on) the book holds real contracts and rolls by its own rule:
sections 1 to 12 describe that mode. With it off the bars are the vendor's continuous series and
the roll is read from the vendor's contract switches: section 13 summarises that mode, which is
kept so that a run with the switch off stores exactly what it stored before the rule existed.

Companion documents: [FRONT_MONTH_EXAMPLES.md](FRONT_MONTH_EXAMPLES.md) (the cycle, the deadline
of each root, dated examples), [DATA_SOURCES_OF_TRUTH.md](DATA_SOURCES_OF_TRUTH.md) (the futures
tables), [COST_MODEL.md](COST_MODEL.md) (how a fill is priced, netting),
[LIVE_RUN_CYCLE.md](LIVE_RUN_CYCLE.md) (the run's date frame and the stored columns),
[OPTIMIZER_AND_RISK_DESIGN.md](OPTIMIZER_AND_RISK_DESIGN.md) (the hold set inside the rebalance),
[TREND_FOLLOWING_SYSTEM.md](TREND_FOLLOWING_SYSTEM.md) (the strategy end to end),
[CONFIG_GUIDE.md](CONFIG_GUIDE.md) (the keys).

The design comments that carry the detail are in the headers:

| Header | What it holds |
|---|---|
| `include/trade_ngin/data/contract_roll_rule.hpp` | the roll rule, quoted in full, and every case it decides |
| `include/trade_ngin/data/roll_cycle.hpp` | the cycle table: the delivery months each root holds |
| `include/trade_ngin/data/roll_calendar.hpp` | the trading days per product group, the exchange rules for the last trading day and the first notice day |
| `include/trade_ngin/data/contract_bars.hpp` | the per-contract rows, a contract's identity, the vendor's grid |
| `include/trade_ngin/data/per_contract_source.hpp` | the bar source: what a run is handed on each grid date |
| `include/trade_ngin/data/roll_series.hpp` | the roll library: the roll bar's move, the hold on a vendor switch, the legs, the split |
| `include/trade_ngin/live/live_roll_legs.hpp` | which rolls a live run books |
| `include/trade_ngin/core/report_rolls.hpp`, `include/trade_ngin/live/report_rolls_json.hpp`, `include/trade_ngin/data/report_roll_view.hpp` | what the book e-mail says about rolls |
| `include/trade_ngin/data/listing_dates.hpp` | listing dates, the micro pairs, the relabels |
| `include/trade_ngin/data/session_classifier.hpp` | which bars are consumed |

---

## 1. The switch

| Key (`portfolio.json`) | In the templates | Meaning |
|---|---|---|
| `per_contract_rolls` | `true` in `config_template/portfolios/conservative/portfolio.json`, `config_template/portfolios/base/portfolio.json` and the three QT templates | on: the bars come from the per-contract rule; off or absent: the vendor's `.v.0` series (section 13) |
| `per_contract_switch_bar` | `"held"` in the conservative and base templates; the QT templates leave it out, which means `"held"` | the bar handed out on a vendor switch bar the next bar has not yet kept (section 10) |

The four futures runners turn the source on or off with one call,
`configure_per_contract_source` (`src/data/per_contract_source.cpp`), right after the config is
loaded (`apps/backtest/bt_portfolio.cpp`, `apps/backtest/bt_portfolio_conservative.cpp`,
`apps/strategies/live_portfolio.cpp`, `apps/strategies/live_portfolio_conservative.cpp`). It
loads the futures calendar (section 3.3) and refuses to run when the calendar cannot be loaded or
`per_contract_switch_bar` is neither `"held"` nor `"vendor"`. Carry needs the switch on: carry
reads two contracts' own closes, which only the per-contract source has, and the call refuses
carry with the switch off.

What the switch needs in the database: `futures_data.contract_ohlcv_1d` with `expiration` filled
(a contract the rule needs with no expiration stops the run, naming it), and the vendor's grid in
`futures_data.ohlcv_1d` and `futures_data.ohlcv_1d_raw`. The desk's morning run also waits for
the per-contract pull's `ops.data_ready` row ([LIVE_RUN_CYCLE.md](LIVE_RUN_CYCLE.md)).

## 2. Where the bars come from

### 2.1 Three tables

| Table | What the rule reads from it | Key |
|---|---|---|
| `futures_data.contract_ohlcv_1d` | every outright contract's own daily bar, raw prices, with `raw_symbol` (GCZ6), `instrument_id` and `expiration` (the last trading day) | `(instrument_id, date)` (`platform/db/migrations/002_contract_ohlcv_1d.sql`); the rule names a contract by its identity, below |
| `futures_data.ohlcv_1d` | the date grid: the instants of the root's `<root>.v.0` rows | `(time, symbol)` on production (AlgoGators/algogators#24); a copy made without it may hold repeats |
| `futures_data.ohlcv_1d_raw` | the vendor's contract id on each grid bar, joined by symbol and instant | `(ts_event, symbol)` |

data-ngin's model declares the key (`services/data-ngin/src/data_ngin/infrastructure/db_models.py`), and production
has held it since AlgoGators/algogators#24 removed the repeated rows; a database or copy without the key can still hold
repeated rows of a symbol and instant, so the grid read takes `SELECT DISTINCT` instants (section 2.2; the repeated stretches are in
[DATA_SOURCES_OF_TRUTH.md](DATA_SOURCES_OF_TRUTH.md), section 4.3).

A contract's identity is `(root, raw_symbol, expiration)` (`ContractKey`,
`include/trade_ngin/data/contract_bars.hpp`). Neither the id nor the raw symbol names a contract
on its own: the vendor reuses ids across roots years apart (2017 is 6BX0 in 2020 and GCQ4 in
2023 and 2024), and `raw_symbol` carries a one-digit year (GCZ6 is the 2016 and the 2026
contract). The delivery year is resolved by the expiration, and an expiration outside the span
from two months before the delivery month to its end is refused (`ContractKey::make`). Contracts
are ordered by expiration: the rule's "expiry order".

### 2.2 The grid stays the vendor's

The source hands out bars only on the dates of the vendor's continuous series, the instants
`SELECT DISTINCT o.time FROM futures_data.ohlcv_1d` returns for the root
(`grid_window_query`, `src/data/per_contract_source.cpp`). The engine never adds a date. So a
missing `.v.0` row removes a real bar from every run even when the contract table has it, and the
continuous tables stay a required input with the switch on. The vendor's id on each grid bar is
read from `ohlcv_1d_raw` by symbol and instant (`VendorBar`, `contract_bars.hpp`), never by
matching the print; an empty id means the raw table has no row for that instant, or a row with no
id (a day filled by rule V, section 2.2a). The id feeds
the vendor trigger (section 3.1) and the case of a held contract with no bar (section 4).

### 2.2a Missing continuous rows: the hole fill

Because the grid is the vendor's, a `.v.0` row the vendor never wrote removes that bar from every
run. The hole fill writes such rows from the contract table, once over the history at the futures
release, by the rule of `scripts/futures_release/fill/sql/fill_core.sql` (run by
`scripts/futures_release/04_fill_holes.sh`; AlgoGators/algogators#36). AlgoGators/algogators#87 is
a draft that would fill holes after each daily pull.

A hole is a symbol and UTC date where the symbol is a `<root>.v.0` already in
`futures_data.ohlcv_1d`, the date lies inside that symbol's own span (its first to its last row) and
is not a Saturday, neither continuous table has a row of the symbol on that date, and the contract
table has a bar of the source root that day. The source root is the root of the contract the vendor
named on the symbol's nearest earlier row (the raw id joined to the contract table on id and date,
because ids are reused across years), so a hole in `MES.v.0` reads ES contracts before 2026-02-22
and MES contracts from then, as its existing rows do. The contract is chosen by the first rule
that applies:

| Rule | The contract | `instrument_id` on the raw row |
|---|---|---|
| B, bracket | the nearest rows before and after the hole name the same contract, and it has a bar on the date | the contract's |
| C, carry | the contract the nearest earlier row names, when it has a bar on the date | the contract's |
| V, volume | otherwise, on a weekday: the busiest contract of the source root on the previous weekday that has contract rows (ties: the earlier expiration, then the lower id), or the busiest of the date itself when that contract has no bar on it; on a Sunday: the previous weekday's V pick when it has a bar, else the busiest of the date | NULL |

The filled row is that contract's bar exactly (open, high, low, close, volume), dated 00:00 UTC; the
raw row copies `rtype` and `publisher_id` from the symbol's nearest earlier raw row. B and C keep
the contract the vendor already names, so a filled day never shows the roll rule a vendor switch
the vendor did not make: the switch stays on the next real row. On a V day the vendor's contract is
not known, and the rule reads the NULL id as no vendor information (section 4), so a V day cannot
trigger a vendor roll either; the crossover and the deadline still read the contract table.

### 2.3 What a run is handed on each grid date

The one futures bar query under every loader (`PostgresDatabase::get_market_data` and the id rows
of `get_futures_instrument_ids`) hands out the rule's series instead of the vendor's bars
(`per_contract_bars`, `src/data/per_contract_source.cpp`). On each grid date the bar is the held
contract's own OHLCV, and `instrument_id` is the held contract's id. Every loader, history store
and window above the query is unchanged.

No look-ahead: what a run is handed for a bar depends only on that bar and the bars before it, so
a live run's window, its estimator history and a backtest's whole range all read the same bar for
a date, and a backtest is a chain of dated live runs. The rule runs from the root's series start
(`series_start`, `include/trade_ngin/data/contract_rolls.hpp`: the first grid date on which the
contracts the series reads have a row), never from the window's start, and only on the grid up to
the window's last instant. Window dates before the series start are dropped with a WARN
(`drop_before_series_start`).

A bar that is part of a roll carries a roll field, `Bar::roll_from` (`RollFromKind`,
`include/trade_ngin/core/types.hpp`), that flows through every store untouched:

| Kind | On which bar | The bar | The field |
|---|---|---|---|
| `KNOWN` | a roll known on its own bar: a crossover, a deadline, a vendor switch an earlier bar kept | the new contract's bar | the old contract's close and id on this date |
| `AWAITS_NEXT` | a vendor switch bar only the next bar can confirm (`per_contract_switch_bar` `"held"`) | the held (old) contract's own bar | the target's close and id on this date |
| `AWAITS_NEXT_VENDOR` | the same bar with `per_contract_switch_bar` `"vendor"` | the target's own bar | the held contract's close and id |
| `AFTER_ROLL` | every bar after a roll, up to the next, where the contract left printed | the held (new) contract's bar | the contract left's close on this date, for a consumer that withheld the roll bar |
| `SOURCE_CHANGE`, `AFTER_SOURCE_CHANGE` | the micros' change of price source (section 11) and the bars after it | as `KNOWN`, `AFTER_ROLL` | as `KNOWN`, `AFTER_ROLL`; no trade follows |

## 3. The roll rule

The rule is quoted in full in `include/trade_ngin/data/contract_roll_rule.hpp`, with the points
where its text is silent and how the code decides them. It is built in
`src/data/contract_roll_rule.cpp` (`apply_roll_rule`). It reads only the vendor's grid (dates
and ids), the contract bars and the calendar: it never reads a price to name a contract.

### 3.1 Deadline, window, triggers, target

For the contract held:

| Term | Rule | Code |
|---|---|---|
| Deadline D* | its first notice day, or its last trading day where that comes first or where the contract has no notice day | `contract_dates` |
| Last trading day | the stored `expiration`; the exchange rule of `ExchangeRules::last_trading_day` only cross-checks it, and a mismatch is a WARN | `contract_dates`; `Rule::dates_of` |
| First notice day | `ExchangeRules::first_notice_day`, for the physically delivered roots whose notice period opens before the last trade (metals, grains, Treasuries, live cattle); there is no column for it | `src/data/roll_calendar.cpp` |
| Window | from the 5th trading day strictly before D* to the last actionable bar | `contract_dates` |
| Last actionable bar (LAB) | the latest trading day t with t + 1 calendar day still before D*. The live runners run seven days a week and the run dated R trades the bar of R-1 (a Saturday run trades Friday's bar at Friday's settle), so the LAB is the last bar whose trade happens before D* | `contract_dates` |

The roll happens on the first of three triggers:

| Trigger | When it fires | Logged as |
|---|---|---|
| (a) vendor | the vendor's contract differs from the last known vendor contract, expires later than the held one, and is the vendor's contract on the next grid bar too (the confirmation rule): the roll is on the vendor's date | `vendor` |
| (b) crossover | the first trading-day bar inside the window on which the target and the held contract both print and the target's volume is strictly greater | `crossover` |
| (c) deadline | the LAB, or the first trading-day bar after it when the grid has no bar on it | `deadline` |

On one bar the vendor trigger wins over the crossover, which wins over the deadline
(`Rule::run`). A vendor switch is detected on any grid bar; a roll falls only on a trading-day
bar.

**Target.** The front month of the root's cycle: the earliest-expiring contract that prints on
the roll bar, whose month is in the cycle and which expires after the held one (`cycle_target`).
When the cycle front itself prints nothing that day, the next cycle contract that does, with a
`target_not_front` WARN (`listed_cycle_front` decides what the front is: a month never listed,
such as 6LF1 in 2010, is passed over). A vendor switch into a month outside the cycle, or into a
contract later than the target, gives the timing only: the book still rolls into the cycle
front. Only a switch into a month outside the cycle is counted (`vendor_off_cycle`); a switch
past the target in a cycle month is not.

**Never back.** A vendor switch to an earlier-expiring contract is ignored, and so is a one-bar
stray (a switch the next bar does not keep): the held contract's own bar is read.

**Series start.** On the first grid date the book holds the vendor's contract if its month is in
the cycle and the date is not past that contract's LAB; otherwise the earliest cycle contract that
prints and is not past its LAB (`Rule::start_contract`). No such contract stops the series.

### 3.2 The cycle

`roll_cycle_table` (`include/trade_ngin/data/roll_cycle.hpp`) is a fixed table of the delivery
months each root holds: the months the vendor's own continuous series held in at least half of the
years it covers, measured over the whole history to 2026-10-02 (a month counts as held when the
vendor named the contract on two consecutive grid bars, so a one-bar stray does not count). The
table is fixed in the code so that the history does not move as data grows; a database test
(`test_roll_cycle_db`) recomputes it from the data and must match. The roll rule's target and the
e-mail's Rollover notice and Contract months column read the same table. The months of each root
are listed in [FRONT_MONTH_EXAMPLES.md](FRONT_MONTH_EXAMPLES.md).

### 3.3 The calendar: CME Group, per product group

The rule counts trading days on the futures exchange's own calendar, never on the NYSE equity
calendar (`FuturesCalendar`, `include/trade_ngin/data/roll_calendar.hpp`). A trading day for a
root is a weekday inside the file's coverage on which no closure lists the root's product group.
A Saturday bar (MBT) and a Sunday bar are never trading days, and a Globex session on a holiday
belongs to the next trade date. A Sunday bar is the evening open of the Globex session that CME settles as
Monday's trade date: the engine consumes it as a bar, with its own price move, but it has no settlement, so the
calendar never counts it, and a switch seen on a Sunday bar rolls on the next trading-day bar (section 5) (`TradingCalendar::is_trading_day`,
`src/data/roll_calendar.cpp`).

The calendar is `include/trade_ngin/core/futures_holidays.json`, written by
`scripts/generate_futures_holidays.py` (do not edit it by hand; `--check` regenerates and
compares). It covers 2010 to 2040 with 297 closures and eight product groups: equity index,
interest rates, FX, energy, metals (MGC and SIL with GC and SI), grains, livestock and crypto
(MBT). The groups differ on days CME closed some products and not others: a Good Friday with the
employment report settles only rates and FX (and crypto too on 2023-04-07 and 2026-04-03); Hurricane Sandy,
2012-10-29 and 10-30, closed only equity index; and on the two national days of mourning the NYSE
closed while CME closed only equity index and rates (2018-12-05) or only equity index
(2025-01-09), every other group trading. A date outside
the coverage throws `CalendarCoverageError` and the rule fails closed rather than guess.
`include/trade_ngin/core/holidays.json` (NYSE) stays for equities.

The rest of the futures side still reads the NYSE calendar: the live runners' "yesterday was a
holiday" check, the session classifier's closure lookup and the QT day kind
(AlgoGators/algogators#168). The runners' check decides whether the e-mail shows its closed-day
banner (the report's `--no-session` flag); the Python report then names the holiday in the
banner's title from its own CME closures by product group (`holiday_name`,
`services/rpc/algogators_rpc/services/report/builder.py`). Backtests make no NYSE closed-day check; the roll rule counts CME trading days in a backtest as in a live run. The ICE calendar and the
expiry rules of new futures are AlgoGators/algogators#169.

### 3.4 One roll by the rule: GC, August 2026

GCQ6 (August 2026) is held. Its first notice day is the last trading day of July, Friday
2026-07-31; its last trading day is 2026-08-27; so D* is 07-31. The five trading days before D*
are 07-24, 07-27, 07-28, 07-29 and 07-30: the window opens on 07-24. The LAB is Wednesday 07-29,
because Thursday 07-30 plus one day is not before D*. The target is GCZ6: GC's cycle is GJMQZ, so
GCU6 (September) and GCV6 (October) are never held. Volumes are contracts traded that day. The
case is pinned by the database test
`ContractRollRuleDbTest.GcAugust2026RollsOnTheCrossoverOfWednesday0729`
(`tests/data/test_contract_roll_rule_db.cpp`).

| Bar | Trading day | Vendor names | GCQ6 (held) volume | GCZ6 (target) volume | What the rule does |
|---|---|---|---:|---:|---|
| Fri 07-24 | yes | GCQ6 | 77,474 | 8,982 | window opens; no crossover |
| Sun 07-26 | no | GCQ6 | 5,437 | 507 | not counted |
| Mon 07-27 | yes | GCQ6 | 77,116 | 13,313 | no crossover: hold |
| Tue 07-28 | yes | GCQ6 | 77,452 | 23,314 | no crossover: hold |
| Wed 07-29 | yes | GCQ6 | 22,870 | 117,650 | crossover, and the LAB (its run is Thu 07-30, before D*): roll to GCZ6, old close 4,081.7, new close 4,142.0 |
| Thu 07-30 | yes | GCQ6 | 4,153 | 123,040 | GCZ6 held, its own bar (close 4,164.4); the vendor's earlier contract is ignored |
| Fri 07-31 (D*) | yes | GCZ6 | 435 | 99,065 | vendor switch to a contract not later than the held one: nothing |

The roll is known on its own bar (`KNOWN`): the run dated 07-30 consumes the 07-29 bar, books the
two legs at 4,081.7 and 4,142.0 and trades on before first notice. A book that followed the
vendor would have rolled on 07-31 and traded it on the next run, on or after first notice. More
dated rolls, with the run-date deadline in live, are in
[FRONT_MONTH_EXAMPLES.md](FRONT_MONTH_EXAMPLES.md).

## 4. How the rule handles each case

| Case | What the code does | Code |
|---|---|---|
| The held contract has no bar on a grid date and the vendor names another contract (or its id names none) | the held contract's last close is carried; volume 0; a `carry` WARN names the date and the close. With no earlier row there is no close to carry and the source stops the run | `Rule::fill_bar`; `bars_of_series` |
| The held contract has no bar and the vendor names the held contract | the vendor's own print of that contract is the bar, with a `vendor_bar` WARN | `Rule::fill_bar` |
| The vendor switches to an earlier-expiring contract | ignored: never back; the held contract's own bar is read | `Rule::run` (`vendor_candidate`) |
| The vendor switches for one bar only (the next bar does not keep its contract) | no roll. The switch bar is handed out exactly as a kept switch bar would be, because a run whose bars end there cannot tell the two apart (`TentativeRoll`); the next consumed bar shows that nothing rolled | `Rule::run`; `per_contract_source.hpp` |
| The vendor switches into a month outside the cycle, or past the target | the switch times the roll; the target is still the cycle front; counted only when the month is outside the cycle | `Rule::run` (`vendor_off_cycle`) |
| The vendor's grid bar has no id in the raw table | that day has no vendor contract (`unknown_vendor_days`); the vendor trigger cannot fire there, the crossover and the deadline still can | `Rule::run`; `VendorBar` |
| A vendor switch on a Sunday or on a holiday session | detected on that bar, kept by the next grid bar, and rolled on the next trading-day bar on which the held contract has a row and a target prints; that roll is known on its bar | `Rule::run`; `per_contract_source.hpp` |
| A roll is due and the held contract has no bar that day | the roll waits for the next trading-day bar where both print, never later than the LAB (`deferred` WARN) | `Rule::run` |
| A roll is due at or after the LAB and the held contract still has no bar | it rolls with a fallback: the old contract's move on the roll bar is read as the new contract's move from the previous grid date when the new contract printed on that date, else as 0. Each case is logged (`roll_new_move`, `roll_zero`; `PER_CONTRACT_ROLLS fallback roll`) and listed | `Rule::run`; `bars_of_series` |
| No cycle contract after the held one prints | the contract stays held and the roll waits (`no_target` WARN) | `Rule::run` |
| The LAB is not on the vendor's grid | the deadline rolls on the next grid bar, flagged `late` (`late_deadline` WARN) | `Rule::run` |
| The vendor's own roll would come after the deadline | the deadline trigger rolls at the LAB whatever the vendor does later | `Rule::run` |
| Two rolls in a short span | no minimum time between rolls: each roll is booked with its two legs. Two rolls resolved on one bar (a vendor switch kept by a bar that is itself a roll) cannot be booked: `RollTracker::add` throws and the run stops | `RollTracker::add`, `src/data/roll_series.cpp` |
| A contract the rule needs has no expiration | the series is refused with an error naming the contract | `ContractBook::require` |

Every fallback WARN is logged once per run, and only for the dates of the run's own window (a live
run logs from its T-1 bar, the bar it consumes for the first time).

## 5. The roll bar

### 5.1 A roll known on its bar

A crossover, a deadline, or a vendor switch an earlier bar kept (a Sunday switch rolled on
Monday) is known on the roll bar itself. The bar is the new contract's own bar, carrying the old
contract's close and id (`KNOWN`). On that bar (`roll_series.hpp`, the per-contract paragraphs;
`RollTracker::Status`):

- **P&L** is the old contract's own move: `quantity x (old close on the roll bar - previous close)
  x multiplier`. It is in the day's P&L like any other day's move.
- **Return** is the old contract's own: `(old close - previous close) / previous close`.
- **The step** (new close minus old close, both of the roll bar) moves the back-adjustment
  constant only, so the adjusted level moves by the old contract's move and never by the gap
  between the contracts.
- **The legs** are both booked on this bar at its two closes (section 6). There is no hold: the
  symbol trades on the roll bar's rebalance.

Within each contract stretch the daily rows sum to `quantity x (last close - first close) x
multiplier` of that contract, so the drift of each contract toward expiry (the roll yield) stays in
the daily P&L and in the trend signal.

### 5.2 A vendor switch only the next bar can confirm

On a bar where the vendor switches and neither the crossover nor the deadline would roll, only the
next grid bar can say whether the switch is kept. With `per_contract_switch_bar` `"held"` the
source hands out the held contract's own bar, the field naming the target (`AWAITS_NEXT`):

- every price-level consumer (sizing, the optimiser, the overlay, the marks) reads the held contract's
  close, and the bar's move is the held contract's own;
- the symbol is held at that rebalance: it stays at its quantity and no fill is made in it
  (`RollTracker::Status::holds`; the log line is `CHANGE_BAR_HOLD`);
- the next consumed bar decides. If the book goes into it in the target, the roll happened on the
  switch bar: both legs at the switch bar's two closes, booked on the confirming bar's
  rebalance, and the confirming bar's move measured from the target's close on the switch bar.
  Otherwise nothing rolled and nothing changes.

A run whose consumed bars end on the switch bar holds the symbol until the next bar arrives.

### 5.3 A roll bar a consumer withholds

Every bar after a roll, up to the next roll, carries the contract left's close on that date
(`AFTER_ROLL`). A consumer that withheld the roll bar (the session classifier's consumed-bar rule)
books the roll on its first consumed bar: the old contract's move up to it and both legs at that
bar's closes. A consumer that consumed the roll bar reads nothing from the field.

### 5.4 Where each engine books it

| Engine | Where |
|---|---|
| backtest | each symbol's consumed bars are walked through a `RollTracker` one bar at a time; a held symbol is taken out of the cycle's session set (`CHANGE_BAR_HOLD`); the legs of every roll confirmed on the cycle are made after the cycle's rebalance and inserted ahead of its strategy fills (`src/backtest/backtest_coordinator.cpp`) |
| live | `consumed_t1_settlement` books the T-1 row to the old contract's close on a roll bar and from the target's close on the bar that confirms a vendor switch (`include/trade_ngin/live/session_book_gate.hpp`); `roll_status_of` gives each symbol's status on its consumed bars (`src/data/roll_series.cpp`); the legs are made in `apps/strategies/live_portfolio_conservative.cpp` and `apps/strategies/live_portfolio.cpp`, which carry the same code |

### 5.5 Which series each consumer reads

Anything that is a price change reads the adjusted series; anything that is a price level reads
the close of the contract held. The adjusted level is the close plus the sum of the later steps,
anchored on the latest bar (`adjusted_levels`, `src/data/roll_series.cpp`); it can be zero or
negative and is only ever differenced. Every consumer builds it on each run from its own consumed
bars; nothing of it is stored.

| Consumer | Reads | Where |
|---|---|---|
| the EMAs of the trend rules | adjusted level (differences only) | `TrendFollowingStrategy::on_data`, `src/strategy/trend_following.cpp` |
| the volatility estimate, the forecast's own volatility, the attenuation | adjusted returns | `src/strategy/trend_following.cpp` |
| the optimiser's covariance | adjusted returns over the sleeve's last 756 levels and closes | `src/strategy/trend_following.cpp`; `optimiser_covariance`, `src/optimization/one_pass.cpp` |
| the risk overlay's gate window and covariance | adjusted returns | the sleeve's `overlay_returns`, `src/strategy/trend_following.cpp`; `overlay::gate_window`, `src/risk/overlay.cpp` |
| the risk reporter's readings in the live snapshot | adjusted returns | `src/risk/risk_manager.cpp` |
| the cost model's volatility multiplier | its own log return, the old contract's move on a roll bar | `include/trade_ngin/live/futures_cost_feed.hpp` |
| the price a forecast and a position size divide by, notional, exposures | the held contract's close | `src/strategy/trend_following.cpp` |
| fill prices, cost reference prices, margin | the held contract's close | [COST_MODEL.md](COST_MODEL.md) |
| marks and daily P&L | the held contract's close; on a roll bar the old contract's | `src/backtest/backtest_coordinator.cpp`; `src/live/live_pnl_manager.cpp` |

A percentage, a log or a ratio is never taken of the adjusted level.

## 6. The two ROLL legs

On the rebalance that books a roll, each sleeve holding the symbol books two fills of type `ROLL`
at its held quantity (`make_roll_legs`, `src/data/roll_series.cpp`):

| Leg | Side | Price | Contract written on the row |
|---|---|---|---|
| closing | against the position | the old contract's close on the roll bar | the outgoing id |
| opening | with the position | the new contract's close on the roll bar | the incoming id |

- The quantity is the book held at the start of the bar: the filled positions in the backtest,
  the stored Day T-1 book in live. A symbol not held has no legs.
- Neither leg carries realised P&L.
- Both prices must be positive closes; a leg without one stops the run (`ROLL_LEG STOP`). No
  default price is used.
- Order within the day: closing leg, opening leg, then the day's `STRATEGY` fills.
- Ids: live `EXEC_<symbol>_<YYYYMMDD>_RC` and `_RO`, the date being the confirming bar's (the roll
  bar itself for a roll known on its bar, the next bar for a vendor switch); backtest
  `RL-<sleeve>-<n>`, n even for the closing leg.
- A strategy never sees a leg as a trade: `on_execution` returns on any row that is not
  `STRATEGY` (`src/strategy/base_strategy.cpp`, `src/strategy/trend_following.cpp`).

**Costs: two outright legs, an upper bound.** Each leg is priced by the same cost model as any
fill of that size at that price: the contract's fee plus the model's spread and impact
([COST_MODEL.md](COST_MODEL.md)). A roll traded as one calendar-spread order pays less than two
outright trades; the engine has no spread prices, so it books the two-outright figure, an upper
bound on the cost of a roll done as a spread, and the `ROLL_LEG` log line says so. Robert Carver,
Advanced Futures Trading Strategies, makes the same assumption in his cost arithmetic (two sets of
trading costs for each roll, strategy three, the holding cost calculation), and his Tactic one,
"Contract selection and rolling", notes that a spread order would be expected to pay about half
the spread cost of two separate trades. The spread roll is item 10 of AlgoGators/algogators#183.

**Which rolls a live run books.** A live run books every roll confirmed since the contract
recorded on the symbol's stored Day T-1 positions row, never the rolls of a calendar span
(`live_rolls_by_state`, `include/trade_ngin/live/live_roll_legs.hpp`). So a confirming bar that
arrives late is legged by the first run that consumes it, with ids dated the confirming bar, and
that run's Day T-1 row books the consumed moves no earlier run booked (`LateRollSettlement`,
`late_booked_points`). A re-run reads the contract its own stored legs rolled out of
(`PostgresDatabase::get_stored_roll_contracts`, `src/data/postgres_database_extensions.cpp`),
unless the rolls stored for earlier dates of the run's window rolled out of that contract since
they last rolled into it: those legs are stale and the run takes the contract from the stored T-1
row (`rolled_out_of_since_entered`, `contract_held_coming_into`; the earlier rolls are read by
`get_previous_rolls`). A recorded contract that is on no consumed bar of the window refuses the
run. The legs are stored before the Day T-1 rows are re-written, in one transaction shared by
every sleeve with legs (`replace_roll_day_executions`).

## 7. What is stored

| Migration | Column | Meaning |
|---|---|---|
| 015 | `trading.executions.execution_type`, `backtest.executions.execution_type` | `STRATEGY` (default), `ROLL` or `BORROW`; NOT NULL with a CHECK |
| 015 | `executions.instrument_id` (both schemas) | on a ROLL leg, the contract it traded: outgoing on the closing leg, incoming on the opening leg; NULL on every other row |
| 016 | `trading.positions.instrument_id`, `backtest.final_positions.instrument_id` | the contract held after that date's bar; NULL when unknown and on every equity row |
| 017 | `trading.live_results.daily_roll_costs`, `total_roll_costs` | the part of `daily_transaction_costs` and `total_transaction_costs` paid on ROLL legs |
| 018 | `backtest.results.transaction_costs`, `roll_costs`, `total_roll_fills` | the run's cost total, its ROLL part, and the count of ROLL rows (two per roll per sleeve holding the symbol) |
| 040 | `roll_bar_move`, `roll_gap_points`, `roll_gap_value` on `trading.executions` and `backtest.executions` | the roll's split, below |
| 040 | `backtest.results.roll_bar_moves`, `roll_gap_values` | the run's sums of the two dollar figures |

**The split (migration 040).** Every roll stores three figures on its closing leg only, so a sum
over rows counts each roll once (`attach_roll_split`, `src/data/roll_series.cpp`;
`migrations/040_roll_split.sql`):

| Column | Meaning |
|---|---|
| `roll_bar_move` | the held (old) contract's move booked on the roll bar, in dollars: `quantity x (old close - previous close) x multiplier`. It is P&L and is already inside the day's P&L; the column only names it |
| `roll_gap_points` | the new contract's close minus the old contract's close on the roll bar: the opening leg's price minus the closing leg's |
| `roll_gap_value` | `roll_gap_points x quantity x multiplier`, signed with the position (a long pays it). Shown, never P&L: the same exposure held in a contract that costs that much more or less |

A CHECK keeps all three NULL, or all three set on a ROLL row. They are NULL with the switch off,
on every row that is not a ROLL leg, on every opening leg, and on the micros' change of price
source (section 11), which books no leg. The results totals are NULL with the switch off and 0
for a switch-on run that legged no roll. Roll yield is not stored: it is inside the held
contract's daily P&L. The third total, the roll cost, is migration 018's `roll_costs`.

**Cost totals.** The cost columns are a superset pair: the transaction cost total holds every fill
(STRATEGY, ROLL and BORROW) and the roll column is the part of it paid on ROLL legs.

- Every total charges a fill its cost after netting, `total_transaction_costs -
  netting_adjustment` (`transaction_cost::net_cost`,
  `include/trade_ngin/transaction_cost/netting.hpp`; `add_net_costs`,
  `src/transaction_cost/netting.cpp`).
- A ROLL leg is never netted. The legs stay out of the sleeve netting and are created with an
  adjustment of exactly 0, so a leg's net cost is its own cost.
- The roll totals read the leg's own cost through `transaction_cost::unnetted_cost`:
  `backtest.results.roll_costs` in `run_cost_totals` and the trade statistics' roll cost
  (`src/backtest/backtest_metrics_calculator.cpp`). The live `daily_roll_costs` adds each leg's
  `total_transaction_costs` as the leg is made; `total_roll_costs` is the previous row's total
  plus the day's.
- A ROLL (or BORROW) row that carries a non-zero netting adjustment is refused, never corrected:
  `unnetted_cost`, `add_net_costs` and `run_cost_totals` throw `NettingRefused`. The backtest
  fails the run and returns no result (no `backtest.results`, `backtest.equity_curve` or
  `backtest.executions` row; the `backtest.final_positions` rows of the cycles already run stay).
  In live the exception reaches `main`, which exits 1; the day's `live_run_metadata` row and its
  signals are already stored by then and none of the day's executions is.

## 8. Trade statistics across a roll

A roll is not a round trip. In the backtest's trade statistics
(`BacktestMetricsCalculator::calculate_trade_statistics`,
`src/backtest/backtest_metrics_calculator.cpp`):

- a ROLL leg never moves the tracked position, scores no trade and leaves the trade's open time;
  it is counted in `roll_fills` and its cost goes to `roll_costs`, never into a trade;
- the open trade's entry price is carried across the roll by the leg gap,
  `entry := entry + (opening leg price - closing leg price)`, once per roll, longs and shorts
  alike (`RollEntryCarry`).

So a trade held through a roll scores the move of the contracts actually held: entry to the
closing leg in the old contract plus the opening leg to the exit in the new one, which is the move
the trade's daily P&L rows book. Legs that cannot be paired stop the calculation.
`calculate_symbol_pnl` applies the same carry and charges each leg's cost to the symbol.

## 9. One roll, day by day: silver, November 2025

Book: long 1 contract of SI, multiplier 5,000, in a two-year backtest with the switch on: an
illustration on SI's contracts, a book that trades SI at full size. The template books trade SIL
from its listing on 2013-06-17; SIL rolls by the same rule, cycle and deadline, on SIL's own
contracts and volumes, so its roll bar can differ. The arithmetic below is the rule's for any held
contract. SIZ5 (December) is held. Its first notice day is the last trading day of November, Friday
2025-11-28; Thanksgiving, Thursday 11-27, is a CME closure for settlement, so the five trading days
before D* are 11-20, 11-21, 11-24, 11-25 and 11-26 and the window opens on 11-20. The target is
SIH6 (March; SI's cycle is HKNUZ). On Tuesday 11-25 March out-trades December for the first time:
the crossover rolls on that bar. P&L booked is dollars on the one contract; Return read is the
contract's own return, the figure every return consumer reads; the adjusted level is anchored on a
later bar of the run.

| Bar | SIZ5 close (volume) | SIH6 close (volume) | Held | P&L booked | Return read | Adjusted level |
|---|---|---|---|---:|---:|---:|
| Fri 11-21 | 49.660 (87,200) | 50.325 (14,287) | Dec | -4,075 | -1.61% | 53.105 |
| Mon 11-24 | 51.175 (39,012) | 51.840 (14,658) | Dec | +6,825 | +2.74% | 54.620 |
| Tue 11-25, roll bar | 50.960 (19,103) | 51.630 (69,702) | Dec to Mar | -1,075 (December's own move) | -0.42% | 54.405 |
| Wed 11-26 | 53.165 (6,422) | 53.860 (74,554) | Mar | +11,150 | +4.32% | 56.635 |
| Thu 11-27 | 53.160 (652) | 53.915 (38,561) | Mar | +275 | +0.10% | 56.690 |
| Fri 11-28 (first notice) | 56.380 (811) | 57.085 (51,851) | Mar | +15,850 | +5.88% | 59.860 |

The Sunday 11-23 bar books +750 and is left out of the table. Thursday 11-27 has a bar (a Globex
session) but is not a trading day for the rule.

- **The roll bar.** P&L is December's move, (50.960 - 51.175) x 5,000 = -1,075; its return is
  -0.215 / 51.175 = -0.42%. The next bar's move is March's own, (53.860 - 51.630) x 5,000 =
  +11,150.
- **The legs**, booked by the run that consumes 11-25: SELL 1 SIZ5 at 50.960, cost 22.62; BUY 1
  SIH6 at 51.630, cost 22.67.
- **The split** on the closing leg: `roll_bar_move` -1,075; `roll_gap_points` 51.630 - 50.960 =
  0.670; `roll_gap_value` 0.670 x 1 x 5,000 = 3,350 (not P&L). Roll cost 45.29.
- **The adjusted level** steps by the move only: before the roll it is the December close plus
  3.445 (51.175 + 3.445 = 54.620), after it the March close plus 2.775 (51.630 + 2.775 = 54.405);
  the two constants are 0.670 apart, the gap.
- **The e-mail line**: "Silver, December to March: market move booked -$1,075; contract price
  difference 0.670 ($3,350, not profit or loss); roll cost $45.29."

## 10. The held/vendor switch-bar setting

`per_contract_switch_bar` decides only which bar is handed out on a vendor switch bar the next bar
has not yet kept (`SwitchBar`, `include/trade_ngin/data/per_contract_source.hpp`):

| Value | The switch bar | The field |
|---|---|---|
| `"held"` (the default, and the templates') | the held contract's own bar | names the target (`AWAITS_NEXT`) |
| `"vendor"` | the bar of the contract the vendor switched to, a one-bar stray included (no run can tell a stray from a switch on the bar) | names the held contract (`AWAITS_NEXT_VENDOR`) |

Booked P&L and the roll legs are the same under both values: under `"vendor"` the bar's move still
ends at the held contract's close and, when the switch is not kept, the next move starts from the
held contract's close (`roll_series.hpp`). What can differ is every level read on that bar
(sizing, marks, notional, the overlay) and the session classifier's verdict on it, so the consumed
history, and through it the volatility and the forecasts of a few symbols. Every other bar is the
same. The setting and `per_contract_rolls` both shipped as keys; they are read by the four futures
runners.

## 11. The micros

### 11.1 The equity index micros: a change of price source

Until the vendor's change of 2026-02-22, `MES.v.0`, `MNQ.v.0`, `MYM.v.0` and `M2K.v.0` carried the
full-size contracts' bars (ES, NQ, YM, RTY: same index price and expiries, another multiplier and
volume). With the switch on, each micro's series reads the full-size root's contracts before the
change and its own from it (`MicroAlias`, `micro_alias`, `src/data/contract_roll_rule.cpp`). The
change is one roll, trigger `alias`, between the two contracts of the same expiry, on the first
trading-day bar on or after 2026-02-22 where both print: the Sunday change rolls on Monday
2026-02-23. It is a change of price source, not a trade (`SOURCE_CHANGE`): the full-size
contract's move is booked to its close on that bar, and there is no leg, no cost, no trade count,
no split and no e-mail line. Each engine logs one `PRICE_SOURCE_CHANGE` line with the gap
(`backtest_coordinator.cpp`; `live_portfolio_conservative.cpp`, `live_portfolio.cpp`). The change also restarts the
symbol's volume norm in the session classifier, so the micro's first bars are judged against its
own volume, not the full-size contract's (`src/data/session_classifier.cpp`).

Before their listing date, 2019-05-06, the micros did not exist. With the `listing_dates` block a
backtest whose window trades before that date holds the full-size contract (ES, NQ, YM, RTY; ten
times the size, with its own fee and margin) on the same price history, and switches to the micro
on the listing date by the configured rule, `open_at_target` by default: the full-size contract is
exited and the micro entered at its own target, as `STRATEGY` fills with ids `LC-` and `LO-`
(`plan_listing_switch`, `make_listing_switch_fills`, `src/data/listing_dates.cpp`;
`src/portfolio/portfolio_manager.cpp`). A live run trades no predecessor and refuses one in its
universe or its stored book (`include/trade_ngin/live/live_listing_guard.hpp`).

### 11.2 The gold and silver micros from their listing

MGC and SIL trade on their own contracts by the rule, on their own cycles (GJMQZ and HKNUZ, those
of GC and SI), the CME metals calendar and their own costs. Before its listing each reads its
full-size root's contracts and grid: MGC reads GC before 2010-10-04 and SIL reads SI before
2013-06-17 (`micro_alias`, the full-size-grid kind: their own `.v.0` starts at the listing). On
the listing date the series changes source by the same alias roll; a micro that lists no contract
of the held expiry changes into its cycle target (SIL on 2013-06-17 lists from September while SI
holds July). Both pairs are in the `listing_dates` block of the conservative and base templates
(MGC before GC, ratio 10; SIL before SI, ratio 5), so the book trades GC or SI before the listing
and switches by `open_at_target`; each switch leg is priced on its own contract's volume
(`ListingDates::add_predecessor_bars`).

### 11.3 What the runners refuse

The four futures runners refuse, before any write (`src/data/listing_dates.cpp`):

| Refusal | Why |
|---|---|
| a micro of the alias table stored beside its full-size root with no `listing_dates` pair for the two (`unpaired_micro_refusals`) | the run would trade the full-size contract and the micro side by side |
| a pair whose predecessor's `.v.0` is stored and the micro's is not (`refusals`) | the predecessor reads the micro's rows, which are not there, and would get no bars |
| a full-size-grid pair (MGC, SIL) with `per_contract_rolls` off (`refusals`) | the history before the listing is the full-size root's contracts, which only the per-contract source reads |

### 11.4 Relabels are not read with the switch on

`instrument_id_relabels` (the four micros' id change of 2026-02-22, declared as "not a roll") is
read only with the switch off. With it on the rule names that change as the alias roll, so no id
is read as another; a run that declares relabels logs one line saying they are not read
(`relabels_read`, `relabels_not_read_line`, `include/trade_ngin/data/listing_dates.hpp`).

## 12. What the book e-mail says

The Python report module (`services/rpc/algogators_rpc/services/report`) is the only book e-mail
path. With the switch on, each live runner hands the module the roll texts as JSON
(`report_rolls_json`, `include/trade_ngin/live/report_rolls_json.hpp`; the module's `--rolls`
argument). The texts are made by the engine (`include/trade_ngin/core/report_rolls.hpp`, read by
`report_roll_view`, `include/trade_ngin/data/report_roll_view.hpp`); the module lays them out and
computes none of them. With the switch off none of the items below prints.

| Item | Where it prints (`services/rpc/algogators_rpc/services/report/templates/daily_report.html`) | What it says |
|---|---|---|
| Roll legs | under each sleeve's executions, headed "Roll legs · costs only, not trades": symbol, side, quantity, price, cost | the day's ROLL rows; they are not counted as trades |
| Roll line | under the roll legs, one per roll | from the closing leg's split and both legs' costs, as for the roll of section 9: "Silver, December to March: market move booked -$1,075; contract price difference 0.670 ($3,350, not profit or loss); roll cost $45.29." The names come from the contract metadata, the months from the contract table; the price difference prints without a sign (it is stored signed) to the decimals of the contract's tick |
| Rollover notice | a red box near the top, headed "Rollover notice": "These contracts are approaching their roll. Each position moves to the next contract of its cycle on or before the date shown." | one line per held contract whose window is open on the settled day: "<label>: currently holding X → switch to Y by MM-DD, first notice MM-DD" (or "last trading day MM-DD"). The label is the IB symbol; contracts print as the IB symbol, the month code and the year's last digit (JPYZ5). "By" is the run date of the LAB (the day after it). A contract the rule has rolled while the book still holds the old one (its legs not yet booked) is not listed |
| Front month | a column of the Symbols reference, shown when any row has one | the contract the book holds after the settled bar (the old contract until its roll legs are booked); a micro before 2026-02-22 prints in its own IB format (ESZ5 under MES prints MESZ5) |
| Contract months | the same table | the root's cycle in month names ("Mar May Jul Sep Dec") |
| Roll-bar note | a numbered note on the settled table's row of a symbol that rolled on the settled bar | "Contract rolled that day. The old contract's move that day is booked; the price difference between the two contracts is not profit or loss." |
| Roll costs | the summary ("Roll costs", `total_roll_costs`) and the settled day's box ("Daily roll costs", `daily_roll_costs`) | the stored totals of section 7 |
| Footer | the closing note | "This strategy is based on daily OHLCV data. Each position is held in the front contract of its cycle and rolled to the next one before the first notice day or the last trading day." |

Where these are built: `services/rpc/algogators_rpc/services/report/builder.py` (`footnotes`,
`mark_roll_bars`, `number_notes`, `_symbols_with_rolls`, the payload's `roll_lines` and
`rollover`). The outlook behind the Rollover notice and the Front month column is the rule's own
series, built from the root's series start to the settled day (`report_outlook_of`:
`in_window` is the settled day on or after the window's first day, `roll_by` the day after the
LAB). `report_rollover_warning_html` in `report_rolls.hpp` is the C++ e-mail's form of the box and
is called only by its tests.

## 13. With the switch off: the vendor's series

With `per_contract_rolls` off or absent the bars are the vendor's `<root>.v.0` series, a raw
splice: on each day the vendor's resolver names one contract, ranked by the previous day's volume,
and the day's bar is that contract's own bar. On the day it switches, the previous bar is the old
contract's close and the day's bar the new contract's, and the old contract's close on the switch
day is stored nowhere. The engine then reads the roll from the vendor's ids on the consumed bars
(`classify_instrument_changes`, `RollTracker::add`, `src/data/roll_series.cpp`; the rules are the
first paragraphs of `roll_series.hpp`, applied bit for bit when no roll field is passed):

| Consumed ids, oldest first | Reading |
|---|---|
| A, A, B, B | bar 3 is a change bar (pending; the symbol is held). Bar 4 keeps B: a roll from A to B, booked on bar 4, closing leg at bar 2's close, opening leg at bar 3's close |
| A, A, B, A | a flip: no legs; bars 3 and 4 both book 0 and are held |
| A, A, B, C, C | bars 3 and 4 are one pending sequence; bar 5 keeps C: a roll from A to C |
| A, A, B, (none), B | a bar with no id is not judged; the roll is kept on bar 5 |
| A, A, B, B, A, A | two rolls, A to B then B to A: four legs |

On a change bar the return is 0 and the P&L is 0, because the step is the gap between two
contracts and the old contract's own move that day cannot be separated from it. The two legs are
priced at closes of two different days. The roll date is the vendor's ranking, one consumed bar
later; a switch that lasts two bars or more and then returns is booked as two rolls. The
`instrument_id_relabels` list declares vendor id changes that are not rolls (section 11.4), and
the session classifier gives an unconfirmed id change on a thin print a JUNK verdict while still
consuming it (`kIdChangeHoldFraction`, `include/trade_ngin/data/session_classifier.hpp`). The
migration 040 columns stay NULL. A full-size-grid micro pair (MGC, SIL) is refused in this mode
(section 11.3).

## 14. Known limits

- **Roll costs are an upper bound** (section 6): two outright fills where a spread order is the
  usual way to roll (AlgoGators/algogators#183, item 10).
- **The first notice day comes from the exchange rule only.** The last trading day is the stored
  expiration, cross-checked against the rule; the first notice day has no column and is not
  checked against data (AlgoGators/algogators#183).
- **The grid is the vendor's.** A missing `.v.0` row removes a real bar from every run, even when
  the contract table has the bar (section 2.2).
- **Two rolls on one bar stop the run** (section 4); AlgoGators/algogators#183 lists the case.
- **The rest of the futures side reads NYSE dates** (section 3.3; AlgoGators/algogators#168), and
  there is no ICE calendar (AlgoGators/algogators#169).
- **Broker close-out rules** before first notice are not modelled against the five-day window
  (AlgoGators/algogators#183, item 9).
- **Three limits of a late roll** (section 6, "Which rolls a live run books"; AlgoGators/algogators#183,
  item 3, `include/trade_ngin/live/live_roll_legs.hpp`):
  - where the stored book stands is read from the contract, not from the booked P&L: the late sum
    starts after the last bar before T-1 held in the contract on the stored T-1 row (`stands` in
    `live_rolls_by_state`), so a bar of that contract that no run booked, as in a catch-up over
    several days, is not in it;
  - what earlier runs booked is read from the stored rows dated between that bar and T-1
    (`late_booked_points`), and a date with no stored row reads as nothing booked;
  - a late roll's legs are priced on the run that books them, at the closes of that run's consumed
    bars (the last close before the change bar and the change bar's close).

  The same item lists a late correction applied at the T-1 quantity, not the quantity the moves
  were booked on: the settlement is in points per contract (`LateRollSettlement`).

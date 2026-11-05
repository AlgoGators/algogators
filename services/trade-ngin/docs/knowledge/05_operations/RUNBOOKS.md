# Runbooks

How to operate the engine under `services/trade-ngin/` in `AlgoGators/algogators`. `main` below is that repository's
`main`, and every command runs from `services/trade-ngin/` unless it says otherwise. Each step comes with its reason, so
whoever runs it can tell when a step matters and what goes wrong without it. The jobs: the futures release seed, dated live
runs, the watchdog, the live mode gate, the test database, migrations, backtests, the e-mail, and closed days, restarts and
data holes. Section 10 holds the team's standing rules for any change that reaches `main`. For what a run does row by row,
read [LIVE_RUN_CYCLE.md](../../LIVE_RUN_CYCLE.md); for the schedule, the wrapper and catch-up runs, read
[performance_upkeep.md](../../performance_upkeep.md).

Five standing rules run through every job:

| Rule | Why |
|---|---|
| A live run always names its date | the date decides which stored row the run settles and which bars it reads, so a named date makes a run repeatable and makes a replay the same command as the production run. The runners refuse a run with no date (section 2.1) |
| Dates run one at a time, oldest first | the run dated D settles the row dated D-1 and seeds from it, so each date is built on the row the date before left |
| Tests run on a copy of production's data, never on production, and never send e-mail | a test writes the book's rows, and on production they would mix with the real book's, where the only way back is the archive a production step takes first; a test e-mail reaches the book's readers as if it were a real day |
| Nothing is written to production without an explicit go, production steps run by hand from a written order, and a read-only check follows each step | a wrong write found late is hard to undo, because every later step builds on it; the written order keeps the steps in the sequence that was tested, and the check finds a wrong write before the next step does |
| Every table a production step writes is archived before the step runs | the archive is the only undo the release scripts provide for a write that turns out wrong (`10_live_reset.sh` restores from it) |

## 1. The futures release seed

### 1.1 The operator's text

The operator's text is `services/trade-ngin/scripts/futures_release/README.md`: read the steps, commands and undo for each
step there. This section adds only why the order exists and what to check after. The README's order replaces the order in
the body of AlgoGators/algogators#180, the tracking issue, which is where the team reads what production holds.

The seed runs once, at the futures release, by one operator, in one sitting, and targets CONSERVATIVE_PORTFOLIO. Which
steps have run is recorded on AlgoGators/algogators#180, not here.

`REF` in the README is one commit on `main`, used for both the daily image and the seed, so the seed's engine is the daily
engine and the stored history is what the daily run would have written. A commit id from `AlgoGators/trade-ngin` does not
exist in the mono repo. The build runs from a linked worktree at the mono root, with the engine in its `services/trade-ngin/`
folder, so the release folder being run is not swapped for another version part way through.

### 1.2 Why the order exists

One fact drives the order: the live runner takes its universe from the stored `.v.0` symbols, so a stored continuous row
changes what every futures book trades on its next run. Contract rows do not do that.

| Order | Why | Does a script refuse the wrong order? |
|---|---|---|
| Contract bars first (part A, then A2) | contract rows alone change no book: measured on a copy, they moved no stored cell | no order check; a second run refuses |
| Steps 3 to 7 before the first daily run of the per-contract pull (AlgoGators/algogators#84) | part B needs its continuous rows to reach the last stored contract bar; a daily pull that lands first leaves them short | yes: part B refuses, and the README gives the way on |
| The hole fill before part B | part B first left both fill steps stuck | yes: part B refuses |
| QT runs stopped before part B, restarted after their configs and the merged build | no config is valid on both sides of part B (`services/trade-ngin/src/data/listing_dates.cpp`): with the MGC and SIL pairs it refuses while MGC.v.0 and SIL.v.0 are not stored, without them it refuses once they are, and code without that refusal takes MGC and SIL in beside GC and SI, so gold and silver enter the book twice | no: a warning only, since no script can see a server's config |
| AlgoGators/algogators#85 (daily MGC.v.0 and SIL.v.0) after part B | part B loads the two series' whole history in one step, which assumes none of their rows exist yet | yes: part B refuses once AlgoGators/algogators#85 has stored those rows |
| The data check before the backtests and the chain | every reference number was measured on the data to 2026-10-23, so a run on other data cannot be compared with it | yes: steps 09 and 11 refuse unless the fingerprint is equal |
| The live reset, with its archive, before the chain | the chain builds the live history from an empty book, and the archive is the only way back | yes: step 11 refuses a book that is not empty |

The seed runs with the release, not ahead of it: QT_CONSERVATIVE_PORTFOLIO writes to production every day, and part B can
land only together with QT configs that carry the MGC and SIL pairs (the fourth row above).

The whole history is re-run rather than switching the roll rule on part way through a chain, so every stored day is priced
under one rule: see [ROLLS_AND_DATA.md](../01_the_book/ROLLS_AND_DATA.md). The same holds for any later change that moves
stored numbers (section 10).

### 1.3 Before the seed

| Item | Why it matters |
|---|---|
| SIL's exchange fee checked against CME's schedule or a SIL fill, before step 13 | the optimiser's search reads the fee, so a different fee makes a different whole-contract book from the first traded day, and the reference anchors would have to be measured again |
| `max_locks_per_transaction` and `max_connections` read by the database owner | the fills have never run on a TimescaleDB hypertable; one transaction over about 857 chunks of each of the two hypertables needs about 5,000 locks by estimate. If the settings are too small, the dry run stops with nothing written |
| Migration 041 is not in the release order | nothing fails, because the writer checks for the columns, but seeded rows leave `mark_price` and the settle columns NULL, so they lose the e-mail's notes |
| `TRADE_NGIN_EMAIL_BODY_DIR`, which `11_live_chain.sh` sets, is read by no code on `main` | the step that kept each e-mail body has nothing to keep, so check the e-mail body output at the seed |

The README's section on what was and was not tested lists what has run only on copies: among them the real data pull, A2 on real
rows, the fills on production's hypertables, the full chain and any live run on the mono build.

### 1.4 What to check after

| Check | Must read | Why |
|---|---|---|
| The data | `01_readonly_checks.sh production after_data` equals `EXPECTED.md`, line by line | every later step's reference rests on this data |
| The backtests | four more stored runs (2y, 5y, 10y, 16y), all ending on one last bar, with their ids in `runs.tsv` | one last bar makes the four windows one picture of the book |
| The reset | 0 live rows for CONSERVATIVE_PORTFOLIO, `live_start_date` 2025-10-05 | the chain starts from an empty book on the book's first live date |
| The chain | `11_live_chain.sh check`: every identity passes and other books are unchanged; to 2026-10-23, 384 rows and book value 519,541.86, as the rehearsal | the only live reference ends on 2026-10-23 |
| E-mail | no e-mail line in any run's log | a seed run is a replay of past days, not a report |
| The package | `12_package.sh` builds `backtest_results/conservative_end_<END>/` and `live_results/conservative_live_2025-10-05_to_<END>/` under `RUN_DIR`, never committed because it holds licensed vendor bars | these folders are what other team members read |
| The first daily run | the `first CONSERVATIVE cron day` line of `EXPECTED.md`, on the first day the cron runs the book (AlgoGators/algogators#185 moves the daily jobs) | the first day the cron writes on top of the seed |
| The watchdog | `LIVE_PORTFOLIOS` names CONSERVATIVE_PORTFOLIO as well as the QT book (section 3) | otherwise a refused CONSERVATIVE date is not reported |
| The record | each step's log, and who ran the seed, posted on AlgoGators/algogators#180 | the tracking issue is where the team reads what production now holds |

No day after 2026-10-23 has a reference, so a wrong day after it cannot be told from a right one by comparison; the README's
section on dates after 2026-10-23 says what holds there. Read a finished run in full: see section 7.

## 2. Dated runs and the run modes

### 2.1 How a run is started

| How it is started | What it does | Who uses it |
|---|---|---|
| No date | prints the usage, exits 2, and runs, stores and sends nothing (`services/trade-ngin/include/trade_ngin/live/run_args.hpp`) | nobody: it is a mistake |
| A date equal to the host's date | a full run; on a futures runner a held symbol past the staleness tolerance refuses it | production |
| A past date | a full run; the same finding is a warning | replays, catch-ups, every test |

A date later than the host's date behaves as a past date. Only one decision differs between the modes, the held feed hole:
on today's futures run the refusal names the remedy, refresh the feed and run the date again, so the book does not trade on
a stale mark; a replay of a past date runs and holds the symbol at its last price (`rule_held_feed_holes`,
`services/trade-ngin/include/trade_ngin/live/session_book_gate.hpp`). The equity runner calls the same rule, but it reads its
date as midnight UTC and the rule compares host-local dates, so on a host behind UTC, as the image's New York zone is, its
run counts as a past date and the finding is only a warning (section 4.4). The table is in
[LIVE_RUN_CYCLE.md section 1.2](../../LIVE_RUN_CYCLE.md#12-the-two-run-modes).
`services/trade-ngin/tests/live/test_mode_branch_guard.cpp` fails when a new line reads anything a run's mode can be told
from, so a protection cannot quietly exist in one mode only, where the tests would never see it.

On the two futures runners the date may be given positionally or as `--date`; both name the same run, and two different
dates are refused (`two_run_dates_message`, `services/trade-ngin/include/trade_ngin/live/run_args.hpp`), so a typo cannot
run one date while the log names another. The equity runner takes the date positionally only: it rejects `--date` as an
invalid argument (exit 1), and given two dates it runs the last one
(`services/trade-ngin/apps/strategies/live_equity_mean_reversion.cpp`).

### 2.2 What production runs

`services/trade-ngin/live_portfolio.cron` starts `services/trade-ngin/scripts/run_live_portfolio.sh` at 09:30 New York time
every calendar day. The wrapper reads the date from the container and runs `live_portfolio_conservative <date> --send-email`.
Every calendar day, because the runner refuses a date whose previous day was not run, so a skipped weekend would stop the
book on Monday. Nothing else is scheduled in the tree for BASE or the equity book.
[performance_upkeep.md sections 4 and 5](../../performance_upkeep.md#4-the-schedule) carry the detail.

Three things to know when reading a production run:

- Which production database the daily jobs and the image point at is AlgoGators/algogators#185: read it before reading a
  production run, since a book with no daily run on a database has no rows there after its seed. A push to `prod` that
  touches `services/trade-ngin/` deploys the `engine-rpc` service (AlgoGators/algogators#120, AlgoGators/algogators#123).
- The wrapper takes a lock, a directory removed on exit, so two runs never write one book at once. A killed wrapper can leave
  it behind, and every later run then logs "skipping" and exits 0 without starting the binary. A binary started by hand takes
  no lock.
- The image sets `TZ=America/New_York` (`services/trade-ngin/Dockerfile`, the `TZ` line of the runtime stage) because the cron expression and the wrapper's
  date are written in Eastern time, although servers and the database run UTC; so a dated futures run stores `05:00:00Z`. A
  book is run under one zone for its whole life:
  [LIVE_RUN_CYCLE.md section 1.1](../../LIVE_RUN_CYCLE.md#11-the-run-date-and-the-host).

### 2.3 Test runs

A test run passes a date and never `--send-email`, against a test database (section 5):

```
build/bin/Release/live_portfolio_conservative 2026-04-24
```

That covers the trading and storage path. It does not cover the e-mail (section 8).

### 2.4 One run per calendar date, in ascending order

The run dated D settles the row dated D-1 and seeds from it, so a lone re-run of an older date leaves every later row built
on a book that no longer exists. The runner does not stop you: its missed-run check only asks whether the previous day is
stored. The rule is [LIVE_RUN_CYCLE.md section 11](../../LIVE_RUN_CYCLE.md#11-operating-rules).

### 2.5 What a refused date does, and the restart

A refused date stores nothing, not even a `trading.live_run_metadata` row. Every later date is then refused by the
missed-run guard, so one refusal stops the book until someone restarts it. The guard is deliberate: a date built on a
missing day would size from a book that was never settled.

The live mode gate runs this exact shape, so its dates make the example (section 4). The bars of `6M.v.0`, a held symbol,
are removed for 2026-04-21 to 04-25, leaving its last bar on 04-20. The tolerance is 4 days.

| Run date | Host date | Age of the last bar | Exit | What happened |
|---|---|---|---|---|
| 2026-04-24 | same | 3 days (the day before is 04-23) | 0 | complete day |
| 2026-04-25 | same | 4 days (the day before is 04-24) | 0 | complete day, not past the tolerance |
| 2026-04-26 | same | 5 days | 1 | `FEED_HOLE_REFUSAL`, nothing stored |
| 2026-04-27 to 05-03 (7 dates) | same | | 1 | the missed-run refusal, nothing stored |
| 2026-04-26 to 05-03 (8 dates, oldest first) | 2026-05-04 | | 0 | the restart: every stored table identical to the past-date chain |

To restart:

| Step | Why |
|---|---|
| 1. Fix the cause: read the log's refusal line, and refresh the feed if a bar is missing | on the restart each date is a past date, so a held hole is only a warning and the run goes through; a restart that skips the fix succeeds and leaves the symbol held at its last mark (the gate's restart passes with the bars still missing) |
| 2. Check that no scheduled run is in progress or about to start | two runners on one book race on upserts to the same keys |
| 3. Run `<binary> <date>` for each missed date, oldest first, one at a time, stopping at the first non-zero exit, without `--send-email` ([performance_upkeep.md section 6](../../performance_upkeep.md#6-manual-and-catch-up-runs)) | each date settles the one before; after a failed date the next is refused anyway, and a catch-up e-mail for a past day would reach readers as today's report |
| 4. Check that each date has its `trading.live_results` row | that row is the evidence of a completed day; exit 0 alone is not ([LIVE_RUN_CYCLE.md section 10.1](../../LIVE_RUN_CYCLE.md#101-exit-codes)) |

## 3. The watchdog

`services/trade-ngin/scripts/check_live_trading.py` exists because a live book can stop without anyone seeing it: once the
cron called a binary that did not exist and logged the error where nothing read it, and the stop went unnoticed for 81
days. So the watchdog checks the outcome, not the process: did a run actually reach the database. That one question covers a stopped
container, a dead cron, a missing binary, missing credentials and a crash part way through.

The workflow `.github/workflows/svc-trade-ngin.watchdog.yml` runs it daily at 15:00 UTC, about an hour and a half after the
09:30 New York run, so a slow run is not taken for a missing one. It reads, per book:

| Signal | What it is | Why this one |
|---|---|---|
| The run clock | the latest `created_at` of the book's `trading.live_results` rows | a completed run writes that row last, so it dates the latest finished run |
| The book stamp | the latest `trading.live_results.date` not in the future | which trading date the book has been run through; replay rows dated ahead are ignored |
| The positions stamp | the latest `trading.positions.date` with `portfolio_type` `system`, when the book holds positions | a run that wrote results but not positions is a partial failure; a desk save (`qt`) must never satisfy it |
| The run mark | a `risk_refusal` or `strict_assertion` mark on the latest `trading.live_run_metadata` row | a day whose risk step held the book (exit 3) stores its rows, so the first three signals read it as healthy |

A signal more than `MAX_CALENDAR_DAYS_SILENT` calendar days old (default 1) is stale. Calendar days, because the futures
book runs every day. With the default, a date refused today is reported at the next day's check, since today's run may still
be running when the check reads. A stale book files or updates an issue labelled `live-trading-down` on the repository the
workflow runs in, and the job fails. Without the database secrets the job fails rather than passes.

The books read are those in `LIVE_PORTFOLIOS`, which defaults to QT_CONSERVATIVE_PORTFOLIO, and the workflow does not set
the variable. After the seed, set it to name CONSERVATIVE_PORTFOLIO as well; otherwise a refused CONSERVATIVE date is not
reported. A listed book that the cron does not run reads as stale every day and files the issue, so list a book once its
daily run exists on the database the workflow reads. On a pull request the workflow runs only the script's `--self-test`, which needs no database.

## 4. The live mode gate

### 4.1 What it proves

Every test run is a past-date run, because test data ends before today, while production runs with a date equal to the
host's date. Without a gate, production's mode would be the one mode no test runs.
`services/trade-ngin/scripts/live_mode_gate/gate.sh` runs the same dates both ways, on one unpatched binary, from one
restored book, and fails unless production mode does what the tests do. `hostclock_shim.c` shifts the wall clock of the
runner's own process, so the product carries no test switch that could be left on. The database server's clock is not
touched.

| Part | Book and dates | What must hold |
|---|---|---|
| 1 | CONSERVATIVE, 2026-04-24 to 05-03 | the table in section 2.5: refusal, guard on every later date, restart ending identical to the past-date chain |
| 2 | BASE, 04-24 to 04-26 | the same on the twin runner |
| 3 | Equity, 2026-06-15 to 06-19 | five dates identical in both modes; a date whose previous day never ran is refused and stores nothing, the metadata row included |
| 4 | CONSERVATIVE | the re-run repair: a day's bars arrive late, two runs are made without them, the dates are re-run in order, and every table ends identical. The late bar books a held roll, the shape of AlgoGators/trade-ngin#188 |
| 5 | CONSERVATIVE, with `GATE_SWITCH=on` only | a held roll priced at its two contracts' closes of one bar; a micro's roll, on through 2026-06-17; an ignored one-bar stray and an ignored backward switch; a run killed while it writes and one whose roll legs fail to store, each replayed; a crossover day's contract rows arriving late |

`gate.sh` holds 15 checks, and 22 with `GATE_SWITCH=on`. Its header comments mention a report body; no check on `main` reads
one, since the e-mail moved to the Python report. The header of `chain.sh` explains each unit.

The feed hole in part 1 is made by the gate, in the database it is given: `6M.v.0` loses its bars of 2026-04-21 to 04-25 in
both bar tables for the whole gate and gets them back at the end. A gate stopped part way leaves them out, so do not reuse
that database.

### 4.2 The database and inputs it needs

The gate writes to the three live books of the database it is given, so it gets its own test database (section 5), never
the template others are made from. The inputs below are not in the repository; build them in these states:

| Item | State it must be in | Why |
|---|---|---|
| The database | a test database with the migrations of the line under test; bars through 2026-06-17 in `futures_data.ohlcv_1d`, `futures_data.ohlcv_1d_raw` and `futures_data.contract_ohlcv_1d`; equity bars through 2026-06-19 for the equity book's symbols | 2026-06-17 is the gate's last date with the switch on, and 06-19 the equity part's last |
| `S0_conservative`, `S0_base` | one `trading_<table>.csv` per table of the `trading` schema that has a `portfolio_id` column (CSV with a header), holding that book's rows only, with `trading_strategy_trading_days_metadata.csv` beside them, ending on the row dated 2026-04-23 | the first gate date settles that row. The book must hold `6M.v.0` on it, or part 1 has nothing to refuse; for part 5 it must hold a micro (the snapshots in use hold MES and MYM) |
| `S0_equity` | the same for the equity book, ending on the row dated 2026-06-14 | the equity part starts on 06-15 |
| Columns and tables a snapshot lacks | `chain.sh` proves each restore with `s0_project.py`: a column a later migration added must read its history value, and a table with no snapshot file (`position_overrides`, `strategy_config`) must hold no row of the book | so a restore is the book as it was, not the book plus newer defaults |
| `defaults.json.entry` | `config_template/defaults.json` with the `database` block on the loopback address `127.0.0.1` (the driver asserts that literal, so `localhost` fails), the port as the string `"5433"` and the test server's login; `chain.sh` sets the database name for each run | a config still carrying production's port cannot reach the test server |
| `portfolios/<book>/` | one folder each for `conservative`, `base` and `equity_mr`, taken from `config_template/portfolios/<book>/` without `listing_dates` and `instrument_id_relabels`, and without `per_contract_rolls` | pairs reach a run only through the `TPL` arm (section 4.3), which reads `config_template`; `GATE_SWITCH=on` sets `per_contract_rolls` true, and with the switch off the file's own value stands. A folder that keeps `listing_dates` or `instrument_id_relabels` fails at setup: `chain.sh` asserts both are absent |
| The password file | one line setting `SCRATCH_PGPASSWORD`, the test server's `postgres` password, readable only by its owner and outside the repository | the password is never printed or typed on a command line |

The gate refuses any database whose name does not hold `scratch`, any host but loopback, any port but 5433, and any database
not at time zone UTC, so it can never reach production. It never passes `--send-email` and never runs without a date.

### 4.3 How to run it

From `services/trade-ngin/`:

```
GATE_SWITCH=on GATE_WORKTREE=<linked worktree>/services/trade-ngin GATE_OUT=<empty folder> GATE_S0_DIR=<snapshots> \
GATE_CONFIG_SRC=<entry config> GATE_RUN_LOCK=<lock file> GATE_PG_ENV=<password file> \
scripts/live_mode_gate/gate.sh <test database> [conservative-arm base-arm equity-arm]
```

The three optional arguments choose, for the conservative, base and equity books in that order, what `chain.sh` adds to
the book's `portfolio.json` (section 4.2) before its runs. The scripts call each choice an arm; the defaults are `TPL`,
`ABSENT`, `ABSENT`.

| Arm | What the run's `portfolio.json` gets |
|---|---|
| `ABSENT` | the book's folder as it is: no `listing_dates`, no `instrument_id_relabels` |
| `TPL` | that copy plus the template's own `listing_dates`, `instrument_id_relabels` and `trading_rule_removals`, from `config_template/portfolios/<book>/portfolio.json`; `chain.sh` stops if the template lacks one of the three |
| `RL` | that copy plus four `instrument_id_relabels` entries (MES, MNQ, MYM and M2K, dated 2026-02-22) |
| `LD` | that copy plus `listing_dates` for the four index micro pairs (MES/ES, MNQ/NQ, MYM/YM and M2K/RTY, listed 2019-05-06, ratio 10) |
| `BOTH` | `RL` and `LD` together |

| Setting | What it must be |
|---|---|
| `GATE_WORKTREE` | the engine's folder in a linked worktree made at the mono root, with the runners built under `build/bin/Release` there. Never the main checkout: the gate rewrites `config/` there and deletes its logs. The worktree's root is refused (`no built runner`). By the code, its git checks accept that subfolder |
| `GATE_OUT` | an empty folder. The result is `GATE.txt`, ending `LIVE MODE GATE PASSED`, and exit 0 only when every check passed and every run finished |
| `GATE_S0_DIR`, `GATE_CONFIG_SRC`, `GATE_PG_ENV` | the snapshots, the entry config and the password file of section 4.2 |
| `GATE_RUN_LOCK` | the path of a run lock file. `chain.sh` creates it for each run and removes it after, waiting while it exists, so one runner writes the database at a time. A lock file left by a stopped gate blocks the next: remove it once no gate is running |
| `GATE_PSQL` | optional; the `psql` 16 to use when it is not at the scripts' default path |

`GATE_EVALUATE_ONLY=1` makes no run and evaluates the runs already in `GATE_OUT`. The scripts are written for macOS
(`clang -dynamiclib`, `DYLD_INSERT_LIBRARIES`, `caffeinate`, `pmset`, `libtrade_ngin.dylib`). The shim's header says how to
build it on Linux (`-shared`, `LD_PRELOAD`); the scripts would need those lines changed too.

### 4.4 What it does not cover

- **The e-mail.** No run sends, and no check reads a body.
- **The wrapper and the cron.** The gate starts the binary directly. The lock and the wrapper's date are not exercised.
- **The production image.** It runs on a macOS build, not the container or its New York zone.
- **QT modes.** Only the model run of three books runs; `--desk`, `--publish` and the rest do not.
- **The desk's data rule.** It reads no `ops.data_ready` row (section 9.3).
- **The equity book's feed hole.** Part 3 has no held hole, and on a host behind UTC the equity check warns instead of
  refusing ([LIVE_RUN_CYCLE.md section 1.2](../../LIVE_RUN_CYCLE.md#12-the-two-run-modes)).
- **Dates outside its windows,** and production's data. It is not in CI: start it by hand.

### 4.5 The arms against the release universe

The listing refusals of `services/trade-ngin/include/trade_ngin/data/listing_dates.hpp` decide which arms can pass on a
given database. A micro stored beside its full-size root with no declared pair is refused
(`ListingDates::unpaired_micro_refusals`), and so is a declared pair whose full-size series is stored while the micro's is
not (`ListingDates::refusals`). Read from the code on `main`, not run:

| Database | Arms | Result as read |
|---|---|---|
| MGC.v.0 and SIL.v.0 stored | base, any arm | refuses. `ABSENT` has no pair, and `RL`, `LD` and `BOTH` carry only the four index pairs, so `ListingDates::unpaired_micro_refusals` fires. `TPL` cannot be used for base: `chain.sh` asserts blocks the base template lacks (`instrument_id_relabels`, `trading_rule_removals`) |
| MGC.v.0 and SIL.v.0 not stored | conservative, `TPL` (the default) | refuses: `ListingDates::refusals` finds GC stored and MGC not |
| MGC.v.0 and SIL.v.0 not stored | `gate.sh <database> BOTH ABSENT ABSENT` | no refusal found: ES, NQ, YM and RTY are not stored, and MES has `full_size_grid` false. It passes with the switch off, but it does not exercise MGC or SIL, so it does not cover the release |

So for the default arms and the release universe, expect the driver to need a change before the gate passes on `main`.

## 5. The test database

### 5.1 What it is and why

Every test run and every database-backed test runs on a copy of production's data, never on production: a test writes the
book's rows, and a write path proved against fakes can still fail against the real schema. The rule on the team is to test
every write path against the real DDL; a copy of production is how a test meets it.

| Property | State | Why |
|---|---|---|
| Server | PostgreSQL 16 on port 5433, loopback only | not 5432, so a config still carrying production's port cannot reach it by accident; the gate requires both |
| Name | holds `scratch` | the gate refuses any other name |
| Time zone | `TimeZone` set to `UTC` on the database itself (`ALTER DATABASE <name> SET TimeZone = 'UTC'`), and on each database made from a template, since `createdb -T` does not copy the setting | production runs UTC. The bar tables are `timestamp with time zone` cast to `date` on the server, so a database at New York time puts every date bucket one day early, which looks like a logic regression |
| Port in the config | the JSON string `"5433"`, not a number | `DatabaseConfig::port` is a `std::string`, read with `get<std::string>()` (`DatabaseConfig`, `services/trade-ngin/include/trade_ngin/core/config_loader.hpp`), so an integer throws at config load. `num_connections` is the opposite: a number |
| Migrations | those of the line under test; say which ones a run used | a run on missing columns fails, and a run on extra ones may not match production |
| Templates | one untouched database to make each run's database from; never written to | every database made from it inherits what it holds, and the gate needs the same starting rows each time |
| Fingerprint | a row count and an md5 of the tables a run reads, kept with the run | proves each run's database holds the template's data |

Differences from production to keep in mind:

- TimescaleDB is not installed, so production's hypertables are plain tables. A step proved on the copy is not proved on a
  hypertable.
- The copy is partial on purpose: the data of the three largest backtest tables is left out, since the engine only writes
  them, and the equity bar tables are filtered to the symbols to be run. A run that fails on a missing equity symbol has
  usually hit that filter.

Database-backed tests connect only through `TRADE_NGIN_TEST_DSN` and skip when it is unset (AlgoGators/trade-ngin#86), so a test never
reaches the database named in `config/defaults.json`, which may be production. Point it at a test database.
`services/trade-ngin/migrations/test_*.sh` drop schemas, and several create cluster roles, so they run on a throwaway server
made for the purpose, never on a database that holds a book.

### 5.2 Building one

Every read of production is read-only, and its login is read from `config/defaults.json` into a private file, never typed:
a password on a command line shows in the shell history and the process list.

1. **The server.** PostgreSQL 16 on port 5433, listening on loopback only, with a `postgres` superuser on a throwaway
   password (the one in the gate's password file). Create the roles production grants to, as roles with no login, or the
   `GRANT` statements of the schema restore fail.
2. **The template.** A database whose name holds `scratch`, with `TimeZone` set to `UTC` and `DateStyle` to `ISO, MDY`.
3. **The schema.** `pg_dump --schema-only` of `trading`, `auth`, `futures_data`, `metadata`, `macro_data`, `backtest` and
   `equities_data`. Remove the `ts_insert_blocker` triggers from the dump, because TimescaleDB is not installed.
4. **The data.** Load with triggers disabled (`pg_restore --disable-triggers` or the same setting in the connection): four
   foreign keys otherwise block the load order.

| Schema | What to copy |
|---|---|
| `trading`, `auth`, `metadata`, `macro_data` | whole |
| `futures_data` | the bar tables. A hypertable gives no rows to `pg_dump --data-only`, so read it with `COPY (SELECT ...) TO STDOUT` |
| `backtest` | `run_metadata`, `results` and `signals`. The three large tables stay empty |
| `equities_data` | the reference tables whole, and the three bar tables filtered to the symbols you will run |

5. **The migrations.** Apply what the line under test needs (section 6), then treat the database as the template.
6. **The fingerprint.** Record a row count and an md5 of the tables a run reads.

## 6. Migrations

Applied by hand: nothing in the release path applies a migration
([performance_upkeep.md section 9](../../performance_upkeep.md#9-migrations)), and no database table records them, so
`services/trade-ngin/migrations/README.md` is both the list and the record.

| Step | Why |
|---|---|
| 1. **Look first.** Check with read-only queries that what the file needs is there and what it adds is not. Keep the password out of any new file and off the command line | a file applied where its prerequisites are missing fails, and one applied twice is a no-op only where its header says so |
| 2. **Read the file's header.** It says what the file adds, what it needs, whether a second apply is a no-op, and how it is undone | the header is the file's own runbook |
| 3. **Apply each file in one transaction, in number order,** from `services/trade-ngin/` at the merged commit: `psql -X -v ON_ERROR_STOP=1 -1 -f migrations/<file>.sql` | with `ON_ERROR_STOP` a failed file changes nothing; later files assume the columns earlier ones add. Every file on `main` carries its own `BEGIN` and `COMMIT`, so `-1` prints two harmless warnings (one at the file's own `BEGIN`, one at the outer `COMMIT`), and `services/trade-ngin/scripts/futures_release/03_apply_040.sh` omits it |
| 4. **Prove it** with a read-only query after each file | the transaction proves the file ran, not that it did what was meant |
| 5. **Record it:** fill in the date in the table in `services/trade-ngin/migrations/README.md`, in the same PR or straight after | the README is the only record of what a database holds |

Never apply to production without the explicit go. The deploy notes in `services/trade-ngin/migrations/README.md` say which
files ship with a binary: 001 and 021 change keys the engine names and 027 goes first; 041's own row says it may go
before or after.

| Numbers on `main` | Note |
|---|---|
| 001 to 006, 012 to 029, 034 to 041 | 32 files |
| 007 to 011, 030 to 033 | unused and never reused, so a number always means one file |
| 034 to 039 | were 028 to 033 on the line that later joined `main`, and were applied to production under those numbers. Their headers say so. Do not apply them there again |

Every migration but 024 has a rollback beside it, `<number>_<name>_rollback.sql`; 024 is irreversible (its row in
`services/trade-ngin/migrations/README.md`, the full list and the record). A rollback refuses while it would lose rows a
later book wrote: read its header first.

Two things on `main` that read differently from the above:

- A blank cell in `services/trade-ngin/migrations/README.md` means the date was not recorded, not that the file was not
  applied: check the database with a read-only query before applying a file whose cell is blank.
- The `test_NNN_*.sh` scripts are destructive. Each says how to call it; several need a throwaway server because they create
  cluster roles.

## 7. Backtests

| Book | Binary |
|---|---|
| CONSERVATIVE, the default | `bt_portfolio_conservative` |
| BASE, the repository's base template, `config_template/portfolios/base/` | `bt_portfolio` |
| Equity | `bt_equity_mr`, with `bt_equity_validation` for its checks |

A backtest takes no argument. It reads `config/` in the current directory, so run it from the folder whose config it should
read. The window is `backtest.lookback_years` back from the wall clock when it starts, so two runs on different days cover
different days. To compare runs, pin the end with `backtest.frozen_end_date`, a test-only key that a production config does
not carry.

The warm-up is inside the window, so a window is named by the years it trades
([CONFIG_GUIDE.md, the backtest window](../../CONFIG_GUIDE.md#backtest-window)):

| Called | `lookback_years` |
|---|---|
| 2y | 3 |
| 5y | 6 |
| 10y | 11 |
| 16y | 16 |

A backtest is one sample of history: a change is judged on several windows and start dates, never on one run
([HOW_WE_VERIFY.md](../04_lessons/HOW_WE_VERIFY.md)).

**The config snapshot.** A stored number is only as good as the record of what produced it, so keep the run config beside
the results in `config_used/`, without the login. `save_config` in `services/trade-ngin/scripts/futures_release/lib.zsh` is
the pattern. The seed writes every run from `services/trade-ngin/scripts/futures_release/config_reference/`, which is checked
against its `SHA256SUMS` before each run.

**Reading a run.** Exit 0 does not prove the results are stored
([LIVE_RUN_CYCLE.md section 10.1](../../LIVE_RUN_CYCLE.md#101-exit-codes)). The standard read reconciles the run's log
against every table it writes, row counts and values, and derives a few values by hand. The method is in
[HOW_WE_VERIFY.md](../04_lessons/HOW_WE_VERIFY.md). Results are in [RESULTS.md](../03_tuning_and_results/RESULTS.md).

## 8. The e-mail

The book e-mail is the Python report only (AlgoGators/trade-ngin#261 removed the C++ e-mail). With `--send-email`, after the day is stored, each of the three live runners runs
`python -m algogators_rpc.services.report`. A day that is stored and not sent exits 4, so a mail failure is told apart from
a trading failure. The details are in [LIVE_RUN_CYCLE.md section 11](../../LIVE_RUN_CYCLE.md#11-operating-rules).

Test runs never send. No script in `services/trade-ngin/scripts/live_mode_gate/` or
`services/trade-ngin/scripts/futures_release/` passes the flag, and the seed checks every log for an e-mail line. To read a
stored day's report without mailing it, run the module with `--preview-dir DIR`
([performance_upkeep.md section 6](../../performance_upkeep.md#6-manual-and-catch-up-runs)).

## 9. Closed days, a live book restart, and a data hole

### 9.1 Closed days

There is nothing to do on a closed day except let the run happen. The futures runner runs every calendar day, and the engine
decides per symbol whether the previous day was a trading day. It carries the whole book only when no symbol has a usable
bar: no order, exit 0, the day stored. A skipped run is not a carry: skip a Sunday and Monday's run is refused by the
missed-run guard. The equity runner decides from the calendar and writes the day with `carry_forward`. See
[LIVE_RUN_CYCLE.md sections 2](../../LIVE_RUN_CYCLE.md#2-the-previous-day-and-closed-days) and
[9](../../LIVE_RUN_CYCLE.md#9-how-the-futures-runners-differ), and
[performance_upkeep.md section 4](../../performance_upkeep.md#4-the-schedule) for what each day of the week does.

### 9.2 Restarting the book

| Situation | What to do |
|---|---|
| A date was refused or missed | run every missed date, oldest first (section 2.5) |
| A bar arrived late, or a past date's data was corrected | run the date that settles the bar and every later date in order. A `LATE_BAR` warning names the symbol, the date and the unbooked amount |
| A run was killed while writing | run that date again and go on in order. The gate proves the replay ends identical |
| Exit 3, the book held | the held day is stored, and the next run settles and sizes from it normally. Fix the cause |
| An engine change moves stored numbers | re-seed the whole history from the first live date: archive, reset, one dated run per calendar date (`10_live_reset.sh`, `11_live_chain.sh`). The reset names CONSERVATIVE_PORTFOLIO only |
| A new live strategy | insert its `trading.strategy_trading_days_metadata` row by hand before its first run |

On the last row: no runner writes that table, and a missing or late row silently changes every annualised figure.
`live_start_date` is the anchor. The metadata row is authoritative, and a runner only warns when it disagrees with the
book's first stored day and never moves the anchor, because stored rows can mislead: a rule that took the anchor from the
stored rows was once fooled by two backdated rows and annualised over 450 days for a 164-day stream. A wrong row is
corrected in the data, not by the runner. Check a new row with
`trading.get_trading_days`:
[LIVE_RUN_CYCLE.md section 7](../../LIVE_RUN_CYCLE.md#7-trading-day-count-and-annualisation). The equity book's case is in
[THE_EQUITY_BOOK.md](../01_the_book/THE_EQUITY_BOOK.md).

### 9.3 A data hole

Three rules apply, at three places. Which one speaks depends on who started the run.

| Where | Rule | Effect |
|---|---|---|
| The QT desk scheduler, before it builds (`services/trade-ngin/services/rpc/algogators_rpc/services/desk/catchup.py`) | the morning AND rule | the run is held, with the `data_stale` alert and nothing built, only when more than N symbols have each had no bar for more than X days. Otherwise it runs, and each missing symbol is alerted and held at its last price (`data_partial`). N is `QT_DATA_READY_MAX_MISSING`, 2. X is `live.data_staleness_tolerance_days`, 4 |
| The same, on the per-contract row | the same form | N is `QT_CONTRACT_DATA_READY_MAX_MISSING`, 2, counted in roots. X is `QT_CONTRACT_DATA_READY_STALE_DAYS`, by default the same 4. Each missing root is priced by the vendor's bar or carried (`contract_data_partial`) |
| The engine, on every run | the per-symbol classifier and the held-hole refusal | a feed hole is held at the last mark and logged as an error. On a futures runner a held symbol past X days refuses a run for the host's date (`FEED_HOLE_REFUSAL`, exit 1); the equity runner on a host behind UTC only warns (section 2.1). A symbol not held only warns (`FEED_HOLE_NOT_HELD`) |

Both conditions must hold, because one late symbol should not stop a whole book that can price the rest; the held symbol
waits at its last price (AlgoGators/trade-ngin#250). Exactly X days is not stale. A failed pull is not by itself a hold: it
counts every symbol the table knows as missing, and the same rule decides. It holds the run outright only when it leaves
nothing to judge: no symbol named by the row or found in the table in the last 60 days, and none expected beyond them
(`_missing_verdict`, `catchup.py`). How each case reads, with N of 2 and X of 4:

| Case | Desk | Engine |
|---|---|---|
| One symbol 5 days stale, not held | runs, alert | warns, runs |
| One symbol 5 days stale, held | runs, alert | a futures runner refuses, exit 1 |
| Three symbols, two of them 5 days stale, one 2 days | runs, alert | as above, per symbol |
| Three symbols, each 5 days stale | held, `data_stale` | no run is started |

The desk rule does not bind the engine: the futures runners' guard still refuses today's run when the book holds a symbol
past the tolerance; bringing the two together is AlgoGators/algogators#170. The 09:30 cron wrapper reads
no `ops.data_ready` row: it starts the binary whatever the data state, so only the engine's rules apply to it.
`services/trade-ngin/deploy/engine-rpc.compose.yml` carries the settings and their defaults. The gate row in
`services/trade-ngin/docs/design/desk-service.md` describes an earlier form of the rule; the code is as above.

What to do:

| Step | Why |
|---|---|
| 1. Read the log or the alert to find which rule spoke and which symbols it named | the three rules have different fixes |
| 2. Tell a closure from a hole. A missing Sunday on a grain or livestock root is a closure, and the classifier treats it as one: [DATA_SOURCES_OF_TRUTH.md section 4.5](../../DATA_SOURCES_OF_TRUTH.md#45-dates-weekends-and-the-calendar). Bad bars stored once are section 4.4 there | a closure needs nothing; a hole needs the data |
| 3. Get the data in: refresh the feed, then run again | a refused date stored nothing, so running it again is safe |
| 4. If later dates ran without the bar, replay from the date that settles it, in order (section 9.2) | those dates held the symbol at its last mark and booked no move for it |
| 5. If the contract table has the bar and the `.v.0` row does not, it is a hole in the continuous tables | the continuous tables give the date grid, so the hole removes a real bar from every run, and through the shared covariance dates it changes every other symbol's book too. The release's hole fill writes the known holes by the rule of [FUTURES_ROLLS.md section 2.2a](../../FUTURES_ROLLS.md#22a-missing-continuous-rows-the-hole-fill); the causes and the effect are in [ROLLS_AND_DATA.md](../01_the_book/ROLLS_AND_DATA.md). AlgoGators/algogators#87 is the pull's side of the fill |

## 10. Team rules for a change that reaches main

These rules hold for every pull request into `main` or an integration branch. No check enforces them: CI does not require
them, so the author and the reviewer apply them by hand. Each exists for one reason: a stored number of this book is read
by investors and by the next run, so no number may move unless someone meant it to, can say why, and has measured it.

### 10.1 Before a change merges

| Rule | Why |
|---|---|
| A change that alters what the engine computes or trades needs its own decision and its own backtest comparison against the base branch | so no number moves by accident; a latent defect found in passing is reported and decided on its own, not fixed inside an unrelated change |
| The comparison runs `bt_portfolio_conservative` over the 2y and the 5y windows, cell by cell against the base branch, and the 16y as well when the change could depend on the period (section 7 names the windows) | one window is one sample of history: a whole-contract book on 500,000 swings by tens of points on its start date alone, so one window can hide or invent an effect |
| The regression set for every PR: the unit tests, a full build of every application and the backtest comparison above | a green unit suite has passed over live defects before; the books themselves are the bar |
| The PR says "moves no stored number", or lists the numbers that moved and why (AlgoGators/algogators#179, item 3) | the reviewer can only check a move the author has named |
| A refactor moves no stored value, log line, key or expression; a refactor that moves a number has found a hidden behaviour, which is recorded and decided on its own (AlgoGators/algogators#152) | a refactor is meant to change the code's shape, not the book |
| A merge of two lines of work adds no behaviour; anything else is its own PR | so the merge itself moves no number and each change can be read and reverted alone |
| The two live futures runners (`live_portfolio.cpp`, `live_portfolio_conservative.cpp`) stay identical in their shared blocks; a fix to one goes into the other in the same PR | the two books must not behave differently where they are meant to be the same; one runner is AlgoGators/algogators#152 |
| A change to the equity book: each fix with a test that fails on its parent; a change to shared code compared on a futures backtest too; both equity windows run | a test that passes before the fix proves nothing about the fix, and shared code reaches the futures book as well |

Two more steps for every PR are the work of AlgoGators/algogators#181 (item 6), not rules in force:

- **The live mode gate on past dates.** It does not pass on `main` with its default arms. Section 4.5 reads, from the code,
  which arms pass on which database: with MGC.v.0 and SIL.v.0 not stored, `gate.sh <database> BOTH ABSENT ABSENT` finds no
  refusal and passes with the switch off, but it exercises neither MGC nor SIL; the default `TPL` arm refuses there; with
  MGC.v.0 and SIL.v.0 stored, the base book refuses with any arm.
- **The same backtests through the Python wrapper, equal to the engine's.** That equality is what AlgoGators/algogators#181
  exists to prove: the wrapper's parity test runs three synthetic symbols, not the books.

### 10.2 Tests and CI

| Rule | Why |
|---|---|
| Write paths are tested against the real schema: database tests connect only through `TRADE_NGIN_TEST_DSN` and run on migrated tables (AlgoGators/trade-ngin#86), and the suite runs after a clean rebuild both with and without `TRADE_NGIN_REQUIRE_DB=1` | a test on a mock or a fake can pass over a write path that cannot write, and a stale build object can hide a changed header |
| No merge with a failing test; CI green on every push | a red test merged becomes the next author's noise, and a real failure then hides among known ones |
| Pushed code has coverage above 80 percent, reached by a separate coverage PR when needed, not by reopening each PR | untested code is where the next defect sits; [performance_upkeep.md section 2](../../performance_upkeep.md#2-ci) says what CI measures and what it does not, so this is checked by hand |

### 10.3 History, commits and PR text

| Rule | Why |
|---|---|
| Squash by default: every merged PR is one commit on `main`, titled with the PR title; a merge commit only when the PR says why (AlgoGators/algogators#179, item 1). The repository's default squash message copies every commit message of the branch into the squash commit, so whoever merges clears it unless the repository setting leaves it blank | each change then reads, bisects and reverts as one unit |
| No AI attribution in commits or PR text: no AI co-author line, generated-with footer or tool link. Human co-author lines stay (AlgoGators/algogators#179, item 2) | the history credits the people who answer for the change |
| One plain subject line, a body where the change needs one, no trailers, documents not mixed into code commits; plain PR titles and bodies | the default squash message copies each branch commit's message into the commit on `main`, trailers included (AlgoGators/algogators#179), so whatever a branch commit carries becomes part of `main`'s history |

### 10.4 Production, migrations and live runs

| Rule | Why |
|---|---|
| Migrations are applied by hand, in number order, before any binary that needs them, and the date is recorded in `services/trade-ngin/migrations/README.md`; a new file takes the next free number, and an unused number is never reused (section 6) | no table records what a database holds, so the README is the record, and a binary run against tables it does not expect fails or writes wrong rows |
| Live runs are dated: production is `<date> --send-email`, a test run passes a date and never `--send-email` (section 2) | the date fixes which rows a run settles, and a test e-mail reaches the book's readers as a real day |
| When a merged change moves the book's stored history, the stored backtests (2y, 5y, 10y, 16y) and the live book's history are re-run on the merged engine and the data folders rebuilt; the PR names who re-seeds and when (AlgoGators/algogators#179, item 3; the steps are section 1 and section 9.2) | otherwise production's stored history was computed by an older engine than the one that trades, and live and backtest no longer describe the same book |
| Regime work runs on copies of the database, never production (AlgoGators/algogators#128) | the same reason as the standing rule on tests at the top: test rows on production mix with the book's |

### 10.5 Documents and issues

| Rule | Why |
|---|---|
| A change of behaviour changes its document in the same PR; reference documents state what is true now, without line numbers, production database names, or who decided and when | a reader follows the document as written, so a stale one sends the next operator the wrong way |
| Open work is a GitHub issue with an owner, and the issues are the list | nothing outstanding is lost when a person or a note moves on |
| Issues and PRs are written in full, `AlgoGators/<repository>#N`, and a move of issues between repositories is checked with a snapshot, a diff and a restore | a transfer renumbers issues, and a bare `#N` then points at a different one |

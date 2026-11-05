# The live mode gate

`scripts/live_mode_gate/` holds one test: does a live runner do in production what it does in every test run? It is
run by hand against a test database. It is not part of CI.

## What it proves

Production runs a live binary with a date equal to the host's date. Every test and replay runs a date in the past,
because test data ends before today. Those two modes take different branches in a few places (the feed-hole refusal,
above all: [docs/LIVE_RUN_CYCLE.md](../../docs/LIVE_RUN_CYCLE.md), section 1.2). Without this gate, the mode production
uses would be the one mode no test runs.

`gate.sh` runs the same dates both ways, on one unchanged binary, from one restored starting book, and fails unless
production's mode ends where the past-date runs end. To make a past date look like the host's date it shifts the wall
clock of the runner's own process only (`hostclock_shim.c`, below). Nothing in the product reads a test switch, so
there is no switch that could be left on in production. The database server's clock is not touched.

| Part | Book and dates | What must hold |
|---|---|---|
| 1 | CONSERVATIVE, 2026-04-24 to 05-03 | Past-date mode: ten runs, every exit 0. Production mode: 04-24 and 04-25 exit 0; 04-26 is refused (exit 1, a `FEED_HOLE_REFUSAL` line) because a held symbol has no bar past the tolerance; every later date is refused by the missed-run guard ("A run was missed"); the refused runs store nothing after 04-25. Then the restart: the eight missed dates run in order on a later host day, every exit 0, and every stored table ends identical to the past-date chain |
| 2 | BASE, 04-24 to 04-26 | The same refusal on 04-26, and after the restart every stored table identical to the past-date chain: the twin runner still behaves as a twin |
| 3 | The equity book, 2026-06-15 to 06-19 | Five dates, every exit 0 in both modes, every stored table identical. A run dated 06-24, whose previous day never ran, is refused (exit 1) and stores nothing, the run-metadata row included |
| 4 | CONSERVATIVE | The repair after late bars: one day's bars are moved aside, the two runs after it are made without them, the bars are put back, and that date and every later one are re-run in order. The late day is one on which a held roll books (2026-04-27 with the switch off, 2026-04-28 with it on), so a first pass that legged the roll late must not book it twice (AlgoGators/trade-ngin#188). Every stored table ends identical to the clean chain |
| 5 | CONSERVATIVE, with `GATE_SWITCH=on` only | Every `ROLL` pair is priced at its two contracts' own closes of its roll bar and stored once, including a micro's roll (the chain run on to 2026-06-17, the micros' June roll); a one-bar backward stray and a backward switch by the vendor (ZS on 04-30 and 05-01) are ignored, so the tables equal the clean chain; a crossover day's per-contract rows arriving late, then re-run; a run killed while it writes, then run again; a run whose `ROLL` legs fail to store, which must exit 1, then replayed. Each replay ends identical to the clean chain |

`gate.sh` has 15 checks, and 22 with `GATE_SWITCH=on`. It exits 0 only when every check passed and every run it
started finished; the result is printed and kept in `$GATE_OUT/GATE.txt`, ending `LIVE MODE GATE PASSED` or
`LIVE MODE GATE FAILED: <n> check(s)`. A driver that stops (a refusal of its own, a failed restore) fails the gate by
itself, so no check can pass on runs that were never made.

"Identical" means `compare_csv_dirs.py --tol 0`: every column of every exported `trading` table, on every row key,
exactly equal. The columns are taken from the CSV headers, so a column a migration adds is compared from the day it
exists. Two kinds of column are left out and named in the output: provenance (insert clocks and surrogate keys, which
two identical runs must differ in) and `trading.live_results.settled_at`, which holds the wall clock of the run that
wrote it and is compared only as NULL or not NULL. A column present on one side only fails the comparison.

## The files

| File | What it does |
|---|---|
| `gate.sh` | The gate: makes the feed hole, runs each part through `chain.sh`, evaluates the checks, puts the bars back |
| `chain.sh` | Runs one book over a range of dates, one dated run at a time, and snapshots the book's `trading` rows. Its units: `chain` (the dates in order), `prodrestart` (production mode, then the restart), `latebars` (the late-bar repair), `refused` (one run that must be refused), `interrupt` (a kill or a failed write, then the replay) |
| `hostclock_shim.c` | The clock shim: adds `TDATED_HOST_CLOCK_OFFSET` seconds to `clock_gettime(CLOCK_REALTIME)`, `gettimeofday` and `time` in the one process it is loaded into. Every clock read of the runner moves together, so a clock-reading decision added to the runner later is exercised too |
| `compare_csv_dirs.py` | The comparison of two snapshot folders described above |
| `late_roll_check.py` | Proves that part 4's late day really books a held roll that the first pass legged later (or, for late per-contract rows, traded flat), and that the bars moved aside were all put back |
| `roll_legs_check.py` | Part 5's check of every `ROLL` pair against the contract table's closes and the vendor's contract id of each bar: one closing and one opening leg, each roll stored once, priced on the right bar. It reads exports of the tables, not the engine's code |
| `s0_project.py` | Proves each restore of the starting book: every column a later migration added (013 to 040) must hold the value that migration leaves on old rows, and a table the snapshot has no file for (`position_overrides`, `strategy_config`) must hold no row of the book |
| `settled_at_backfill.sql` | The backfill statement of migration 035, copied unchanged, which the restore runs on the restored rows, scoped to that one book |

The scripts call the two modes `M3` (a past date on the real clock) and `M2` (the run date shown as the host's date,
12:00 local) in their folder names.

## Settings

Run from the engine's folder:

```bash
GATE_SWITCH=on GATE_WORKTREE=<built linked worktree> GATE_OUT=<empty folder> GATE_S0_DIR=<snapshots> \
GATE_CONFIG_SRC=<entry config> GATE_RUN_LOCK=<lock file> GATE_PG_ENV=<password file> \
scripts/live_mode_gate/gate.sh <test database> [conservative-arm base-arm equity-arm]
```

| Setting | What it is |
|---|---|
| `<test database>` | The database the gate writes to. Refused unless its name contains `scratch` |
| `GATE_WORKTREE` | Required. A built linked worktree (the runners under `build/bin/Release`). The main checkout is refused: the gate writes its run config into `config/` there, deletes the logs its runs leave and moves `apps/strategies/results` out |
| `GATE_OUT` | Required. An empty folder for the runs, the proofs and `GATE.txt`. A gate that makes runs refuses a folder whose `runs/` is not empty |
| `GATE_S0_DIR` | Required. The folder holding the three starting books, `S0_conservative`, `S0_base` and `S0_equity` (below) |
| `GATE_CONFIG_SRC` | Required. `defaults.json.entry` and `portfolios/<book>/` with `portfolio.json`, `risk.json` and `email.json` for `conservative`, `base` and `equity_mr` (below) |
| `GATE_RUN_LOCK` | Required. A lock file path: each run takes it, so one runner writes the database at a time. A lock left by a gate that was stopped blocks the next one until it is removed |
| `GATE_PG_ENV` | Required. A file that sets `SCRATCH_PGPASSWORD`, the test server's password; it is never printed |
| `GATE_SWITCH` | `off` (the default) or `on`. `on` sets `per_contract_rolls` true in both futures books' `portfolio.json` and adds part 5. The shipped futures templates have the switch on, so `on` is the mode production runs |
| `GATE_PSQL` | Optional. The `psql` to use; the default is a Homebrew PostgreSQL 16 path |
| `GATE_LATE_DATE` | Optional. Part 4's late day, in place of 2026-04-27 (switch off) or 2026-04-28 (switch on) |
| `GATE_EVALUATE_ONLY=1` | Makes no run and changes no data: the checks are evaluated on the runs already in `GATE_OUT`. This is how a check is shown to fail: copy a finished gate, plant a difference, evaluate |

The three optional arguments choose, for the conservative, base and equity books in that order, what is added to the
book's `portfolio.json` before its runs. The scripts call each choice an arm. The defaults are `TPL`, `ABSENT`,
`ABSENT`.

| Arm | What the run's `portfolio.json` gets |
|---|---|
| `ABSENT` | the entry copy as it is |
| `TPL` | the entry copy plus the worktree's own template blocks `listing_dates`, `instrument_id_relabels` and `trading_rule_removals` (`config_template/portfolios/<book>/portfolio.json`); the driver stops if the template lacks one of the three |
| `RL` | the entry copy plus four `instrument_id_relabels` entries for MES, MNQ, MYM and M2K, dated 2026-02-22 |
| `LD` | the entry copy plus `listing_dates` for the four index micro pairs (MES/ES, MNQ/NQ, MYM/YM, M2K/RTY), listed 2019-05-06, ratio 10 |
| `BOTH` | `RL` and `LD` together |

## What the test database and inputs must be

The gate writes to the three live books (`CONSERVATIVE_PORTFOLIO`, `BASE_PORTFOLIO`, `EQUITY_MR_PORTFOLIO`) of the
database it is given, so give it a database of its own, never a production one or a shared copy.

| Item | State it must be in |
|---|---|
| The server | Reached on the loopback address (the entry config must hold the literal `127.0.0.1`), port 5433, user `postgres`. The driver refuses the database unless the server reports that name, port 5433 and time zone `UTC` |
| Migrations | Those of the code under test; 040 is needed for `GATE_SWITCH=on`. See the limit on migration 041 below |
| Futures bars | `futures_data.ohlcv_1d` and `futures_data.ohlcv_1d_raw` through 2026-05-03, and through 2026-06-17 with the switch on, plus `futures_data.contract_ohlcv_1d` for the switch-on parts. `6M.v.0` must have its bars of 2026-04-21 to 04-25 (the gate moves them aside, below). Part 5's stray and backward switch rewrite the vendor's id of `ZS.v.0` on 04-30 and 05-01 from 194904 to 42009683 and back, so those ids must be the stored ones |
| Equity bars | The equity book's symbols through 2026-06-19 |
| The starting books | `S0_conservative` and `S0_base`: one `trading_<table>.csv` per `trading` table that has a `portfolio_id` column, with a header and that book's rows only, ending on the day before the first gate date, plus `trading_strategy_trading_days_metadata.csv` (the start date it restores). The book must hold `6M.v.0`, or part 1 has nothing to refuse; for the micro roll of part 5 it must hold a micro. `S0_equity`: the same for the equity book, ending on the day before 2026-06-15 |
| `defaults.json.entry` | `config_template/defaults.json` with the `database` block filled in for the test server: host the literal `127.0.0.1`, port the string `"5433"`. The driver sets the database name for each run and checks each run's log for it |
| `portfolios/<book>/` | The three books' files without `listing_dates` and `instrument_id_relabels` (the driver stops if the entry copy has them: the pairs reach a run only through an arm). With the switch off, the file's own `per_contract_rolls` stands |

What the gate changes in that database and puts back: the `6M.v.0` bars of 2026-04-21 to 04-25 are moved into a schema
`gate_hole` for the whole gate and put back at the end; part 4 moves one day's bars into a schema `tdated_late` and puts
them back; part 5 installs and drops a trigger `gate_interrupt`. A gate killed part way can leave any of these in place,
so do not reuse that database for another gate. The book's rows themselves are replaced from the starting snapshot
before each unit.

What it never does: run without a date, pass `--send-email`, use another host or port, or touch a database whose name
lacks `scratch`.

## What it does not cover

- The e-mail: no run sends one, and no check reads the report.
- The wrapper and cron: the gate starts the binary directly, so the wrapper's lock and its date are not exercised.
- The production image: the gate runs on a local build, not in the container or on its New York time zone.
- The QT desk's modes (`--desk`, `--publish` and the rest) and the desk's data-ready rule: only the daily model run of
  the three books runs.
- The equity feed hole: part 3 has no held hole, and on a host behind UTC the equity runner warns where the futures
  runners refuse ([docs/LIVE_RUN_CYCLE.md](../../docs/LIVE_RUN_CYCLE.md), section 1.2).
- Any date outside its windows, and production's own data.

## Limits of the scripts as they stand

- **macOS only.** The scripts build the shim with `clang -dynamiclib`, load it with `DYLD_INSERT_LIBRARIES`, hash
  `libtrade_ngin.dylib`, and call `caffeinate` and `pmset`; the default `psql` is a Homebrew path. The shim's header
  names the Linux route (build with `-shared`, load with `LD_PRELOAD`), but the shim's interpose section and these lines
  would have to change with it. A run also stops when the disk has less than 2.5 GB free.
- **The default arms and the gold and silver micros.** The run refuses a book whose stored symbols and declared pairs
  disagree (`ListingDates::refusals` and `ListingDates::unpaired_micro_refusals`, `include/trade_ngin/data/listing_dates.hpp`).
  On a database that stores `MGC.v.0` and `SIL.v.0` beside `GC.v.0` and `SI.v.0`, the base book's runs refuse under
  every arm: `ABSENT`, `RL`, `LD` and `BOTH` declare no gold or silver pair, and `TPL` stops because the base template
  has no `instrument_id_relabels` or `trading_rule_removals`. The conservative book with `TPL` needs `GATE_SWITCH=on`
  there, because the gold and silver pairs refuse with `per_contract_rolls` off. On a database without `MGC.v.0` and
  `SIL.v.0`, the conservative book with `TPL` refuses (GC is stored and MGC is not). So with the release's universe the
  base arm needs a change before the gate passes.
- **Migration 041.** `s0_project.py` knows no column of 041 (`mark_price`, `settle_from_price`, `settle_to_price` and
  `settle_mark` on `trading.positions`, `statistics_sessions_per_year` on `trading.live_results`). Starting books
  exported from a database without 041 therefore fail the restore proof on a database with it (the restored side has
  columns the snapshot lacks, and the comparison fails on the header), and the driver stops. Export the starting books
  from a database at the same migrations, or name those columns in `COMPARE_PROJECT_OUT` (for example
  `positions.mark_price,live_results.statistics_sessions_per_year`), which `compare_csv_dirs.py` drops from the one side
  that has them and reports with their count of non-empty cells.

How to build the starting books and run the gate step by step is in
[docs/knowledge/05_operations/RUNBOOKS.md](../../docs/knowledge/05_operations/RUNBOOKS.md), section 4.

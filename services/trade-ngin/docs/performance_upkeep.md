# Performance and Upkeep

How the engine is built, tested, released and operated: the build and test commands, the CI pipeline, the container
and its schedule, the wrapper and its exit codes, the rules for manual and catch-up runs, the watchdog, and the
database migrations.

What a live run does on a given date, and what each stored row means, is in [LIVE_RUN_CYCLE.md](LIVE_RUN_CYCLE.md).
Configuration is in [CONFIG_GUIDE.md](CONFIG_GUIDE.md).

## 1. Build and test

Install the dependencies with the scripts in `requirements/` (`install_ubuntu.sh`, `install_macos.sh`), then copy
the configuration template and fill in its placeholders (`config_template/README.md`):

```bash
cp -r config_template config
```

Build and test:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j
ctest --test-dir build --output-on-failure
```

The build produces the library `trade_ngin`, one test executable, one benchmark executable (`trade_ngin_bench`,
`benchmarks/CMakeLists.txt`) and nine applications, seven runners and two tools
(`apps/strategies/CMakeLists.txt`, `apps/backtest/CMakeLists.txt`, `apps/tools/CMakeLists.txt`):

| Binary | Source | Book |
|---|---|---|
| `live_portfolio_conservative` | `apps/strategies/live_portfolio_conservative.cpp` | CONSERVATIVE, the futures trend book (the scheduled one) |
| `live_portfolio` | `apps/strategies/live_portfolio.cpp` | BASE, the two-sleeve futures test book |
| `live_equity_mr` | `apps/strategies/live_equity_mean_reversion.cpp` | the equity mean reversion book |
| `bt_portfolio_conservative` | `apps/backtest/bt_portfolio_conservative.cpp` | CONSERVATIVE backtest |
| `bt_portfolio` | `apps/backtest/bt_portfolio.cpp` | BASE backtest |
| `bt_equity_mr` | `apps/backtest/bt_equity_mean_reversion.cpp` | equity backtest |
| `bt_equity_validation` | `apps/backtest/bt_equity_validation.cpp` | equity validation run |
| `qt_settings_check` | `apps/tools/qt_settings_check.cpp` | a tool: the QT desk asks it whether a settings change would be accepted by a live run |
| `contract_rolls_dump` | `apps/tools/contract_rolls_dump.cpp` | a tool: the per-contract roll rule's held series per root, as CSV, read only |

A Release build puts them in `build/bin/Release/`. The options `TRADE_NGIN_BUILD_TESTS` and
`TRADE_NGIN_BUILD_APPS` (both on by default, `CMakeLists.txt`) leave out the tests, or the
applications and the benchmark.

Tests are GoogleTest, compiled into `trade_ngin_tests` and discovered by CTest (`tests/CMakeLists.txt`).
Two Python suites run through CTest as well: `MigrateRiskJson.ScriptSuite` and `CheckLiveTrading.ScriptSuite`
(`tests/CMakeLists.txt`). Run a subset with a name pattern, for example `ctest --test-dir build -R CheckLiveTrading`.

Three test scripts run the real binaries against a test database. None is in CI: each is started by hand, and each
deletes or rewrites rows of the books it tests, so point it only at a test database. Their guards differ (last column):

| Script | What it tests | What it needs |
|---|---|---|
| `scripts/live_mode_gate/gate.sh` | that a live runner does in production (a date equal to the host's date) what it does in every past-date test run | a database of its own, whose name contains `scratch`, starting books and a built linked worktree ([scripts/live_mode_gate/README.md](../scripts/live_mode_gate/README.md)) |
| `scripts/qt_e2e.sh` | the QT desk end to end on `QT_E2E_PORTFOLIO`, as AlgoLens drives it: it writes the `trading.position_overrides` rows and calls the gRPC commands, then reads the books back. The steps: the model run; a desk edit and its desk run; an override request, a refused self-approval and the approver's decision; an approval of a past day refused by the 10:00 cutoff; the catch-up fallback publishing the model's book with no e-mail | run on the host next to an `engine-rpc` container (`QT_AGENT_CONTAINER`, default `engine-rpc`) whose own `DB_NAME` is the same test database (so production's `engine-rpc` is refused too); `DB_NAME` must start with `scratch_`, `test_` or `qt_e2e_`. Each run takes the latest date on or before `QT_E2E_LAST_DATE` that has no command rows yet and first deletes that book's rows. Exit code = the number of failed checks |
| `scripts/qt_runner_check.sh` | the QT runner's own rules on the real binary and `QT_E2E_PORTFOLIO`: the model run of a desk-editable book finalises the system book's previous day from its own rows; an unpublished day refuses the next run; the daily cutoff (`--publish` freezes the day and sends nothing, `--send-now` sends from the stored rows, the model run publishes nothing, `--fallback` resets the desk book to the model's and publishes it, `--send` refuses an unpublished day); a desk flatten stores a 0-quantity row priced at the latest known price; an approval of a proposal that moved after its snapshot is refused; a published day refuses a second publish and a desk run | `QT_RUN_CMD` (runs the engine and prints `EXIT=<code>`), `QT_PSQL_CMD`, market data through 2026-10-07, migration 027; it deletes that book's rows first. Its only guard refuses the production database by name. Exit code = the number of failed checks |

The QT rules these two scripts check are in `docs/design/qt-master-rulings.md` and `docs/design/qt-contract.md`.

## 2. CI

The engine's workflows are in the AlgoGators repository's `.github/workflows/`, each named
`svc-trade-ngin.*.yml`; shared jobs they call begin with `_`.

| Workflow | When | What it does |
|---|---|---|
| `svc-trade-ngin.quality.yml` | every pull request and push to `main` that touches the engine | the shared C++ job `_quality-cpp.yml`: configure with the `ci-gcc13` CMake preset (gcc 13; the preset sets warnings as errors, but the engine's CMake adds `-Wno-error`, so warnings do not fail the build; dependencies from the repository's vcpkg manifest), build, and run CTest. On a push to `main` it also builds the engine image and pushes it tagged with the commit (section 8) |
| `svc-trade-ngin.rpc.yml` | a pull request or push to `main` that touches `services/rpc`, `proto`, `scripts`, `deploy`, `migrations`, `src/data` or `src/storage`; called by the deploy | the gRPC server's stubs and Python tests; migrations 025, 027, 028 and 029 applied and rolled back on a throwaway PostgreSQL 16; and the schema ownership guard |
| `svc-trade-ngin.python.yml` | a pull request or push to `main` that touches the Python module or the code it wraps; a tag `tradengin/v*` | builds and tests the `tradengin` Python wheel (`python/`); on the tag, every wheel and the upload to PyPI |
| `svc-trade-ngin.deploy.yml` | a push to `prod` | section 8 |
| `svc-trade-ngin.publish.yml` | a tag `trade-ngin/v*` | the quality job, then a release image |
| `svc-trade-ngin.watchdog.yml` | daily, and on a pull request that touches the watchdog | section 7 |

The schema ownership guard fails if a string literal in `src/data` or `src/storage` (`.cpp`, `.hpp`) begins with
`futures_data.`, `equities_data.`, `options_data.`, `synthetic.`, `auth.` or `research.` (`svc-trade-ngin.rpc.yml`, the
pattern). A schema named later in a literal (for example `"FROM equities_data.corporate_action "`,
`src/data/postgres_database.cpp`) is not caught, and reads of those schemas exist.

What the quality job does not do: it runs no coverage measure and no coverage threshold, no lint (clang-tidy and
clang-format are switched off in `svc-trade-ngin.quality.yml`), no memory check and no sanitizer build. The team's rules
that pushed code has coverage above 80 percent and a green CI therefore stand, and the coverage part is checked by
hand.

## 3. What runs where

One image, built from `Dockerfile`, runs in two shapes. Started as it is, the container's only job is to run
cron: that is the scheduled futures book of this section and sections 4 to 6. Started with the command `rpc`
(`scripts/docker-entrypoint.sh`), it is `engine-rpc`, the engine's gRPC server, whose first service is the QT desk;
it runs the desk's engine runs, its catch-up scheduler (the model runs, the 09:30 send and the 10:00 fallback) and
the book e-mail of the QT books, with its own host cron of watchdogs (`deploy/engine-rpc.compose.yml`,
`deploy/qt-engine.cron`). `docs/design/desk-service.md` and `docs/design/rpc.md` describe it; the deploy workflow
rolls it (section 8). Moving the daily jobs to the production database is AlgoGators/algogators#185.

The cron container:

| Piece | What it does | Where |
|---|---|---|
| Time zone | `TZ=America/New_York` is set in the image, so the cron line and the wrapper's `date` are New York time. The futures runners read the date argument as local midnight (`std::mktime`) and format it back through UTC, so on a New York clock the date keeps its day and every stored instant of a run carries the 05:00Z stamp on every date of the year (the parse leaves the daylight flag at zero, so local midnight is read as standard time in summer as well). On a clock ahead of UTC the same parse would land the run a day early; the pin keeps the container on the one zone the stored rows are written in. | `Dockerfile`; `apps/strategies/live_portfolio_conservative.cpp` and the comment above it |
| Entrypoint | cron gives each job a fresh, minimal environment, so variables passed into the container are not visible to a scheduled job. At start the entrypoint writes the variables the job needs (names matching `TRADING_*`, `DB_*`, `PG*` and `QT_MAIL_*`, plus `TZ` and `LD_LIBRARY_PATH`) to `/app/.cron_env`, mode 600, warns if neither the encryption key variable nor a key file is present, and then runs cron in the foreground. | `Dockerfile`; `scripts/docker-entrypoint.sh` |
| Cron file | installed as `/etc/cron.d/live_portfolio` and loaded as the crontab | `Dockerfile`; `live_portfolio.cron` |
| Healthcheck | every five minutes, `pgrep -x cron`; a container whose cron daemon has died reports unhealthy. Pair it with a restart policy in the compose file on the host. | `Dockerfile` |
| Wrapper | the script cron calls (section 5) | `scripts/run_live_portfolio.sh` |

One binary is scheduled: `live_portfolio_conservative`, the wrapper's default
(`scripts/run_live_portfolio.sh`). The wrapper reads `LIVE_BINARY` to run another binary, but a variable set in
the container's environment does not reach the scheduled job: the entrypoint copies only `TRADING_*`, `DB_*`, `PG*`,
`QT_MAIL_*` (the book e-mail's settings, section 5), `TZ` and `LD_LIBRARY_PATH` into `/app/.cron_env` (`scripts/docker-entrypoint.sh`), and the cron line sets no
variable (`live_portfolio.cron`). `LIVE_BINARY` takes effect only when the wrapper is started by hand with the
variable set in that shell. The equity book (`live_equity_mr`) and BASE (`live_portfolio`) have no scheduled job: they run only
when started by hand under the rules of section 6.

## 4. The schedule

```cron
30 9 * * * /app/scripts/run_live_portfolio.sh >> /proc/1/fd/1 2>&1
```

09:30 New York time, every calendar day (`live_portfolio.cron`). The wrapper has no weekday guard
(`scripts/run_live_portfolio.sh`).

The futures book runs seven days a week because a run dated D settles the row dated D-1, and the runner refuses to
start when the previous calendar day has no stored run (section 6). Each day of the week has work to do:

| Run on | What it does |
|---|---|
| Tuesday to Friday | books the previous day's session and trades |
| Saturday | books and trades the Friday session |
| Sunday | carries the book over the closed Saturday (a symbol that printed on Saturday is booked; the others are held) |
| Monday | books the Sunday Globex session |

A Monday-to-Friday schedule would drop a session a week and break the chain from each day to the one before it. The
schedule does not decide whether the previous day was a session: the engine decides that per symbol, and on a day no
symbol printed it carries the whole book (see "How the futures runners differ" in [LIVE_RUN_CYCLE.md](LIVE_RUN_CYCLE.md)).

## 5. The wrapper, its exit codes and the logs

`scripts/run_live_portfolio.sh` does five things in order:

1. Sources `/app/.cron_env`; if the file is missing it logs a warning and goes on.
2. Takes the lock: `mkdir /tmp/live_portfolio.lock`, removed on exit. If the directory already exists it logs
   "skipping" and exits 0.
3. Checks that the binary exists and is executable.
4. Changes to `/app`, because the runners resolve `./config` and `logs/` from the current directory.
5. Runs `<binary> <today> --send-email`, where today is the container's New York date, logs the result and exits
   with the binary's own exit code.

| Exit code | When it happens | What it can leave stored |
|---|---|---|
| 0 | A futures live runner (`live_portfolio`, `live_portfolio_conservative`) reaches the end of the run with the book not refused by the risk step, no sizing hold, and its book e-mail, when the run sends one, sent. Three cases share this code: a completed day; a carried day (no symbol has a consumed bar for the previous day, so the whole book is carried and no order is made); a run in which a store failed, was logged and was passed over. A refusal by the risk step never ends at 0: see the rows for 3 and for 1 after the `live_run_metadata` row. | A completed day: the `live_run_metadata` row, the signals, the executions, the settled rows of the previous day, the day's positions, the day's `live_results` row and its equity point. A carried day: the same without signals; the book is unchanged, so no order is made. A passed-over failure: the day without the table whose store failed, which can be the `live_run_metadata` row, the signals, the executions of a sleeve that has no roll leg, the previous day's positions, the day's positions, the previous day's equity point, or the day's `live_results` row and equity point. Exit 0 is not proof that the day is stored in full: the evidence of a completed day is the date's `trading.live_results` row. |
| 0 | The wrapper `scripts/run_live_portfolio.sh` finds its lock directory present and skips. The binary is not started. | Nothing. |
| 1 | A futures live runner refuses before it writes the day's `live_run_metadata` row: a bad argument; a logger that cannot be initialised; a config, database or instrument registry failure; a missed previous run; a listing-date refusal; missing margin metadata; a sleeve that cannot be built or started; bars that cannot be loaded; a feed in which no symbol has a usable bar date; a holiday calendar that is not loaded or does not cover the run date or the day before it; with `per_contract_rolls` on, a futures roll calendar that cannot be loaded; a held symbol with no bar for longer than the tolerance on a run for the host's date; roll state that cannot be read or placed; a stored book the run does not load; a sleeve book the sizing read cannot load; a sizing capital the portfolio manager rejects. | Nothing. |
| 1 | A futures live runner stops after it wrote the day's `live_run_metadata` row. | It depends on where the run stops. The estimator history cannot be seeded, or the portfolio step fails: the `live_run_metadata` row (marked `risk_refusal` when a sizing hold was recorded first, when the portfolio step failed because the risk step refused a book that has no stored previous positions to be held at, or, with scope `sleeve`, when a sleeve risk module or the sleeve's risk step could not answer on a sleeve that has no stored previous positions). A roll leg has no usable close, executions cannot be generated with a roll owed, the strict assertion fails (the row is marked `strict_assertion`), the netting refuses a row, or the day's roll executions cannot be stored: the row and the day's signals. The margin calculation fails: the row, the signals, the executions and the rewritten positions of the previous day. The previous day's `live_results` row cannot be updated: the row, the signals, the executions and both days' positions; the previous day's `live_results` row and equity point are left as they were. The previous cumulative roll cost cannot be read: a half-written day, with the executions, both days' positions and the previous day's settled `live_results` row and equity point stored, and no `live_results` row for the day. Any other error: whatever was written before it. |
| 2 | A live runner (`live_portfolio`, `live_portfolio_conservative`, `live_equity_mr`) is started with no date. It prints the usage and runs nothing. The wrapper always passes a date. | Nothing. No email is sent. |
| 2 | A futures live runner in one of the desk's modes refuses the command (`QT_REFUSED` in the log), for example: the command row is already finished, or is of another kind, portfolio or date; the book is not desk-editable; the override decision cannot be checked; the day is published and so frozen; yesterday's desk book is missing or an earlier day is unpublished; there is no proposal or no desk book to act on; the desk's book is over the fund's margin cap (`live_portfolio_conservative` only). A desk run that fails rather than refuses (a read or a store that did not happen, `QT_FAILED`) exits 1, and so do the model run's own desk refusals (yesterday's desk book missing, an earlier day not published). | The command row marked `refused`, except when it was already finished. A desk or override run refused at the margin cap has already stored the day's desk book, left unmarked so that it cannot be published. |
| 3 | Futures live runners only. The risk step refused the book, or the sizing read failed with every sleeve book loaded. The risk step refuses the book when the one pass cannot produce its answer (the overlay sleeve is not running, a held symbol that cannot be weighed has no usable close or multiplier, an input or an overlay reading is not a finite number, the pass fails), and when a sleeve risk module refuses or replaces its sleeve or cannot evaluate it: one search on the summed book cannot hold one sleeve apart, so the whole book is held. Every such refusal is recorded with its reason as an error, whether a module decided it or a module failed. The book is held and the run goes on to the end. A report that fails on such a day is logged and the code stays 3. | A held day: the held book as the day's positions, no order, the day's `live_results` row and its equity point, and the day's signals. On a sizing hold the portfolio step is not run, so the sleeves compute no forecast and the signal stored for each symbol with seeded history is 0. The `live_run_metadata` row is marked `risk_refusal`, and the subject and the body of the report are flagged. |
| 4 | A live runner (all three) run with `--send-email` stored the day, but the book e-mail was not sent: the report module (`python -m algogators_rpc.services.report`) failed, timed out or could not start. One `EMAIL_FAILED` line at error level says so. A run without `--send-email` never exits 4. A futures day that exits 3 keeps 3, and an equity run whose results were not all saved exits 1. | The day, as on exit 0, without the e-mail. |
| 0 or 1 | The equity live runner (`live_equity_mr`) returns these two codes, 2 and 4 as above, and never 3. It returns 1 on each of its refusals, and also when the previous day's positions or `live_results` row cannot be settled, when an execution cannot be stored or applied, and at the end of a run whose results could not all be saved. | On exit 1: nothing if the stop comes before the run's first store, as every refusal does (the feed checks, the missed-run guard, the held-symbol check and the corporate-action de-duplication included); otherwise the `live_run_metadata` row and whatever was written before the stop. On exit 0: the day; a failed store of the `live_run_metadata` row or of the previous day's equity point is logged and passed over. |
| 0 or 1 | The backtest runners (`bt_portfolio`, `bt_portfolio_conservative`, `bt_equity_mr`) take no argument. They return 1 when the config, the database, a strategy or the backtest itself fails, or when a storing run's run id already has rows in `backtest.results` or `backtest.final_positions` (the run is refused before its first cycle), and 0 when the backtest ran. `bt_equity_validation` returns 0 only when every check passes. | On exit 1: nothing when the run id was refused; otherwise no `backtest.results` row and nothing saved at the end of the run; the `backtest.final_positions` rows of the bar dates processed before the failure remain, because they are written during the run. On exit 0: the results, unless the save to the database failed; that failure is logged and passed over, so exit 0 does not prove the results are stored. |
| 127 | The wrapper cannot find the binary, or it is not executable. | Nothing. |
| 1 | The wrapper cannot change to the application directory. | Nothing. |
| any other code | No runner and no script returns any other code of its own. The wrapper passes on whatever code the binary ended with and logs it on one line. | Not defined by the code. Read the log and the tables. |

Exit 0 is the code of every run that reaches its end with the book not refused by the risk step, no sizing hold and
its book e-mail, if it sends one, sent, so it does not prove that the day is stored in full: a store
that fails in a futures runner is logged and the run goes on (`apps/strategies/live_portfolio_conservative.cpp`), and the wrapper returns 0 without
starting the binary when it finds the lock held (`scripts/run_live_portfolio.sh`). Read the date's
`trading.live_results` row; the watchdog reads it (section 7).

Exit 1 before the day's `trading.live_run_metadata` row is written
(`apps/strategies/live_portfolio_conservative.cpp`) stores nothing. A stop after it leaves what was already
written, as the table says step by step: that row, and depending on the step the signals, the executions and
the positions. The strict assertion (a book change with no price and no execution) stores no book and adds
`strict_assertion` to that row's `portfolio_config`; the row can also carry `risk_refusal` at
exit 1, as the table says.

The futures runners refuse with exit 1 in two calendar cases, on every run:
the holiday file did not load, and the calendar does not cover the year of the run date or the year of the
day before it. Both are checked before the day's `live_run_metadata` row is written, so nothing is stored.
The equity runner refuses with exit 1 in three: the file did not load, the calendar does not cover the year
of the run date, and no trading day is found in the fourteen days before the run date. All three also come
before its `live_run_metadata` row. (Futures: `apps/strategies/live_portfolio_conservative.cpp`; equity: `apps/strategies/live_equity_mean_reversion.cpp`.)

On an exit 3 the email subject opens with `[RISK MODULE FAILED - BOOK HELD]` and the body with the alert (the runner passes `risk_module_failure_text` to the report module as `--alert`). Code 3 is
`kRiskModuleFailureExitCode` (`include/trade_ngin/live/risk_module_failure.hpp`); `main` returns it
through `futures_run_exit_code` (`include/trade_ngin/live/python_mail.hpp`, `apps/strategies/live_portfolio_conservative.cpp`).
The causes are set in `live_portfolio_conservative.cpp` (the sizing hold, and the
risk step refused the book, whichever module caused it); the marks are written by `mark_risk_refusal` and `mark_strict_assertion`
(`include/trade_ngin/live/run_metadata_marks.hpp`). The design comment at the top of
`risk_module_failure.hpp` is the specification. Code 3 is a futures runner code: the equity runner never returns it.

A sleeve risk module that does answer, and answers REFUSE, refuses the whole book in the same way: the row is marked
with `risk_refusal` (`live_portfolio_conservative.cpp`), the refusal is recorded with its reason as an error and
the run exits 3. A refusal of a book that has no stored previous positions to be held at fails the portfolio call
instead: the row is marked the same way and the run exits 1. No refusal by the risk step ends at 0. The
watchdog reports the mark either way (section 7). The modules and the refusal contract are in [RISK_MODULES.md](RISK_MODULES.md).

Logs:

| Log | Where |
|---|---|
| The wrapper's lines and everything the runner prints | the container's standard output (the cron line redirects to `/proc/1/fd/1`), read with `docker logs trade-ngin`. Wrapper lines are timestamped and tagged `[live-portfolio]`. |
| The runner's log file | `/app/logs/<prefix>_YYYYMMDD_HHMMSS_partN.log`. Prefixes: `live_trend_conservative` (CONSERVATIVE), `live_trend` (BASE), `live_equity_mr` (equity) (`live_portfolio_conservative.cpp`, `live_portfolio.cpp`, `live_equity_mean_reversion.cpp`). The logger keeps ten files per prefix per directory (`include/trade_ngin/core/logger.hpp`). |
| A dated equity run | `/app/logs/<YYYY-MM-DD>/`, one directory per replayed date, so a replay does not evict another date's logs (`live_equity_mean_reversion.cpp`) |

## 6. Manual and catch-up runs

| Rule | Why |
|---|---|
| A run always carries an explicit date: `<binary> YYYY-MM-DD`. | A run with no date prints the usage and exits 2, and runs, stores and sends nothing (`include/trade_ngin/live/run_args.hpp`), so a live binary that `scripts/dev_build_run.sh` builds and starts with no argument prints the usage and exits 2. The two run modes (a date equal to the host's date, a past date) and what differs between them are in [LIVE_RUN_CYCLE.md](LIVE_RUN_CYCLE.md), section 1.2. |
| Production is `<date> --send-email`. A test or a replay passes the date and omits `--send-email`. | Without the flag nothing is mailed. The book e-mail is sent by the Python report module (`services/rpc/algogators_rpc/services/report`), which the runner runs after storing the day (`include/trade_ngin/live/python_mail.hpp`). To read the report of a stored day without mailing it, run `python -m algogators_rpc.services.report --portfolio-id <id> --date <date> --preview-dir DIR`; it writes `report.html` and the chart images there. `QT_MAIL_TEST_TO=<addr>` on a QT `--send-email` run mails only that address and records nothing: `sent_at` is left as it was, so a day already sent can be sent again as a preview. |
| One runner at a time. Never start two live or backtest runners together, whatever the books. | The runners share tables and each assumes it is the only writer for the length of its run; inside a process the market data bus is a single instance (`include/trade_ngin/data/market_data_bus.hpp`) that a runner switches off and on around its own steps. |
| The lock protects wrapper runs only. | `/tmp/live_portfolio.lock` is taken by `run_live_portfolio.sh`. A binary started directly takes no lock and is not stopped by it, so before a manual run check that no scheduled run is in progress and that none will start while it runs. |
| A replay runs every date forward, in order. A lone re-run of an older date is not supported. | The run dated D settles the row dated D-1 and seeds from it, so every later row is built on the earlier ones. After a missed day, or after correcting data for a past date, run that date and then each later date in turn up to today. The runner does not refuse a lone re-run: its missed-run check asks only whether the previous day is stored (`live_portfolio_conservative.cpp`). A futures re-run of a date replaces each sleeve's execution rows of that date, of every type; the equity runner replaces by order id, so a fill its re-run no longer makes keeps its row. A sleeve's `trading.signals` rows of the date are replaced when the re-run stores at least one signal for that sleeve. |
| A missed day stops the next run. | When the previous calendar day has no results row and no stored positions while an earlier run exists, the runner logs "A run was missed. Replay every date from ... forward, in order" and exits 1 before any write (`live_portfolio_conservative.cpp`). |
| A held symbol with no bar for longer than `live.data_staleness_tolerance_days` stops a run for the host's date and only warns on a replay of a past date. | The rule is exactly that: the symbol is held (a non-zero stored quantity), it has had no bar for more days than the tolerance, and the run's date is the host's date (`rule_held_feed_holes`, `include/trade_ngin/live/session_book_gate.hpp`). On a replay of a past date the same finding is a warning. The line is tagged `FEED_HOLE_REFUSAL`. The equity runner applies the same rule, but its run instant is midnight UTC of the run date (`core::parse_utc_date`) and the rule compares host-local dates: on a host behind UTC, as the image's New York zone is, that is the day before the host's date, so the equity check warns and does not refuse. A symbol the book does not hold never stops a run: one `FEED_HOLE_NOT_HELD` warning names every such symbol. A feed in which no symbol has a usable bar date refuses every run. The remedy is to refresh the feed and run again. |
| A late bar is a warning, and its remedy is the replay. | When a bar of a held symbol arrives after the run that should have consumed it, the first run that consumes it prints one WARNING line naming the symbol, the bar's date, the amount no stored row carries and the remedy: re-run the date that settles that bar and every later date in order. No stored value changes on its own (`include/trade_ngin/live/late_bar_warning.hpp`, `live_portfolio_conservative.cpp`). |

## 7. The watchdog

The mono repo's `.github/workflows/svc-trade-ngin.watchdog.yml` runs `scripts/check_live_trading.py` every day at 15:00 UTC, after the
09:30 New York run (`svc-trade-ngin.watchdog.yml`), and on demand. It asks whether the engine's output reached the
database, which covers a stopped container, a dead cron, a missing binary, missing credentials and a run that
failed part way. It reads only; it connects with the repository's database secrets.

Per book (`LIVE_PORTFOLIOS`, a comma list of portfolio ids, default `QT_CONSERVATIVE_PORTFOLIO`,
`check_live_trading.py`) it reads four things (`_fetch`, `_fetch_run_marks`) and `evaluate` decides. The workflow
sets no `LIVE_PORTFOLIOS`, so it watches the QT book only: `CONSERVATIVE_PORTFOLIO` is watched once the variable
names it, and until then a refused or missing CONSERVATIVE day is not reported.

| Reading | Source | Reported when |
|---|---|---|
| The run clock | `max(created_at)` of `trading.live_results` for the book, read in UTC. A completed run writes its results row at the end, with only the equity point after it. | there is no row, or it is more than `MAX_CALENDAR_DAYS_SILENT` calendar days old (default 1) |
| The book stamp | the latest `trading.live_results.date` not after today | it is more than the same number of calendar days old |
| The positions stamp | the latest `trading.positions.date` with `portfolio_type = 'system'`, not after today | the latest results row says the book holds positions and the positions are older than it: a partial write. A flat book writes no position rows and is healthy. |
| The run marks | the latest `trading.live_run_metadata` row not after today: whether its `portfolio_config` carries `risk_refusal` or `strict_assertion` | either mark is present. A held day stores its results and positions, so the first three readings pass it; the mark is what says the book was held. |

Thresholds are calendar days, because the book runs every day. The default of 1 allows today's run to be still in
progress and does not allow yesterday's to be missing.

How it fails: any reported reason makes the script exit 1, so the workflow run fails, and it opens a GitHub issue
labelled `live-trading-down`, or adds a comment to the open one (`_file_issue`). A run that refused to
start writes no row at all, so it shows as silence on the run clock on the following day. If the database cannot be
reached the script stops on the connection error: the workflow run fails and no issue is filed. On a pull request
that touches the script or the workflow only the self-test runs (`svc-trade-ngin.watchdog.yml`), with no
database.

## 8. The release path

| Step | Trigger | What happens |
|---|---|---|
| Image | a push to `main` that touches the engine (`svc-trade-ngin.quality.yml`) | the image is built from `Dockerfile` and pushed to the GitHub container registry as `algogators/trade-ngin-engine`, tagged with the commit (`sha-<commit>`) and the branch, with a build provenance record (`_publish-container.yml`). It is built beside the tests; the deploy takes it only when that run passed |
| Deploy | a push to `prod` (a fast-forward of `main`) that changes the engine outside its documents (`svc-trade-ngin.deploy.yml`) | the workflow takes `main`'s image of the commit when `main`'s quality run for it passed and its rpc checks did not fail; otherwise it runs the quality and rpc jobs and builds the image itself. Then it connects to the trading host and runs `deploy/deploy.sh` with the image's digest, never a moving tag: the script waits for no engine run in progress and outside 09:00 to 10:15 New York time (the 09:30 send and the 10:00 cutoff), switches `engine-rpc` to the new image, waits for its health check, and switches back if the check never passes |
| Release image | a tag `trade-ngin/v*` (`svc-trade-ngin.publish.yml`) | the quality job, then a tagged release image; production deploys come from `prod`, not from tags |

`deploy/deploy.sh <image>` in more detail: it changes only the `QT_ENGINE_IMAGE` line of the host's compose `.env`
(it stops unless there is exactly one), and does nothing when that image is already deployed. It pulls the image and
stops, changing nothing, unless the image holds the engine binary. It then waits, checking every 30 seconds, while an
engine run is in progress inside `engine-rpc` or the New York time is between 09:00 and 10:15 (`QUIET_FROM`,
`QUIET_TO`), because a restart would kill a model run or an override part way through its writes; after 45 minutes
(`WAIT_MINUTES`) it gives up and changes nothing. It keeps a copy of `.env` (the ten newest), switches the image and
waits up to 300 seconds for `engine-rpc` to report healthy; if it does not, it switches back to the previous image and
fails. On success it records the previous image in `.previous-image`, removes older engine images, and prints the
command that rolls back.

The deploy rolls `engine-rpc` only. No workflow deploys the cron container of section 3: its image is built by hand from the release commit (`scripts/futures_release/README.md`, step 11), and the move of the daily jobs is AlgoGators/algogators#185; a change made by hand
inside a running container does not survive its next recreation. Rolling back is `deploy.sh` with the earlier image,
which the script prints, or a re-run of an earlier deploy. The deploy applies no database migration (section 9).

## 9. Migrations

The files are in `migrations/`. The numbers present are 001 to 006, 012 to 029 and 034 to 041; 007 to 011 and
030 to 033 are unused and never reused. `migrations/README.md` lists them in the order to apply and records by
hand the date each was applied to each database. They are
applied to the database in number order, by hand, before the binary runs (the one column read with a fallback is
the fee of 014: without it the registry warns and prices every fill at the code default,
`src/instruments/instrument_registry.cpp`). A backtest against a database missing 037 or 038 still exits 0 and
stores nothing or only part of the run. No workflow and no deploy applies them, and no table records which are
applied. One script applies one migration: `scripts/futures_release/03_apply_040.sh` (AlgoGators/algogators#124)
applies 040 from the checkout's own `migrations/040_roll_split.sql`, refusing a file that differs from the commit,
and compares the schema before and after. Every other
migration is applied by hand. The binary from 021 on names `portfolio_type` in every read and write of the live tables and fails
against a database without 021 (`migrations/README.md`, "Deploy notes"). 040 goes before a binary that runs with
`per_contract_rolls` on. 041 may go before or after its binary, which writes its columns only when the database
has them; rows written without it keep those columns NULL, and the book e-mail then leaves out the notes they carry.

021 to 025 and 027 to 029 are the QT desk's migrations. 034 to 039 were numbered 028 to 033 on the line that
joined `main` (AlgoGators/trade-ngin#237) and were applied to the production database under those numbers
(`migrations/README.md` records the dates). Each file's header says so; none is applied there again. Their SQL is unchanged, so the
comments they stored still name the old numbers.

| Number | File | What it does |
|---|---|---|
| 001 | `001_add_portfolio_type.sql` | the `portfolio_type` column that separates the system's own rows from the desk's |
| 002 | `002_corp_action_applied.sql` | `trading.corp_action_applied`, the record that makes corporate actions apply once |
| 003 | `003_equity_query_indexes.sql` | indexes for the equity read paths of a live run |
| 004 | `004_get_trading_days_portfolio_scope.sql` | `trading.get_trading_days()` under version control, with the portfolio-scoped overload |
| 005 | `005_corp_action_applied_run_date.sql` | `run_date` on `trading.corp_action_applied` |
| 006 | `006_corp_action_applied_basis_ratio.sql` | `basis_ratio` on `trading.corp_action_applied` (see [BROKER_BASIS_RECONCILIATION.md](BROKER_BASIS_RECONCILIATION.md)) |
| 012 | `012_strategy_id_width.sql` | `strategy_id` widened to 100 characters on positions, live results and signals |
| 013 | `013_executions_netting_adjustment.sql` | `netting_adjustment` on both executions tables (see [COST_MODEL.md](COST_MODEL.md)) |
| 014 | `014_contract_metadata_fee_per_contract.sql` | `"Fee Per Contract"` on `metadata.contract_metadata` |
| 015 | `015_executions_execution_type.sql` | `execution_type` (STRATEGY, ROLL, BORROW) and `instrument_id` on both executions tables (see [FUTURES_ROLLS.md](FUTURES_ROLLS.md)) |
| 016 | `016_positions_instrument_id.sql` | `instrument_id` on `trading.positions` and `backtest.final_positions` |
| 017 | `017_live_results_roll_costs.sql` | `daily_roll_costs` and `total_roll_costs` on `trading.live_results` |
| 018 | `018_backtest_results_costs.sql` | `transaction_costs`, `roll_costs` and `total_roll_fills` on `backtest.results` |
| 019 | `019_contract_metadata_fixes.sql` | corrections to nine contract metadata rows (data only) |
| 020 | `020_risk_detail.sql` | `risk_detail` on `trading.live_results` and `backtest.equity_curve` (see [RISK_MODULES.md](RISK_MODULES.md)) |
| 021 | `021_qt_books.sql` | the three books: `qt_proposal` allowed in positions and equity_curve, `portfolio_type` on executions and live_results and in their keys, `positions.moved_by` |
| 022 | `022_strategy_config.sql` | `trading.strategy_config` (the desk's settings, versioned, one active row per portfolio); `settings_used`, `published_by`, `published_at` on `trading.live_run_metadata` |
| 023 | `023_qt_command_log.sql` | `trading.position_overrides` rebuilt as the desk's command log; `live_results.book_source`; `strategy_registry.portfolio_group` and `desk_editable` |
| 024 | `024_drop_retired_qt_tables.sql` | drops four retired QT tables; irreversible, no rollback file |
| 025 | `025_qt_command_log_hardening.sql` | the command log's one-decision and status-order rules, and its grants |
| 026 | `026_contract_metadata_ibkr_fees.sql` | the per-contract IBKR fee in each `"Fee Per Contract"` cell, with a CHECK that the fee is a positive number; needs 014 |
| 027 | `027_qt_daily_cutoff.sql` | `publish_source` and `sent_at` on `trading.live_run_metadata` (the desk's daily cutoff) |
| 028 | `028_ops_data_ready.sql` | schema `ops` and `ops.data_ready`, the data pull's "ready" record that the QT data-driven run reads |
| 029 | `029_qt_rescind.sql` | the desk's Rescind command on `trading.position_overrides` |
| 034 | `034_column_comments.sql` | comments on columns and tables of `trading` and `backtest`; no data change |
| 035 | `035_live_results_settled_at.sql` | `settled_at` on `trading.live_results`; each existing row that has a later row of its book is stamped `1970-01-01 00:00:00+00` |
| 036 | `036_live_results_statistics.sql` | twenty statistics and overlay columns on `trading.live_results` |
| 037 | `037_backtest_statistics.sql` | thirteen statistics columns on `backtest.results` and six overlay columns on `backtest.equity_curve`; needs 018 and 020 |
| 038 | `038_final_positions_row_mark.sql` | `row_mark` (`NO_BAR` or NULL) on `backtest.final_positions`; needs 016 |
| 039 | `039_backtest_end_date_comment.sql` | comments on `end_date` of `backtest.results` and `backtest.run_metadata`; no data change |
| 040 | `040_roll_split.sql` | the roll split: `roll_bar_move`, `roll_gap_points` and `roll_gap_value` on a roll's closing leg in both executions tables, with a CHECK, and `roll_bar_moves` and `roll_gap_values` on `backtest.results`; NULL with `per_contract_rolls` off, moves no number; needs 015 and 018 (see [LIVE_RUN_CYCLE.md](LIVE_RUN_CYCLE.md), section 5) |
| 041 | `041_book_email_columns.sql` | what the book e-mail shows that only the run knew: `mark_price`, `settle_from_price`, `settle_to_price` and `settle_mark` (with a CHECK) on `trading.positions`, `statistics_sessions_per_year` on `trading.live_results`; no backfill |

Each file's header is its specification: what it adds, why, what it leaves untouched, its guards (for example
whether a second apply is a no-op, or which earlier migration it needs) and how it is undone. Read the header before
applying a file.

Every migration but 024 has a rollback beside it, `<number>_<name>_rollback.sql`; 024 drops the retired QT tables
and cannot be undone.

Shell tests in the same folder apply a migration and its rollback to a real PostgreSQL and check the result:

| Test | Covers |
|---|---|
| `test_001_migration.sh` | 001 |
| `test_012_strategy_id_width.sh` | 012 |
| `test_013_executions_netting_adjustment.sh` | 013 |
| `test_014_contract_metadata_fee_per_contract.sh` | 014 |
| `test_015_017_018_roll_columns.sh` | 015, 017 and 018 |
| `test_016_positions_instrument_id.sh` | 016 |
| `test_019_contract_metadata_fixes.sh` | 019 |
| `test_020_risk_detail.sh` | 020 |
| `test_021_qt_books.sh` | 021 |
| `test_022_strategy_config.sh` | 022 |
| `test_023_qt_command_log.sh` | 023 and 024 |
| `test_025_qt_command_log_hardening.sh` | 025 (a throwaway server only: it creates cluster roles) |
| `test_026_contract_metadata_ibkr_fees.sh` | 026 |
| `test_027_qt_daily_cutoff.sh` | 027 (a throwaway server only) |
| `test_028_ops_data_ready.sh` | 028 (a throwaway server only) |
| `test_029_qt_rescind.sh` | 029 (a throwaway server only) |
| `test_034_column_comments.sh` | 034 |
| `test_035_live_results_settled_at.sh` | 035 |
| `test_036_live_results_statistics.sh` | 036 |
| `test_037_backtest_statistics.sh` | 037 |
| `test_038_final_positions_row_mark.sh` | 038 |
| `test_039_backtest_end_date_comment.sh` | 039 |
| `test_040_roll_split.sh` | 040 |

Migrations 002 to 006 and 041 have no shell test. CI applies and rolls back 025, 027, 028 and 029 on a throwaway
PostgreSQL 16 (`svc-trade-ngin.rpc.yml`); the other tests are run by hand. A test's own header says how to run it; the full form is destructive and is
pointed at a throwaway database made for the purpose, never at a database that holds data.

## 10. Troubleshooting

| Symptom | Look at |
|---|---|
| No run today | `docker ps` (is `trade-ngin` up and healthy), then `docker logs trade-ngin` for the wrapper's lines |
| "skipping: another run already holds" | a run is in progress, or a killed run left `/tmp/live_portfolio.lock` behind inside the container; remove the directory only after checking that no runner process is alive |
| "binary not found or not executable" (127) | the image, or a wrong `LIVE_BINARY` on a wrapper started by hand |
| "WARNING: /app/.cron_env missing" followed by a database failure | the container was not started through `scripts/docker-entrypoint.sh` |
| Exit 1 with "A run was missed" | replay the missing dates in order (section 6) |
| Exit 3 | the day is stored and the book is held; read the `RISK_MODULE_FAILURE` or `SIZING_HOLD` line in the log for the cause, fix it, and the next run trades from the held book |
| Exit 4 | the day is stored and the report was not delivered; read the `EMAIL_FAILED` line in the log for the cause (the book's `email.json`, or the report module's interpreter, application directory and time limit: `QT_MAIL_PYTHON`, `QT_MAIL_APP`, `QT_MAIL_TIMEOUT`) |
| A watchdog issue | the reasons listed in the issue map to the four readings of section 7 |
| A hung or failing test | `ctest --test-dir build -R <name> -VV` |

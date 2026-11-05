# How we verify

A change on this repository counts as done when stored results, read independently, agree with it. A clean exit proves
nothing. Each change is first put in a class by what it is allowed to move, and the class sets the proof: a change that
should move nothing must leave every stored cell equal to its parent's; a change that moves the book must match a
reference model run outside the engine, written from the design alone; and every figure that moves is worked by hand
from stored rows on a date or two. A dated chain of live runs must reproduce the backtest, and production's run mode is tested against the
past-date mode on the same binary. Then the change is read by someone who did not write it: the code, the evidence
recomputed, and a clean rebuild and rerun. Each method below exists because something false passed without it.
Day-to-day commits use a lighter check; the full one applies at branch heads, migrations, merges and anything that can
move the book.

Paths are in `AlgoGators/algogators`; a bare file name repeats a path given nearby. "main" means AlgoGators/algogators
main. Issues are cited by their number in `AlgoGators/algogators`; pull requests by their number on
`AlgoGators/trade-ngin`.

Related: [WARNINGS.md](WARNINGS.md) for the failure modes of the code, data, repository and production, each with the
rule that prevents it, and [RUNBOOKS.md](../05_operations/RUNBOOKS.md) for
the commands. Results belong to [TUNING_STORY.md](../03_tuning_and_results/TUNING_STORY.md) and
[RESULTS.md](../03_tuning_and_results/RESULTS.md).

## 1. What counts as evidence

**What it is.** A run is verified against three things together: its log, every stored table it writes, and a figure
worked by hand from inputs the code does not share.

**Why.** Each of these alone has passed something false:

| Evidence | Why it is not enough alone | What passed |
|---|---|---|
| Exit code | A run can fail and exit 0 | With migrations 037 and 038 rolled back, a 2-year backtest exited 0 with 2,089 ERROR lines and stored nothing. It still ends on the success line: `main` in `services/trade-ngin/apps/backtest/bt_portfolio_conservative.cpp` logs a failed save and carries on. A live run has the same gap (AlgoGators/algogators#160) |
| Log | A success line does not prove a row changed | A futures runner logged success on an UPDATE that matched zero rows, so $153.75 written to `positions` was discarded; only the next write's row count showed it |
| One stored column | A column can look unchanged while the book moves | `live_results.risk_scale` read 0.8811 to 0.8823 on all ten days while the book moved by up to 7 contracts: the column then evaluated the final book, not the multiplier that sized it |
| Row counts | Equal counts are not equal rows | An equity backtest kept 889 executions on both sides while 1,171 execution values, 787 position values and 475 equity-curve values moved |
| A check that reads the code's own output | It agrees with the code by construction | An early equity realized-P&L check read the stored `average_price`, the code's own basis. Of its 67 and 9 matches, only 36 and 3 held real amounts; the rest were zeros that could not fail |

**How, on this repository.**

- **Exit 0 is nothing.** Count the ERROR lines and read the tables. Never read only the last line of a log.
- **Unit tests are weak alone.** The first per-contract roll code was reported clean, and the reference model,
  once fed the engine's real inputs, found 419 cells wrong on 2 years (section 4). In the desk (QT) work,
  35 green tests and green CI sat beside a write path that could not write on the real schema.
- **Build every app, not only the test target.** A check that built only the tests missed `bt_portfolio`,
  `bt_equity_validation` and `bt_transaction_cost_report`, two of which the change edited.
- **Run the full suite, serially, and compare failing test names, not counts.** `ctest -R` subsets are not the suite, and a
  subset misses the failures in what it does not run. A count can hide one new failure behind one fixed.
  Database tests share one database, so they run serially. A rerun after a correction is the whole suite again.
- **Coverage above 80 percent, and CI green, read with `gh pr checks`.** Adopted 2026-10-07: code that is pushed has more
  than 80 percent line coverage on the code it pushes (its added and changed lines), reported without the database and with it. The
  per-contract roll work ended at 82.1 percent (2,238 of 2,726 changed lines) without and 93.5 percent with. The CI port
  (AlgoGators/algogators#120) did not carry over the coverage gate, trade-ngin lint, Sonar or Valgrind, and CI runs no
  C++ database test (below), so coverage and the database suite are checked by hand: see [WARNINGS.md](WARNINGS.md),
  warning 8.3.
- **A local build does not stand for CI.** CI builds with gcc on Linux, and a test with a dangling temporary that clang
  accepted failed there. While a test step is red the coverage step never runs, so the coverage figure is unknown. CI
  runs no C++ database test: its only database is a throwaway postgres:16 for four migrations
  (`.github/workflows/svc-trade-ngin.rpc.yml`).
- **Check a claim at the number it cites,** in the stored row or the file, not by a summary of it.
- **Use the identities the repository already states.** `services/trade-ngin/scripts/futures_release/sql/live_identity_checks.sql` checks a
  live book over a date range: one row per calendar date, value moves by the day's P&L, day P&L equals realized plus
  unrealized less costs, costs add up to the day's fills, each roll is two legs that net to zero, positions change only by
  the day's trades, and every day but the last is settled.
- **Keep what is needed to re-read a run without the database:** the commit and tree, the config used (logins left out of
  anything pushed), the exported tables, the logs, the exit code and a completion marker. A run with no completion marker
  is not cited. A later reader may have nothing else: one check had to re-derive 211-day figures from a saved snapshot
  because the database copy the run used was gone. Results handed to other teams use the same shape as the seed's
  package ([RUNBOOKS.md](../05_operations/RUNBOOKS.md), section 1.4).

## 2. Change classes, and what each must prove

**What it is.** Adopted 2026-09-08, when the refactoring program set the rule that the engine's trading behaviour does not
change. Each change is put in a class before it is built, and a measurement can move it to another class.

| Class | What may change | What it must prove | Example |
|---|---|---|---|
| A, identity | Nothing stored | Every stored cell on both futures books and the equity book equal to the parent, at tolerance 0, with nothing allowed to differ | A UTC date parse expected to be byte-identical moved 225 of 6,480 stored signal values, by up to 1.42. It was reverted and treated as class C |
| B, reported column only | Named reported columns, nothing that feeds a position | Each moved cell predicted before the run and tied to one item; the moved figure worked by hand; no other column moves | A date fix moved exactly one `backtest.results.end_date` cell per backtest (six) and the one known `net_notional` cell on the BASE book, nothing else |
| C, book-moving | Positions, fills, sizing | Waits for a decision on the design. Then the design is the judge: the reference model accepts it, every unit is compared, every moved cell is explained | Reordering the price-history read before the optimiser moved a backtest from 399 to 407 executions and Sharpe 0.608967 to 0.619561, so it did not ship as an identity change |

**Why.** A class is a claim until measured: both class C examples above were expected to be inert. An inert refactor and
a change that moves reported columns never share a commit, because one comparison cannot carry both assertions and a
combined commit leaves the diff unattributable.

**How, on this repository.** A new switch is proved by showing that with the switch absent every stored cell and both
one-pass records equal the base, after every commit. Before AlgoGators/trade-ngin#261 that proof also covered every
e-mail body; on main a run without `--send-email` writes no body to compare.

## 3. Identity gates and cell-for-cell compares

**What it is.** Two runs, the parent (OLD) and the change (NEW), on the same data, compared cell for cell across every
stored table. The units in use since 2026-10-10: backtests named by the years they trade (2y, 5y, 10y, 16y, all on one
end date), the CONSERVATIVE and BASE futures books' ten-day live chains, and the equity chain. A per-commit check uses 2y
and 5y; a full check adds 10y and 16y.

**Why, step by step.**

| Step | Why | What it caught |
|---|---|---|
| Same-binary control first: OLD against OLD | A compare measures noise before it can blame a change | A stored-book read without `ORDER BY` flips three BASE cells and one DEBUG log family in the last digit between runs of one binary. They are declared in advance, and BASE's one `net_notional` cell is compared parsed at 1e-9 |
| Expected differences written down and hashed before the run | A rule written after the diff explains the diff | A declaration too tight on log lines exposed a row-order effect of a fee migration, which was then measured on its own |
| Tolerance 0, nothing allowed to differ, every stored table | A broad allowance can explain away a real diff | Cumulative rules that matched whole log families on every unit could not prove later commits inert; compares against the previous commit with no allowances did |
| Columns derived, not listed | A hand list goes stale when a migration adds a column | `services/trade-ngin/scripts/live_mode_gate/compare_csv_dirs.py` derives its columns from the CSV headers and names what it skips. Widening an older comparator to 47 of 49 `live_results` columns found a stored Sunday volatility nobody had compared |
| Backtest tables reset per side | Stored runs persist across A/B sides: a second run with the same run id is refused at start | The first backtest A/B of the program ran without a reset and meant nothing. Since the statistics work a backtest whose run id is already stored is refused at start (`services/trade-ngin/include/trade_ngin/backtest/stored_run_refusal.hpp`) |
| Chains compared one day short | A chain's last row is not yet settled | Compared to the end, a chain's last day differed (17 `live_results` values, `total_pnl` 2841.0504 against 2758.0504) only because one side's row had been rewritten by its next run |
| Logs compared as a multiset of normalised lines, every family named | Equal stored rows can hide compensating differences | Wall clocks, durations, run ids, paths and thread ids are masked and declared. Both sides are counted before a line is called gone. A rule anchored on a logger tag missed the same line printed by a runner, so rules anchor on the text after the tag |
| The two futures runners stay byte-twin | `live_portfolio.cpp` and `live_portfolio_conservative.cpp` carry the same logic | Every runner change is made in both, and the diff of the two hunks must be empty |
| An unexplained value diff reverts the commit | Otherwise a gate becomes a report | Work stops; a rerun after the correction is the whole gate again |

**A compare must be able to fail.** A match from a check that cannot fail proves nothing, so each compare carries a
control that must fail.

- `backtest.signals` has no writer on main (`services/trade-ngin/docs/SYSTEM_ARCHITECTURE.md`), so a match on it was vacuous in every run. It
  never counts.
- A comparator printed a match for two folders that both lacked every table. `compare_csv_dirs.py` on main still exits 0
  on two folders with no CSV, so a compare refuses a run without its completion marker, and a folder with no stored table,
  before it compares.
- The statistics check was rerun with a deliberately wrong annualisation constant (K = 312): 63 cells differed, so the
  clean result at the right K meant something.
- A null diff is trusted only when the changed path is shown to run. One proof counted the 46 log lines of the changed
  resolver before accepting the match.
- An identity holds for the window it ran on. A short live window was identical because of where its rounding fell while
  the backtest moved, so chain identity is never generalised. A one-year identity anchor could not reach the listing switches at all,
  so an identity check also records the commit, the tree, the shared library's hash, and a 16-year run.

**Determinism.** The same run is repeated and compared.

- A full run repeated was identical in tables, exports and 20,246 log lines.
- Runs in parallel on separate databases were used only after a proof that parallel output is byte-identical.
- Compare the hash of the shared library `libtrade_ngin`, not of a runner: the code is in the library, and a runner
  executable carries its build path, so two builds of one commit in two directories differ.
- Backtest figures are compared only between hosts in the same time zone, because the backtest window depends on the
  host's zone (AlgoGators/algogators#153).
- Known unstable row order is compared as a multiset: the BASE export CSVs differ in row order only.

## 4. A reference model run outside the engine

**What it is.** A second implementation of the trading loop, in Python, run outside the engine: written from the design
text alone with no engine file opened, frozen by a sha256 list and run on real bars. Each review searched it for engine
identifiers and found none. At its last freeze it had 19 hashed files and 144 tests. The engine is accepted when it
matches the reference at a stated tolerance: contracts exact, continuous values 1e-9 relative. Where the two disagree, the
design text decides; where the design is silent, work stops until it is written.

**Why.** Tests written by the author of the code share the author's reading. A reference run on an empty input agrees
with anything: per-contract roll code that matched a reference run on an empty withheld set mismatched 419 cells on 2
years, 1,489 on 5 and 5,919 on 16 once the reference was fed the engine's own withheld verdicts. So an empty input at
acceptance is refused.

**How it is used.**

- **Feed it the engine's inputs.** The reference has no bar classifier, so what the engine consumed is given to it: the
  consumed-bar set, the hold set with roll holds, the cost vector, the instrument ids, the sizing capital and the stored
  book. One input file is not always enough: the consumed-series hold record has no row for a symbol with no bar on the
  signal date, and fed only that file the reference traded such symbols (63 mismatched symbol-days on 2y).
- **Fault injection.** Each fault is one asserted text replacement on a copy of the package, and the tests then run on
  the copy. The count rose from 16 of 16 to 89 of 89 by the fifth version; survivors of the first passes were closed by a
  new test before the freeze. Faults go stale when code moves: in a rerun on 2026-10-07, 2 of 89 printed NOT APPLIED and
  87 were caught, so a fault run reads that column before it reads the score.
- **A control that must fail.** An empty-input run must fail. It did; then the engine's inputs were shown non-empty and the
  output byte-identical.
- **P&L is not the gate.** The book, fills, legs, readings and scale are. The reference takes spread and impact from a
  cost vector, its impact uses a 20-day average volume where the engine uses the signal bar's volume, it does not model
  the sleeve split, and its own-capital mode is one bar behind the engine. So it cannot reproduce the engine's sizing
  capital to the contract, and at acceptance the engine's stored sizing capital is fed in. P&L is checked separately on
  stored rows: the capital closed form to the cent, and the row identity in section 6.
- **What it shares with the engine.** Both read one design, so a design error passes both. To tell a result of the
  design from a bug both share, a third backtest is written from Carver's book alone, with neither engine nor reference
  code. Acceptance compares every symbol-day of the windows, fills, roll fills and cut days included: for the trading
  loop that is 13,248, 57,977 and 164,340 symbol-days on the 2y, 5y and 16y.

The same idea is used outside the loop. The per-contract roll rule was re-implemented in Python by someone who read no
C++: 148,676 rows by 16 columns, 0 differences. A carry reference written before reading the engine's output agreed to
1.5e-14 on 20,851 rows. A copied tool is proved on stored runs before use.

**On this repository.** The reference is not in the repository. To build one for a new change, write it from the tracked
design ([TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md),
[OPTIMIZER_AND_RISK_DESIGN.md](../../OPTIMIZER_AND_RISK_DESIGN.md), [FUTURES_ROLLS.md](../../FUTURES_ROLLS.md)) without
opening the engine's source, freeze it, inject faults, feed it the engine's inputs, and compare at the tolerances above.

## 5. Dated chains and the live mode gate

**What it is.** Live runs replayed date by date on a copy of the database, compared with the backtest and with
production's own mode.

**Why.** A live run reads its stored previous day, so one day cannot be judged alone, and production runs with the date
equal to the host's date while every test runs a past date. Without a chain, the guard behaviour below was never seen:
when production mode was first measured on 2026-10-10, complete days matched past-date mode, and one refusal made the
guard refuse every later date, so the April chain stored 2 days of 10.

**How, on this repository.**

- **Every run is dated.** A run with no date exits 2 on main (`kDatelessRunExitCode`,
  `services/trade-ngin/include/trade_ngin/live/run_args.hpp`). Test runs pass the date and never `--send-email`.
- **One runner per database at a time.** Runners on one database race on upserts to the same row keys. Check the database
  line the runner logs before trusting its rows.
- **Snapshot first.** A replay upserts over the rows you compare against. Snapshot, and prove the restore against a
  fingerprint taken with a total order: earlier restores called byte-identical were identical only as sets.
- **Replay the chain.** The missed-run guard refuses a lone day. After a late bar, re-run that date and every later date
  in order; re-running one middle date reverts it to its as-written state. Never insert a placeholder row to satisfy a
  guard.
- **The backtest must equal a dated chain.** A 55-run chain from an empty book matched a backtest with 0 differences on
  571 position rows, 30 fills, 16 roll legs and 54 days of sizing capital. Account value differed by at most $0.000129,
  because live stores four decimals.
- **The live mode gate.** `services/trade-ngin/scripts/live_mode_gate/gate.sh` runs the same dates in both modes on the same binary, shifting
  only the runner's own clock with `hostclock_shim.c`, so no test switch exists in the product. It covers the feed-hole
  refusal, the missed-run guard, the restart, a re-run after a late bar and, with the switch on, held rolls, a kill while
  writing and a failed leg write. On main it holds 15 checks, and 22 with `GATE_SWITCH=on`; it held 18 and 25 until
  AlgoGators/trade-ngin#261 removed the report body comparison. It requires `GATE_WORKTREE`, a separate checkout, and
  refuses the main one. Steps: [RUNBOOKS.md](../05_operations/RUNBOOKS.md).
- **The mode guard.** `services/trade-ngin/tests/live/test_mode_branch_guard.cpp` lists every line that reads a clock, the environment or a
  mail flag; a new mode branch fails it until a test executes both arms, and so does a change that moves one of
  those lines, even through a clean textual merge.

## 6. Hand-worked days

**What it is.** Every figure a change moves is computed by hand from stored rows and shown as a table, with inputs,
arithmetic and stored value. The table is the proof, and a reader can repeat it. Hand values come from the stored inputs,
never from the code's own intermediate. Use the rows that carry an edge: an ex-date, a holiday, a flat day.

**Why.** A worked day checks the arithmetic against the inputs, which no comparison of two runs does, and it finds what
the reader did not look for. The example below found a stored basis restated after the margin was priced.

Margin on the equity book, 2026-06-15, a positioned day and the ex-date of the META dividend (worked 2026-10-10):

| Name | Quantity (stored) | Basis the run read | Quantity x basis |
|---|---:|---:|---:|
| AAPL | 21.134566 | 290.571518 | 6,141.102925 |
| GOOGL | 15.830673 | 367.705871 | 5,821.031404 |
| META | 7.147202 | 570.951224 | 4,080.703733 |
| Sum | | | 16,042.838062 |

| Stored check | By hand | Stored |
|---|---:|---:|
| `margin_posted` | 16,042.838062 | 16,042.838059 |
| `cash_available` = 99,785.8680 - margin | 83,743.029941 | 83,743.029891 |
| `equity_to_margin_ratio` = value / margin | 6.219963552148451 | same |

The small differences are stored precision: six-decimal inputs allow 0.000637, and the cash UPDATE subtracts from its
unrounded value, which allows 0.00005. META's stored `average_price` is 570.446600, not the 570.951224 above, because the
next day's run restates the basis after this day's run priced the margin. So a stored ex-date margin sits 3.61 above
quantity times the basis now stored.

**How, on this repository.** A single cell reads the same way: the buffer fraction on 2025-04-08 was
a = (0.046645 - 0.01) / 0.046645 = 0.785614, by hand and in the run. Over a whole table the check is an identity:
quantity x (mark - prior mark) x multiplier equals stored P&L on every settled row. A year of live P&L was checked on
4,280 symbol-days this way, with 4,095 exact and every exception classified. Attribution ties to the stored total to the
cent on every run (273 runs in the last tuning round). `live_identity_checks.sql` (section 1) runs the book-level
identities.

## 7. Start-date grids, and why choices rest on mechanism

**What it is.** A backtest result is one run from one start date. At a 500,000 book the whole-contract decision on a few symbols can carry a
window's return, so a difference between two runs is not a difference between two rules until it clears the noise of the
start date.

**Why choices rest on mechanism.** Two measured quantities, named apart, set the size a Sharpe difference must have
before it says anything:

- Carver's standard error of the Sharpe ratio (AFTS, strategy three): about 0.26 on the 16-year window at a Sharpe near
  0.30, 0.45 on the 5-year and 0.69 on the 2-year. The formula assumes independent returns; trend returns are
  autocorrelated and fat-tailed, so the real error is larger.
- The spread across start dates: the chosen setup's Sharpe runs from 0.206 to 0.453 across its five 5-year starts and
  from 0.145 to 0.394 across its 10-year starts. The 16-year window is one run, so its spread is not measured.

The largest paired t-statistic between the sets of the second tuning round was 1.14
([RESULTS.md](../03_tuning_and_results/RESULTS.md), section 7). A choice rests on what it does to the book and why that
should pay, on Carver's foundation where it applies, and the numbers are read to check they do not contradict the
mechanism.

**Start-date grids.** Run both sides over many starts and read the distribution:

| Question | Grid | Reading |
|---|---|---|
| Does the classifier prefix commit change results? (2026-10-08) | 18 five-year windows, both sides | Median difference -1.72 points, mean -3.33, SD 10.92; the standard window's -16.16 was the extreme, not the typical gap |
| Do IBKR fees change results against the flat fee? (2026-10-08) | 18 five-year windows | Higher in 12 windows, lower in 6; median +1.46, SD 15.12, inside one side's own window-to-window swing (17.49). The direct cost effect is about +127 dollars a year; the rest is path |
| Is the drawdown caused by the ratchet? (2026-10-06) | 123 monthly five-year starts | Median drawdown 21.5, 95th 30.4, max 37.1 percent; the earlier single-window 18.7 and 30.5 sat at the 25th and 95th percentiles |

**Reading a grid honestly.** Starts two weeks apart share almost all their days: five of them are close to one draw, not
five. Overlap makes a sign-count p-value too small by an unknown amount. A paired daily-P&L t-test on one run overstates
t: the differences are serially dependent and the effective sample is the number of divergence episodes; on one chain a t
of 1.81 came from 8 non-zero observations. No P&L claim rests on that test.

**What counts as a difference.** The noise can be measured. Halving the roll cost changes about 700 dollars a year of cost
(+0.010 of Sharpe on a fixed path), yet it moved the four standard windows by +0.00, +0.25, -0.01 and -0.09, because any
change re-deals which whole contracts are bought. So a change smaller than the spread across start dates (about 0.25 of
Sharpe on the 5-year window) is read as no change on one window.

How the final setup was chosen: among the candidates the mechanism allowed, the pass rules were fixed before any result
was seen, because a rule chosen after the results would fit the period they came from. A window's value was the five-start mean of after-cost account Sharpe, paired by start against the parent, with
nothing rounded before a comparison; the mean of the five windows had to rise, no window could drop by more than 0.1,
and for the equity variants the 16y and 10y could fall by no more than 0.02. Path-free readings sit beside single runs:
the unrounded book, fixed-path arithmetic, and old fills repriced at new fees.

## 8. Independent review of code, evidence and reruns

**What it is.** A change is checked in four separate ways by someone who did not make it, none told what to find:

| Check | What it reads | What it proves | Rule |
|---|---|---|---|
| Code reading | Every changed line, from git, with mutations and coverage | The code does what the change claims on every execution path, including paths no run reaches | Read-only; the code is read before any report |
| Recomputation | The stored rows and logs of the change's runs | Every number the change reports, from inputs the author's scripts do not share | Its own checker is written before the author's is read |
| Rerun | A clean build on its own copy of the database, and chosen units | The result reproduces from the commit, not only on the author's build and data | It first proves it built the same library, and runs a control that must fail |
| Adversarial run | Its own build, data and dates | No pass is false: it assumes each is and tries to break it | The author's results are read only after its own runs |

**Why.** The author's own checks share the author's reading, so a defect the author did not think of passes them. Each
check reaches a different class: a code reading finds a rule or calendar that no run exercises; a recomputation finds a
wrong figure in a column the author's checks never read; a rerun finds what holds only on the author's build or data; an
adversarial run finds a pass that could not fail.

**How.** The value of a review is where it disagrees, so a review reports what it did not read, and a code review goes by
execution path, with a table mapping every changed file to the PRs that touched it as proof nothing was skipped. Reference
docs are checked the same way: every claim against the code, every worked number reproduced by a query.

## 9. Red-first tests, and tests against the real schema

**What it is.** Each test is shown failing on the parent and passing on the fix, both outputs kept. Counts are reported as
they fell: 11 of 18 red on one parent, 15 of 17 on another.

**Why.** A test that never failed may not test anything. One test set passed with its whole rule block deleted from
`services/trade-ngin/src/core/config_loader.cpp`, because the tests re-derived the rule in a local lambda.

- When the test names something the parent lacks, a shim adds the name with the parent's behaviour, so the test compiles
  and fails for the right reason.
- Mutations extend this: sets of 33 of 33 and 19 of 20 caught. Survivors are test gaps, and they have been real: mutations
  found ten rules of the one-pass search pinned by no unit test.
- Small inputs can fail to go red. With five or fewer strategies on libc++, last-writer-wins and first-registered-wins
  agree, so red needed a six-strategy rotation. A test can also pass on the parent only through hash-map walk order, so
  test both registration orders.
- A test of the design's own worked example may have no red phase, because the engine was already right; that is accepted
  when said.

**How to write a test that catches a regression.** Coverage alone is not the aim: a test counts if it fails when the
behaviour breaks.

| Rule | Why | Example |
|---|---|---|
| Run the real schema | Fakes accept what a database refuses | The desk (QT) work had blocking defects that every fake survived and that all failed against a Postgres carrying the shipped shape. Rule: at least one database-backed test against the real schema per PR |
| Use a state production produces | A hand-built state pins nothing | A held-check test built the equity run instant at local midnight, the futures runners' frame, while the equity runner uses UTC midnight, so the production-zone defect stayed green (AlgoGators/algogators#163) |
| Call the code | A copy of the rule tests the copy | The local lambda above |
| Do not re-implement the query | A fake cannot find a wrong WHERE clause | A corporate-action de-duplication test ran against a fake, so dropping `strategy_name` from the real WHERE clause would have left it passing |
| Assert the figure that matters | A substring passes while a neighbouring total is wrong | A margin test asserted one label while `Total Margin Posted` stayed on the other basis, $2,500.00 against $2,600.00 on one page |
| Assert stored values | Rendered output rounds drift away | Stored 0.8811 to 0.8823 against a rendered 0.88 |
| Say which kind it is | Source-reading tests differ from behaviour tests | Label each test as one that executes the code or one that reads the source. A source test can pass on a commented-out call, and about 30 of them skip when the runner file is not found, reading as passes |
| A skip is not a pass | Silent skips make green mean nothing | Nine files skipped without `TRADE_NGIN_REQUIRE_DB=1`; plain `ctest` verified none of the database commits |
| Test what acceptance cannot reach | Rare inputs do not occur in data | No exact half occurs in 171,401 rows, so half-away-from-zero rounding needs a unit test |
| Capture nothing that can abort | One abort loses the run | `CaptureStdout` around a throwing load aborts the whole test binary (rc 134) instead of failing one test |

**On this repository.** Add a new test file to `TEST_SOURCES` in `services/trade-ngin/tests/CMakeLists.txt` and reconfigure, then check that
`ctest -N` counts it. Run database tests with `TRADE_NGIN_TEST_DSN` set and `TRADE_NGIN_REQUIRE_DB=1`, so a missing
database fails instead of skipping. Logic in `services/trade-ngin/apps/` is not compiled into `trade_ngin_tests`, so a fix that must be
pinned moves into a header first, as with `services/trade-ngin/include/trade_ngin/live/corp_action_window.hpp`.

## 10. The lighter per-commit check, and when the full one applies

**What it is.** Adopted 2026-10-10: make the change, run a date or two, hand-compute, compare, move on. For each commit:

1. A red test on the parent, green on the fix.
2. Build, and run the suites the commit touches, not the full suite.
3. Run one or two dates of the units the commit can reach: a futures change runs two dates of CONSERVATIVE (and BASE if
   the code is shared), an equity change two dates of the equity chain, a backtest change the 2y.
4. Hand-compute the changed figure from stored rows on those dates: two dates per book, three where a statistic is new or
   its definition changes, naming the edge covered. Keep the arithmetic.
5. On those dates, one cheap check that nothing outside the commit's declared columns moved.

**Why.** A full gate on every commit is slow. The lighter check keeps the red test and a hand-worked figure on every
commit and moves the whole-table compare to the points below. Its cost is known: a move no commit declares is found
later, at the next full gate, by bisecting.

**When the full one applies.** A full gate is every unit, every stored column against OLD, and the full serial suite. It
applies:

- at each migration and each branch head;
- to anything that can move the book: a moved position stops the work under either check;
- to any change a measurement has moved to class C;
- at every merge: a merge gate compares every stored cell, exit code and CSV across all the units, and runs the
  migrations on an empty database.

Under both: the same-binary control, one concern per commit, the declaration before the run, migrations with a rollback
and a test on an empty database, byte-twin runners, and one independent rerun of the branch head from a clean build. If a
full gate shows a move no commit declares, bisect over the commits since the last clean gate. For a very small change the bar goes lower with a stated reason: a text-only change to a refusal
line needed a build and the tests, and no gate run.

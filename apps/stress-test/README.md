# stress-test

A command-line black-swan stress test of the book on a given day, and of that book with proposed
changes. It replays the events of the fund's black-swan catalogue (a private repository; its events
package is passed with `--events`) against the system, proposal and final desk books.

- **Tier 1, `shock`:** the book held unchanged through each event. Needs no engine.
- **Tier 2, `replay`:** the strategy trades through each event, through the trade-ngin engine as
  published (no change to the engine).

## Read-only by construction

`snapshot` is the only command that touches a database. It opens a read-only session and
runs SELECTs only, then writes a folder of local files. Every other command reads that
folder and never connects to anything; the engine runs with storage off and a database address that
resolves nowhere. Snapshots and reports hold fund-internal positions and prices. They go to
`$STRESS_TEST_OUT`, else to `apps/stress-test/out/` in a checkout (gitignored), else to
`~/.stress-test/out`; never commit them.

## The engine (Tier 2)

Tier 2 needs the trade-ngin engine's Python module, `tradengin`, **unchanged**: the tool uses only
its published surface (a book run from a config directory, a `MarketDataSource`, the CSV export),
never its internals, so it keeps working across engine updates. `engine-info` reports what the
installed module offers; a bar's contract id and a contract's own fee are handed over where a
module version has them, and named as caveats where it does not (the published module has
neither).

`pip install tradengin` installs a published wheel (Linux and Windows). Elsewhere, build the module
from `services/trade-ngin` (see its `python/README.md` and the trade-ngin page of
`docs/knowledge-base/`) and pass its directory with `--engine` or `$TRADENGIN_DIR`.

The published engine starts every backtest flat; it cannot start from a given book. So a replay
lets the strategy trade from flat through the last `--lead-in` sessions before the day (default 20)
and arrive with its own book, and a tested book's difference from that book is carried alongside
(see Method). `replay-check` shows how far the strategy's own book is from production's stored
books; run it after an engine update.

## Install and run

It is a member of the repository's uv workspace. From the repository root:

```sh
uv sync --locked --all-packages --all-groups   # installs the `stress-test` command
uv run stress-test --help
```

A wheel built from this folder (`uv build apps/stress-test`) installs the same command elsewhere.

```sh
# 1. Export one day (default: the latest positions date) - read-only.
uv run stress-test snapshot --portfolio QT_CONSERVATIVE_PORTFOLIO --date 2026-11-02

# 2. Tier 1: every event against the three books, plus a proposed edit of the final book.
uv run stress-test shock apps/stress-test/out/QT_CONSERVATIVE_PORTFOLIO_2026-11-02 \
    --events <catalogue>/catalogue/events-package --edit "6S=0,ZT=-1"

# 3. Tier 2: how close the strategy's own book is, then the replay.
uv run stress-test engine-info --engine <dir of tradengin>
uv run stress-test replay-check apps/stress-test/out/QT_CONSERVATIVE_PORTFOLIO_2026-11-02 \
    --engine <dir of tradengin>
uv run stress-test replay apps/stress-test/out/QT_CONSERVATIVE_PORTFOLIO_2026-11-02 \
    --events <catalogue>/catalogue/events-package --edit "6S=0,ZT=-1" --engine <dir of tradengin>

# Optional: confirm our prices reproduce the catalogue's measured moves.
uv run stress-test selfcheck apps/stress-test/out/QT_CONSERVATIVE_PORTFOLIO_2026-11-02 \
    --events <catalogue>/catalogue/events-package
```

`--edit` takes target contracts per instrument root (`6S=0` closes the franc position).
`--edit-base` picks the book it starts from (default `qt`, the final book). `--only MIN-01,MAJ-06`
runs a subset. `snapshot` reads the database connection from `--db-config`, else
`$STRESS_TEST_DB_CONFIG`, else the engine's own `services/trade-ngin/config/defaults.json`
(gitignored) in the checkout it runs in. The engine is found through `--engine`, else
`$TRADENGIN_DIR`, else an installed `tradengin` package.

`shock` writes `report.md` and `results.csv` (every event, book, scope and severity) in about two
seconds. `replay` writes `replay.md` and `replay.csv`: one engine run per event and severity, plus
a no-event run per event length, each in its own process, `--jobs` at a time (39 events: about 100
runs whatever the number of books, about five minutes on eight cores). `replay-check` writes
`replay_check.csv`. Engine runs and their logs go under `<out>/<portfolio>_<day>/engine-runs/`.

## Method

The snapshot reads the bars as the engine reads them: one copy per symbol and instant, with the
vendor's contract id of each print.

For each event and each instrument, the real move over the event window is recomputed from our
daily closes (`futures_data.ohlcv_1d`, weekend prints dropped), following the conventions of the
catalogue's own generator (`engine/synth_ab.py` in the catalogue repository):

- single-session events: previous close to event close; windows: relative to the close on
  the window's first session;
- volatility rescale k = clip(today's 252-session vol / the 252 sessions before the event,
  0.5, 2.0), applied in log space; severity 1 is central, "severe" is the generator's
  95th-percentile draw (lognormal sd 0.25);
- realism floor: no price below 80% of the instrument's all-time low.

Three scopes say which instruments move: named (the catalogue's own list for the event), sector
(the headline: plus instruments in the same sector, because the catalogue never measured ZT, ZF, 6A
or 6L) and all (every instrument as it really moved, including unrelated drift on long windows).

**Tier 1** prices the held book: P&L = contracts x (last close x contract size) x level, per
session; the report shows the worst cumulative point as % of the account at the close before the
day.

**Tier 2** turns the same moves into daily bars after the last stored bar (session i of the event
is the i-th weekday) and runs the published engine on them, one run per event and severity:

- the strategy and its settings are the ones production recorded for the day
  (`live_run_metadata.settings_used`), sized as production sizes the day's book (half compounding:
  the account at the close before the day less its drawdown from the running peak), warmed up
  on the stored history (weekend prints included) and trading from flat over the lead-in, so it
  reaches the day with its own book;
- an engine without contract ids gets the history with each roll's price gap taken out (the roll
  bar's return set to 0, earlier prices scaled; production books a roll the same way); otherwise a
  monthly-rolling contract such as CL looks far more volatile than it is and is sized to nothing;
- instruments the scope moves follow the event from their last close; every other instrument
  alternates half its daily standard deviation around its last close, so the engine's volatility
  estimate neither collapses (a flat price would make it size up) nor moves; every bar carries the
  instrument's recent volume and intraday range so the engine's session classifier trades it;
- from the event's first session the strategy rebalances once a day on the previous close with its
  buffer, risk overlay and costs;
- a tested book (stored system, proposal or final book, or an edit) = the strategy's own book,
  trading, plus the tested book's difference from it on the day. By default each symbol's
  difference lasts until the strategy itself trades that symbol, and the tested book then follows
  the strategy (production's buffer leaves a position in place until the strategy acts on the
  symbol); `--difference held` keeps it through the whole event (a desk change kept in place);
- each event run has a no-event twin of the same length, so the report separates what the event did
  from what the strategy would have done anyway.

`selfcheck` recomputes the catalogue's measured moves with this code: 429 of 431 match to
0.001 (as of 2026-11-01). The two misses are data differences in the package itself.

**How close Tier 2 gets** (measured 2026-11-02 on the conservative QT book, with the published
engine: no contract ids, no per-contract fees). The strategy's own book after a 20-session lead-in
matched the stored system book on none of the 25 stored days, so the difference rule carries real
weight. Against a replay started from the stored book itself (measured once with an engine build
that can do it), Tier 2's worst point per event was off by 0.4 points of the account on average
(median 0.01) over the 39 events at both severities, against 1.7 for Tier 1's held book; the largest
miss was the longest event (MAJ-10, 364 sessions), about 7.5 points. Without the roll adjustment the
average miss was 1.2 points.

## Limits

- Tier 1 holds the book: right for single-session events, a bound for multi-week ones.
- Tier 2 cannot start the engine from the stored book (the published engine starts flat), so the
  part of a book the strategy would not hold itself is carried by a rule (see Method), not traded
  by the engine.
- Prices only: no margin calls, intraday gaps, limit locks or liquidity beyond the cost model.
- `ohlcv_1d` `.v.0` series are the raw front month, not back-adjusted, so a window spanning a
  roll includes the roll gap (as the catalogue's own measurements do). Tier 2's event bars carry no
  roll; the history before them is roll-adjusted for an engine that reads no contract ids.
- An event session that falls on an exchange holiday is held by the engine; its move lands on the
  next session.
- No catalogued event names a crypto contract, and our MBT history starts in 2021.
- One historical path per event; the generator's randomised paths are not replayed.

## Tests

The repository's gates, as CI runs them (`.github/workflows/app-stress-test.quality.yml`):

```sh
COVERAGE_MIN=90 uv run just quality apps/stress-test
```

The tests build a synthetic snapshot and events package, and stand in for the engine with a small
module of the same surface (`tests/fake_tradengin.py`); they need no database, no engine and no
fund data. A release tag `stress-test/vX.Y.Z` runs the gates and keeps the built wheel as a
workflow artifact (`app-stress-test.publish.yml`); nothing is uploaded or deployed.

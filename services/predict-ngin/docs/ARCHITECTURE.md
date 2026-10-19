# Architecture

Last revised: 2026-10-17

`predict_ngin` is the installable core of this repository. It is being extracted from `src/` and
`scripts/research/` with a strangler-fig migration (plan step 5, 2026-10-15): the canonical code
moves into `predict_ngin`, and the old modules become thin shims, so the ~550 research scripts keep
working unchanged.

## Context map

| Context | Holds | May import |
|---|---|---|
| `shared` | time conversion, paths (`PREDICT_NGIN_DATA`), logging, money units (`ceil_cents`) | - |
| `markets` | market/series/event identity, contract terms, cross-venue pairing, settlement access, Polymarket YES/NO token canonicalisation | `shared` |
| `venues` | one anti-corruption layer per venue: `kalshi` (signer + GET-only read client), `polymarket` (gamma, CLOB, data-api) | `markets`, `shared` |
| `costs` | the point-in-time Kalshi fee schedule, Polymarket fees, perps fee tiers, `FeeModel` | `venues`, `markets`, `shared` |
| `tapes`, `books`, `capture` | historical tape readers (Kalshi, Polymarket; lake-aware), the order-book type, recorders and collectors | `venues`, `markets`, `shared` |
| `lake` | the data lake: catalog views, Polymarket builder, data contracts, manifest, the `predict-ngin data` command | `tapes` and the layers below |
| `evaluation` | Sharpe, deflated Sharpe, bootstrap, trial log | the layers below |
| `paper` | optional paper-runner base and promoted strategy rules | anything in the core |

The Kalshi and Polymarket tape readers moved to `tapes` on 2026-10-17 (plan step 5.4, D15);
`src/trading/arbitrage/kalshi_tape.py` and `src/data_quality/polymarket_tape.py` are shims. The
memory-mapped stores in `src/data/tapes.py` are unchanged.

## Dependency rule

A higher layer may import a lower one, never the reverse. `costs`, `tapes`, `books` and `capture`
are siblings and do not import each other. The core never imports `scripts/` or a legacy package.

`uv run lint-imports` enforces this, locally and in CI, with two contracts in `.importlinter`:

- `context-layers` (type `layers`) lists the contexts from top to bottom: `paper`, `evaluation`,
  `lake`, then `costs : tapes : books : capture` (independent siblings), `venues`, `markets`, `shared`. An
  import from a lower layer into a higher one, or between siblings, fails the check.
- `core-not-legacy` (type `forbidden`) forbids `predict_ngin` from importing `scripts` and every
  legacy package listed in D14, directly or transitively.

When a new import breaks a contract, move the code to the right layer instead of loosening the
contract.

## Where do I put new code

| Kind of code | Put it in |
|---|---|
| A new study | `scripts/research/<prefix>_*.py`, scaffolded by `scripts/research/new_strategy.py` (see `docs/strategies/README.md`) |
| A helper several studies share | the core context it belongs to, with tests in `tests/unit/` |
| Time, path, unit or rate-limit utilities | `predict_ngin.shared` |
| Tickers, series, events, settlement, token ids | `predict_ngin.markets` |
| A new venue API, or new endpoints of one | `predict_ngin.venues.<venue>`, GET-only, on `venues.http.ReadOnlyClient` (D6) |
| Fees, fill or impact models | `predict_ngin.costs` |
| Order-book logic | `predict_ngin.books` |
| Collectors, recorders, scheduled jobs | `predict_ngin.capture` (use `capture.runner`, D12) |
| Sharpe, deflated Sharpe, bootstrap | `predict_ngin.evaluation` |
| Paper runners and promoted strategy rules | `predict_ngin.paper` (optional, D13) |
| Data downloads and refreshes | `scripts/data/` |

Never add code to a legacy package (D14). Order placement does not belong in the core (D5).

## Shims (strangler-fig policy)

Old modules are replaced, never broken (D1, D2):

1. Freeze the old code verbatim in `tests/unit/_legacy_*.py`.
2. Write the canonical code in `predict_ngin`, with golden tests that compare it with the frozen copy
   by exact equality over input grids.
3. Turn the old module into a shim that keeps every public name and signature and delegates to the
   core.
4. Repoint a research helper only when the golden tests show identical numbers. Where a study
   deliberately used other assumptions, leave it unchanged and list it under "Deliberate variants".

Research scripts are never moved or deleted, so published results stay reproducible.

## Legacy code

The whale-era packages (`src/trading`, `src/whale_strategy`, `src/backtest` and others) are
quarantined, not deleted. Each has a `LEGACY.md`; their reported numbers are artifacts. Research
scripts and tests may still import them; the core and new production code may not. D14 lists the
packages, the files inside them that stay current, and the rule.

## Decisions

**D1. Strangler-fig with shims.** Canonical code goes into `predict_ngin`; the old module keeps
every public name and signature and delegates to it. Research code is never moved or deleted. A
research helper is repointed only when golden tests show bit-identical numbers.

**D2. Golden tests against frozen copies.** Before a module becomes a shim, its old code is copied
verbatim into `tests/unit/_legacy_*.py`. The golden tests compare the shim and the canonical code
against those copies with exact equality over input grids. The copies are never edited.

**D3. One Kalshi fee schedule (`predict_ngin.costs`).** Taker 0.07, maker 0.0175, rounded up to the
cent per order, multiplier inside the round-up (the fee schedule as published), maker fees only on
`*maker*` series from their start date (2025-04-01 unless the fee-change log says otherwise), and
the point-in-time series schedule from the API fee-change log (`kalshi_schedule`). One `ceil_cents`
lives in `predict_ngin.shared.units`. Two legacy conventions are kept as explicit parameters,
so older studies reproduce exactly: `multiplier_in_ceil=False` (kalshi_flb) and the arbitrage
noise guard `eps=1e-9`. On every cent and 0.1-cent price with whole contract counts the two guards
give the same fee; they differ only within 5e-7 cent of a whole cent.

**D4. Studies keep their own fee assumptions.** Where a study deliberately used a different
assumption (no maker fee, unrounded fees, a per-contract fee from a 100-lot, the multiplier outside
the round-up, a different rate table), its helper is left unchanged and listed below. Changing it
would change published results.

**D5. One Kalshi signer and one GET-only read client (`predict_ngin.venues.kalshi`).**
- `endpoints`: `HOST`, `API_PREFIX`, `BASE_URL`, `WS_URL`, `WS_SIGN_PATH`, defined once.
- `auth`: one signer (RSA-PSS/SHA256 or Ed25519 over `timestamp_ms + METHOD + path`, query string
  never signed). Credentials come from the arguments, then the environment, then the repo `.env`.
- `client.KalshiReadClient`: GET only by construction (`request()` raises `ReadOnlyViolation` for
  any other method; the session is only ever called through `get`), a limiter acquired before every
  attempt (`TokenBucket`, `MinInterval` or `AimdLimiter`, in `predict_ngin.shared.ratelimit`), retry
  with back-off on network errors, 429 and 5xx (`Retry-After` honoured), optional per-request
  authentication.
- Choosing a limiter (2026-10-17): a **published** limit gets a fixed `TokenBucket(0.9 x limit, 1)`
  (capacity 1 never bursts into a sliding window); e.g. `PolymarketReadClient` (Gamma 27, CLOB 90,
  Data API 18 req/s) and the Gamma lookups in `polymarket_extend.py`. An **unpublished** limit gets
  `AimdLimiter`, which finds it: the rate climbs about `increase` req/s per second of successes,
  halves on 429/503 at most once per `cooldown` (`Retry-After` holds every caller), remembers the
  throttled rate as `ceiling`, and above `near` x ceiling climbs `probe` times slower, so it settles
  just under the limit instead of cycling through 429s. `slow_factor` turns a latency spike into a
  throttle for servers that queue rather than refuse. `ReadOnlyClient` reports every answer to a
  limiter that has `feedback`. Users: the public Polygon RPC nodes in `polymarket_extend.py` tail
  (one per node) and the OddsBlaze harvest.
- Order placement is not in the core. The LIP pilot keeps its gated trading client
  (`src/live/lip_pilot/client.py`: three-gate `LiveGate`, one-shot mutation permit, post-only);
  only its signer, token bucket and default base URL now come from the core.
- Shims and adopters: `lip_pilot/auth.py` and `lip_pilot/ratelimit.py` (shims), the recorder's
  `ws_auth_headers` and URL constants, `kalshi_flb_api.KalshiReadOnly` (a `KalshiReadClient`
  configured with its old spacing, back-off and errors), `fin_B_common.KalshiAuth`/`KalshiAPI`,
  `latency_probe.kalshi_headers`, and the base-URL constants of `capfwd_data`,
  `market_feature_watch`, `perps_carry_paper` and the lip_pilot config. `latency_ws` and
  `parlay5_rfq_logger` already sign through the recorder's `ws_auth_headers`.
- Not adopted yet, on purpose: `capfwd_data.Api` (atlas forward test; it schedules the next
  slot from `Retry-After`, counts 429s, has an offline mode and a text mode) and
  `market_feature_watch.Api` (its tests pin its exact retry accounting: 200-only success, no sleep
  after the last attempt). Both take the base URL from the core. `scripts/research/tape_refresh.py`
  and `scripts/data/refresh_kalshi_markets.py` (background jobs were running them) are future
  adopters, as are the ~50 research scripts that still spell the URL out.
- Behaviour change: a path passed with a query string inside it (`get("/markets?x=1")`) is now
  signed without the query, as Kalshi requires. `KalshiReadOnly` used to sign it with the query,
  which Kalshi rejects.

**D6. One GET-only read loop for every venue (`predict_ngin.venues.http.ReadOnlyClient`).**
`KalshiReadClient` adds signing to it; `PolymarketReadClient` holds one per host.

**D7. One Polymarket read client (`predict_ngin.venues.polymarket`).**
- `endpoints`: `GAMMA_URL`, `CLOB_URL`, `DATA_API_URL`, defined once.
- `PolymarketReadClient`: Gamma markets/events (offset pagination), CLOB book, price history and
  markets (cursor pagination), Data API trades (offset pagination up to the 10,000 limit); one
  token bucket per host, one shared session, 404 returns None, GET only.
- Token parsing lives in `predict_ngin.markets.polymarket_tokens` (`parse_list_field`,
  `clob_token_ids`, `yes_no_token_ids`; YES = outcome index 0).
- The three extra `clobTokenIds` parsers now call `parse_list_field`:
  `src/trading/data_modules/fetcher.py` `_parse_token_list`,
  `scripts/data/collect_research_by_market_list.py` and
  `scripts/data/fetch_research_trades_and_prices.py` `_parse_clob_token_ids`.
  Behaviour change (a fix): on the doubled-quote export that every market file on disk uses
  (`[""""id"""", ...]`, 8,000 of 8,000 rows checked), the old parsers returned `[]`; the old
  fetcher parser also returned `[]` for Python-repr lists. They now return the token ids. On JSON
  input and missing values the results are unchanged (golden-tested).
- Deferred: moving `src/data_quality/polymarket_tokens.py` (`TokenMap`, `normalize_trades`, ...)
  into `predict_ngin.markets.polymarket_tokens` with a shim. It had uncommitted edits from another
  job (data/polymarket_clean/trades_ext). Until then `parse_list_field` exists in both places and
  a golden test keeps them identical. Future client adopters: `polyx_common.get_json` /
  `gamma_events`, `fetch_research_trades_and_prices`' request loops, the `src/trading` fetcher's
  CLOB calls, and `scripts/data/polymarket_extend.py` (a background job was running it).

**D8. Research helpers find the core by path.** The old `venv/` does not install `predict_ngin`.
A repointed research helper that did not already put the repo root on `sys.path` appends it
(append, not insert, so nothing is shadowed). `src/` shims need nothing: importing `src.*` already
requires the repo root on the path.

**D9. Series, ticker and settlement access (`predict_ngin.markets`).**
- `tickers`: `series_of` (text before the first dash, after `str()`), `series_column` (the same,
  vectorised, index kept) and `event_of_market` (the market ticker minus its last part).
- `domain`: four frozen value objects, `Series`, `Event`, `Market` and `Settlement`. They name
  things and hold no behaviour beyond building themselves from a ticker.
- `settlement`: `settled_frame` (the `settle_common.load_markets` convention: `close_ts` and
  `settle_ts` as the floor of the nanosecond timestamp in seconds, `win` = 1.0 for `yes`, only
  `yes`/`no` results kept), `read_settled_markets(path)`, `iter_settlements` (typed rows) and `win_of`.
- Shims and adopters (golden-tested, identical output): `kalshi_flb.series_from_event`,
  `capbook_common.series_of`, `lip_pilot.selection.series_of`, `market_feature_watch.series_of`
  (keeps its own `None` -> `""` mapping by passing `ticker or ""`), `lpland_landscape.series_of`
  (`series_column`) and `settle_common.load_markets`.
- Behaviour change: `lip_pilot.selection.series_of` used to raise on a non-string; it now returns
  the series of `str(x)`. Every caller passes a ticker string.
- Not repointed: the inline `split("-")[0]` / `rsplit("-", 1)[0]` expressions inside one-off
  research scripts (frozen with their results).

**D10. One Sharpe, PSR/DSR and bootstrap (`predict_ngin.evaluation.metrics`).**
- The arithmetic is the one of `kalshi_flb` (used through `audit_flb_common` by about 50 studies):
  `statistics.NormalDist`, the full Euler-Mascheroni constant, a 1e-9 floor on the PSR denominator,
  `max(n_obs - 1, 1)`. Functions: `sharpe`, `expected_max_sharpe`, `psr_z`,
  `probabilistic_sharpe`, `deflated_sharpe`, `bootstrap_totals`, `cluster_bootstrap_totals`,
  `cluster_bootstrap_ci`.
- **Annualisation is a parameter, never implied.** `periods_per_year` defaults to 365 (the
  calendar-day, zero-filled P&L series of the Kalshi studies); 252, 8760 or `None` (per
  observation) are passed explicitly. The studies' conventions differ and stay as they are (table
  below); none was unified.
- Shims and adopters (golden-tested, identical numbers): `kalshi_flb.sharpe_daily`,
  `deflated_sharpe` and `cluster_bootstrap_ci`; `audit_flb_common.metrics` (its Sharpe and its
  bootstrap CI); `capbook_forward._boot_ci`. Through `kalshi_flb`, `audit_flb_common.dsr`,
  `mkgap_common.dsr`, `tailcarry_sim.TC_dsr` and `kalshi_flb_report.dsr_table` now use the core too.

**D11. One order-book type (`predict_ngin.books`).**
- `book.OrderBook` is the recorder's L2 book (`src/recorder/book.py`), moved verbatim: price
  levels keyed by 6-dp price, snapshots, absolute and incremental deltas, version and validity
  flags, `top(n)`, best bid/ask. `src/recorder/book.py` is a shim re-exporting it, and the
  recorder's `to_yes_terms` comes from the core.
- Additions, not used by the recorder: `from_levels`, `levels(side)`, `mid`, `spread`,
  `is_crossed`, `walk(side, qty)` (depth walk), `depth(side, within)`, `no_side()`, and the free
  functions `complement` (YES/NO conversion), `to_yes_terms` (Kalshi bids-only to YES bids and
  asks) and `walk_levels`.
- **The recorder's Parquet output is byte-identical.** `test_golden_books.py` replays Kalshi WS
  sequences through the recorder's own `KalshiWsState` and `Sampler` with the frozen book and with
  the core book, and compares the rows and the zstd Parquet bytes: one synthetic sequence (6,000
  messages with sub-cent prices, fractional sizes, negative levels and sequence gaps) and, with the
  `data` marker, sequences rebuilt from recorded book rows (which the replay also reproduces).
- Adapters (`books.adapters`, duck-typed so the core imports no legacy module), each also
  reachable as a `to_order_book()` method on the legacy class: `from_kalshi_bids` (lip_pilot
  `Book`), `from_book_snapshot` (backtest_v3 `BookSnapshot`; sizes stay in USD) and
  `from_clob_snapshot` (the order router's `OrderBook`). The legacy classes are otherwise unchanged.
- Not adapted, on purpose: `src/trading/live/clob_streamer.BookSnapshot`,
  `src/trading/paper_trading_execution/clob_liquidity_manager.OrderBookSnapshot` and
  `src/trading/data_modules/tick_store.OrderBookStore` (legacy `src/trading`, quarantined in plan
  step 7), `scripts/data/fetch_pmxt_and_clob.OrderBookSnapshot` and
  `scripts/research/legal_venue_xvenue.Book` (frozen research scripts).

**D12. One one-pass runner for scheduled jobs (`predict_ngin.capture.runner`).**
- `run_one_pass(data_dir, body, *, stale_s=900, write_pid=True, alert=None)`: create the
  directory; `STOP` file -> exit 0 without work; exclusive `.lock` (a lock older than `stale_s`
  is taken over) -> exit 3 when held; the body returns the exit code (0 ok, 2 essential data
  unavailable); the lock is released even when the body raises; the optional `alert(code, msg)`
  hook runs for every non-zero result (1 for an exception, which is re-raised) and can never
  change the result. Pieces: `Lock`, `stop_requested`, `atomic_write_text`, `atomic_write_json`
  (`<file>.tmp` then `os.replace`; `json.dumps` keywords pass through, so formats stay
  byte-identical) and `status_fields` (`status.json` with `name`, `mode`, `ok`, `last_run` first).
- Adopters (golden-tested, identical files and exit codes): `scripts/live/perps_carry_paper.py`
  (its `Lock` is the core one; `_atomic_write` and both status documents go through the runner)
  and `scripts/research/perps_funding_collect.py` (an empty lock file, `write_pid=False`, and
  `last_ok.json`). `scripts/research/capbook_forward.py` now writes its daily state atomically
  (it used a plain `write_text`; the content is unchanged).
- Not adopted, on purpose, because the runner would change what they do:
  `aaa_source_collector`, `earlyinfo_collect`, `settlement_rules_watch` and `measles_collector`
  have no lock, STOP file or status file today (adding them changes their behaviour; they write
  append-only CSV, unique run directories or SQLite, so no state file needs an atomic write);
  `wxlive_obs_collector` is a daemon (a PID file with a process check, STOP polled every second),
  not a one-pass job. They can adopt it when their schedules are next touched.
- Atlas runs these with `python -I`, which does not put the script directory or the repo root on
  `sys.path`; each adopter appends the repo root itself before importing `predict_ngin` (D8).

**D13. Optional paper area (`predict_ngin.paper`, plan step 6).**
- Committed but optional: nobody needs it to use the core, and other users of the repo will not
  paper trade. Installed with `uv sync --extra paper` (the extra adds no dependencies yet). The
  atlas deployment that schedules the runners is local-only and is not referenced from here.
- `paper.runner` is the paper-runner base on top of `capture.runner` (D12), holding only what the
  runners share: `run_one_pass`, `paper_status` (`status.json` with `mode` "paper"), `write_json`
  (atomic, `indent=1, default=str`), `load_state`, `append_jsonl` (decision, event and snapshot
  journals) and the P&L-versus-benchmark helpers `accrue_benchmark` (simple rate, compounded per
  pass) and `excess_vs_benchmark`. Formats are those `perps_carry_paper` already wrote, so its
  files are unchanged.
- `paper.strategies` holds promoted, frozen strategy rules, so runners stop importing research
  scripts. A rule is promoted only when a golden test shows identical decisions against the
  research original (`tests/unit/test_golden_paper_strategies.py`); the original stays in
  `scripts/research`, unchanged, for reference.
  - `perps_carry`: rule R (`rule_r_signal`, `ann_funding`, `interval_s`) from
    `perps_carry_backtest`. Identical signals on a synthetic grid (zero runs, gaps, 8h and 24h
    spacing, unsorted input, parameter variants, prefix-by-prefix as the runner calls it) and,
    with the `data` marker, on every ticker of the saved funding history and the Hyperliquid 8h
    windows. `scripts/live/perps_carry_paper.py` imports it from the core and no longer puts
    `scripts/research` on `sys.path`; its journals, state, status and benchmark go through
    `paper.runner`. The backtest keeps its own copy.
  - `wxlive`: the pre-registered ASOS feed edge taker from `wxlive_paper_eval` (feed temperature,
    bracket upper bound, running-max trigger, signal window, book-row validity, ladder fill with
    the Kalshi taker fee). Identical on grids; the evaluator is not repointed (atlas runs it
    daily and nothing needs it to change), so the core copy is the reference for a future runner.
- Deferred, with reasons:
  - `capbook_forward` (`capbook_common`, `capfwd_data`, `capfwd_legs`): the decisions are several
    legs inside `capfwd_legs`, built on a numba engine (the `research` extra) and on the
    `capfwd_data` day cache and API. A faithful move is the whole module, and the golden check
    needs recorder data plus cached API days; not done in this step.
  - `lipscan_forward`: the rule is a frozen configuration (`RULES`: posture, size, pull, universe)
    passed to the `lipscan_eval.py` replay simulator in a subprocess, then scored by
    `lipscan_report`. There is no separable decision function; the simulator is research code.
  - `lipx_forward`: the same shape. Its own rule is the universe split `rule_of` plus capped
    quoting mechanics written inline over the outputs of `lipx_reward` and `lipx_recfills`.
- Entry points: the script paths stay as they are, because atlas runs them by path with
  `python -I` (D8, D12). No console scripts are declared, since the runners are scripts outside
  the package.

**D14. Legacy quarantine (plan step 7, no deletions).**
- Legacy packages: `src/trading`, `src/whale_strategy`, `src/backtest`, `src/research`,
  `src/collection`, `src/storage`, `src/analysis`, `src/validation`, `src/tools`, `src/taxonomy`
  and the module `src/config.py`. Each package has a `LEGACY.md` (what it was, its era, the audit
  that invalidated its results, what replaced it); `src/config.py` says so in its docstring. They
  are kept for reference and audit, and are not maintained.
- Rule: `predict_ngin` never imports a legacy package (`.importlinter`, contract
  `core-not-legacy`). New production code (recorder, `src/live`, collectors, paper runners) does
  not import them either; an AST import scan on 2026-10-15 found none, directly or transitively,
  from `predict_ngin`, `src/recorder`, `src/live`, `src/collectors`, the atlas jobs in
  `config/collectors.yaml`, or the current paper runners (`perps_carry_paper`, `capbook_forward`,
  `wxlive_paper_eval`, the `lip*` forward tests). The whale-era runners
  `scripts/live/{run_live_strategy,run_paper_trading,pipeline,watch_whale_trades}.py` import
  legacy packages and are legacy themselves. Research scripts and tests may still import them, so published studies reproduce; their tests
  keep running. No `DeprecationWarning` is emitted on import, because research scripts and tests
  import these packages routinely and the warning would only add noise.
- Current files inside legacy packages stay current: `src/trading/arbitrage/fees.py` (shim, D3),
  `src/trading/arbitrage/kalshi_tape.py` (tape reader used by research),
  `OrderBook.to_order_book()` in `src/trading/live/order_router.py` (D11) and the
  `predict_ngin` imports in `src/trading/data_modules/fetcher.py`.
- The Polymarket live order path is hard-disabled: `OrderRouter(dry_run=False)` raises
  `LiveTradingDisabledError`, and `scripts/live/run_live_strategy.py --live` exits with an
  error. Its code is kept, unreachable, for audit. This removes a footgun; it is not preparation
  for real trading.

**D15. Data lake: Parquet partitioned by month, queried through DuckDB views (2026-10-17).**
- Storage stays Parquet: `<data>/lake/<venue>/<dataset>/year=YYYY/month=MM/part-NN.parquet`, zstd,
  1M-row row groups, at most 40M rows per file. Inside each file rows are sorted by
  category → event → market → time, so filters on those skip row groups through min/max
  statistics. Category is a sorted column, not a folder: labels change, sports is ~60% of volume,
  and category × month folders would be tiny. No per-market folders (millions of small files).
- DuckDB is the query engine, not the store. A `.duckdb` file has a single writer while fetchers,
  the atlas sync and studies touch the data concurrently, and duckdb has no Python 3.15 wheel on
  atlas. `predict_ngin.lake.connect()` registers views at run time; `predict-ngin data catalog`
  writes the same views to `catalog.duckdb` for external tools.
- Readers in `tapes` serve the lake only when its state file says the build is complete, else the
  original files, so callers never change. Builders read the originals explicitly
  (`source="legacy"`), never the lake.
- Every partition is checked before it replaces the old one: row count and value sums reconcile
  with the source, no row falls outside its month, and the dataset's contract
  (`predict_ngin.lake.contracts`) passes. Builds are incremental and locked.
- One way to open DuckDB (`predict_ngin.shared.duck.connect`): memory, threads and a cap on temp
  spill, all overridable by environment. Studies that open DuckDB themselves are not rewritten.
- Duplicated datasets were deleted (docs/DATA_LAKE.md, "Removed datasets"). Old code keeps
  working: `TradeStore` and the arbitrage backtest serve the deleted monthly Polymarket extract
  from `trades.parquet` (`src/data_quality/legacy_pm_trades.py`), so their scripts are unchanged.
  The 25 research scripts that read `data/kalshi/trades_fixed` directly were switched to
  `tapes.kalshi.tape_sql`: alone, that folder ends on 2026-01-27. Their windows and cut-offs are
  unchanged. Everything else in old research code is left as it was.

**D16. Only rejected strategies are published (2026-10-17).**
- A study family is open while any of its strategies is unproven, capacity-capped, a candidate or live. Open
  families (scripts, docs, ledger entries, tests, promoted paper rules) stay on the maintainer's disk and are not
  committed; the generated catalog, ledger, trial log and results database are private too.
  `docs/strategies/REJECTED.md` and the code of fully rejected families are public.
- `scripts/research/build_private_split.py` computes the private list from the catalog and writes it to a
  generated block in `.gitignore`; `--apply` untracks newly private files. A family becomes public when its last
  open strategy is rejected: re-run the script.
- A public file that imports private code becomes private too, so the public repo never has a dangling import.
  Infrastructure (`predict_ngin`, `src/`, `scripts/data/`, the core golden tests) may only import private modules
  optionally: the golden tests use `pytest.importorskip` and skip those comparisons in a public clone.
- History was not rewritten: files published before this decision remain in earlier commits.

## Deliberate variants (fees)

These fee functions were left unchanged on purpose. Each row says how it differs from the
canonical schedule. The "Reproduce with" column, where filled, gives the canonical call that
returns identical numbers; the golden tests check those marked (G).

| Helper | Differs from the schedule | Reproduce with |
|---|---|---|
| `src/strategies_legal/kalshi_flb.py` `taker_fee`, `maker_fee` (now a shim) | multiplier outside the round-up | `kalshi_order_fee(..., multiplier_in_ceil=False)` (G) |
| `scripts/research/fin_D_common.py` `ceil_cent`, `taker_fee`, `maker_fee` | copy of kalshi_flb: multiplier outside the round-up | as above (G) |
| `scripts/research/mkgap_common.py` `maker/taker_fee_per_contract` | multiplier outside; per-contract; count floored at 1 | `kalshi_order_fee(n, p, multiplier=m, multiplier_in_ceil=False) / n` (G) |
| `scripts/research/tailcarry_common.py` `maker/taker_fee_total` | multiplier outside; zero for n <= 0 | `multiplier_in_ceil=False` |
| `scripts/research/mxarb_common.py` `taker_fee_c` | multiplier outside; per-contract; count floored at 1 | `multiplier_in_ceil=False` |
| `scripts/research/mrev_common.py` `taker_fee_c` | returns cents, not dollars | `100 * kalshi_order_fee(n, p)` |
| `src/trading/arbitrage/fees.py` `kalshi_fee`, `FeeModel` (now a shim of `costs.models`) | noise guard `ceil(100x - 1e-9)`; price clipped to [0, 1] | `costs.models.kalshi_fee` (G) |
| `scripts/research/fin_A_common.py` `kalshi_fee_fill` | noise guard `eps=1e-9`; no multiplier | `kalshi_order_fee(c, p, rate=coef, eps=1e-9)` |
| `scripts/research/fin_A_common.py` `kalshi_taker_fee_pc` | unrounded per-contract fee (screening) | `rounding="exact"` |
| `scripts/research/fin_B_common.py` `kalshi_taker_fee_per_contract` | per-contract fee of a 100-lot; `eps=1e-9` | `kalshi_order_fee(100, p, eps=1e-9) / 100` |
| `scripts/research/mhaz_common.py` `taker_fee_c` | per-contract fee of a 100-lot; `eps=1e-9` | as above |
| `scripts/research/alpha_decay_common.py` `kalshi_fee_pc`, `FeeSpec` | per-contract of a 100-lot; `eps=1e-9`; price clipped; `k_coef=0` means gross | `kalshi_order_fee(c, p, rate=coef, eps=1e-9) / c` |
| `scripts/research/sharp_common.py` `maker_fee` | unrounded maker fee, charged on every series and date | `kalshi_order_fee(c, p, maker=True, multiplier=m, rounding="exact")` |
| `scripts/research/klip_economics.py`, `klip_fill_sim.py` `maker_fee` | unrounded per-contract maker fee | `rounding="exact"` |
| `scripts/research/audit_flb_r2.py` `fee_pc` | unrounded per-contract maker fee, multiplier outside | - |
| `scripts/research/legal_venue_kalshi_mm.py` `fee_fn` | unrounded by default; `eps=1e-9` when rounded | - |
| `scripts/research/negrisk_common.py` `fee_per_share` | Polymarket fee with an exponent, zero outside (0, 1) | - |
| `src/whale_strategy/execution_model.py` `DEFAULT_FEE_RATES` | Polymarket rate table keyed by title-case category; no crypto key (crypto pays the 0.05 default, not 0.07) | `polymarket_fee_per_share` with that table |
| `src/backtest_v3/execution/book_executor.py` `_fee_bps` | fee in bps of notional, `rate x (1 - p)`, plus a flat config add-on | - |
| `src/backtest_v3/backtest/engine.py` `_fee_rate` | own category table and default | - |
| `scripts/research/capbook_forward.py` `pit_fees` | adds event-level overrides and a live fee-change fetch on top of `load_schedule()` | future adopter of `kalshi_schedule` |
| One-off study scripts (about 40: `asr_*`, `cult_netflix_fp`, `e1cnt_backtest`, `elex26_hist`, `fin_B_k15_report`, `fin_B_kalshi_tape_analysis`, `fin_B_kd_taker`, `fin_C_*`, `fin_E_*`, `inplay_race`, `lipnext_net`, `micro_h6_ladders`, `nowc_mention_eval`, `parlay5_analyze`, `postevent_convergence`, `predexon_book_arb`, `probalytics_tick_arb_analysis`, `release_*`, `settlement_rules_candidates`, `twc_hourly*`, `wx_converge`, `audit_mention_sim`, `legal_venue_xvenue`) | each study's own inline fee, frozen with its results | - |

Repointed at `predict_ngin.costs` (golden-tested, identical numbers): `sharp_common.taker_fee`,
`trumpx_common.taker_fee` and `maker_fee`, `capbook_forward.fee_c` and `kalshi_fee`,
`wxlive_paper_eval.fee`, `polyx_common.pm_taker_fee`, `alpha_decay_common.pm_fee_pc`,
`whale_strategy.execution_model.taker_fee_per_share`, and the `perps_carry_paper` `TAKER`/`MAKER`
constants. `feeaudit_common` (schedule, `pit`, `fee`) and `kalshi_flb` (fees) are shims.

## Deliberate variants (metrics)

Left unchanged on purpose: each gives slightly different numbers from the canonical metrics, and
changing it would change published results.

| Helper | Annualisation | Differs from `predict_ngin.evaluation` |
|---|---|---|
| `src/backtest_v3/backtest/engine.py` `_sharpe` | 252 | NaN (not 0) for one observation; NumPy float |
| `src/backtest_v3/backtest/sensitivity.py` `deflated_sharpe`, `_sr_cutoff` | optional `periods_per_year` | Acklam inverse-normal approximation and its own normal CDF; probabilities clamped to [1e-9, 1 - 1e-9]; zeros for `n_returns <= 1`; key `cutoff_sr` is per observation |
| `src/whale_strategy/retest_stats.py` `deflated_sharpe_ratio` | per period | SR, skew and kurtosis computed from the returns (SciPy); default trial variance `1/(n-1)`; denominator floor 1e-12 |
| `src/trading/attribution.py` `calculate_sharpe`, `src/trading/engine.py` `sharpe_ratio`, `scripts/backtest/tick_based_backtest.py` `_compute_sharpe` | 252 | legacy engines; per-trade or per-day returns, own zero guards |
| `scripts/research/fin_A_common.py` `dsr`, `fin_D_common.py` `deflated_sharpe` | per observation | SciPy normal; Euler constant truncated to 0.5772156649; floor 1e-12 (fin_D: NaN for fewer than 2 trials or 3 observations) |
| `scripts/research/fin_B_common.py` `deflated_sharpe` | per observation | as fin_A; trials floored at 2; trial variance defaults to `1/n`; NaN below 3 observations |
| `scripts/research/uma_common.py` `deflated_sharpe`, `sharpe_daily` | 365 | as fin_A with variance `1/n`; Sharpe on returns on capital, zero-padded to the day span, NaN for zero variance |
| `scripts/research/polyx_elon_eval.py` `dsr` | per event | SciPy normal, `np.euler_gamma`; no denominator floor |
| `scripts/research/metals15_capacity.py` `dsr` | per fill | `erf`-based normal CDF; truncated Euler constant; variance `1/n` |
| `scripts/research/lipscan_report.py` `hourly_sharpe` | 8760 | hourly return on capital; NaN below 3 hours or zero variance |
| Inline bootstraps (about 25: `asr_eval.boot_ci`, `polyx_elon_eval.boot_ci`, `uma_common.cluster_boot`, `wxlive_paper_eval.boot_ci`, `lipscan_*`, `lipx_net.boot`, `mhaz_eval`/`mrule_eval.ev_boot`, `fin_C_*`/`fin_E_*`, `elex26_hist`, `klip_economics`, `ment5_*`, `metals15_h1`, `postevent_convergence`, `sharp_calib`, `snap_rt_*`, `trumpx_flow`, `db_*_eval`, `legal_venue_kalshi_mm_report`, `lipnext_net`, `whale retest_stats.bootstrap_mean_diff_ci`) | - | each resamples its own statistic (mean, ratio, Brier difference) with its own resampler (`rng.choice` or `integers`), seed and draw count; frozen with its results |

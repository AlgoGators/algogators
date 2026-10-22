# Strategy catalog

Last revised: 2026-10-17

Every strategy idea tested in this repo, grouped by outcome, with links to its report, its code, its trials and
its ledger entry. This is an index over files that stay where they were written: nothing is moved or deleted.

| page | what is in it |
|---|---|
| [PROMISING.md](PROMISING.md) | `candidate`, `live` and `capacity_capped`: real, measured edges |
| [NEXT_STEPS.md](NEXT_STEPS.md) | `unproven` ideas with the concrete next action, plus open items from the backlog |
| [REJECTED.md](REJECTED.md) | `rejected` and `invalid`, each with its reason and "re-test only if"; plus docs whose numbers must not be trusted |
| [CODE_ARCHIVE.md](CODE_ARCHIVE.md) | every family's scripts, docs, tests and trials; shared infrastructure; unclaimed scripts and docs |
| [families.yaml](families.yaml) | the editable map from ledger entries to code, docs and trials |
| [TEMPLATE.md](TEMPLATE.md) | skeleton for a study report |
| `catalog.json` | the same catalog, machine-readable |

<!-- BEGIN GENERATED: build_strategy_catalog.py -->

Current catalog: **207 strategies** in **65 families**.

| page | statuses | strategies |
|---|---|---|
| [PROMISING.md](PROMISING.md) | candidate, live, capacity_capped | 16 |
| [NEXT_STEPS.md](NEXT_STEPS.md) | unproven + open backlog | 59 |
| [REJECTED.md](REJECTED.md) | rejected, invalid + untrusted docs | 132 |
| [CODE_ARCHIVE.md](CODE_ARCHIVE.md) | every family's code, docs, tests, trials | 65 families |

Unseeded (in YAML, not yet in `results.db`): none.

Families auto-mapped from a ledger YAML stem (not yet listed in `families.yaml`): `mmq`, `whale_pit`.

Families with no implementing code found: none.

<!-- END GENERATED -->

## Where the truth lives

- **Status per strategy:** `scripts/research/ledger_entries/*.yaml` (one file per workstream) plus the
  2026-10-12 audit verdicts in `scripts/research/seed_ledger.py` (`ENTRIES`). `seed_ledger.py` writes them to
  `backtests/results.db` (table `strategy_ledger`) and exports [../RESEARCH_LEDGER.md](../RESEARCH_LEDGER.md).
- **This catalog** is generated from those sources by `scripts/research/build_strategy_catalog.py`. It reads the DB
  read-only. YAML entries not yet in the DB are shown with the tag `unseeded`. When a later YAML file repeats a
  `strategy_id` (the `zz_*.yaml` audit files do this on purpose), the later file wins, as in `seed_ledger.py`.
- **Links** come from the ledger `evidence` field (any `docs/...md`, `scripts/...py` or `src/...py` path in it) and
  from the family globs in [families.yaml](families.yaml). Fix a wrong link by editing `families.yaml`, never the
  generated pages.
- **Trials** are counted from `research/trials.csv` by family prefix (`strategy` column, `-` and `_` equal).

Regenerate after any ledger, doc or code change:

```
uv run python scripts/research/build_strategy_catalog.py          # write
uv run python scripts/research/build_strategy_catalog.py --check  # exit 1 if stale
```

## How to add a strategy

1. **Check it is new.** Search [REJECTED.md](REJECTED.md) and [../RESEARCH_LEDGER.md](../RESEARCH_LEDGER.md). A
   rejected idea is retested only if its *Re-test only if* condition has happened.
2. **Brief.** Put the idea (source, claimed edge, who loses) in `docs/research_briefs/` and add a `- [ ]` line to
   `docs/research_briefs/BACKLOG.md`. Open backlog lines show up in [NEXT_STEPS.md](NEXT_STEPS.md).
3. **Scaffold.** Pick a short unused prefix and run
   `uv run python scripts/research/new_strategy.py <prefix> "<title>" --venue kalshi`.
   It creates `scripts/research/ledger_entries/<prefix>.yaml` (an `unproven` stub),
   `docs/<PREFIX>_<TITLE>.md` (from [TEMPLATE.md](TEMPLATE.md)) and `scripts/research/<prefix>_study.py`
   (imports for the building blocks below, plus `log_trial()`). It refuses to overwrite anything.
   **Docs: topic names, not dates.** Name docs by topic, never by date (`docs/MMQ_QUEUE_AWARE_MARKET_MAKING.md`, not
   `docs/MMQ_QUEUE_AWARE_MARKET_MAKING_2026-10-17.md`). Keep a `Last revised: YYYY-MM-DD` line under the title and
   update it whenever the content changes. Update the existing topic doc in place instead of creating a new dated
   copy; put dated facts inside the doc (a dated results table, or a History section for superseded versions).
4. **Pre-register.** Fill in the doc's pre-registration section (universe, split, rule, fill model, fees, sizing,
   kill rule, trial budget) before computing any test-period number. Optionally log a `PREREG` row in
   `research/trials.csv`.
5. **Implement** with the shared building blocks. Name every file `scripts/research/<prefix>_*.py` so the catalog
   finds it with no extra mapping. Put data under `data/vendor/<prefix>/` and tests in
   `tests/unit/test_<prefix>*.py`.
6. **Log trials.** One row per variant in `research/trials.csv`, including failures (`log_trial()` in the stub).
7. **Verdict.** Set `status`, `verdict`, `evidence`, `key_numbers`, `gaps`, `next_tests` and `retest_if` in the
   ledger YAML (one entry per distinct strategy; see `mkgap.yaml` for a good example). Seed it with
   `uv run python scripts/research/seed_ledger.py` (that writes `results.db`; skip it if another agent
   owns the DB and leave the entry `unseeded`).
8. **Regenerate** the catalog (command above). If the code uses a different prefix, or the study links to docs
   outside `docs/<PREFIX>_*.md`, add a family to [families.yaml](families.yaml).

## Standard building blocks

Reuse these instead of writing a new framework. The core lives in `predict_ngin` (installed by `uv sync`; context map
and rules in [../ARCHITECTURE.md](../ARCHITECTURE.md)). The older helpers are importable after
`sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts/research"))`.

| need | import | notes |
|---|---|---|
| Kalshi fees, per order | `from predict_ngin.costs import kalshi_order_fee, kalshi_fill_fee, maker_fee_applies` | taker 0.07, maker 0.0175, rounded up to the cent per order; `multiplier=`, `maker=` |
| Point-in-time Kalshi fee schedule | `from predict_ngin.costs.kalshi_schedule import load_schedule, pit` | series fee type and multiplier at each timestamp |
| Interest (APY) on idle Kalshi balance | `import feeaudit_common as FEES` | `FEES.apy_income()`; not yet in the core |
| Polymarket and perps fees | `from predict_ngin.costs import polymarket_fee, polymarket_fee_per_share, perps_fee` | |
| Sharpe, PSR / deflated Sharpe, bootstrap | `from predict_ngin.evaluation.metrics import sharpe, deflated_sharpe, cluster_bootstrap_ci` | pass `periods_per_year` explicitly; count every trial for the DSR |
| Series, events, settlements | `from predict_ngin.markets import series_of, series_column, event_of_market, settled_frame, read_settled_markets` | `settled_frame` keeps only `yes`/`no` results, `win` = 1.0 for `yes` |
| Kalshi API (GET-only) | `from predict_ngin.venues.kalshi import KalshiReadClient` | rate-limited, retries 429/5xx; public reads need no key; `signer=KalshiSigner(...)`, `auth=True` to sign |
| Polymarket Gamma / CLOB / Data API (GET-only) | `from predict_ngin.venues.polymarket import PolymarketReadClient` | YES/NO token ids: `predict_ngin.markets.polymarket_tokens.yes_no_token_ids` |
| Order books | `from predict_ngin.books import OrderBook, walk_levels, to_yes_terms` | depth walk, YES/NO conversion |
| Kalshi trade tape (fast, memory-mapped) | `from src.data.tapes import open_tape` | `open_tape("kalshi_base")` or `"ment5"`; returns a `TapeStore` (`src/data/tape_store.py`) with `chunks()`, `codes_where()`, `duckdb()` |
| Any dataset by name | `from predict_ngin import lake`; `con = lake.connect()` | DuckDB with standard limits and views `kalshi_trades`, `kalshi_markets`, `polymarket_maker_fills`, `polymarket_markets`, books; see docs/DATA_LAKE.md |
| SQL over the full Kalshi tape | `from predict_ngin.tapes.kalshi import tape_sql, read_tape, tape_end_ts` | DuckDB SQL string; pass `start_ts`/`end_ts`; `count_fp` = exact contracts |
| Many strategies in one pass | `from src.data.runner import run_strategies, ram_sized_workers` | a strategy is any object with `name`, `on_chunk(chunk, ctx)` and `finalize(partials)`; `ctx.memo(key, fn)` shares work. Example: `scripts/research/mkgap_store.py`. Design: [../ENGOPT_BACKTEST_ENGINE.md](../ENGOPT_BACKTEST_ENGINE.md) |
| Causal maker fills (Fa / Fb / Fc) | `from src.data.fills import causal_fills, MODELS` | numba (`--extra research`); pure-Python reference is `audit_flb_common.causal_opps` |
| Portfolio simulation | `import audit_flb_common as AF` | `AF.simulate`; its metrics now come from `predict_ngin.evaluation` |
| Polymarket YES/NO-normalised fills | `from predict_ngin.tapes.polymarket import clean_trades_sql` (or the `polymarket_maker_fills` view) | never mix YES and NO token prints; `maker_rows_only=True` for counts and volume |
| Ledger records | `from src.backtest_v3.results_db import LedgerEntry, ResultsDB, LEDGER_STATUSES` | prefer YAML in `ledger_entries/` |
| Live order books | the recorder (`docs/RECORDER.md`) | read-only for research |

`feeaudit_common`, `kalshi_flb` and the fee and metric functions of `audit_flb_common` are shims over the core; old
studies that import them still give identical numbers. New code imports the core directly.

Before reporting any result, run the `backtest-audit` checks (look-ahead, point-in-time universe, causal fills,
real fees, trial count).

## Code archive (old studies stay in place)

Past studies are never deleted or moved, so any idea can be read back exactly as it was implemented.
[CODE_ARCHIVE.md](CODE_ARCHIVE.md) lists every family's scripts, docs, tests and trials, the shared infrastructure,
and any script or doc no family claims yet. Pre-audit engines (`src/trading/strategies/`, `src/backtest_v3/`,
`scripts/backtest/`) are kept too, but their reported numbers are artifacts: see the untrusted documents in
[REJECTED.md](REJECTED.md). To rerun an old study, check out the commit named in its doc or in `research/trials.csv`
(`git_commit`), since shared helpers may have changed since.

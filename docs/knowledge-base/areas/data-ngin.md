# data-ngin

**LAST UPDATED:** 2026-11-02 · **Commit:** `8c534c1b` (main, the code this page was checked against)

Airflow DAGs that pull daily bars from the vendors into PostgreSQL/TimescaleDB, plus the readiness
row the engine waits on. The old `AlgoGators/data-ngin` repository is archived; its issues moved here.

## Who reads its output

| Reader | What | Source |
|---|---|---|
| engine-rpc catch-up scheduler | `ops.data_ready`: dataset `futures_ohlcv_1d`, plus `futures_contract_ohlcv_1d` for a book with `per_contract_rolls` on. It builds the next trading day's books once a usable row lands, and rebuilds on a newer `finished_at` | `services/trade-ngin/services/rpc/algogators_rpc/services/desk/catchup.py` |
| the C++ engine | `futures_data.ohlcv_1d`, `ohlcv_1d_raw` (the contract's `instrument_id`), `contract_ohlcv_1d`, `equities_data.ohlcv_1d` | `src/data/postgres_database.cpp`, `src/data/contract_bars.cpp` under `services/trade-ngin/` |

Which table to read, and which never to, is set by `services/trade-ngin/docs/DATA_SOURCES_OF_TRUTH.md`.

## Layout

| Path | What |
|---|---|
| `dags/` | the three DAGs; baked into the Airflow image, not part of the Python package |
| `src/data_ngin/application/` | `close_pipeline.py` (the daily futures pull), `trading_calendar.py`, `pipeline_tasks.py` (what the DAG tasks call), `orchestrator.py` (the older date-range pipeline) |
| `src/data_ngin/infrastructure/` | fetchers (Databento, Tiingo), cleaners, `repository/` (OHLCV upserts, readiness), CSV loader |
| `src/data_ngin/domain/` | contract filter (outrights only, no spreads) and contract definitions (expirations) |
| `src/data_ngin/config/*.yaml` | one config per pipeline: fetcher, cleaner, database, schema, tables |
| `src/data_ngin/contracts/*.csv` | the symbol lists each pipeline loads |
| `src/data_ngin/ops/` | DAG-failure notifier, readiness failure callback, freshness check, contract verification |
| `src/data_ngin/api/` | a FastAPI/GraphQL read API; the local `docker-compose.yml` runs it, the production compose does not |
| `scripts/` | one-off loaders: historical per-contract bars, contract backfill, expirations, acceptance checks |
| `deploy/` | `deploy.sh`, `docker-compose.prod.yml`, host cron jobs, the deploy README |
| `platform/db/` | shared `DatabaseConfig` (DB_* env) used by data-ngin; migration `002_contract_ohlcv_1d.sql` |

## The DAGs

All times New York (`America/New_York` in the DAG files).

| DAG | Vendor | Schedule | Pulls | Writes |
|---|---|---|---|---|
| `data_pipeline_dag` | Databento GLBX.MDP3, ohlcv-1d, volume roll, front contract (`.v.0`) | none: manual only since #185 | `contract_valid.csv` | `algo_data`: `futures_data.ohlcv_1d`, `ohlcv_1d_raw` |
| `new_data_pipeline_dag` | Databento, same | 04:30 every day | `new_full_contracts.csv`; then the per-contract pull for 40 roots | `new_algo_data`: `futures_data.ohlcv_1d`, `ohlcv_1d_raw`, `contract_ohlcv_1d`; `ops.data_ready` |
| `tiingo_data_dag` | Tiingo equities | 07:15 weekdays | `contract_tiingo.csv` | `new_algo_data`: `equities_data.ohlcv_1d`, `ohlcv_1d_raw` |

No EODHD pipeline exists in this code. Sources: the DAG files, `src/data_ngin/config/`.

**The futures run** (`application/close_pipeline.py`, README "Futures schedule", #33):

- It pulls bar date D, the UTC day that ended before the run (yesterday's New York date). The
  vendor's historical data trails real time by about 8 hours, so bar D appears around 08:00 UTC.
- Per contract, the exchange calendar decides whether it traded on D. No contract traded (a
  Saturday, Christmas): the run does nothing. CME crypto futures count every day from 2026-06-13.
- It waits up to `DATABENTO_AVAILABILITY_WAIT_MINUTES` (default 60) for the vendor to publish D,
  then re-pulls every expected bar for D and the 5 days before, with `ON CONFLICT DO UPDATE`.
- Retries: 3, 15 minutes apart; a run with bars still missing retries while retries remain.
- A manual run can pin the date: `airflow dags trigger new_data_pipeline_dag --conf '{"bar_date": "YYYY-MM-DD"}'`.

**Readiness, `ops.data_ready`** (`infrastructure/repository/readiness_repository.py`): one row per
`(dataset, bar_date)` with `expected`, `loaded`, `missing`, `status` (`complete`, `partial`,
`failed`), `run_id`, `finished_at`. Only `new_data_pipeline_dag` writes it: `futures_ohlcv_1d`
after the continuous pull, `futures_contract_ohlcv_1d` after the per-contract pull. A re-run
overwrites the row. A run that fails for good writes `failed` from its failure callback, only for
the pull that failed (#82). Nothing is written for a day no contract traded. The table is created
by `services/trade-ngin/migrations/028_ops_data_ready.sql`, not here.

**Failure signals.** Every DAG's `on_failure_callback` files or updates a `dag-failure` GitHub
issue (`ops/dag_failure_notifier.py`); there is no SMTP on the box. A host cron at 13:00 UTC runs
`ops/check_data_freshness.py` against every packaged config's table and files or updates the
`[data-freshness]` issue (#22) when one is older than 96 hours (`deploy/cron/`).

## Run and test locally

From the repository root, after `uv sync --all-packages --all-groups`:

| Command | What |
|---|---|
| `just test services/data-ngin` | unit tests with coverage, `-m "not integration"` (`services/data-ngin/justfile`) |
| `just lint services/data-ngin`, `just typecheck services/data-ngin` | ruff check and format, type check (root `justfile`) |
| `uv run pytest services/data-ngin -m integration` | needs a real Postgres/TimescaleDB and vendor keys |
| `docker compose up --build` in `services/data-ngin/` | a local Airflow stack; DB and vendor settings from a `.env` (see the service README) |

CI: `svc-data-ngin.quality.yml` runs lint, typecheck and tests on Python 3.11 for a PR or a push to
`main` that touches `services/data-ngin/`, `platform/db/`, `uv.lock` or the root `pyproject.toml`;
coverage floor 80%.

## Deploy

Push to `prod` (a fast-forward of `main`; shared with the engine and AlgoLens deploys).
`svc-data-ngin.deploy.yml` runs only when `services/data-ngin/**` or `platform/db/**` changed
(#62). It runs the quality gate (coverage floor 70%), builds `Dockerfile.airflow` into
`ghcr.io/algogators/data-ngin-airflow`, parses the three DAGs and `webserver_config.py` inside that
image, then SSHes to the production box and runs `deploy/deploy.sh <digest>`. deploy.sh waits up
to 45 minutes for running DAG runs, restarts the stack from `docker-compose.prod.yml` (Airflow
3.3.2: api-server, scheduler, dag-processor), checks health and DAG registration, and installs
`deploy/cron/` as the host's cron file. The box never builds; it pulls by digest (#16).

Secrets live in an env file on the box; `deploy/README.md` names the file and the keys, never the
values. Rollback: `deploy.sh` with the previous image ref the box records, or re-run an earlier
deploy run (`deploy/README.md`). Airflow login is GitHub through Dex, with GitHub teams mapped to
Airflow roles (data-ngin#124). `svc-data-ngin.publish.yml` is a separate, tag-triggered
(`data-ngin/v*`) workflow for the `algogators/data-ngin` image; no such tag exists yet.

## Decisions

- **04:30 ET pull of the previous UTC day, not after the close** (#33, 2026-10-12). It first ran
  at 20:00 ET; the vendor's historical plan lags about 8 hours, so bar D is not there until D+1.
- **Re-pull D-5 to D every run, upsert** (#33). The old pull started at the table's newest row + 1
  and never re-fetched a missed day (#91); a provisional bar is now overwritten by the final one.
- **The readiness table belongs to trade-ngin** (migration 028); data-ngin only upserts, and a
  failed run still writes `failed` so the engine is not left waiting (#33, #82).
- **Expirations come from the vendor's definitions**, matched on `(instrument_id, raw_symbol)`;
  a calendar guess is never written (#78).
- **E-mini symbols are fetched and stored as their micros** (`symbol_remap` in the configs,
  `6d80b05e`).
- **A failed pipeline task fails the DAG run.** A trailing always-run task once turned failures
  green (#18); failures go to GitHub issues, staleness to the freshness cron.
- **Airflow is not a workspace dependency.** Its pins conflict with the workspace, so only the
  image installs it, and the deploy's DAG parse is the only place Airflow 3 imports are tested
  (`pyproject.toml`, `svc-data-ngin.deploy.yml`). Configs' `end_date` is inclusive; the fetcher
  adds a day for Databento's exclusive `end` (data-ngin#34).

## Gotchas

- **Two databases.** Production runs on `new_algo_data`, which the scheduled pipelines write. `algo_data`
  gets nothing unless `data_pipeline_dag` is run by hand, and is retired by #202.
- **The service README is partly stale**: its project tree and Poetry steps predate the monorepo.
  Use uv and the paths above.
- **Keep DAG files light.** The dag-processor re-parses them constantly; heavy imports at module
  level once timed out the DagBag on a small box (data-ngin#31). Import inside the task.
- **`time_range` in a config is ignored by the DAGs.** It drives only the orchestrator. A fixed
  range left in place re-pulls the same window every day (comment in `config.yaml`).
- **`DB_USER` needs INSERT and UPDATE on `ops.data_ready`**; a new role must get them (028 header).
- **The per-contract pull must cover ES, NQ, YM and RTY**: the morning rule counts them as roots,
  so a stop there holds the run (#91).
- **The `equities_data` indexes are trade-ngin's** (its migration 003). Recreating those tables
  drops them silently (`DATA_SOURCES_OF_TRUTH.md`).
- **`futures_data.ohlcv_1d` has held duplicate `(symbol, time)` rows** from past re-loads; the
  engine keeps one per pair (`DATA_SOURCES_OF_TRUTH.md` section 4.3). Do not read the dead
  `new_data_ohlcv_1d*` or `sharadar_ohlcv_1d` tables (#31).
- **Deploy outside the ingestion window**: 08:30 to 12:30 UTC per `deploy/README.md`, and the
  repository-wide 09:00 to 10:15 New York rule. A missing `GITHUB_TOKEN` makes the failure
  notifier log and skip, not fail.

## Open work

- #23 What the engine needs from data-ngin and the database (the parent issue)
- #29 Pipeline health: no silent failures, a run status and freshness view, Grafana alerts
- #22 The automated `[data-freshness]` issue
- #185 Production runs move to `new_algo_data`
- #91 Follow-ups after the continuous fill: acceptance check, script gaps, metadata cells, data asks
- #36 Futures history: missing and wrong bars still to fill
- #26 Equities daily bars: universe, adjustment and gaps
- #27 Corporate actions: a live deal-terms source; #28 Macro data: deploy and keep current
- #30 Trading calendars published as data (#146 is the engine half); #31 drop unused tables
- #32 Options data: Databento history, IvyDB bridge
- #162 Contract metadata cells with stray characters; #64, #67 CSV and close-column checks
- #164, #165 Strategies declare data needs; NATS JetStream instead of polling `ops.data_ready`

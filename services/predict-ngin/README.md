# predict-ngin

Last revised: 2026-10-18

A research incubator for prediction-market strategies on Kalshi and Polymarket. It holds the data
tools, the cost and evaluation code, about 550 research scripts, and a ledger of every strategy
tested, with its verdict.

The repository is for research. It reads public market data and runs backtests and studies. It does
not trade: **live trading is not supported**, and the old Polymarket order path is hard-disabled
(see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), D14).

New here? [QUICK_START.md](QUICK_START.md) goes from a fresh clone to queryable Kalshi and Polymarket
data, step by step.

## Install

You need [uv](https://docs.astral.sh/uv/) and Python 3.11 or later. CI tests 3.11.
predict-ngin is a member of the [AlgoGators monorepo](../../README.md)'s uv workspace: the
environment is `.venv` at the monorepo root, and `uv.lock` there pins every dependency. Run the
commands below from this folder (`services/predict-ngin`).

```bash
uv sync --extra research --extra dev   # what most people want: research libraries + pytest
uv sync --all-extras                   # everything
```

The extras: `research` (plotting, statistics, numba, polars: most research scripts need these),
`dev` (pytest, ruff, import-linter, pre-commit), `viz` (the `predict-ngin viz` explorer), `paper`
(the optional paper runners; no extra libraries yet). Plain `uv sync` installs the core package only.
Each `uv sync` installs exactly the extras it names and removes the others, so name every extra
you want in one command.

Run everything through `uv run`, which uses the project's `.venv`. A few scripts need packages
the project does not declare (for example `databento`); add them per command with
`uv run --with <package> ...`.

`requirements.txt` is a pip-compatible export of the lock (core and research) for those who do not
use uv.

## Configuration

```bash
cp .env.example .env             # then fill in only the keys you need; .env is never committed
```

`.env.example` lists every variable. Research needs no keys: the data refreshes below use public,
unauthenticated endpoints. A Kalshi API key (`KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`) is only
for authenticated reads.

**Data root.** `PREDICT_NGIN_DATA` names the data directory; it defaults to `./data` in the
checkout. `predict_ngin`, the tape store (`src/data/tapes.py`), the download and refresh scripts below
and the test suite honour it. Many research scripts still read `data/` inside the checkout, so if your
data lives elsewhere, also make `data` a symlink (on Windows, a junction) to it.

```bash
export PREDICT_NGIN_DATA=/path/to/data       # PowerShell: $env:PREDICT_NGIN_DATA = 'D:\predict-data'
```

**Optional cold storage.** By default all data stays in the data directory. If you have a slower or
larger second drive, name it in `PREDICT_NGIN_COLD` or in `config/storage.local.yaml` (gitignored; copy
`config/storage.example.yaml`). Then `scripts/data/storage_tier.py demote <folder>` moves a rarely used
data folder there and leaves a link at its old path, so scripts read it unchanged, only more slowly.
`promote <folder>` brings it back before a long study, and `list` shows what is where. With no cold
drive configured, `demote` does nothing.

## Data

No data is committed. The main datasets and how to build them:

| Dataset | Path under the data root | Source |
|---|---|---|
| Kalshi trade tape, to 2026-01-28 | `kalshi/trades_fixed/` | Hugging Face `TrevorJS/kalshi-trades` |
| Kalshi trade tape, from 2026-01-28 | `kalshi/trades_fixed_ext/` | Kalshi public API |
| Kalshi markets and settlements | `kalshi/markets.parquet` | Kalshi public API |
| Polymarket fills, YES-normalised | `polymarket_clean/trades_ext/` (older setups also `polymarket_clean/trades/`) | Hugging Face `SII-WANGZJ/Polymarket_data` (2026-10-05 release), then Polygon `OrderFilled` logs |
| Polymarket markets | `polymarket/hf_20261005/markets.parquet` | the same release, plus Gamma API lookups for newer markets |
| Per-study outputs | `vendor/<prefix>/` | written by each study |

To build them from scratch, run from the repository root:

```bash
# Kalshi: download the Hugging Face tape, repair its timestamps, then extend it from the API
uv run python scripts/data/download_kalshi_hf.py                    # streams through DuckDB (~6 GB)
uv run python scripts/data/repair_kalshi_trades.py
uv run python scripts/research/tape_refresh.py run --skip-verify    # resumable; `status` shows progress
uv run python scripts/data/refresh_kalshi_markets.py                # markets and settlements

# Polymarket: download the published snapshot (~64 GB), check it, catch up to today
uv run predict-ngin data pull polymarket

# ... or build it yourself from the 2026-10-05 Hugging Face release
uv run python scripts/data/download_hf_polymarket.py                # markets.parquet (0.5 GB)
uv run python scripts/data/polymarket_extend.py hf --full           # ~46 GB read remotely
uv run python scripts/data/polymarket_extend.py tail                # new days via Polygon RPC; `status` shows coverage
```

**Polymarket snapshot on Hugging Face.** [domdd/predict_ngin](https://huggingface.co/datasets/domdd/predict_ngin)
holds the Polymarket lake plus the source files `data update polymarket` needs to carry on from it.
`data pull` downloads it into the data root only when it is newer than what you have, removes lake files a
newer snapshot replaced, checks every month against `fills_state.json`, then runs the normal update. It
never replaces a lake you built yourself unless you pass `--force`. The maintainer refreshes it with
`uv run predict-ngin data publish polymarket` (after `hf auth login`): one commit adds the changed files
and deletes the ones the snapshot no longer has. Set `PREDICT_NGIN_HF_REPO` (or `--repo`) to use another
dataset. Kalshi data is not published: Kalshi's Developer Agreement (3.1) forbids sharing API data
without written permission.

Every refresh is append-only and resumes from its checkpoints (re-run the same command after an
interruption). The downloads are large (about 50 GB for Polymarket) and the Kalshi API catch-up takes
days. The Hugging Face downloads are pinned to fixed dataset revisions, so every setup gets the same rows.

The Polymarket release Hugging Face published in Feb 2026, which older setups built
`polymarket_clean/trades/` from, is no longer available. `hf --full` builds the whole history from the
2026-10-05 release instead. Before 2025-12-30 that release's timestamps are not block times (off by
minutes to hours), so `ts_chain` comes from the Polygon block anchors committed in
`src/data_quality/polygon_block_anchors.parquet`.

After the first build, the pipeline command keeps everything current and builds the partitioned **data lake**
(month folders, sorted files, contracts checked before publishing):

```bash
uv run predict-ngin data status                  # coverage and freshness of every dataset
uv run predict-ngin data update polymarket       # new fills + lake rebuild of changed months
uv run predict-ngin data verify                  # data contracts
uv run predict-ngin viz                          # browse it in the browser (uv sync --extra viz)
```

Query any dataset by name with `con = predict_ngin.lake.connect()` (views `kalshi_trades`,
`polymarket_maker_fills`, `polymarket_markets`, ...), or build SQL with `predict_ngin.tapes.kalshi.tape_sql`
and `predict_ngin.tapes.polymarket.clean_trades_sql`, which join the old and the extended parts without
duplicates. Layout, design choices and commands: [docs/DATA_LAKE.md](docs/DATA_LAKE.md).

## Checks

```bash
uv run pytest -m "not data"      # tests that need no local data (what CI runs)
uv run pytest                    # all tests; a `data` test skips when its files are missing
uv run ruff check predict_ngin   # lint (enforced on the predict_ngin package)
uv run lint-imports              # layer contracts in .importlinter
uv run pre-commit install        # optional: ruff and safety hooks on every commit
```

## Code layout

| Path | What it is |
|---|---|
| `predict_ngin/` | The installable core, one package per bounded context: `shared`, `markets`, `venues`, `costs`, `tapes`, `books`, `capture`, `lake`, `evaluation`, `paper`. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). |
| `scripts/research/` | One script family per study, named by prefix (`mkgap_*`, `fin_B_*`, ...). |
| `scripts/data/` | Downloads and refreshes. |
| `src/` | Older code. Some packages are current (`src/data`, `src/recorder`, `src/data_quality`); others are legacy and quarantined (each has a `LEGACY.md`). |
| `docs/` | Study reports, the research ledger and the strategy catalog. |
| `tests/` | The test suite; `tests/unit/` holds the golden tests for the core. |

The core imports nothing from `scripts/` or from the legacy packages; `uv run lint-imports` checks
this.

## Strategy catalog

[docs/strategies/](docs/strategies/README.md) indexes every strategy tested, grouped by outcome:
`PROMISING.md`, `NEXT_STEPS.md`, `REJECTED.md` (each with its reason and when to re-test) and
`CODE_ARCHIVE.md` (each family's scripts, docs, tests and trials). The ledger behind it is
[docs/RESEARCH_LEDGER.md](docs/RESEARCH_LEDGER.md). Check both before testing an idea: many have been
tried, and most failed.

## Running and adding a study

A study is a script in `scripts/research/`. Run it from the repository root:

```bash
uv run python scripts/research/<prefix>_study.py
```

To start a new one:

```bash
uv run python scripts/research/new_strategy.py <prefix> "<title>" --venue kalshi
```

This creates a ledger stub, a report from the template and a study script that imports the core
building blocks. [docs/strategies/README.md](docs/strategies/README.md) describes the workflow:
pre-register, implement, log every trial in `research/trials.csv`, record the verdict, then
regenerate the catalog with `uv run python scripts/research/build_strategy_catalog.py`.

## Paper runners

`predict_ngin.paper` and `scripts/live/` hold paper-trading runners for a few promoted strategies.
They are optional: nothing else depends on them, and you need them only to forward-test a strategy.
Deployment scripts are site-specific and not included.

## License

MIT

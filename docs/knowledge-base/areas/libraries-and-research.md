# Libraries and research

**LAST UPDATED:** 2026-11-02 · **Commit:** `8c534c1b` (main, the code this page was checked against)

The research side of the repository. AlgoLens has its own page; here research-api appears only for
its research parts. All five members are in the one uv workspace (platform.md). Each runs its
checks with `just quality <path>` from the repository root.

| Member | Released as | State | Imported by |
|---|---|---|---|
| `libs/algosystem` | `algosystem` on PyPI (0.1.9) | real code | nothing in this repository |
| `libs/research-core` | `research-core`, never published | real, two small modules | `services/research-api` |
| `services/predict-ngin` | image `algogators/predict-ngin` | real code, research only | nothing outside itself |
| `services/research-api` | image `algogators/research-api` | real code, AlgoLens's API | the AlgoLens web app, over HTTP |
| `apps/algoterminal` | `algoterminal-cli`, never published | real code | nothing; a stand-alone app |

The root `README.md` still calls research-core and algoterminal "placeholders"; both have code.

## algosystem

**For.** Backtest a price or equity series, save runs to Postgres, render quantstats tearsheets
and test a parameter search for overfitting, from Python or the `algosystem` CLI
(`libs/algosystem/README.md`). #58 makes it the one shared statistics library: the research API
should import it rather than write its own statistics.

**Layout.** Contexts under `src/algosystem/`: `backtesting`, `validation` (CSCV/PBO, PSR/DSR,
shufflers, walk-forward), `marketdata` (yfinance benchmarks, parquet cache), `portfolio`,
`interfaces` (CLI and the `AlgoSystem` facade). The custom dashboard was removed; quantstats
tearsheets are the only reporting path (`CHANGELOG.md`).

**CI and release.** `lib-algosystem.quality.yml`: Python 3.11 and 3.13 on Linux, macOS and Windows,
80% coverage, 30-minute timeout (the suite takes about 15 minutes). A tag `algosystem/vX.Y.Z` runs
`lib-algosystem.publish.yml`, which uploads through `_publish-pypi.yml` (Trusted Publishing, `pypi`
environment; a manual run defaults to a dry run). PyPI already has 0.1.9, the version in
`pyproject.toml`, so the next release needs a bump. No `algosystem/v*` tag exists here yet.

**Decisions.** It keeps its own copy of the database config reader instead of using `platform-db`,
because it is published and must install outside the workspace (`platform/db/README.md`,
`778f2e83`). mypy checks it for syntax and imports only; entries leave that list as modules are
cleaned and none are added (root `pyproject.toml`).

**Gotchas.**
- **Two copies.** `AlgoGators/algosystem` is not archived, and the copy here was last changed on
  2026-08-15 (`778f2e83`). Which is canonical is open in AlgoGators/algosystem#34.
- **PSR and DSR are overstated** (an annualised Sharpe scaled by the root of the day count). Until
  AlgoGators/algosystem#35 closes, pass `annualize=1.0` and per-period Sharpes, and show nothing from
  the validation package to the desk (#58, #133).
- Its `.importlinter` contracts do not run here: the root `lint` recipe is ruff only and algosystem
  has no justfile. The README's `poetry install` is stale; the backend is hatchling, the
  environment is uv's.

**Open issues.** #58 AlgoLens statistics through algosystem; #133 walk-forward and Monte Carlo
evaluation (lists the validation defects); AlgoGators/algosystem #34 canonical copy, #35 PSR/DSR,
#36 benchmark comparison of a trade-ngin run, #37 run against run and live against backtest, #6
Trusted Publisher and version bump.

## research-core

**For.** `research_core.returns` (`compute_return_stats`, `compute_sharpe`; stdlib only on
purpose) and `research_core.errors`. Both moved verbatim out of research-api, which re-exports
them (`778f2e83`). #58 keeps it a small errors-and-helpers package. Its README says algoterminal
uses it; algoterminal does not.

**CI and release.** Linux and Windows, 85% coverage. `lib-research-core.publish.yml` exists, but
nothing needs a PyPI release: research-api's image installs it from the source tree.

**Decision.** A workspace lib that is not on PyPI must ship inside every artifact that depends on it.
`rule_workspace_deps_are_packaged` in `platform/ci/check_workflow_paths.py` fails CI when a
PyPI-published member depends on one, or when a container member does not build from the repository
root and copy it (`f5a52b8b`, `5a2806d3`). That is why algoterminal dropped its unused research-core
dependency.

## predict-ngin

**For.** Prediction-market research on Kalshi and Polymarket: downloads and a data lake, fee and
cost models, study scripts and a ledger of every strategy tested. It reads public data and does not
trade (`services/predict-ngin/README.md`). Imported without history in #38 (`7050fd54`).

**Layout.** `predict_ngin/` is the installable core, one package per bounded context (`shared`,
`markets`, `venues`, `costs`, `tapes`, `books`, `capture`, `lake`, `evaluation`, `paper`, `viz`).
`src/` is older code, partly quarantined (each legacy package has a `LEGACY.md`); studies are in
`scripts/research/`, downloads in `scripts/data/`. Design: `docs/ARCHITECTURE.md`, D1 to D16.

**Run** (from `services/predict-ngin`): `uv sync --extra research --extra dev`, then
`uv run predict-ngin data status`, `uv run pytest -m "not data"` (what CI runs, 70% floor) and
`uv run lint-imports`. Its `justfile` overrides lint, typecheck and test. Data lives under
`PREDICT_NGIN_DATA` (default `./data`) and is never committed; `QUICK_START.md` builds it.

**Release.** A `predict-ngin/v*` tag pushes the CLI image to GHCR (`svc-predict-ngin.publish.yml`).
There is no deploy job: nothing runs it on a server (#38).

**Decisions** (ARCHITECTURE.md).
- D1, D2: strangler-fig. Canonical code moves into `predict_ngin`; old modules become shims checked
  by golden tests against frozen copies (`tests/unit/_legacy_*.py`). Studies are never moved or
  deleted, so published results reproduce. D4: studies keep their own fee assumptions.
- D5, D6, D14: venue clients are GET-only; the old Polymarket order path raises
  `LiveTradingDisabledError` and is kept only for audit.
- D15: the data lake is Parquet partitioned by month; DuckDB queries it but is not the store.
- D16: only rejected strategies are published. Open study families are kept out by a generated block
  in `.gitignore` (`scripts/research/build_private_split.py`).
- `docs/SENSITIVITY_DECISION_MATRIX.txt` is a scenario table for the whale-era strategies. Those
  are quarantined legacy (D14) and their numbers are artifacts: read it as history, like
  `DEPLOYMENT_STATUS.md`.

**Gotchas.**
- `uv sync` installs only the extras it names and removes the rest; name them all at once.
- Many studies read `data/` in the checkout: link it to your data as well as setting the variable.
- The README's "about 550 research scripts" is the maintainer's full tree; the public one has about
  100 entries in `scripts/research/` and only `REJECTED.md` of the catalog (D16).
- Only Polymarket data is shared on Hugging Face; Kalshi's developer agreement forbids sharing.

**Open items.** No open issue. Open PRs #73, #76 and #77 (dependency and CodeQL fixes) touch it.

## research-api, research side

Portfolio statistics go through research-core (`domain/portfolio/calculations.py`). The strategy
lifecycle is `incubating`, `live` or `retired` on `trading.strategy_registry`; an incubating
strategy runs on mock capital for a 120-day window and is hidden from the live book
(`migrations/002_strategy_lifecycle.sql`, `domain/portfolio/incubation.py`, `8c56188e`). The column
defaults to `live` so existing rows stay in the live book. 80% coverage in CI; tests need no
database. The service README still describes the old `algolens-api/` layout. Under #58, a number the
engine stores is shown as stored, never recomputed. Open: #58, #88 (stored statistics, NULL-safe
reads), #65 (validate the strategy-list response).

## algoterminal

**For.** A full-screen terminal workbench (Textual) for the Quantitative Research team: free
public data (yfinance, Stooq, NASA POWER, USGS, FRED, World Bank) with a local parquet cache,
comparisons (correlation, cointegration, spreads) and a versioned hypothesis-to-write-up cycle
(`apps/algoterminal/README.md`). By design it never connects to the fund's data. Records, universes
and the cache live under `~/.algoterminal/`.

**Run.** `uv run --package algoterminal-cli algoterminal` from the root. CI: 3.11 on Linux and
Windows, 80% coverage. `app-algoterminal.publish.yml` on an `algoterminal/v*` tag publishes
`algoterminal-cli` to PyPI; it never has, so `install.sh`'s default `pipx install` does not work yet.

**Gotchas.**
- The README says there is no AI component, but the Design tab (`research/design_agent.py`) runs the
  `claude` or `codex` command-line agent on one record's `strategy.py`.
- The README's install steps name an old repository folder (`AlgoGatorsCLI`).
- The publish workflows' coverage floors are lower than the PR gates': 65 against 80 here, 60
  against 80 for research-api.

**Open issues.** #67 flag market data missing the `close` column (PR #122 open).

# Where things live

As of 2026-11-02.

## Code

| What | Where |
|---|---|
| The engine (C++): strategies, risk, backtests, live runners | `services/trade-ngin/` (`src/`, `include/trade_ngin/`, `apps/`) |
| Engine tests | `services/trade-ngin/tests/` (GoogleTest; `ctest --preset ci-gcc13`) |
| The desk service, scheduler and daily report (Python, in the engine image) | `services/trade-ngin/services/rpc/algogators_rpc/` |
| Engine config templates | `services/trade-ngin/config_template/` |
| Engine database migrations | `services/trade-ngin/migrations/` |
| Engine deploy | `services/trade-ngin/deploy/deploy.sh` |
| The `tradengin` Python module | `services/trade-ngin/python/`, `services/trade-ngin/pyproject.toml`; pybind11 is a submodule at `services/trade-ngin/extern/pybind11` |
| Market data pipelines (Airflow) | `services/data-ngin/` |
| AlgoLens | `apps/algolens-web/` (front end), `services/research-api/` (API), `infra/algolens/` |
| Website (algogators.com) | `apps/website/` |
| Shared statistics library | `libs/algosystem/` |
| Black-swan stress test (command line, `stress-test`) | `apps/stress-test/` |
| Issue tracker script | `tools/issue-tracker/` |
| The code graph agents query first | `graphify-out/graph.json`, `graphify-out/GRAPH_REPORT.md` (see `AGENTS.md`) |
| CI generators and the path-filter check | `platform/ci/` |
| C++ build presets and dependencies | `CMakePresets.json`, `vcpkg.json` (repository root) |

## Workflows

Each member has `<kind>-<name>.<purpose>.yml`; reusable ones start with `_`.

| Workflow | Does |
|---|---|
| `svc-trade-ngin.quality.yml` | build and test trade-ngin; on `main`, push its image |
| `svc-trade-ngin.rpc.yml` | the desk service's Python checks and the migration tests |
| `svc-trade-ngin.deploy.yml` | deploy engine-rpc from `prod` |
| `svc-trade-ngin.python.yml` | `tradengin` wheels; publish on tag `tradengin/vX.Y.Z` |
| `svc-trade-ngin.watchdog.yml` | scheduled live-run checks |
| `meta-workflow-paths.yml` | fails when a quality workflow's path filters stop matching the tree |
| `meta-graphify.yml` | Mondays: rebuilds the code graph and pushes `chore/graph-refresh` for a PR |

## GitHub

| What | Where |
|---|---|
| Migration checklist for trade-ngin | #90 (old-to-new issue table in its comments) |
| Order of work | #156 |
| Tracker page | https://algogators.github.io/issue-tracker/ (built by `AlgoGators/issue-tracker`) |
| Standing merge rules | #179 |
| Project board | the organisation project "AlgoGators master tracker" |
| Archived repositories | `AlgoGators/trade-ngin` (being archived), `AlgoGators/data-ngin`; their history and closed PRs stay readable |
| Former trade-ngin PR branches | `trade-ngin-pr/<branch>` here |

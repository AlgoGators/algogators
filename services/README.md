# services/

Things that **run unattended**: ingestion pipelines, trading engines,
schedulers, model servers. Nobody opens them; they wake up on a timer or a
message and are expected to already be running.

| | |
|---|---|
| Publishes to | container registry (GHCR), digest-pinned |
| Release trigger | tag `<name>/vX.Y.Z`, then a deploy job |
| Quality bar | coverage floor tuned per service; contract compatibility is mandatory |

Services are the tier where **`platform/contracts/` compatibility is enforced**.
A service that reads a schema it no longer matches fails silently at 4am on a
market open, so the quality gate diffs each service's contract usage against
`main` rather than only running its unit tests.

## Planned occupants

| directory | source repo | archetype |
|---|---|---|
| `data-ngin/` | `data-ngin` | FastAPI + Airflow DAGs, Poetry, Postgres |
| `trade-ngin/` | `trade-ngin` | C++20, CMake + vcpkg, GTest, Arrow |
| `research-api/` | shared backend for AlgoLens and AlgoTerminal | Python API |

`data-ngin/contracts/` is the seed for `platform/contracts/` — promoting it is
the first migration step, because it is the shared dependency that makes this a
monorepo rather than a folder of unrelated repos.

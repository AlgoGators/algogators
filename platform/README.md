# platform/

Cross-cutting code that more than one service depends on. This is the part that
justifies a monorepo: a change here breaks consumers **in the same pull request**
instead of at deploy time three weeks later.

## `contracts/`

One source of truth for data shapes crossing a service boundary.

```
contracts/
├── schemas/          # the definitions — YAML, hand-edited, reviewed
└── codegen/          # generators: schemas -> pydantic models AND C++ headers
```

`data-ngin` writes a bar; `trade-ngin` reads it. Today those two definitions live
in separate repos and drift. Here, editing `schemas/bar.yaml` fires **both**
services' CI, and a mismatch is a red PR.

Generated output is committed, not built at import time, so a C++ build does not
need a Python toolchain. `just contracts` regenerates; CI fails if the working
tree is dirty afterwards.

## `db/`

Shared database access: the canonical `DatabaseConfig` and the access-gate
seam (`AccessRequest` / `AccessGate`) that services call to obtain database
credentials. Today the gate is an allow-all wrapper over `DB_*` environment
variables; it exists so an IAM-backed gate can replace it without touching any
service's call sites. A workspace member with its own quality workflow
(`platform-db.quality.yml`), unlike the plain directories below.

## `configs/`

Shared instrument definitions, venue metadata, trading calendars. Data, not code
— but versioned and reviewed like code, because a wrong contract multiplier is
indistinguishable from a strategy bug.

## `ci/`

The machinery that keeps per-service pipelines honest.

| file | purpose |
|---|---|
| `check_workflow_paths.py` | enforces that every service's workflow path filters match reality (see [CONTRIBUTING.md](../CONTRIBUTING.md)) |
| `new_service.py` | generates the quality / skip / publish workflow triple for a new service |
| `templates/` | the templates it renders |

Anything under `platform/` must be listed in the `paths:` filter of every service
that consumes it. `check_workflow_paths.py` verifies that — it is the guard that
makes path-filtered CI safe.

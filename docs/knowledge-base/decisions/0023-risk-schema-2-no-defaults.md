# 0023. Risk config schema 2: no defaults, schema 1 refused

- **Status:** Accepted
- **Date:** 2026-10-08
- **Sources:** `config_template/README.md` ("Risk schema 2"); `src/core/config_loader.cpp`; commit `4b53f954`

## Context

Schema 1 silently fell back to fixed values when `max_drawdown` or `max_leverage` were missing, and a book could turn its risk layer off without a ruling.

## Decision

Every gating value is written in the book's own `risk.json`; `use_optimization` is required in `portfolio.json`; the loader rejects a schema-1 file or a removed key at load.

## Consequences

See decision 0006 for what this meant on the production box. `scripts/migrate_risk_json.py` converts old files.

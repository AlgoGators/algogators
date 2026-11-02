# 0012. algosystem keeps its own database config

- **Status:** Accepted
- **Date:** 2026-08-15
- **Sources:** commit `778f2e83`; `platform/db/README.md`

## Context

`libs/algosystem` is published to PyPI and must install outside the workspace, so it cannot depend on `platform/db`.

## Decision

algosystem keeps a private copy of the `DB_*` config reader.

## Consequences

Two readers of the same variables exist; a change to the variable names changes both.

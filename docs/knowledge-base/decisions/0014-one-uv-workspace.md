# 0014. One uv workspace, one lockfile, members listed by hand

- **Status:** Accepted
- **Date:** 2026-08-17
- **Sources:** #1; root `pyproject.toml`; commit `1b843c33`

## Context

Dependency clashes between Python members should fail at resolve time, not in production.

## Decision

Every Python member resolves against one root `uv.lock` (Python 3.11 floor). Members are listed explicitly in the root `pyproject.toml`, not by glob. trade-ngin and Airflow stay outside it.

## Consequences

Adding a Python member means adding it to `[tool.uv.workspace].members` (CONTRIBUTING.md).

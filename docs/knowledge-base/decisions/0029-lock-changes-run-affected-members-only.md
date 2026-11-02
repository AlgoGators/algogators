# 0029. A lockfile change runs only the members whose dependencies changed

- **Status:** Accepted
- **Date:** 2026-10-08
- **Sources:** #17 (`5746599d`); `platform/ci/quality_needed.py`

## Context

Every Python member lists `uv.lock` as an input, so any lock bump ran every member's full matrix.

## Decision

`_quality-python.yml` runs `platform/ci/quality_needed.py` first: when only `uv.lock` changed, a member runs only if its resolved dependency closure changed, and runs anyway when that cannot be told.

## Consequences

A lock bump is cheap in CI unless it changes what a member resolves.

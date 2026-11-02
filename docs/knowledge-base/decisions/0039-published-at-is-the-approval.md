# 0039. Only published_at says a desk day is approved

- **Status:** Accepted
- **Date:** 2026-10-13
- **Sources:** AlgoGators/AlgoLens#129

## Context

Rows in `trading.position_overrides` never change, so a rescind must reopen the day without deleting history.

## Decision

`trading.live_run_metadata.published_at` is the only approval signal; a done publish row no longer locks the day by itself. A rescind clears `published_at` and leaves the row as history.

## Consequences

Read approval state from `published_at`, never from the override rows.

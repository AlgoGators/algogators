# 0030. AlgoLens calls the engine over gRPC; Postgres stays the record

- **Status:** Accepted
- **Date:** 2026-10-09
- **Sources:** AlgoGators/AlgoLens#113, #115; `docs/algolens/README.md`

## Context

The desk needs the engine to act on an approval at once, and a failed call must lose nothing.

## Decision

AlgoLens writes the command row to `trading.position_overrides` first, then calls engine-rpc's DeskService with the row's id; the engine re-drives pending rows every 60 seconds.

## Consequences

This changed the master document's original rule that the two never call each other. The database row, not the call, is the source of truth.

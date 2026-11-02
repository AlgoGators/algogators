# 0038. ops.data_ready belongs to the engine; data-ngin only writes rows

- **Status:** Accepted
- **Date:** 2026-10-12
- **Sources:** #33, #82; `services/trade-ngin/migrations/028_ops_data_ready.sql`

## Context

The engine's scheduler polls this table to decide when to build books, so a consumer must never be left waiting.

## Decision

The table is created by trade-ngin's migration 028. data-ngin upserts one row per dataset and bar date and, from its failure callback, a `failed` row for the pull that failed.

## Consequences

A schema change to `ops.data_ready` is an engine migration.

# 0037. Each pull re-fetches the last five days and upserts

- **Status:** Accepted
- **Date:** 2026-10-12
- **Sources:** #33; #91; `application/close_pipeline.py`

## Context

A provisional bar must be replaced by the vendor's final one, and a missed day must be filled; the old pull never re-fetched a missed day.

## Decision

Each run re-fetches every expected bar for D and the five days before and writes it with `ON CONFLICT DO UPDATE`; other writers keep `DO NOTHING`.

## Consequences

A late correction from the vendor lands within five days.

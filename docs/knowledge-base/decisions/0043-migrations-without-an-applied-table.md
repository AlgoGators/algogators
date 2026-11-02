# 0043. Migrations keep no applied-state table; numbers are never reused

- **Status:** Accepted
- **Date:** 2026-10-21
- **Sources:** `services/trade-ngin/migrations/README.md`; `services/research-api/migrations/README.md`; #232 (`241cceda`)

## Context

Master-document ruling 26 chose no migrations table, and two lines of work had applied clashing numbers to one database.

## Decision

Applied state is a hand-kept table in each `migrations/README.md` (the engine's and research-api's). Retired numbers are never reused; the engine's main-nonQT 028 to 033 became 034 to 039 while the QT line kept 028 and 029.

## Consequences

Record every application in the README the same day. Check the README before picking a number.

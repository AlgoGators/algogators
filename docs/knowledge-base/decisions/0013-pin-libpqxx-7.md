# 0013. Pin libpqxx to the 7.x API

- **Status:** Accepted
- **Date:** 2026-08-15
- **Sources:** commit `43949c50`; `vcpkg.json` (`$reason`)

## Context

libpqxx 8 changed result iteration to `row_ref`, which breaks every row lambda in trade-ngin.

## Decision

vcpkg pins libpqxx to 7.10.5 through a manifest override.

## Consequences

Lifting the pin is its own migration of the engine's database code.

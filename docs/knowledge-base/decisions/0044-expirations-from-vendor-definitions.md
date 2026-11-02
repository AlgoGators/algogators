# 0044. Contract expirations come from vendor definitions, never a calendar guess

- **Status:** Accepted
- **Date:** 2026-10-25
- **Sources:** #78 (`4b9be913`); #37; `domain/contract_definition.py`

## Context

Vendor ids can be reused across contracts, and a guessed date would silently mislabel the contracts rolls depend on.

## Decision

Expirations are read from the vendor's definition records, matched on `(instrument_id, raw_symbol)`; a calendar-computed expiration is never written.

## Consequences

A contract without a definition has no expiration until one arrives.

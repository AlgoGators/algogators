# 0017. platform-db is an unpublished platform member with an access-gate seam

- **Status:** Accepted
- **Date:** 2026-08-21
- **Sources:** commit `2ed574f2`; `platform/db/README.md`

## Context

The database helper was only used inside the workspace, and services are headed toward credentials issued per service.

## Decision

`libs/platform-db` moved to `platform/db` and its PyPI publishing was dropped. Services get credentials through `AccessGate`, today an allow-all `EnvAccessGate`.

## Consequences

Switching to issued credentials changes the gate, not the call sites.

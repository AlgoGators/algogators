# 0031. The engine's protos are vendored with a version header

- **Status:** Accepted
- **Date:** 2026-10-09
- **Sources:** AlgoGators/AlgoLens#115, #119; `services/research-api/scripts/sync_protos.sh`

## Context

The AlgoLens API and the engine must not drift apart silently.

## Decision

AlgoLens copies the protos and `gen_versions.py` from trade-ngin at the commit pinned in `proto/algogators/SOURCE`, and every call sends `x-algogators-api-version`; the engine refuses a different major version.

## Consequences

No workflow in the mono repo runs `sync_protos.sh --check` any more: re-add it, or run it by hand when the engine's protos change.

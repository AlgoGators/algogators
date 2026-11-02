# 0031. The engine's protos are vendored with a version header

- **Status:** Accepted
- **Date:** 2026-10-09
- **Sources:** AlgoGators/AlgoLens#115, #119; `services/research-api/scripts/sync_protos.sh`; the `protos` job of `svc-research-api.quality.yml` (2026-11-02)

## Context

The AlgoLens API and the engine must not drift apart silently.

## Decision

AlgoLens copies the protos and `gen_versions.py` from the engine (`services/trade-ngin` in this repository since #90; the commit is in `proto/algogators/SOURCE`), and every call sends `x-algogators-api-version`; the engine refuses a different major version.

## Consequences

CI enforces the copy: the `protos` job of `svc-research-api.quality.yml` runs `sync_protos.sh --check` whenever research-api or the engine's protos change, and fails on any difference (`gen_versions.py` is compared as parsed Python, so the formatter may reflow it). A desk.proto change carries `scripts/sync_protos.sh` and the regenerated stubs in the same PR.

# 0032. AlgoLens yields memory to the engine on the shared box

- **Status:** Accepted
- **Date:** 2026-10-09
- **Sources:** `infra/algolens/deployment/DEPLOYMENT.md`, `docker-compose.prod.yml`; AlgoGators/AlgoLens#104, #116

## Context

The engine's live run on the shared box must never be killed for memory.

## Decision

Every AlgoLens container has a hard memory limit and `oom_score_adj: 800`; AlgoLens's deploy stops the old containers before starting new ones, runs a database pre-flight, and rolls back on its own.

## Consequences

Under memory pressure the kernel kills AlgoLens first.

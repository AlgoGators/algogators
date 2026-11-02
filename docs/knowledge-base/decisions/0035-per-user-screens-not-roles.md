# 0035. AlgoLens grants screens per user instead of new roles

- **Status:** Accepted
- **Date:** 2026-10-11
- **Sources:** AlgoGators/AlgoLens#128, #137; #54; `services/research-api/src/research_api/domain/identity/screens.py`

## Context

Grants and revokes must take effect at once, and the planned GitHub-teams model turned out unnecessary.

## Decision

An admin grants member, QT and investor screens per user; the server re-reads the role and screens from the database on every request, never from the token.

## Consequences

The capability model (#54) was closed as replaced.

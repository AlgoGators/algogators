# 0041. predict-ngin publishes only rejected strategies

- **Status:** Accepted
- **Date:** 2026-10-17
- **Sources:** `services/predict-ngin/docs/ARCHITECTURE.md` (D16); #38

## Context

Strategies still live or promising must stay private in a public repository.

## Decision

Open study families stay out of the repository through a generated `.gitignore` block; only `REJECTED.md` and the code of fully rejected families are public. History was not rewritten.

## Consequences

Never commit an open study family's code or results.

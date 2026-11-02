# 0010. Delete merged and closed PR branches; keep the rest

- **Status:** Accepted
- **Date:** 2026-11-02
- **Sources:** #90

## Context

The mono repo had 126 branches, most of them finished or imported.

## Decision

A branch whose PR is merged or closed, and that no open PR uses, is deleted (35 on 2026-11-02). A branch with no PR is kept: most are history imported from data-ngin, AlgoLens and trade-ngin, and deleting one cannot be undone.

## Consequences

A deleted PR branch can be restored from its PR page. Delete a no-PR branch only with its author's agreement.

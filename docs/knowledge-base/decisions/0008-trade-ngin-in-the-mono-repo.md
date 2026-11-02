# 0008. trade-ngin lives in the mono repo; the old repository is archived

- **Status:** Accepted
- **Date:** 2026-11-02
- **Sources:** #90, #98, #120

## Context

The engine was the last large member outside the mono repo, with its own CI, deploys, issues and board.

## Decision

- The engine is `services/trade-ngin/`. Its history was rebuilt for the import (one commit per merged PR, AI lines removed) and the rebuilt history went into this repository only; trade-ngin's own history was left as it was, so no clone needs a reset.
- Open PRs continued here from `trade-ngin-pr/<branch>`; the mergeable ones landed with a `Moved-from: AlgoGators/trade-ngin#N` trailer and the old PR closed with a link.
- Open issues were transferred (comments, assignees and labels kept), after every bare `#N` in their text was qualified as `AlgoGators/trade-ngin#N`. Done or superseded ones were closed instead. The old-to-new table is on #90.
- AlgoGators/trade-ngin is archived (read-only).

## Consequences

`git log --grep Moved-from` lists the landed PRs. Old links to moved issues redirect. Nothing pushed to trade-ngin carries over.

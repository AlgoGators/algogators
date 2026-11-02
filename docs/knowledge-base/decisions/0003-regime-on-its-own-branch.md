# 0003. Regime detection stays on its own branch

- **Status:** Accepted
- **Date:** 2026-11-02
- **Sources:** #90, #128, #109 to #118

## Context

The regime-detection work (market and macro pipelines, DFM and MS-DFM, EGARCH) was built on a long-lived branch that never merged into the engine's `main`; when trade-ngin moved, it was 34 commits ahead of `main` and hundreds behind.

## Decision

The branch came over as `trade-ngin-pr/regime_detection_updated`. Sivan's fixes continue as PRs #109 to #118 against it. #110, #113 and #117 are proposals that wait on HD's ruling. Bringing regime into `main` is its own piece of work (#128, #148 to #151).

## Consequences

Nothing on `main` imports the regime code. A change to it targets the regime branch, not `main`.

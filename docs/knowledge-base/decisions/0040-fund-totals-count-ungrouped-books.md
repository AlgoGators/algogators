# 0040. Fund totals count only ungrouped books

- **Status:** Accepted
- **Date:** 2026-10-14
- **Sources:** AlgoGators/AlgoLens#131

## Context

The QT desk book and its model twin are paper books until they send positions.

## Decision

They are listed in AlgoLens but left out of the fund value, return and equity curve.

## Consequences

When a QT book starts sending positions, revisit this.

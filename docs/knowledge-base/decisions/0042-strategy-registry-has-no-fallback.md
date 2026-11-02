# 0042. The strategy registry has no built-in fallback

- **Status:** Accepted
- **Date:** 2026-10-19
- **Sources:** AlgoGators/AlgoLens#143; `services/research-api/src/research_api/infrastructure/portfolio/strategy_registry.py`

## Context

A default list once replaced a failed read and silently hid every portfolio except the trend book, the QT books included.

## Decision

A failed or empty read of `trading.strategy_registry` is an error the screens show (D-23).

## Consequences

An empty AlgoLens portfolio list means the registry read failed; check it rather than adding a default.

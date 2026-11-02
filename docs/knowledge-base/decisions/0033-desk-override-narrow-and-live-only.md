# 0033. The desk's config override is narrow and live-only

- **Status:** Accepted
- **Date:** 2026-10-09
- **Sources:** QT plan E2; rulings 2, 3, 4, 24; D-17; `include/trade_ngin/core/config_loader.hpp`; `apps/tools/qt_settings_check.cpp`

## Context

A key the files do not hold is a typo, a refused setting must never stop the 09:30 send, and backtests must stay reproducible from files.

## Decision

The `trading.strategy_config` override may only change keys the files already hold, with the same JSON type, no nulls and no credential-like keys. Backtests never read it. `qt_settings_check` vets it before AlgoLens saves it.

## Consequences

Adding a new desk setting means adding it to the files first.

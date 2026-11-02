# 0024. Futures books are sized on half-compounding capital

- **Status:** Accepted
- **Date:** 2026-10-08
- **Sources:** `include/trade_ngin/portfolio/sizing_capital.hpp`; `_sizing_note` in the templates; commit `04b76297`

## Context

Carver sizes on current trading capital, and backtest and live must use one figure to stay comparable without reading the future.

## Decision

Sizing capital is starting capital minus the drawdown of cumulative settled net P&L from its peak, never above starting capital, the same in backtest and live.

## Consequences

A change to sizing capital moves stored numbers (decision 0001).

# 0034. The live mode gate tests production with a clock shim

- **Status:** Accepted
- **Date:** 2026-10-10
- **Sources:** `services/trade-ngin/scripts/live_mode_gate/gate.sh`, `hostclock_shim.c`; commit `33804800`

## Context

Every test runs past dates while production runs today, and a test switch left in the product could stay on.

## Decision

Production mode is proven by running the unpatched binary under a wall-clock shim on a scratch database; every stored table must match the past-date reference.

## Consequences

Never add a test-only switch to a runner; extend the gate instead.

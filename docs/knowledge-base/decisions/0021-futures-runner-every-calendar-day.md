# 0021. The futures runner runs every calendar day

- **Status:** Accepted
- **Date:** 2026-09-17
- **Sources:** `services/trade-ngin/live_portfolio.cron` header; `scripts/run_live_portfolio.sh`

## Context

A Monday-to-Friday schedule drops the Sunday Globex session and breaks the T-1 chain (a phantom rebuild or a refusal every Monday).

## Decision

The live futures run is scheduled at 09:30 New York time seven days a week, with no weekday guard; the engine decides per symbol whether T-1 was a session.

## Consequences

Do not add a weekday guard to the schedule.

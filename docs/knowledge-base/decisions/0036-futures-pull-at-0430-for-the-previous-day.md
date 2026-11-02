# 0036. Futures bars are pulled at 04:30 New York time for the previous UTC day

- **Status:** Accepted
- **Date:** 2026-10-12
- **Sources:** #33 (`2716ca63`); `services/data-ngin/README.md`; `application/close_pipeline.py`

## Context

The vendor's historical plan publishes a day's bar about 8 hours after it ends, so a pull right after the close would miss it.

## Decision

Both futures DAGs run at 04:30 New York time every day and pull bar date D, the UTC day that ended before the run, waiting up to 60 minutes for it.

## Consequences

The engine's morning run waits on `ops.data_ready` for that pull (decision 0038).

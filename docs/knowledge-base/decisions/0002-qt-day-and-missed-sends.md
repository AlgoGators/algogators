# 0002. The QT day, and why a missed day is not sent late

- **Status:** Accepted
- **Date:** 2026-10-09
- **Sources:** `services/trade-ngin/services/rpc/algogators_rpc/services/desk/catchup.py` (module docstring: contract C6, C7; rulings 18 and 29 as amended 2026-10-09; D-40)

## Context

The QT desk approves a book each morning and the e-mail must go out once, at 09:30 New York time, from the stored rows. A restart or an outage can leave a day published but not e-mailed.

## Decision

The scheduler (`desk.catchup`, in engine-rpc) runs the model about 07:00, waits for the desk's approval until 09:30, sends at 09:30 and publishes the model's book itself at 10:00 if nobody approved. A day that was published but never e-mailed before it passed is **not sent late**: an alert is raised instead (D-40). A missed past day is caught up and published, not e-mailed.

## Consequences

Do not "fix" a missing e-mail by sending an old day. Look for `missed_send` and `send_failed` in the engine's logs. Everything is decided from the database and the clock, so a late pass does what the missed one would have.

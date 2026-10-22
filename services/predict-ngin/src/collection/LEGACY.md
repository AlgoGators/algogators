# LEGACY: `src/collection`

**What it was.** Early collectors with a common `BaseCollector` interface: trades, prices and
liquidity (order-book snapshots).

**Era.** 2026-02-07 to 2026-02-11.

**Audit.** `docs/STRATEGY_AUDIT.md` section 1 lists the data-level problems of this era's datasets (truncated trade
tapes, mixed YES/NO token prints, broken Kalshi timestamps).

**Replaced by.** `predict_ngin.capture` (one-pass runner), `predict_ngin.venues` (read-only
clients), `src/recorder` (L2 book recorder) and the atlas collectors in `config/collectors.yaml`.

## Status

Kept for reference; not maintained. Do not build new work on it. It is kept, not deleted, so that
past results can be audited and reproduced. Its tests still run in `uv run pytest`.
`predict_ngin` must not import it (`.importlinter`, contract `core-not-legacy`; see
`docs/ARCHITECTURE.md` D14).

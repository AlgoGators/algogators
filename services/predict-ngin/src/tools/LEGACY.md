# LEGACY: `src/tools`

**What it was.** `cli_utils` (rich terminal output) and `market_scanner` (scores active Polymarket
markets from the Gamma API by volume, expiry, movement and liquidity).

**Era.** 2026-02-07 to 2026-02-08.

**Audit.** Not a strategy; no result in `docs/STRATEGY_AUDIT.md` depends on it.

**Replaced by.** `predict_ngin.venues.polymarket` for Gamma reads, and study-specific scanners in
`scripts/research/`.

## Status

Kept for reference; not maintained. Do not build new work on it. It is kept, not deleted, so that
past results can be audited and reproduced. Its tests still run in `uv run pytest`.
`predict_ngin` must not import it (`.importlinter`, contract `core-not-legacy`; see
`docs/ARCHITECTURE.md` D14).

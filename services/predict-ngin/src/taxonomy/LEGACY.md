# LEGACY: `src/taxonomy`

**What it was.** `MarketTaxonomy`: keyword and hybrid hierarchical categorisation of markets.

**Era.** 2026-02-07.

**Audit.** Its category labels fed the whale and liquid-markets backtests that `docs/STRATEGY_AUDIT.md` section 3
invalidated.

**Replaced by.** `predict_ngin.markets` (series and ticker parsing, market identity, cross-venue
pairing).

## Status

Kept for reference; not maintained. Do not build new work on it. It is kept, not deleted, so that
past results can be audited and reproduced. Its tests still run in `uv run pytest`.
`predict_ngin` must not import it (`.importlinter`, contract `core-not-legacy`; see
`docs/ARCHITECTURE.md` D14).

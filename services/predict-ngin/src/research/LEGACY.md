# LEGACY: `src/research`

**What it was.** Report generators for early strategy research: ML whale features
(`whale_features`), market-regime detection (`regime_detection`), multi-strategy ensembles
(`ensemble_research`) and signal mining (`alpha_discovery`).

**Era.** 2026-02-07 to 2026-02-14.

**Audit.** Built on the whale data and pre-audit price data. `docs/STRATEGY_AUDIT.md` sections 1 and 3 invalidate the
inputs, so its reports are not evidence of an edge.

**Replaced by.** Per-study scripts in `scripts/research/` with the trial ledger
(`docs/RESEARCH_LEDGER.md`), and `predict_ngin.evaluation` for statistics.

## Status

Kept for reference; not maintained. Do not build new work on it. It is kept, not deleted, so that
past results can be audited and reproduced. Its tests still run in `uv run pytest`.
`predict_ngin` must not import it (`.importlinter`, contract `core-not-legacy`; see
`docs/ARCHITECTURE.md` D14).

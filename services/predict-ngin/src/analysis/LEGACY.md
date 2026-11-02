# LEGACY: `src/analysis`

**What it was.** `DistributionPlotter`: distribution plots for research data.

**Era.** 2026-02-07.

**Audit.** Plotting only; plots of pre-audit data inherit `docs/STRATEGY_AUDIT.md` section 1.

**Replaced by.** Plotting inside each study script in `scripts/research/`.

## Status

Kept for reference; not maintained. Do not build new work on it. It is kept, not deleted, so that
past results can be audited and reproduced. Its tests still run in `uv run pytest`.
`predict_ngin` must not import it (`.importlinter`, contract `core-not-legacy`; see
`docs/ARCHITECTURE.md` D14).

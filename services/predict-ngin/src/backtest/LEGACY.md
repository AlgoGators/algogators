# LEGACY: `src/backtest`

**What it was.** A generic backtest facade: engine re-exports, a cost model, Monte-Carlo
`simulation`, `walk_forward` validation, and a run `catalog` / `storage` / `comparison` for saved
backtest results.

**Era.** 2026-01-31 to 2026-03-06.

**Audit.** Its engines and cost model are the pre-audit stack. `docs/STRATEGY_AUDIT.md` sections 1-3 apply to every
result produced with it (section 2 also notes that the walk-forward did not retrain).

**Replaced by.** `predict_ngin.evaluation` (Sharpe, PSR/DSR, bootstrap, trial log), the research
ledger (`docs/RESEARCH_LEDGER.md`, `backtests/results.db`, `research/trials.csv`) and per-study
scripts in `scripts/research/`. `src/backtest_v3` is a separate package and is not covered here.

## Status

Kept for reference; not maintained. Do not build new work on it. It is kept, not deleted, so that
past results can be audited and reproduced. Its tests still run in `uv run pytest`.
`predict_ngin` must not import it (`.importlinter`, contract `core-not-legacy`; see
`docs/ARCHITECTURE.md` D14).

# LEGACY: `src/validation`

**What it was.** Data-quality checks and assertions (completeness, consistency, freshness) for the
early research pipelines.

**Era.** 2026-02-07.

**Audit.** These checks did not catch the problems listed in `docs/STRATEGY_AUDIT.md` section 1.

**Replaced by.** `src/data_quality` (token canonicalisation, tape checks) and the golden and data
tests under `tests/`.

## Status

Kept for reference; not maintained. Do not build new work on it. It is kept, not deleted, so that
past results can be audited and reproduced. Its tests still run in `uv run pytest`.
`predict_ngin` must not import it (`.importlinter`, contract `core-not-legacy`; see
`docs/ARCHITECTURE.md` D14).

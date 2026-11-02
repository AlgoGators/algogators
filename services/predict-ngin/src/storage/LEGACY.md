# LEGACY: `src/storage`

**What it was.** Storage wrappers: Parquet `TradeStore`/`PriceStore`, the SQLite
`PredictionMarketDB`, and liquidity-snapshot storage. Mostly re-exports of
`src/trading/data_modules`.

**Era.** 2026-02-07 to 2026-02-11.

**Audit.** The legacy price store booked every exit at the entry price
(`docs/WHALE_RETEST.md`). `docs/STRATEGY_AUDIT.md` section 1 covers the stored data.

**Replaced by.** `predict_ngin.tapes` and the tape stores under `src/data/`, plus the recorder's
own storage (`src/recorder`).

## Status

Kept for reference; not maintained. Do not build new work on it. It is kept, not deleted, so that
past results can be audited and reproduced. Its tests still run in `uv run pytest`.
`predict_ngin` must not import it (`.importlinter`, contract `core-not-legacy`; see
`docs/ARCHITECTURE.md` D14).

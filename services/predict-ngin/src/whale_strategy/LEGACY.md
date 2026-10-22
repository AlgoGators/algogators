# LEGACY: `src/whale_strategy`

**What it was.** The whale-following strategy: whale identification and scoring (`strategy`,
`whale_scoring`, `polymarket_whales`, `whale_surprise`, `confirmed_signals`), its config
(`whale_config`), data loader (`research_data_loader`), backtests (`backtest`,
`polymarket_realistic_backtest`, `whale_following_strategy`), and the 2026-10-12 re-test helpers
(`execution_model`, `favourite_baseline`, `retest_stats`).

**Era.** 2026-01-31 to 2026-02-14; re-test helpers added 2026-10-12.

**Audit.** `docs/STRATEGY_AUDIT.md` section 3: the +$725k / 79.8% headline was an artifact (invented exit prices, a
clip floor, always-buy). The re-test, `docs/WHALE_RETEST.md`, found 14 trades, +$116 on
$5k, DSR 0.16 at N=301 trials, and no capacity for $1k.

**Replaced by.** Nothing: the strategy was rejected. Fees in `execution_model` delegate to
`predict_ngin.costs.polymarket`. `retest_stats.deflated_sharpe_ratio` is a deliberate metrics
variant (ARCHITECTURE.md, "Deliberate variants (metrics)"), kept so the re-test reproduces.

## Status

Kept for reference; not maintained. Do not build new work on it. It is kept, not deleted, so that
past results can be audited and reproduced. Its tests still run in `uv run pytest`.
`predict_ngin` must not import it (`.importlinter`, contract `core-not-legacy`; see
`docs/ARCHITECTURE.md` D14).

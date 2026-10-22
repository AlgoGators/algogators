# LEGACY: `src/trading`

**What it was.** The general trading stack of the Feb-Mar 2026 era: a backtest engine and
strategy classes (`engine`, `strategy`, `strategies/`, `signals`, `momentum_signals*`,
`polymarket_backtest`, `risk`, `portfolio`, `reporting`, `attribution`, `parameter_tuning`), the
old data layer (`data_modules/`: Polymarket/Kalshi fetchers, `PriceStore`/`TradeStore`, SQLite DB,
cost model), the Kalshi-Polymarket cross-venue arbitrage backtest (`arbitrage/`), and a
whale-signal live/paper stack (`live/`, `paper_trading_execution/`, `execution/`).

**Era.** 2026-02-07 to 2026-03-06 (later edits were bug fixes and shims).

**Audit.** `docs/STRATEGY_AUDIT.md` invalidated its headline results: section 1 (data-level problems: mixed YES/NO
token prints, truncated tapes, hindsight universe, broken Kalshi timestamps), section 3
(liquid-markets backtest and CLOB simulator), and section 5 "Existing arb code audit"
(`cross_platform_price_store`, `arbitrage_strategy`, `cross_platform_backtest`, `market_matcher`).
The corrected arb re-run is `docs/ARB_BACKTEST_CORRECTED.md`; arb capacity is five
figures a year.

**Live order path disabled.** In `live/order_router.py`, `OrderRouter(dry_run=False)` raises
`LiveTradingDisabledError` (2026-10-15). The live Polymarket path was never verified end to end.
`scripts/live/run_live_strategy.py --live` was removed for the same reason. Dry-run still works.

**Replaced by.**
- `predict_ngin.costs` (fees), `predict_ngin.venues` (read clients), `predict_ngin.markets`,
  `predict_ngin.books` (order book), `predict_ngin.tapes`, `predict_ngin.evaluation`
  (Sharpe/DSR/bootstrap), `predict_ngin.capture` and `predict_ngin.paper`;
- `src/recorder` (L2 recorder) and the atlas collectors (`config/collectors.yaml`);
- per-study scripts in `scripts/research/`, logged to the research ledger.

**Not legacy inside this package** (current code that happens to live here):
- `arbitrage/fees.py`: a shim over `predict_ngin.costs` (D1, D3);
- `arbitrage/kalshi_tape.py`: the merged Kalshi trade-tape reader used by current research
  scripts (`tailcarry_data`, `sharp_*`, `postevent_convergence`, ...);
- `live/order_router.py` `OrderBook.to_order_book()`: adapter to `predict_ngin.books.OrderBook`
  (D11);
- `data_modules/fetcher.py`: uses `predict_ngin.markets` and `predict_ngin.venues` for token
  parsing and the CLOB URL.

Several research scripts still import `src.trading.arbitrage` and `src.trading.data_modules` to
reproduce published studies; they keep working.

## Status

Kept for reference; not maintained. Do not build new work on it. It is kept, not deleted, so that
past results can be audited and reproduced. Its tests still run in `uv run pytest`.
`predict_ngin` must not import it (`.importlinter`, contract `core-not-legacy`; see
`docs/ARCHITECTURE.md` D14).

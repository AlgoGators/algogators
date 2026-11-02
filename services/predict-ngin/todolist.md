# predict-ngin todolist

Tasks are ordered by priority within each section.
Status: [ ] todo  [x] done  [-] in progress  [~] blocked

---

## Live Strategy Execution

- [x] **`LiveTradeBuffer` class** — incremental real-time equivalent of batch `extract_confirmed_signals`
      - rolling `deque` per `(market_id, direction)` with configurable window
      - applies the 6-step confirmed-whale filter on each new trade
      - tracks per-(market, direction) cooldowns to suppress duplicate signals
      - returns signal dicts compatible with `PaperTrader.process_signal()`
      - file: `src/trading/live/live_trade_buffer.py`

- [x] **`run_live_strategy.py` orchestrator** — wires everything together end-to-end
      - warmup: load research trades → build whale set + winrates → build market liquidity map
      - live loop: poll data-api.polymarket.com → `LiveTradeBuffer` → `calculate_position_size` → `PaperTrader`
      - risk kill-switch: pause signal forwarding when drawdown > threshold
      - weekly whale-set refresh (re-calls build_whale_set, updates buffer in-place)
      - file: `scripts/live/run_live_strategy.py`

- [x] **`--replay` validation mode** — feed historical parquet trades through `LiveTradeBuffer` in
      chronological order to verify live signals match backtest before going live
      - `--replay` flag added to `run_live_strategy.py`
      - feeds all parquet trades chronologically through buffer, logs signals

- [x] **Fix `OrderRouter` authentication** — replaced broken HMAC signing with EIP-712
      - Polymarket CLOB uses EIP-712 typed data signing with wallet private key
      - replaced `ClobAuth` with `_build_clob_client()` using `py-clob-client`
      - exposes `POLYMARKET_PRIVATE_KEY` env var for wallet signing
      - file: `src/trading/live/order_router.py`

- [x] **`clobTokenId` lookup table** — map `conditionId` → `(yes_token_id, no_token_id)`
      - `get_token_ids()` cached in `_token_cache` dict in orchestrator
      - fetches from Gamma API `clobTokenIds` field on demand
      - used by `process_signal()` before placing orders

- [x] **Live position state persistence** — `StrategyState` serialized to JSON on every change
      - `_save_positions()` / `_load_positions()` write `state/positions.json`
      - loaded on startup to resume after restart
      - file: `scripts/live/run_live_strategy.py`

- [x] **Market resolution detection loop** — background thread detects resolved markets
      - `ResolutionMonitor` daemon thread polls Gamma every 300s
      - calls `on_resolved(mid, winner)` → `close_position()` when market closes
      - integrated into `run_live_strategy.py` orchestrator

- [x] **Pre-trade spread gate** — prevent entering illiquid markets with wide spreads
      - queries CLOB `/book` endpoint for live bid-ask before sizing
      - aborts entry if best_ask - best_bid > `--max-spread` (default 5¢)
      - `--max-spread 0` to disable; integrated into `process_signal()`

---

## Strategy & Backtesting

- [x] Remove `min_whale_wr` floor; replace with positive-expected-return filter
- [x] Capital-weighted surprise scoring (`weight = recency_decay * usd_amount`)
- [x] Probability-scaled position cap (`p * max_position_usd`)
- [x] Weekly whale set calculation with file-based cache (SHA-256 param hash)
- [x] Remove 10% capital reserve
- [x] Bayesian shrinkage on whale win rates
- [x] Recency decay on historical trade weighting
- [x] IC scoring (short-term CLOB price direction accuracy)
- [x] Walk-forward validation (`--walk-forward` flag)
- [x] Partial exit logic (50% at 40% gain)
- [x] QuantStats tearsheet generation
- [x] Enriched trade log (market title, URL, whale score, taker address, token side)
- [x] Experiment tracker (`src/backtest/catalog.py`, `storage.py`, auto-saves on each run)

---

## Live Monitoring

- [x] **`watch_whale_trades.py`** — polls data API, alerts on whale trades in unresolved markets
      - incremental research data refresh at startup (only new trades per market)
      - pre-loads all open conditionIds from Gamma (`closed=false`)
      - alerts only when market is unresolved; refreshes active-market set every 30 min
      - colour-coded console output with TIER-1/2/3 labels, market URL, win rate
      - optional JSONL log (`--log`)

---

## Data & Infrastructure

- [x] **Incremental resolutions update** — `data/poly_cat/resolutions.csv` goes stale; add a
      lightweight script to fetch newly resolved markets and append to the CSV
      - polls Gamma for markets with `closedTime > last_update`, deduplicates by conditionId
      - file: `scripts/data/update_resolutions.py`

- [ ] **Credentials & funded wallet** (operational, not code)
      - obtain `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE`
      - fund Polygon wallet with USDC
      - store in `.env` or cluster secret manager (never commit)

---

## Validation Checklist (before live capital)

- [ ] Replay mode produces same signals as backtest on same date range
- [ ] Paper trade for >= 4 weeks with Sharpe > 2 out-of-sample
- [ ] `OrderRouter` test order succeeds in dry-run against real API
- [ ] Position persistence survives process restart (open positions reloaded correctly)
- [ ] Resolution detection closes at least one position correctly end-to-end
- [ ] Drawdown kill-switch tested: pauses trading at configured threshold

# PMFIX — Polymarket YES/NO token normalisation, timestamps and data hygiene (2026-10-15)

Last revised: 2026-10-15

**Bottom line.** Every Polymarket trade/price source on disk mixes the two outcome tokens of a market into one
price column. Read naively, 12-90% of consecutive prints "jump" by more than 0.50. One canonical helper
(`src/data_quality/polymarket_tokens.py`) now maps every print to a YES-equivalent price and side. After mapping,
the jump rate falls to 0.002-0.07% per source, and most of what remains is genuine (resolution prints, tape gaps,
thin-book sweeps). The live readers are patched, the dead ones are guarded, and clean derived datasets are in
`data/polymarket_clean/`. `data/polymarket/trades.parquet` timestamps are wrong by up to 23 h; the true block times
are now in `ts_chain`. No research-ledger verdict changes, so there is no `ledger_entries/pmfix.yaml`.

## 1. Sources, token encoding, and mixing BEFORE → AFTER

"raw jumps" = share of consecutive same-market print pairs with |Δp| > 0.50, computed on the stored `price`.
"YES jumps" = the same on `yes_price`. In the "other" column, jumps at an extreme (≤0.02 / ≥0.98) and jumps after a
>6 h gap are removed. Scripts: `scripts/research/pmfix_measure.py` and `pmfix_build_clean.py`. CSVs:
`data/vendor/pmfix/rates_*.csv`.

| source | token identity | markets mixed | raw jumps | YES jumps (other) |
|---|---|---|---|---|
| `data/polymarket/trades.parquet` (293.3M OrderFilled rows, 223.5k markets) | `asset_id`, `nonusdc_side` token1/token2, maker/taker asset | 99.9% | **33.7%** (97% of markets) | **0.021%** (0.012%) |
| same, taker-order aggregate rows removed (= 170.25M rows, the quant.parquet set) | | 83.6% | 11.6% | 0.038% (0.020%) |
| `data/polymarket/orderfilled.parquet` | raw `maker_asset_id`/`taker_asset_id` + amounts | same rows as trades.parquet (raw vs processed agree on 299,990/300,000 sampled rows; 10 are unknown tokens) | | |
| `data/polymarket/quant.parquet` (170.3M) | **already YES-normalised**: `price` and directions are in token1 terms, `nonusdc_side` relabelled to token1, but `asset_id` is still the real token | n/a | low | n/a. **Do not re-normalise from asset_id** (it would flip twice). The helper detects the schema `quant`. |
| `data/polymarket/users.parquet` | **none** (market, user, role, price) | cannot tell | 0.36% (taker rows, first 4M) | unfixable without a tx-hash join to trades. Price use is guarded. |
| `data/polymarket/trades/trades_YYYY-MM.parquet` (36 files, ~200M rows) | `nonusdc_side` (no market for 0.001-0.6% of rows) | 95-100% | 9-40% per month | 0.009-0.9% (≤0.5%); ~0.02% from 2024-10 on |
| `data/by_category/*/trades_*.parquet` | `nonusdc_side` | 99-100% | 24-45% | 0.008-0.16% |
| `data/research/<Cat>/trades.parquet` == `data/pmxt/ticks/<Cat>_trades.parquet` (byte-identical) | Data API `asset`, `outcomeIndex`, `outcome`, `side` (taker side). Gen-1 rows (`nonusdc_side`, `timestamp=0`, 2.4M in Politics) are unusable | 96-100% | 19.6-28.0% | 0.004-0.067% (0.001-0.037%) |
| `data/polymarket/prices/prices_YYYY-MM.parquet` (CLOB history, one series per token, `outcome` YES/NO) | outcome label | 100% | 65-90% if `outcome` is ignored | ≤6e-7 |
| `data/by_category/*/prices_*`, `data/research/*/prices.parquet`, `data/pmxt/ohlcv` | YES rows only | 0% | ~0 | ~0 (research prices hold one market per category) |
| `data/vendor/tardis/trades` (per token `symbol`) | token id via `token_map.outcome_idx` | 48-57% when grouped by market | 13-16% by market (per-token series are clean) | 0.035-0.09% |
| `data/vendor/dune/pm_pair_hourly.parquet` | normalised to outcome 0 in Dune SQL, from **taker rows** | 0% | 0.078% hour-to-hour | see §5 |
| `data/vendor/{negrisk,predexon,probalytics,mm_rewards}` books | per token id | n/a | n/a | not price-series mixing |
| `data/vendor/telonex` | empty | | | |

## 2. Convention: verified, not assumed

`scripts/research/pmfix_verify_yes_index.py` → `data/vendor/pmfix/verify_yes_index.csv`. Sample: 50 random
resolved markets, seed 20261015 — 25 plain Yes/No, 15 neg-risk Yes/No, and 10 named-outcome markets (Up/Down,
teams, candidates).

| check | result |
|---|---|
| CLOB API `tokens[0].token_id == token1 == clobTokenIds[0]` and `tokens[1] == token2` | 50/50 |
| CLOB `tokens[0].outcome == answer1` ("Yes" / first named outcome) | 50/50 |
| CLOB `winner` flag == winner index from metadata `outcome_prices` | 50/50 |
| `nonusdc_side=='token1'` exactly when `asset_id == token1` (all prints of the 50 markets) | 50/50 |
| last on-chain prints of token1 near 1 iff token1 won | 48/50 |

The two misses are genuine. Market 571881 stopped trading in Aug 2025 and resolved in Dec 2025. Market 695393
(Solana range) traded at 0.88 minutes before it resolved NO. Metadata is also consistent across sources:
pmxt Gamma `clobTokenIds[0] == token1` on 3,724/3,724 markets, and research `markets_filtered.csv` 100% in all 8
categories. The merged TokenMap has 537,826 tokens and 0 conflicts.

**Rule:** outcome index 0 = `clobTokenIds[0]` = YES-equivalent. For named-outcome markets it is the *first* outcome
(`outcome0_label`), not literally "Yes".

## 3. Hand-checked fills (raw on-chain record)

`scripts/research/pmfix_hand_check.py` pulls 30 random prints. 20 are rows of `trades.parquet`; 10 are Data-API
ticks across 5 categories. For each, the script fetches the Polygon transaction receipt (free `polygon.drpc.org`;
publicnode refuses archive receipts) and decodes the `OrderFilled` log (topic1 = orderHash, data =
makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled, fee).

YES price and aggressor side were then worked out by hand from the raw amounts. Example: row 6397610 — the maker
pays 52.08048 USDC for 55.17 NO, so p = 0.944. The taker is the NegRisk exchange, so this is the taker-order row
and the aggressor BUYs NO at 0.944, which is SELL YES at 0.056.

The results are frozen in `tests/unit/fixtures/pmfix_hand_checked_fills.json` and asserted by
`tests/unit/test_pmfix_hand_checked.py`:

| | result |
|---|---|
| helper == hand value | 30/30 (processed rows *and* raw OrderFilled) |
| metadata token index == `nonusdc_side` / `outcomeIndex` | 30/30 |
| Data-API `side` equals the wallet's side in the taker-order log | yes |

The last row confirms that the Data-API `side` is the aggressor side.

Side rules confirmed:

- For a normal maker row, the aggressor is the taker and the aggressor side is `taker_direction`.
- For the taker order's aggregate row (`taker` = CTF Exchange `0x4bfb…` or NegRisk Exchange `0xc5d5…`), the
  `maker` field *is* the aggressor, so the side is `maker_direction`. These rows double-count volume; drop them
  (`is_taker_order_row`) for volume.
- Mint and merge matches (taker BUY YES against maker BUY NO) come out consistent once each side is flipped to
  YES terms.

## 4. The helper — `src/data_quality/polymarket_tokens.py`

The helper supports two `TokenMap` loaders:

- `TokenMap.load_default()` builds from `markets.parquet` plus the pmxt Gamma markets in about 1 s.
- `TokenMap.from_markets(df)` accepts three metadata layouts: token1/token2, clobTokenIds/outcomes, and
  yes_token/no_token.

Conditions without exactly two tokens are skipped and counted. Ids that conflict between sources are dropped,
never guessed.

`normalize_trades(df, token_map, schema)` auto-detects the source schema. The supported schemas are `onchain`,
`quant`, `onchain_raw`, `data_api`, `price_history`, `token_series`, `dune_market_trades`, and
`mixed_generations` (a research file holding both row generations). It adds these columns:

- `token_id`, `outcome_index`, `outcome_label`
- `yes_price` and `yes_side`
- `token_source` and `token_conflict`
- `is_taker_order_row`

Token identity comes, in priority order, from the metadata token id, then `outcomeIndex`, then `nonusdc_side`,
then the outcome label. Unknown identity gives `yes_price` = NaN; it is never silently treated as YES.

Neg-risk and multi-outcome events:

- Everything is per condition.
- `1-p` is only ever taken within one market. It is never taken across an event's candidates.
- `test_neg_risk_event_never_complemented_across_markets` shows that grouping an event as one series would
  fabricate jumps.

Other functions:

- `ensure_yes_trades(df)`: an idempotent drop-in for legacy frames. It rewrites `price` and the directions to YES
  terms, keeps `price_raw`, sets `outcome="YES"`, and warns when there is no token identity.
- `yes_price_frame`: helper for price-only frames.
- `mixing_stats`: the mixing and jump statistics used throughout this doc.
- `assert_single_token_series` and `warn_legacy_reader`: guards. `PMFIX_STRICT=1` turns warnings into errors.

`src/data_quality/polygon_blocks.py` provides `block_to_ts(blocks)`. It interpolates true block times and returns
NaN outside the anchors.

**Dune taker-row mislabel (finding from the uma agent).** The TAKER rows of mint/merge matches in Dune
`polymarket_polygon.market_trades` carry the wrong token and side (for example a fake 0.92 print against a true
0.08). The schema `dune_market_trades` therefore drops taker rows by default (`dune_taker_rows="drop"`, giving
`yes_price` NaN and `token_source="dune_taker_row_dropped"`). Taker rows are only used if the caller passes
`"keep"`. Test: `test_dune_market_trades_taker_rows_dropped`.

## 5. Clean datasets — `data/polymarket_clean/` (raw files untouched)

| path | content |
|---|---|
| `trades/part-NNNN.parquet` (17 GB, 293.3M rows) | One row per `trades.parquet` row. Columns: `block_number`, `ts_local` (the file's wrong timestamp), `ts_chain` (true block time), `timestamp_unreliable`, tx/order hash, `market_id`, `condition_id`, `token_id`, `outcome_index`, `outcome0_label`, `price_raw`, `yes_price`, `yes_side` (aggressor, YES terms), `is_taker_order_row`, USD/token amounts, `maker`, `taker`, `token_conflict` |
| `research_trades/<Cat>.parquet` | Data-API ticks with a real timestamp, plus YES columns. 0 conflicts and 0 unknown tokens across 3.5M rows |
| `prices/prices_YYYY-MM.parquet` (39 months) | One YES price per (market, minute): YES row, else 1 − NO; both token prices kept |
| `research_prices/`, `pmxt_ohlcv/` | Same layout as `prices/` |
| `tardis_trades/` | Per-token Tardis trades with market id, `yes_price`, `yes_side` |

**Timestamps** (`data/vendor/pmfix/post_ts_error_by_month.csv`):

- Anchors: blocks 35.8M–56.4M every 500 come from `scripts/research/pmfix_block_anchors.py`, which makes batched
  public-RPC calls at about 1 req/s → `data/vendor/pmfix/block_anchors_lo.parquet`. Blocks 56.4M–81.0M every 100
  come from the existing `alpha_decay` anchors. Every row of trades.parquet is covered (0% unreliable).
- Interpolation error, measured on held-out anchors:
  - low range: median 10 s, p99 76 s at double the spacing, so about half that as used;
  - high range: median 0.8 s, p99 32 s.
- Error of `ts_local` (`ts_chain − ts_local`):
  - 2022-11 to 2024-09 and 2025-02: within about ±7 min, with a mean of −20 s to −96 s;
  - 2024-10 to 2025-10 (except 2025-02): up to **+83,884 s (23 h)**, with monthly means of +2,000 to +17,000 s;
  - 2025-11 to 2025-12: correct.
- The monthly `data/polymarket/trades/trades_YYYY-MM.parquet` files carry **true** block times. They agree with
  `ts_chain` to 1.2–2.3 s MAE (11 s in 2023, where the anchors are sparser), compared with 64 s to 12,700 s MAE
  against `ts_local` (`post_monthly_files_ts.csv`). For sub-hour timing, use `ts_chain` or the monthly files —
  never `trades.parquet.timestamp`.

**Residual YES jumps** (`post_residual_jumps_sample.csv`, 25 random examples): these are not token flips. They
are sweeps of thin books inside one block (Δt = 0), news moves in illiquid markets (pope, airdrops, in-game
sports), and tape gaps.

**Dune `pm_pair_hourly` vs clean maker-row hourly YES** (`post_dune_vs_clean.csv`, 455,794 matched market-hours,
2024-05 to 2025-12):

| check | share of matched hours |
|---|---|
| close differs by > 0.20 | 0.004% |
| close differs by > 0.05 | 0.023% |
| Dune high above the local max by > 0.20 | 0 |
| Dune low below the local min by > 0.20 | 0 |

So the taker-row mislabel did not visibly contaminate this hourly table. Even so, future Dune pulls should use
maker rows for price/side/token, with taker rows for volume only.

## 6. Corrupt `prices_2025-03.parquet` (D6)

The file is 10.4 MB, against 172–195 MB for the neighbouring months. It starts with the `PAR1` magic but has no
footer, so this was a write truncated mid-file. About 6% of the data was written, and the footer (the schema and
row-group index) is missing. Pages could in principle be salvaged with a raw Thrift page decoder, but they would
only be a fragment of the month.

**It is not recoverable in a useful form. Rebuild it instead.** It came from the free CLOB `prices-history`
endpoint via `src/trading/data_modules/fetcher.py`, so re-fetch March 2025 for the same markets, and check the
fidelity the endpoint still serves for closed markets. Alternatively, derive minute YES prices for March 2025 from
`data/polymarket_clean/trades` (true timestamps).

The build logs the file as skipped (`data/vendor/pmfix/build_prices_log.csv`), so `polymarket_clean/prices` has
no 2025-03.

## 7. Readers: patched vs guarded

**Patched (now YES-normalised via the helper):**
- `src/backtest_v3/data/loader.py` `_normalize_to_yes` now delegates to the helper and adds `yes_price`.
- `src/backtest_v3/data/clob_book.py` takes the mid from `yes_price`. A raw tape that interleaves both tokens now
  raises `MixedTokenSeriesError` instead of being used silently.
- `src/whale_strategy/research_data_loader.py`: the price-history path normalises by `outcome`. The trades path
  was already correct.
- `src/trading/data_modules/parquet_store.py`: `TradeStore.load_trades(..., yes_normalize=True)` is the default.
  It adds `nonusdc_side` to the requested columns and keeps `price_raw`; `yes_normalize=False` returns raw rows.
  This fixes every TradeStore consumer: `run_all_strategies.py`, `run_momentum_backtest_from_trades.py`,
  `position_sizing_execution_research.py`, and `polymarket_whales.load_polymarket_trades` (parquet path).
- `src/trading/data_modules/trade_price_store.py`, `src/trading/momentum_signals_from_trades.py` (and through it
  `strategies_from_trades.py` and `nlp_correlation`), and `src/trading/execution.py`: each calls
  `ensure_yes_trades`, which is a no-op when TradeStore has already normalised.
- `scripts/backtest/simple_strategy_backtest.py` (pmxt ticks).
- `scripts/analysis/find_whale_opportunities.py`: live Data-API trades now flip NO prints to YES. Before, the
  price was raw and the side was not flipped.

**Guarded — normalised where trivial, plus a `LegacyPolymarketReaderWarning`. Results stay invalid for other
reasons (invented exits/fills, STRATEGY_AUDIT §3):**
- `src/trading/strategies/{comprehensive_realistic_backtest,v2_strategies_realistic,v2_strategies,multi_strategy_backtest,parameter_sensitivity_test}.py`

**Guarded with a warning only.** These read prices from the SQLite `polymarket_trades` table without
`nonusdc_side`; the DB is not on disk. The last module reads per-market tick dirs that do not exist.
- `src/research/ensemble_research.load_trade_data`
- `src/research/regime_detection.load_trade_data`
- `src/research/whale_features.load_all_trades`
- `src/whale_strategy/correlation.load_whale_trades`
- `src/whale_strategy/polymarket_whales._load_trades_from_sqlite`
- `src/trading/data_modules/tick_store.load_ticks`

**Already correct and left alone:**
- `PriceStore`, `ClobPriceStore`, `pair_observations` and `cross_platform_price_store` (all filter
  `outcome='YES'`)
- `calendar_cascade`, `run_stat_arb_backtest`, `latency_arb_backtest` and `systematic_lag_analysis` (all
  normalise via `outcomeIndex`)
- the polyx / fin_D / alpha_decay / impact / tardis research scripts (per-token)
- `dune_poly_trades` (volume only)

**Tests:**
- New: `tests/unit/test_pmfix_tokens.py` (27), `test_pmfix_readers.py` (8), and `test_pmfix_hand_checked.py`
  (31).
- Full suite (`pytest tests --continue-on-collection-errors`): **680 passed, 1 skipped, 0 failed**. The baseline
  was 604 passed. The same 25 pre-existing collection errors remain (missing `trading.engine` and similar
  modules), and none of them is touched by this change.

## 8. Research-ledger impact (task 5)

Of the 115 ledger entries, 26 touch Polymarket. **No verdict changes.**

- **Already invalid or rejected partly because of token mixing:**
  - `legacy_liquid_markets_suite` is invalid.
  - `v3_round_price_lp` is rejected and stays rejected after the ba8d1ca fix.
  - `v3_hazard_ladder`: its 3 trades were flips, and it has 0 trades after the fix.
  - `v3_calendar_butterfly` and `v3_uma_dispute_discount` are unchanged.
- **Used normalised or per-token data (unaffected):**
  - whale retest / `whale_follow_category`;
  - all UMA entries (Dune maker rows only);
  - `quiver_pm_large_trades`;
  - `latency_arb_single_venue` (already invalid on other grounds);
  - the fin_D, polyx and crypto up/down entries;
  - all Tardis/Predexon/Probalytics book-based market-making and cross-venue entries;
  - the `xvenue_arb_slow_hourly` prices, which are YES-filtered.
- **Used Dune taker rows (possible mislabel exposure):**
  - `arb_alpha_decay_slow_taker`: the 2026 prints come from `alpha_decay_fetch_2026.py`, and the NegRisk baskets
    from `alpha_decay_negrisk.py`.
  - `polymarket_yes_no_intramarket_arb`: its NegRisk history comes from `negrisk_dune.py`.
  - Any bias inflates the opportunity, so both rejections stand. Two knowledge numbers could shift: "fee explains
    86% of the drop" and "NegRisk $30–85 per $1M, not decaying".
  - **Recommendation:** re-pull those two queries on maker rows only. This is cheap, at about 300 Dune credits or
    fewer if aggregated in SQL. Low priority.
- **Timestamp drift:** the only study that used `trades.parquet` times (alpha decay) had already repaired them.
- **Ledger text to fix (not in my namespace):** `docs/RESEARCH_LEDGER.md` lines ~134–135 advise re-tests on Dune
  `market_trades` with `is_taker_side=true`. Per the mislabel finding, this should say maker rows only for
  price/side/token, with taker rows for volume only.

## 9. Open issues

- `users.parquet` has no token identity. Joining it to `polymarket_clean/trades` on (tx hash, user) would fix it;
  this is not done.
- `quant.parquet`'s `asset_id` contradicts its `nonusdc_side`. The helper handles this, but any external
  re-normalisation from `asset_id` would double-flip.
- The SQLite-path legacy modules were only guarded. If the DB is ever rebuilt, add `nonusdc_side` to their
  SELECTs and call `ensure_yes_trades`.
- `scripts/data/merge_trades.py` `TRADESTORE_COLS` drops `nonusdc_side`. Re-running it would erase token identity.
  It was left unpatched (data writer) but is flagged here.
- `src/trading/live/clob_streamer.py` documents `0=NO, 1=YES`, the reverse of the verified convention. It is
  live code outside my scope; check it before any live use.
- Interpolated `ts_chain` error is a few seconds (p99 under about 40 s before block 56.4M). That is fine for
  minute-scale work; for second-level precision, fetch exact blocks.

## 10. Blockers & how to unblock

- **None paid.** Everything here used free sources: the public Polygon RPC (`polygon.drpc.org` for receipts,
  publicnode for block headers) and the public CLOB API.
- publicnode now needs a free personal token for archive receipts. drpc's free tier works but refuses large
  batches.
- To restore 2025-03 prices: re-run the CLOB `prices-history` fetch (free, about 400 markets; check the minute
  fidelity served for closed markets), or build minute YES prices from `polymarket_clean/trades`.

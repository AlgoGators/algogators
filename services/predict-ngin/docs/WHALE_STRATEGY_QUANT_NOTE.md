# Whale-Following Strategy: Quant Operations Note

**Strategy:** Informed-flow following on Polymarket binary prediction markets
**Instrument:** Binary YES/NO tokens (price ∈ [0, 1], resolution ∈ {0, 1})
**Backtest period:** Rolling train/test from historical Polymarket trade data
**Capital base:** $1,000,000

---

## 1. Data Pipeline

### 1.1 Raw Inputs

| Source | Format | Content |
|--------|--------|---------|
| `data/research/{category}/trades.parquet` | Parquet | Per-trade: proxyWallet, side, conditionId, price, size, timestamp |
| `data/research/{category}/markets_filtered.csv` | CSV | Market metadata: endDateIso, endDate, closedTime, volumeClob, category |
| `data/research/resolutions.csv` | CSV | `{market_id, winner}` — 2,247 resolved markets |
| `data/poly_cat/resolutions.csv` | CSV | `{market_id, winner}` — 51,027 resolved markets (merged at runtime) |

**Price data:** No historical CLOB price snapshots exist. `ResearchPriceStore` derives
prices from trade tick data: each trade's `(timestamp, price)` pair forms the price
series for its `conditionId`. This provides last-trade price at any historical timestamp,
covering 2,413 of 2,500 research markets (coverage improves toward 2024–2026).

### 1.2 Resolution Merging

The strategy merges two resolution sources at startup:

```
resolution_winners = poly_cat/resolutions.csv   (51k markets, broad coverage)
resolution_winners.update(research/resolutions.csv)  # research takes precedence
```

`data/poly_cat` provides 23× more resolution data than `data/research` alone, enabling
far richer whale performance scoring. Research values take precedence where they overlap.

### 1.3 Look-Ahead Prevention

Resolution data is globally available for all markets. To prevent using future outcomes
in training, each monthly whale set is built with a cutoff-filtered resolution set:

```
cutoff = start_of_test_month - 1 day
available_resolutions = { mid: winner
    for mid, winner in resolutions
    if resolution_date(mid) <= cutoff }
```

Resolution dates come from `closedTime` (actual close timestamp from markets metadata).
Markets without a confirmed `closedTime` are excluded from resolution data, even if
a winner is recorded — absence of confirmation means the outcome was not yet public.

### 1.4 Train / Test Split

- Default `train_ratio = 0.30`: the first 30th percentile of trade datetimes is the
  training period; the remainder is the test (out-of-sample) period.
- For rolling rebalancing, the effective training window grows each month (all historical
  data up to the prior month end).
- With the current dataset (~1.15M trades, 2022–2026), the split falls at roughly
  May 2025, giving ~10 rolling test months.

### 1.5 Market Liquidity Filter

The signal filter uses `volumeClob` (cumulative CLOB trading volume) as the market
quality proxy, in preference to `liquidityNum` (point-in-time AMM liquidity). This is
because `liquidityNum = 0` for all resolved markets in the dataset snapshot, whereas
`volumeClob` survives market close and reflects the genuine historical trading activity.
Markets with `volumeClob < $10,000` are excluded as micro-markets.

---

## 2. Whale Identification

### 2.1 Universe Definition

A trader qualifies as a **volume whale** in market M at trade t if their cumulative
USD volume in M (measured in all trades strictly before t) is at or above the 95th
percentile of all traders' final cumulative volumes in M. At least 5 distinct traders
must be active in the market for the percentile to be computed.

This is a causal criterion: it uses only data available before the trade, implemented
via running cumulative sums.

### 2.2 Performance Qualification

Volume whales are further filtered to those with demonstrated edge. For each whale,
the following are computed over all resolved trades in the training window:

**Recency-decay weighting**

Each historical trade t is weighted by:

```
weight(t) = exp( -ln(2) / halflife * days_ago(t, cutoff) )
halflife = 90 days  (one quarter)
```

Trades from 90 days ago receive weight 0.5; trades from 180 days ago receive 0.25.
This down-weights stale performance without discarding it entirely.

**Bayesian shrinkage of win rate**

Raw win rate from a small sample is subject to winner's curse: a true-50% trader
has a 17% probability of showing ≥8/10 wins by chance. To correct this, a
Beta-Binomial prior with α=β=2 (prior mean=0.50, effective sample size=4) is applied:

```
shrunk_WR = (weighted_wins + α) / (effective_N + α + β)
          = (weighted_wins + 2) / (effective_N + 4)
```

This pulls extreme estimates toward 0.50 in proportion to sample size. A whale with
40 weighted trades and 60% WR is shrunk to 59.1%; with 10 trades it is shrunk to 55%.

**Qualification criteria:**

- `shrunk_WR >= 0.50`
- `surprise_WR > 0` where `surprise_WR = actual_WR - expected_WR`
  - `expected_WR = price` for BUY trades (market-implied probability of YES)
  - `expected_WR = 1 - price` for SELL trades (market-implied probability of NO)
- Minimum 10 resolved trades

Whales qualifying on all three criteria form the active whale set for the following
test month.

### 2.3 Whale Scoring

Each qualifying whale receives a composite score W ∈ [0, 10]:

```
surprise_score = clip((surprise_WR / 0.15) * 5 + 5, 0, 10)
roi_score      = clip(avg_ROI * 10, 0, 10)
sharpe_score   = clip(sharpe * 2, 0, 10)

W_surprise = 0.50 * surprise_score + 0.30 * roi_score + 0.20 * sharpe_score
```

Where:
- `ROI` for BUY: `(resolution - entry_price) / entry_price`
- `ROI` for SELL: `(entry_price - resolution) / (1 - entry_price)`
- Sharpe uses population std (ddof=0)

**Information Coefficient (IC) blend**

After all monthly whale sets are computed, a separate IC score is computed for each
whale using their training-period trades and the trade-derived price store:

```
IC = fraction of trades where price moved in the predicted direction by t+7 days
   BUY: correct if price(t+7) > price(t_entry)
   SELL: correct if price(t+7) < price(t_entry)
IC ∈ [0, 1]; IC = 0.50 is random
```

The IC is normalized to [0, 10] and blended into the final score:

```
W_final = 0.80 * W_surprise + 0.20 * (IC * 10)
```

IC provides signal independent of binary resolution: a whale can have positive
surprise WR but neutral IC (lucky on outcomes but not directionally informed), or
high IC but average WR (good directional timing, but outcomes are close calls).

In the current backtest, IC was computed for **162 of 305 qualifying whales** — the
remainder lacked sufficient price history within the 7-day horizon window.

### 2.4 Rolling Monthly Rebalancing

Whale sets are recomputed at the start of each calendar month using all historical
data through the end of the prior month. This simulates a live deployment where the
strategy re-scores the whale universe monthly without look-ahead.

At each test trade's datetime, the strategy checks whether the whale is in the active
set for that calendar month. If not, the signal is skipped.

---

## 3. Signal Generation

### 3.1 Raw Signals

Every maker-side trade in the test period by an active whale constitutes a candidate
signal. A `WhaleSignal` carries:

- `market_id`, `category`, `whale_address`
- `side` ∈ {BUY, SELL} (direction of the maker)
- `price` (YES price at trade time)
- `size_usd` (dollar value of the trade)
- `score` W_final
- `datetime`
- `historical_winrate` = shrunk_WR from qualification

### 3.2 Signal Filters (Entry Guards)

Signals are rejected at entry if any of the following conditions are met:

**Already resolved**
`resolution_date(market) <= current_date` — market has closed, no entry possible.

**Near-resolution price filter**
`YES price > 0.98` — at YES > 0.98, the NO token costs < $0.02 and the maximum
payout is < $0.02 per dollar of NO exposure. This is economically unviable regardless
of whale conviction (dust-exclusion principle).

**Scheduled TTR filter** *(min_ttr_entry_days = 3)*
```
days_to_close = endDateIso(market) - current_date
if days_to_close < 3: skip
```
Uses only the published scheduled close date (`endDateIso`/`endDate`), NOT the actual
`closedTime` (which would be look-ahead biased). A 3-day minimum avoids entries in
last-minute noise trades that lack genuine informed conviction.

**Multi-whale confirmation** *(min_confirmation_whales = 1, disabled by default)*
If enabled: within a rolling 7-day window, ≥N distinct qualifying whales must have
traded the same market in the same direction. Filters isolated single-whale signals.

**Existing position — same side**
If a position already exists on the same side, the duplicate signal is skipped.

**Existing position — opposite side ("last signal wins")**
If a position exists on the opposite side, it is closed at the current CLOB price and
a new position is opened in the new direction.

---

## 4. Position Sizing

Position size is determined by fractional Kelly with multiple hard constraints,
applied in sequence. All constraints bind independently; the final size is the
minimum of all applicable limits.

### 4.1 Win Probability Estimate

The strategy does not have a structural model of market outcomes. Win probability is
estimated as a convex blend of the whale's historical accuracy and the market price:

```
p_win = (W/10) * shrunk_WR + (1 - W/10) * market_price
```

At W=10 (maximum conviction), p_win = shrunk_WR (trust whale entirely).
At W=0, p_win = market_price (defer to market). Most whales score W ≈ 7–9.

### 4.2 Fractional Kelly

```
For BUY YES at price p:
  b = (1 - p) / p          (net odds: win (1-p), lose p)
  f* = (p_win * b - (1 - p_win)) / b
  kelly = 0.25 * f*         (quarter-Kelly)

For SELL (buy NO) at YES price p:
  b = p / (1 - p)          (net odds on NO: win p, lose (1-p))
```

Quarter-Kelly is used throughout. Full Kelly maximises geometric growth in theory
but requires an exact model; quarter-Kelly targets ~6.25% of the growth rate with
significantly lower variance.

### 4.3 Hard Caps (applied after Kelly)

```
size = min(
  kelly_fraction * available_capital,
  $50,000,                          # absolute position cap
  market_liquidity * 3%,            # max 3% of market liquidity
  tier_max_position,                # TIER_1=$50k, TIER_2=$30k, TIER_3=$20k
)
```

Tier classification by W:
- TIER_1: W ≥ 8.0 → target 40% of capital, max $50k per position
- TIER_2: 7.0 ≤ W < 8.0 → target 35%, max $30k
- TIER_3: 6.0 ≤ W < 7.0 → target 15%, max $20k

### 4.4 Portfolio Concentration Limits

Applied sequentially after hard caps:

| Limit | Threshold | Action |
|-------|-----------|--------|
| Min position | < $5,000 | Skip entirely |
| Category | ≥ 30% of capital | Halve size |
| Category | ≥ 36% of capital | Skip entirely |
| Market | ≥ 8% of capital | Skip entirely |
| Whale | ≥ 20% of capital | Halve size |
| Tier overallocation | > target × 1.1 | Scale down proportionally |
| Cash reserve | — | 10% of capital always reserved |

### 4.5 Notional Cap (Leverage Control)

Binary markets allow extreme leverage at near-zero prices. Buying NO at $0.02
with $50k entry = 2.5M tokens, each paying $1 if NO wins = $2.45M payout.

The notional cap bounds the maximum possible payout per position:

```
For SELL (buy NO at price 1-p):
  max_size = $200,000 * (1 - p)    (payout bounded at $200k)

For BUY YES at price p:
  max_size = $200,000 * p          (payout bounded at $200k)
```

This cap is non-binding at mid-range prices (0.30–0.70) and binds only when
position is near extremes.

---

## 5. Position Management & Exits

### 5.1 Partial Exit on Gains

Once per position, when unrealized gain exceeds 40% of entry cost, 50% of the
position is closed at the current trade-derived price:

```
For BUY:  gain% = (current_price - entry_price) / entry_price
For SELL: gain% = (NO_price_now - entry_NO_price) / entry_NO_price

if gain% >= 40% and not partial_exit_done:
  close 50% at current price; mark partial_exit_done = True
  remaining 50% continues to full resolution
```

Partial exits are now functional — 19 of 61 trades in the current backtest are
partial-exit records (recorded separately with reason=PARTIAL_EXIT).

Rationale: locks in profit on large unrealized winners and hedges against mean-reversion,
while preserving upside exposure on the remaining position.

### 5.2 Resolution Exit (Primary)

When a market resolves:
- `exit_price = 1.0` if position was correct (BUY YES and YES won; SELL and NO won)
- `exit_price = 0.0` if position was incorrect

```
For BUY:  PnL = (exit_price - entry_price) * (size / entry_price)
For SELL: PnL = (exit_price - entry_NO_price) * (size / entry_NO_price)
          where entry_NO_price = 1 - entry_YES_price
```

### 5.3 Market-Close Exit (CLOB)

When a market closes without a confirmed resolution (or at forced-close triggers),
the position is closed at the last known trade price for that market:

- `exit_price = last_trade_price` (from trade-derived price store)
- Falls back to `entry_price` if no price data is available

Triggers:
- `market_close_date <= current_date` (scheduled close has passed)
- `max_hold_days > 0` and `hold_days >= max_hold_days` (optional force-close)
- End of backtest (remaining open positions closed at final known price)

### 5.4 Conflicting Signal Exit

If a new qualifying whale signal arrives for the same market in the opposite direction,
the existing position is closed at the current known price and the new position is
opened (last-signal-wins logic).

### 5.5 Cost Model

All PnL is reduced by a flat 3% cost factor (net_pnl = gross_pnl × 0.97),
covering estimated spread, slippage, and platform fees.

---

## 6. Portfolio-Level Risk Management

### 6.1 Capital Allocation Summary

| Constraint | Limit |
|------------|-------|
| Cash reserve (always idle) | 10% of capital |
| Max deployed per position | 5% of capital |
| Max deployed per market | 8% of capital |
| Max deployed per whale | 20% of capital |
| Max deployed per category | 30% of capital |
| Max single position (USD) | $50,000 |
| Max position notional payout | $200,000 |

### 6.2 Whale Tier Targets

Capital is allocated preferentially to higher-scored whales:

| Tier | Score | Target Allocation | Max Position |
|------|-------|-------------------|-------------|
| TIER_1 | W ≥ 8.0 | 40% of capital | $50,000 |
| TIER_2 | 7.0 ≤ W < 8.0 | 35% of capital | $30,000 |
| TIER_3 | 6.0 ≤ W < 7.0 | 15% of capital | $20,000 |

Tier overallocation is corrected by scaling down new positions proportionally.

---

## 7. Backtest Mechanics

### 7.1 Simulation Loop

The backtest iterates over unique trading dates (normalised to midnight) in ascending
order. On each date:

1. **Close positions** — check each open position for max-hold expiry, resolution, or market-close trigger
2. **Partial exits** — for remaining open positions, check unrealized gain against threshold using trade-derived price
3. **Process signals** — for each signal with datetime on the current date:
   a. Validate whale is active in the current month's set
   b. Apply all entry guards (price, TTR, confirmation, resolution)
   c. Calculate position size; skip if below minimum
   d. Open position; update all exposure counters

### 7.2 PnL Attribution

The equity curve is built from daily realised PnL (PnL booked on exit_date):

```python
daily_pnl = trades_df.groupby(exit_date)["net_pnl"].sum()
equity = starting_capital + daily_pnl.cumsum()
daily_returns = equity.pct_change()
sharpe = (daily_returns.mean() / daily_returns.std()) * sqrt(252)
```

Open positions at backtest end are closed at the last available trade-derived price,
or at entry_price if no price data exists for that market.

### 7.3 Walk-Forward Validation

To verify that performance is stable across time and not concentrated in a single
lucky window, the strategy supports rolling walk-forward validation:

- Default: 12-month window, 7-day step, 6-month minimum training
- Each fold is a fully independent backtest with its own train/test split
- Results cached by MD5(fold_start + test_start + test_end + config_hash + categories)
- A robust strategy should have the majority of folds profitable with consistent Sharpe

---

## 8. Backtest Results (Out-of-Sample)

**Period:** May 2025 – March 2026 (~10 months)
**Data:** `data/research` trades + `data/poly_cat` resolutions (51k markets)
**QuantStats report:** `data/output/whale_following/quantstats_walk_forward.html`

### 8.1 Summary Statistics

| Metric | Value |
|--------|-------|
| Total trades (incl. partial exits) | 61 |
| Unique positions | ~42 |
| Win rate | 86.9% |
| Net PnL | $4,135,099 |
| ROI (on $1M capital) | 413.5% |
| CAGR | 307% |
| Sharpe (annualised) | 3.76 |
| Max drawdown | -1.1% |
| Avg win | 4.71% daily equity |
| Avg loss | -0.29% daily equity |
| Whales followed | 305 |
| Signals generated | 1,617 |
| IC-scored whales | 162 / 305 |

### 8.2 Direction Breakdown

| Direction | Trades | Notes |
|-----------|--------|-------|
| SELL (long NO) | ~57 | Primary signal: buying NO at high YES prices |
| BUY (long YES) | ~4 | Rare; requires whale BUY conviction at low prices |

### 8.3 Exit Type Breakdown

| Exit Type | Count | Description |
|-----------|-------|-------------|
| Resolution | ~42 | Market resolved YES or NO — primary PnL source |
| PARTIAL_EXIT | 19 | 50% close at 40%+ gain threshold |

### 8.4 Entry Price Distribution

The strategy is concentrated at high YES prices (SELL direction):

| YES Price Range | Characteristic |
|----------------|----------------|
| 0.90 – 0.98 | Core zone: near-certain YES, buying NO |
| 0.50 – 0.70 | Rare BUY entries at genuine uncertainty |

Mean entry YES price: ~0.91 — the strategy is predominantly buying NO tokens
on markets priced at 90%+ YES probability.

---

## 9. Critical Analysis and Limitations

### 9.1 The Strategy Is Primarily a NO-Buying Strategy

~94% of trades are SELL direction (buying NO tokens) at high YES prices. The strategy
is not a general whale-following strategy — it is effectively a bet that Polymarket
**overprices near-certain events** at the 85–98% YES probability level.

If whales are genuinely informed, they are identifying events where the market
consensus is too bullish. Alternatively, if Polymarket markets are well-calibrated,
the strategy profits from a structural feature of CLOB microstructure at extreme prices
(wide effective spread, thin NO-side liquidity).

### 9.2 Extreme Category Concentration

The strategy generates signals predominantly in **Geopolitics**. A regime change in
geopolitical market dynamics (better calibration by market makers, or fewer
near-certain events with residual uncertainty) would materially reduce signal count.

### 9.3 Trade Count and Statistical Significance

61 trades over 10 months (including 19 partial exits). For full positions only (~42
resolved trades), the 87% win rate has a 95% confidence interval of approximately
[73%, 95%]. The sample is insufficient to distinguish a true 95% win rate from an 80%
true win rate with statistical confidence.

The skewed win/loss ratio (avg win ≈ 16× avg loss) means PnL is robust to the exact
win rate: even at 70% WR, the strategy would be profitable.

### 9.4 Price Data Is Trade-Derived

Historical CLOB order book snapshots do not exist in this dataset. All price lookups
(partial exits, IC computation, conflicting-signal exits) use the last trade price
prior to the query timestamp. This introduces three approximation errors:

1. **Partial exit prices** may not reflect the true mid-price at the moment of the
   hypothetical 40% gain — last-trade price can lag the true CLOB mid by hours.
2. **IC computation** is based on trade prices, which include the bid-ask spread's
   direction bias (BUY trades execute at the ask; SELL at the bid), creating systematic
   IC inflation for the direction of the trade.
3. **Markets with sparse trading** have stale price series; some markets have only a
   few ticks per day.

For a production implementation, real-time CLOB WebSocket prices should replace
trade-derived prices.

### 9.5 Execution Assumptions

The backtest assumes:
- Instantaneous fill at the whale's trade price (no additional slippage vs. whale)
- 3% flat cost covers all spread, slippage, and fees (may underestimate at thin markets)
- Trade-derived prices available for partial exits (coverage: ~96% of backtest markets)

In practice, following a whale trade will incur additional slippage from moving the
market after the whale has already moved it. The whale's edge partially derives from
being first — the follower captures a degraded version of the same signal.

### 9.6 Walk-Forward Gap (2024 H1–H2)

Walk-forward folds with test windows in mid-2023 through mid-2024 show "No closed
trades." This reflects a genuine data characteristic: extremely sparse market
resolutions in that period (1–14 markets per month resolving in the relevant
categories). The gap is not a model failure — it reflects a period where the strategy
had no qualifying opportunities that completed within the test window.

---

## 10. Parameters Reference

| Parameter | Value | Derivation |
|-----------|-------|-----------|
| `volume_percentile` | 95.0 | Top 5% of traders by volume in each market |
| `min_whale_wr` | 0.50 | Must beat random prediction after shrinkage |
| `require_positive_surprise` | True | Must beat market-implied probability |
| `min_trades_for_surprise` | 10 | Minimum resolved trades for scoring |
| `bayes_prior_alpha/beta` | 2.0 / 2.0 | Prior mean=0.50, effective sample=4; from winner's curse analysis |
| `recency_halflife_days` | 90 | One quarter; balances stability vs. adaptation |
| `ic_horizon_days` | 7 | Short-term price direction horizon (standard in microstructure) |
| `ic_score_weight` | 0.20 | 20% IC, 80% surprise score; IC is secondary signal |
| `max_entry_yes_price` | 0.98 | Dust exclusion: NO token < $0.02 per dollar is unviable |
| `min_ttr_entry_days` | 3 | Minimum conviction window; avoids last-minute noise |
| `kelly_multiplier` | 0.25 | Quarter-Kelly; ~6.25% of full-Kelly growth with lower variance |
| `partial_exit_gain_threshold` | 0.40 | 40% gain triggers partial lock-in |
| `partial_exit_fraction` | 0.50 | Close half, hold half to resolution |
| `max_notional_usd` | 200,000 | Payout cap; binds below p≈0.25 for standard position sizes |
| `min_reserve_pct` | 0.10 | 10% cash reserve always idle |
| `max_category_pct` | 0.30 | 30% category concentration limit |

## 11. Design Principles and Bias Controls

| Principle | Implementation |
|-----------|---------------|
| **No look-ahead in whale scoring** | Resolutions filtered to `closedTime <= cutoff` before training |
| **No look-ahead in TTR filter** | Uses `endDateIso`/`endDate` (published schedule), never `closedTime` |
| **No parameter fitting to backtest** | All thresholds derived from first principles (Kelly, dust-exclusion, Bayesian priors) |
| **Winner's curse correction** | Bayesian Beta-Binomial shrinkage pulls WR toward 0.50 |
| **Recency adaptation** | Exponential decay (halflife=90d) reduces weight of stale performance |
| **Leverage control** | Notional cap prevents large payout exposure at extreme prices |
| **Liquidity proxy robustness** | `volumeClob` used over `liquidityNum` (survives market resolution) |
| **Resolution coverage** | `poly_cat` (51k) merged with `research` (2.2k) for whale scoring |

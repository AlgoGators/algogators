# Whale-Following Re-test (2026-10-12)

Last revised: 2026-10-14

Reproduce with `venv/Scripts/python.exe scripts/backtest/whale_retest.py --draws 200` (about 12 minutes).
Raw outputs are in `data/output/whale_retest/` (`whale_retest_results.json`, `whale_retest_summary.csv`, and `*_positions.csv`).

## Verdict

1. **The whale signal has no demonstrated edge over the favourite baseline once you control for which markets the whale picked.**
   - Against a baseline that buys favourites at random across the whole eligible universe, whale positions earn about 2.4–3.3 pp more per position after price-bucket stratification. The 95% CIs only just include 0.
   - Against a baseline that buys the favourite at a random time in the same markets the whale traded, the whale does worse:
     - −5.0 pp raw, with a CI of [−8.0, −1.9].
     - −2.2 pp stratified, with a CI of [−5.1, +0.4].
   - Whatever information exists sits in market selection. Whale timing adds nothing, and is probably worse than a random entry.
2. **The result is not statistically meaningful after selection.**
   - The realistic run has 14 positions in 9 months.
   - Its daily SR is 0.11 against an SR0 of 0.17 implied by about 301 trials, giving **DSR 0.16**.
   - The original model's DSR of 0.92 comes from optimistic fills: 100% hit rate, whale-price entry, and a 3% haircut that also shrinks losses.
3. **It cannot absorb $1k of capital.**
   - The strategy opens about 1.5 positions a month, each held 1–3 days.
   - At a $250 max position, average deployed capital is about $95.
   - Raising the max position to $1k only lifts average deployment to about $290; at $2.5k it is about $580.
   - The binding constraint is signal frequency, not volume. The median trailing-24h volume at entry is about $41k, so a 5% cap allows about $2k.

**Recommendation:** do not allocate capital. Treat whale-following as, at most, a market-selection filter to be re-tested on a broader, non-hindsight universe.

## Results ($5,000 capital, $250 max position, size ≤ 5% of trailing-24h volume)

Test window: 2025-05-15 → 2026-02-14, which is 276 calendar days, all of them included in the daily Sharpe.

| Run | Trades | PnL $ | RoC % | Hit | Median trade $ | Mean ret/cost % | Fees $ | Daily SR | Ann. SR | PSR | DSR (N=301) | Avg / peak deployed $ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| original (whale price, 0 latency, ×0.97) | 22 | 280.4 | 5.61 | 100% | 8.72 | 5.10 | 0 | 0.214 | 4.09 | 1.00 | 0.917 | 112 / 750 |
| **realistic** (next print +5s, +1c, taker fee) | 14 | 115.6 | 2.31 | 85.7% | 11.95 | 3.30 | 5.2 | 0.110 | 2.10 | 0.95 | **0.164** | 95 / 750 |
| realistic, max pos $1k | 14 | 432.9 | 8.66 | 85.7% | 47.81 | 3.29 | 19.2 | 0.103 | 1.97 | 0.94 | 0.141 | 292 / 2,321 |
| realistic, max pos $2.5k, cap $25k | 14 | 899.9 | 3.60 | 92.9% | 80.58 | 3.30 | 46.2 | 0.091 | 1.74 | 0.91 | 0.105 | 581 / 5,000 |
| baseline: random favourite print per market (mean of 200 draws) | 528 | −290.7 (p5 −2,052 / p95 +1,341) | −5.81 | 88.8% | 5.90 | −0.55 | 300 | −0.002 | −0.04 | 0.48 | n/a | 2,804 / 5,000 |
| baseline, prints ≥ $1k only (200 draws) | 361 | −1,676 | −33.5 | 88.7% | 8.05 | −1.62 | 262 | −0.046 | −0.89 | 0.23 | n/a | 3,324 / 5,000 |
| baseline, every eligible print | 404 | −832.9 | −16.7 | 82.9% | 5.64 | −2.47 | 348 | −0.021 | −0.40 | 0.36 | n/a | 4,999 / 5,000 |
| baseline, same markets as whale (200 draws) | 9.6 | 182.0 | 3.64 | 98.8% | 13.56 | 8.37 | 2.0 | 0.138 | 2.64 | 1.00 | n/a | 38 / 375 |
| legacy reference ($1M, original fill, lifetime-volume Kelly) | 9 | 27,410 | 2.74 | 100% | 1,767 | 6.35 | 0 | 0.117 | 2.24 | 1.00 | 0.011 | 3,272 / 90,507 |

How to read the table:
- **Hit rate is meaningless here.** Every run, including the random baselines, wins 83–99% of the time because the gates only allow buying favourites (YES ≥ 0.80, or NO ≥ 0.80 via YES ≤ 0.20; five categories use 0.70/0.30).
- **Use per-position return on cost to compare whale and baseline.** The broad baselines spend all $5k of capital, so their portfolio PnL is not comparable with the whale run.

## Whale alpha (per-position return on cost, whale realistic minus baseline)

Bootstrap: 5,000 resamples, clustered by market. "Stratified" reweights the baseline to the whale's entry-price mix, using buckets <0.80, 0.80–0.85, 0.85–0.90, 0.90–0.95 and ≥0.95.

| Baseline | Raw diff (95% CI) | Stratified diff (95% CI) | P(diff ≤ 0), stratified |
|---|---|---|---|
| random favourite print, all eligible markets | +3.8 pp [+0.9, +7.0] | +2.4 pp [−0.3, +5.2] | 0.038 |
| random print ≥ $1k | +4.9 pp [+1.5, +8.6] | +3.1 pp [−0.1, +6.5] | 0.027 |
| every eligible print | +5.8 pp [+1.9, +10.0] | +3.3 pp [−0.4, +7.0] | 0.038 |
| **random print, same markets as whale** | **−5.0 pp [−8.0, −1.9]** | **−2.2 pp [−5.1, +0.4]** | 0.93 |

All of these comparisons rest on only 14 whale positions.

## What changed in the backtest

1. **Realistic fills (now the default).**
   - Entry fills at the first print in the same market that is strictly after the whale's print and at least 5s later (configurable), using YES-normalized prices.
   - A 1c half-spread is added to the entry and subtracted on market exits.
   - A taker fee of rate·p·(1−p) per share is charged. Rates: Politics, Finance and Tech 0.04; Economy, Sports and other categories 0.05; Geopolitics 0. Rates are configurable.
   - A fill priced above 0.99 is skipped.
   - Realistic fills cost a median of **2.0c** versus the whale's price.
   - `FillConfig.original()` reproduces the legacy model.
2. **Point-in-time sizing.** Positions are sized from trailing-24h traded volume up to the decision time, replacing lifetime `volumeClob`. The `max_position_usd` cap is per market and includes layered adds. `SizingConfig.small_account()` sets $5k capital, a $250 max and a $10 min.
3. **One record per position.** Partial exits are aggregated by `position_id` before trades, hit rate and PnL are counted.
4. **Daily Sharpe and DSR.**
   - Returns cover every calendar day in the test window, including zero days, and the Sharpe ratio is per day.
   - The DSR follows Bailey & López de Prado (2014), using the skew and the non-excess kurtosis of daily returns, with n = 276.
   - N = 301 trials: `run_whale_sweep` 54 + `run_whale_full_sweep` 216 + sensitivity grids 17 + about 14 hand-tuned per-category gates. `tune_parameters.py` defines no search space for this strategy.
   - The cross-trial SR variance is unknown, so it is set to the null estimator variance 1/(n−1).
5. **Bugs found and fixed.**
   - **`ResearchPriceStore` lost almost every market's prices.** `prices.parquet` contains a single market keyed by a numeric Gamma id. The trade-print fallback was skipped whenever that file existed, and its all-column `dropna()` would have dropped every row anyway. As a result, every whale exit, flip, max-hold close and open-at-end close in the legacy backtest was booked at the entry price, which hid losses. The fallback now runs per market. This also changes the IC blend, which now covers 828 whales instead of 148.
   - The legacy loop iterated only over days that had a signal, so exits were booked late. It now steps through every calendar day.
   - Realistic exits use the last print before the start of the day, not the end-of-day price.
   - The `unfavored_only` NameError in `_category_backtest_worker` is fixed.

## Remaining caveats (not fixable with this data)

- **The universe is chosen with hindsight.** It is the top 500 markets per category by lifetime volume.
- **Trade history is truncated.** The data keeps about the last 1,000–2,000 trades per market, and most markets close within hours of their last print. Whale scoring and the baselines only see the final days of each market.
- **Entries on the resolution day are skipped using the actual `closedTime`.** That date would not be known in advance. This rule is kept identical across all runs.
- **Prints are a proxy for quotes.** A one-off print can be off-market, which affects some realistic exits.
- **YES-normalization was confirmed correct.** For all 402 resolved Politics markets, the last YES-normalized print is 0.999 when the market resolved YES and 0.001 when it resolved NO.

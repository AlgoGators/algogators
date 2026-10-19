# Whale following with point-in-time wallet skill (full on-chain tape)

Last revised: 2026-10-17

Strategy id `whale_follow_pit_wallet_skill`, status **rejected**. Code: `scripts/research/whale_pit_*.py`.
Outputs (local only): `data/research/whale_pit/`.

## Verdict

Following Polymarket whales chosen by a point-in-time skill score does not work. The frozen
train-selected rule loses 4.1% per $ out of sample, with a 95% CI of [-6.9%, -1.4%]. Even at the
whale's own price, with no delay and no costs, it makes -0.35% [-3.1%, +2.4%]: there is nothing for
better execution to recover.

- **Train:** none of the 48 pre-registered configurations has an event-clustered CI above zero.
  The best lower bound is -3.1%. Only 5 of 48 have a positive mean.
- **Baselines:** the rule does no better than the obvious alternatives. Random activity-matched
  wallets make -2.9% (strategy at the 16th percentile of 500 draws). Following every large trade
  makes -3.5%.
- **Shuffle test:** shuffling the skill scores within a month gives -3.1%, with the strategy at the
  24th percentile.
- **Deflated Sharpe:** about 0 (N = 54 trials).

PIT skill scores carry a small amount of information, but not enough to trade. Wallets ranked by
past 24h markouts sort later resolution returns weakly: top-minus-bottom decile is +4.1 pp
[+1.5, +6.9] at the whale's own price. That spread comes from the worst wallets losing (bottom
decile -3.7%), not from the best ones winning (top decile +0.5%). A post-hoc fade of the worst
decile loses 11-15% per $ after realistic entry and exit, in both train and test.

This was the re-test condition of `whale_follow_category` and `quiver_pm_large_trades`: "a PIT
wallet-skill model on the full (Dune) tape". That condition has now been met, and the answer is no.

## The old whale strategy and its look-ahead

The old code (`src/whale_strategy/`, `scripts/backtest/*whale*`) did the following:

1. Ranked wallets by win rate, volume or resolved ROI.
2. Gated each trade on a composite score (`MIN_WHALE_SCORE`).
3. Copied qualifying trades.

These flaws are documented in docs/STRATEGY_AUDIT.md and docs/WHALE_RETEST.md:

- **Hindsight universe.** The universe was the top 500 markets per category by lifetime volume, and
  positions were sized from lifetime `volumeClob`. Both are known only after the fact.
- **Outcome leakage.** Resolved-trade scoring (`whale_scoring._get_resolved_trades`) maps every
  market to its final winner, whatever the as-of date. The category backtest later filtered winners
  by `closedTime`/`endDate`, but entries on the resolution day were still skipped using the actual
  `closedTime`.
- **Truncated tape.** Only about 2,000 trades per market were kept, so a wallet's history was only
  the final days of each market.
- **Optimistic fills.** Fills were at the whale's own price with zero latency and a 3% haircut. The
  legacy liquid-markets suite also had synthetic exit prices and mixed YES/NO tokens; without those,
  whale-follow was -$251k.

With realistic fills, the 2026-10-12 re-test found +$116 over 9 months and a DSR of 0.16. Whale
timing was 2.2 pp worse than a random entry in the same markets.

## Point-in-time design

### Data

- **Fills:** lake `polymarket/fills`, 2022-11..2025-12 (complete months only), maker rows. The
  `taker` column is the aggressor wallet and `yes_side` its YES-normalised side.
- **Taker trades:** one row per (taker, market, transaction, side) with notional >= $100.
  - Excluded: crypto up/down markets, the Mentions category, and questions matching
    say/mention/tweet (the MNPI mention rule).
  - 9.4M trades come from the 291k wallets that ever trade >= $1k.

### Skill events

Each trade by a wallet becomes two "skill events", each with the time it becomes public:

- **`mk`:** the 24h markout per $, measured against the last hourly bar close available at
  ts + 24h. It becomes public at ts + 24h.
- **`res`:** the resolution return per $. It becomes public at `known_ts`, defined as
  max(the market's last lake fill, its scheduled end) + 1 day. Markets still trading in the last 31
  days of the data never count as resolved.

### Score

- A wallet's score at a signal is S = sum(pnl) / (sum(usd) + $20k).
- It is computed over events whose known time is strictly before the signal's block time. The
  implementation uses per-wallet cumulative sums and a DuckDB `ASOF JOIN ... AND s.ts > c.k`.
- At least 20 prior scored trades are required.
- Every wallet stays in, including ones that later go quiet. Nothing is ranked on the full sample.

### Signal

A taker trade of at least $1k, $5k or $25k from 2024-01..2025-12; 2.27M candidates in total. One
position per (market, side, UTC day).

### Follower

- **Entry:** the follower sees the fill on chain and enters 60 s later (stress: 300 s). It pays the
  last print as of t + 60 s plus 1c, or 2c if that print hit the bid. Further stresses add +1c and
  +2c.
- **Fees:** the Polymarket taker fee (`predict_ngin.costs.polymarket`, 2026 schedule) on entry and
  on a 24h exit. In 2024-25 most markets charged no fee, so the no-fee line is also shown.
- **Exits:** hold to resolution, or sell after 24h at 2c under the last hourly close.
- **Entry band:** the token's reference price must be in [0.05, 0.95].
- **Size:** a stake is capped at min(5% of capital, 10% of the market's trailing-24h $ volume, 50%
  of the whale's $).

### Selection

- Train on 2024; test on 2025.
- Grid: score {mk, res} × size {$1k, $5k, $25k} × θ {0, 0.02, 0.05, 0.10} × exit {resolution, 24h}.
- Pick the best train mean among configurations with at least 100 positions. A configuration
  "passes train" only if its CI lower bound is above 0.
- The pick is frozen and run once on test.

## Results

**Train (2024), 48 configurations.** All have CI lower bounds below 0, and 24h-exit variants lose
5-8% per $. The best mean is mk, >= $25k, S >= 0.02, hold to resolution: +2.7% [-7.1%, +8.3%] on
454 positions in 130 events. It fails the train gate, but it was still run on test as frozen.

**Test (2025), frozen rule.** Return per $ with an event-clustered 95% CI:

| run | positions | events | mean | 95% CI |
|---|---|---|---|---|
| base (60 s, fees) | 2,902 | 1,850 | -4.09% | [-6.86, -1.43] |
| 300 s delay | 2,870 | 1,832 | -4.22% | [-6.96, -1.27] |
| +1c slippage | 2,902 | 1,850 | -5.67% | [-8.30, -3.25] |
| +2c slippage | 2,892 | 1,844 | -7.22% | [-9.72, -4.60] |
| no fees | 2,902 | 1,850 | -2.44% | [-5.11, +0.34] |
| whale's own price, no delay, no cost | 2,911 | 1,857 | -0.35% | [-3.13, +2.44] |

**Books.** The 2025 cash-limited books, with P&L booked at exit:

| capital | P&L per year | daily Sharpe (annualised) | average deployed |
|---|---|---|---|
| $1k | -$968 | -1.9 | $425 |
| $5k | -$3,946 | -1.5 | $2,259 |
| $10k | -$8,379 | -1.6 | $4,275 |

**DSR.** N = 54. The SR variance across the train grid is 6.2 (annualised), which puts the
expected-max null SR at 5.7, so DSR is about 0.

**Capacity.** The liquidity caps alone would allow about $34M a year of stake. The market is deep
enough; the problem is that the return is negative. 83% of positions are in sports.

## Baselines (test, same size and exit)

| baseline | mean per $ | 95% CI |
|---|---|---|
| every trade >= $25k (no score) | -3.50% | [-5.15, -1.80] |
| activity-matched (>= 20 scored trades), any score | -2.80% | [-4.62, -0.92] |
| activity-matched, score below θ | -1.87% | [-4.09, +0.43] |
| random activity-matched subsets, same n (500 draws) | -2.86% | 2.5-97.5%: [-5.09, -0.79]; strategy at the 16th percentile |
| price drift (24h direction) at the same times | -5.27% | [-9.70, -0.82] |
| favourite at the same times | -6.19% | [-9.51, -3.08] |
| fade the whale at the same times | -10.77% | [-17.0, -4.65] |

The score adds nothing: the high-score selection does worse than both the low-score complement and
random activity-matched wallets.

## Leakage checks run

All results are in `checks.json` and `results.json`.

1. **Brute-force as-of recompute.** For 500 random signals, each wallet's mk/res sums, $ and counts
   were recomputed from `events.parquet` with `k < ts`. They match the ASOF-joined values exactly:
   max relative difference 2e-12, and counts identical.
2. **Timing invariants.**
   - No signal uses an event with known time >= its own time: 0 rows.
   - Every resolution event becomes public at least 1 day after the market's last fill and its
     scheduled end: 0 violations. The minimum trade-to-known lag is 1.0 day.
   - Markouts use bars whose hour has closed by ts + 24h.
   - Entry prices are as-of prints at t + 60 s / t + 300 s, and trailing volume uses only completed
     hours before t.
3. **Shuffle-time test.** The scores were permuted within each month 100 times. Mean -3.14%,
   2.5-97.5% [-5.71, -0.82]. The real score sits at the 24th percentile, so the wallet-skill
   link adds nothing.
4. **Look-ahead canary.** Using the full-sample (future-inclusive) wallet score with the same rule
   turns the test result into +3.6% [+0.6, +6.4]. The pipeline therefore shows look-ahead when it is
   there, and the PIT result does not have it.
5. **No conditioning on future fills for entry.** The reference print is the last one at or before
   t + delay. The design does not require a later same-direction fill, which would select momentum.

## Post-hoc lead checked (not pre-registered)

The persistence table (`checks.json`, P_mk) suggested fading the worst wallets. The fade used the
bottom decile of S_mk, cut from train only, on trades >= $1k:

| period | exit | whale's price, gross | realistic |
|---|---|---|---|
| train | resolution | -1.3% | -11.5% |
| train | 24h | +1.4% | -18.8% |
| test | resolution | -3.3% | -13.1% |
| test | 24h | +3.0% [+2.2, +3.9] | -14.5% |

The fade loses after crossing the spread twice. These 4 extra trials are logged in
`research/trials.csv`.

## Limits

- **Resolution time.** The public resolution time is proxied by the last fill + 1 day. Disputed
  markets that settle later could leak slightly into `res` scores, but the selected rule uses `mk`
  scores, which do not depend on it.
- **Spread and depth.** These are proxied from prints. There is no historical order book for 2024-25.
- **Fees.** The 2026 fee schedule is applied to 2024-25. The no-fee line is still negative.
- **Category tags.** These are the current snapshot (static metadata).
- **Out of scope.** Polymarket intl is not accessible to the user, and crypto up/down markets were
  excluded.

## 2026 extension (pre-registered 2026-10-17, before any 2026 result)

The 2025 test used lake months only through 2025-12. On 2026-10-17 the lake became complete through
2026-10-16, so the frozen rule gets one more out-of-sample look.

- **Rule:** frozen, not re-selected: mk score, whale trade >= $25k, S >= 0.02, hold to resolution.
  The 2024 train grid is re-run, and the script stops if it does not pick this same rule.
- **Test window:** signals 2026-01-01..2026-10-16. Skill history runs 2022-11 onward, as before.
  2025 is not looked at again.
- **Unchanged:** stresses, baselines, shuffle test, canary and books are the same as the 2025 test.
- **Unresolved markets:** markets still open at the data end are marked at the last print less 2c.
  This is the same rule as before, but 2026 has more open markets.
- **Hypothesis:** none for an edge. The 2025 result was -4.1% [-6.9, -1.4]. The prior is a negative
  or zero mean, and the status changes only if the 2026 CI lower bound is above 0.
- **Caveat known in advance:** from about 2026-05 the NegRisk collateral adapter can appear as the
  taker. If one address dominates the 2026 taker rows, it is reported, and its trades are shown
  with and without it.
- **Run:** `WHALE_PIT_END=2026-10-17` (env) with the same four scripts. Output goes to
  `data/research/whale_pit/end_2026-10-17/`. The run logs one `oos` row to `research/trials.csv`.

**Results (2026-10-17).**

The 2024 train grid re-selected the frozen rule exactly: 454 positions, +2.66%. The taker
concentration check was clean: the top 2026 address has 2.6% of the >= $25k signals (2025: 1.3%),
so the NegRisk adapter does not dominate.

Return per $ with an event-clustered 95% CI:

| run (2026-01-01..10-16) | positions | events | mean | 95% CI |
|---|---|---|---|---|
| base (60 s, fees) | 9,779 | 5,277 | -4.99% | [-6.58, -3.37] |
| 300 s delay | 9,669 | 5,228 | -4.91% | [-6.54, -3.38] |
| +1c slippage | 9,779 | 5,277 | -6.63% | [-8.07, -5.19] |
| +2c slippage | 9,749 | 5,267 | -8.20% | [-9.67, -6.73] |
| no fees | 9,779 | 5,277 | -3.24% | [-4.82, -1.58] |
| whale's own price, no delay, no cost | 9,795 | 5,296 | -0.72% | [-2.41, +0.92] |

Books (P&L per year / daily Sharpe):

| capital | P&L per year | daily Sharpe |
|---|---|---|
| $1k | -$891 | -1.2 |
| $5k | -$4,480 | -1.1 |
| $10k | -$10,699 | -1.5 |

DSR is 0 (N = 54).

Baselines and checks:

- The strategy beats random activity-matched wallets (-5.33%; strategy at the 72nd percentile) and
  shuffled scores (-5.56%; 83rd percentile). It is also above every-large-trade (-5.38%) and the
  favourite (-4.52% at the same times).
- None of those differences is anywhere near the CI width, and all of the baselines lose.
- The canary (the future-inclusive score) gives +2.0% [+0.6, +3.5], so look-ahead would still show.

**Verdict unchanged: rejected.** The second out-of-sample year agrees with the first. Following
whales loses about 5% per $ at a follower's price, and nothing is there even at the whale's own
price.

## Reproduce

```bash
uv run --no-sync python scripts/research/whale_pit_extract.py   # ~5 min, per-month taker trades + markets
uv run --no-sync python scripts/research/whale_pit_signals.py   # ~1 min, PIT scores + signals
uv run --no-sync python scripts/research/whale_pit_eval.py      # grid, frozen test, baselines, shuffle, canary
uv run --no-sync python scripts/research/whale_pit_checks.py    # brute-force as-of, invariants, persistence, post-hoc fade
```

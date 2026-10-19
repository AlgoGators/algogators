# Alpha Decay in Prediction-Market Arbitrage, 2024 to Oct 2026 (2026-10-13)

Last revised: 2026-10-14

Research only. No orders were placed. This study extends `docs/TICK_ARB_VALIDATION.md`, `docs/INPLAY_ARB_SKEW.md`, `docs/POLYMARKET_INTRAVENUE_ARB.md` and `data/vendor/negrisk/report.md`. Generated tables are in `data/vendor/alpha_decay/report_tables.md` (gitignored; regenerate with `scripts/research/alpha_decay_report.py`).

## Bottom line

1. **Secular decay.** Half-lives are for opportunity dollars per unit of traded volume, at a fixed (today's) fee schedule unless stated otherwise.

   | arb type | measure | trend | half-life (95% CI) |
   |---|---|---|---|
   | **Kalshi↔Polymarket cross-venue** | print-confirmed net $/volume, 2024-05..2026-10 | −7.8%/month | **8.6 months [5.5, 19.8]** |
   | | same, winsorised | | 12.3 [7.2, 40.8] |
   | | same, sports era 2025-04+ | | 4.1 [2.7, 8.6] |
   | | quote-based net-crossed share, 2026 only (Predexon books) | | 2.7 [1.9, 5.0] |
   | | gross (pre-fee) $ | −3.7%/month | 18 months [8.4, none] — not significant |
   | **Polymarket NegRisk** | YES baskets per $ volume | **+14%/month** (growing) | none |
   | | NO baskets per $ volume | +1%/month [−4, +6] | none |
   | **Kalshi ladders** | quote-confirmed net arb minutes | −9%/month (sell side) | 7.6 months [4.3, 33] (sell); buy side 11 [3.2, none] |

   - **Fees vs competition, cross-venue:** the 2026 Polymarket taker fee explains about 86% of the drop in realised $/volume from 2025H2 to 2026H2 (−41% actual vs −7% at constant fees). Competition shows up as a steady compression of the gaps, which pushes them under the fee hurdle: the share of gross edge that survives today's fees fell from 0.52 to 0.24.
   - **NegRisk** shows no decay at either constant or actual fees. The 2026 fee removes 24% of YES-basket and 45-55% of NO-basket gross.

2. **Intra-opportunity decay** (real books, Oct 2026, pre-game and outrights, Kaplan-Meier):
   - **Gross crosses:** median life **4.9 s**. By family: games 2.0 s, Fed 1.8 s, futures 12.7 s.
   - **Net-of-fee crosses:** median **11 s** (games 6.8 s). S(250 ms) = 0.90, S(1 s) = 0.82, S(5 s) = 0.61, S(30 s) = 0.44.
   - **NegRisk:** median 0.3-4 s by liquidity quintile.
   - **No shortening over time.** Over 2024H2 to 2026H2 the print history shows *longer* survival: the single-print share fell from 82% to 46-66%, and the median leg gap stayed at 3 s. Decay is in the *size and frequency* of crosses, not in how long a cross lasts.

3. **Liquidity scaling.**
   - **Real books (net crosses, 42 pairs):**
     - duration ∝ ADV^**−0.52 [−0.64, −0.40]**, so thinner markets keep a cross longer;
     - edge ∝ ADV^+0.26 [0.07, 0.45];
     - $/opportunity ∝ ADV^0.29 [0.09, 0.49] and ∝ touch depth^0.96;
     - frequency ∝ ADV^0.18 [−0.15, 0.51].
   - **Print history (2025-04..2026-10):** frequency per pair-month ∝ Kalshi volume^0.60 [0.51, 0.69], and $ per pair-month ∝ Kalshi volume^0.76 [0.57, 0.95].
   - **Secular decay by liquidity quintile.** Only the most liquid quintile decays: cross-venue Q5 half-life **7.1 months [4.6, 14.9]**, and 2.3 months [1.5, 5.0] on quotes. Q1-Q4 show no significant decay.
   - **Net tradeoff for $1k-$10k, after fees, impact-model unwinds and leg risk:**
     - **No liquidity bucket pays at ≥ 1 s on real books** (cross-venue, 3d below).
     - Cross-venue pays only $0-20/week at ≤ 50 ms.
     - NegRisk convert pays ≤ $16/week at ≤ 250 ms in thin-to-mid groups, $2-6/week at 1 s, and is negative at ≥ 5 s.
     - Account size does not matter above $1-2.5k.
     - A slow trader (≥ 1 s) reaches positive expectancy only in thin/mid markets, where the absolute dollars are tiny, or in long-dated standing mispricings that lock capital (about 2-3%/yr). **Verdict unchanged: nothing clears ~$1k/yr at home latency.**

**A data bug found on the way.** `data/polymarket/trades.parquet` timestamps are wrong:
- **70%** of rows are off by more than 60 s and **26%** by more than an hour, with errors up to 23 h.
- They are a piecewise-linear 2.000 s/block extrapolation; Polygon really runs about 2.14 s/block.
- Any sub-hour timing analysis on that file is invalid until it is repaired. This study repairs it with true block times from Dune `polygon.blocks`; see `scripts/research/alpha_decay_block_times.py`.

## 1. Data and method

| arb | source | period | sample | resolution |
|---|---|---|---|---|
| Cross-venue, prints | Kalshi `trades_fixed`; Polymarket on-chain fills (local file with repaired block times; Dune `market_trades` for 2026) | 2024-05..2026-10 | 1,750 RuleMatcher pairs (2024-25, all) + 622 of 7,458 rule-validated 2026 pairs (stratified random by family × month, weights = stratum size/sample) | Kalshi µs, Polygon block (≈1-2 s after repair) |
| Cross-venue, quotes | Predexon K + PM books (read-only, final), PM-anchored snapshots | 2026-01..10 | 7,125 pair-tasks | PM snapshot (s-min) |
| Cross-venue, books | Probalytics every-change books | 2026-10-06..12 | 375 pairs | ms |
| NegRisk | Dune on-chain taker baskets (definitions of `negrisk_dune.py`); Probalytics NegRisk books; Tardis days | 2024-01..2026-09 | all NegRisk events; 62 groups (books) | block / ms |
| Kalshi ladders, quotes | Kalshi 1-min candle closes (synchronous `yes_bid`/`yes_ask` of every leg), last 24 h of each event | 2024-10..2026-10 | 218 quoted events from a fixed 21-series panel chosen on 2025 event counts | 1 min |
| Kalshi ladders, prints | Kalshi `trades_fixed`, all ME events | 2024-01..2026-02 | 43,593 events (2,779 after the filters below) | µs |

**Rules applied throughout:**
- **YES normalisation.** Every price is converted to the Kalshi-YES proposition. For 2026, polarity was checked against gamma `clobTokenIds`.
- **Kalshi aggressor side.** Kalshi `taker_side` keeps its legacy meaning in 2026: within the same minute, 'yes' prints sit 1c above 'no' prints in every month.
- **Polymarket aggressor side.** Dune `maker_side` on taker rows is the taker's side: BUY prints sit above SELL prints.
- **Fees.**
  - Kalshi: `ceil(0.07·m·C·p(1−p))`, with m = the series multiplier.
  - Polymarket: `rate·p(1−p)`.
  - "const" applies today's schedule (sports/econ 0.05, politics 0.04) to every month.
  - "actual" applies the fee actually charged: none before 2026-03, then 0.03, then 0.05 from about Jul-Aug 2026; NFL 0 in Sep-Oct.
- **Pre-game only.**
  - Games are cut at gamma `gameStartTime`. This replaces the old t_known − 4 h cut, which leaked in-play.
  - Futures stop 7 days before resolution, so the deciding games (e.g. Super Bowl night, 74c "edges") are excluded.
  - The Fed stops 1 h before the decision. Election night is excluded.
- **Pair matching.** Resolution-rule matched (`rule_matcher.validate_pair`). Games are also checked: the Kalshi ticker date must equal the Polymarket game date.
- **Liquidity buckets.** ADV is trailing 24 h USD before the opportunity, or the same month's volume, never future volume. One exception: the Predexon quintiles use lifetime volume, and are only descriptive.
- **Decay fits.**
  - Poisson pseudo-ML: `E[$] = V·exp(a + b·month + family FE)`.
  - Errors are cluster-robust by pair/event, or Newey-West(3) for single monthly series.
  - Half-life = ln 2/−b. "none" means no significant decay.
  - Math is in `scripts/research/alpha_decay_common.py`, with tests in `tests/unit/test_alpha_decay.py` (16 pass).

**Coverage changes that could masquerade as decay:**
1. **2024 is Fed and election only.** Sports pairs start in 2025-04, when Kalshi game markets became matchable. Family fixed effects absorb the composition shift, and the sports-era fits use 2025-04+ only.
2. **The pair universe switches in 2026-01** (RuleMatcher → Predexon-discovered pairs). The method is identical: same prints, same cut, per-fill PM prices on both sides. The local scan was switched to per-maker-fill rows to match Dune's granularity.
3. **The 2026 games window is the 7 days before start.** In 2024-25 it was the whole pre-game life. Normalising per $ of the window's volume handles this.
4. **Dune/gamma volume ratio** is a stable 0.44-0.53 across 2026, except Mar (1.33), so there is no V2-migration coverage break.
5. **Kalshi candle quotes for markets settled in 2024** are sparse: only 5 events have quoted minutes in 2024Q4. Quote-based ladder figures start in practice in 2025Q2.
6. **Kalshi `meta.parquet` lists only some legs** of 812 of 1,620 ME series (crypto and index brackets: 2-9 of 30-75 legs). Those series are excluded from the print ladder. The quote sample re-reads every leg list from the API.

## 2. Secular decay

### 2a. Cross-venue (prints): net $ per $1M of pre-game volume (election excluded)

| period | gross | const (today's fees) | actual | actual/gross | const/gross |
|---|---|---|---|---|---|
| 2024H1 | 79.0 | 41.3 | 59.0 | 0.75 | 0.52 |
| 2024H2 | 50.1 | 27.4 | 36.1 | 0.72 | 0.55 |
| 2025H1 | 131.1 | 64.6 | 87.2 | 0.66 | 0.49 |
| 2025H2 | 63.6 | 21.9 | 34.4 | 0.54 | 0.34 |
| 2026H1 | 42.7 | 10.1 | 16.0 | 0.37 | 0.24 |
| 2026H2 | 85.3 | 20.4 | 20.4 | 0.24 | 0.24 |

Monthly values are noisy (one pair-month can be 50-99% of a month), so read the fits:

| series (3,206 pair-months, 2,312 pairs) | %/month | half-life, months [95% CI] |
|---|---|---|
| $ gross, 2024-05..2026-10 | −3.7 | 18.2 [8.4, none] |
| **$ const, 2024-05..2026-10** | **−7.8** | **8.6 [5.5, 19.8]** |
| $ const, winsorised at p99 pair-month | −5.5 | 12.3 [7.2, 40.8] |
| $ const, 2025-04..2026-10 (sports era) | −15.7 | 4.1 [2.7, 8.6] |
| $ actual, 2024-05..2026-10 | −7.8 | 8.5 [5.6, 17.7] |
| episodes per $ volume, const | −5.6 | 12.1 [7.3, 34.1] |
| $ const, Fed only | −9.6 | 6.9 [4.6, 13.4] |
| $ const, games only / futures only | +7.0 / +5.3 | none |

**Quote-based cross-check, 2026 (Predexon, 7,125 pair-tasks).**
- The share of PM-snapshot instants with a net cross (const) moved from 1.3% (Jan) to 0.1-0.3% (Sep-Oct). Feb and Aug spike to 5-7%, around the Super Bowl and the start of NFL.
- Fits: gross −5.6%/month, HL **12.1 [7.5, 32.4]**; const −22%/month, HL **2.7 [1.9, 5.0]**; actual HL 2.2 [1.7, 3.2].
- Both methods agree. Gross gaps shrink slowly, and the part that clears the fee hurdle shrinks 2-4× faster.

**Fee vs competition.**
- From 2025H2 to 2026H2:
  - realised (actual) $/volume fell 41% (34.4 → 20.4);
  - at constant fees it fell 7% (21.9 → 20.4);
  - so ≈ **86% of the recent drop is the Polymarket taker fee** and ≈ 14% is competition.
- Over the whole window, the competition component is the const-fee trend: −7.8%/month, or −5.5%/month winsorised.
- The gross trend is not significant (CI [−8.3%, +0.7%]/month). Competition compresses the *distribution* of gaps (median gross edge 2c in 2024H2 → 1c), and the fee hurdle turns that compression into a much faster net decay.

### 2b. Polymarket NegRisk (on-chain taker baskets, per $1M of NegRisk taker volume)

| quarter | taker vol $M | YES gross | YES actual | YES at 2026 fees (cf05) | NO gross | NO actual | NO cf05 |
|---|---|---|---|---|---|---|---|
| 2024Q4 | 3,284 | 4.7 | 4.7 | 2.4 | 24.3 | 24.3 | 6.4 |
| 2025Q2 | 813 | 6.3 | 6.3 | 4.6 | 59.3 | 59.3 | 26.0 |
| 2025Q4 | 2,107 | 12.1 | 12.1 | 9.7 | 191.0 | 191.0 | 178.4 |
| 2026Q1 | 3,669 | 14.9 | 14.8 | 9.7 | 20.7 | 20.0 | 10.6 |
| 2026Q2 | 3,749 | 34.2 | 26.0 | 24.2 | 43.5 | 20.2 | 19.2 |
| 2026Q3 | 2,123 | 67.3 | 51.6 | 48.6 | 58.8 | 33.7 | 32.1 |

(This study's YES baskets are 10-min taker-only; the prior report's "$30-85/$1M" combined YES and NO.)

**Fits, Newey-West, 2024-01..2026-09:**
- YES: actual **+13.9%/month** [+8.8, +19.4]; at constant 2026 fees +17.9%/month.
- NO: actual +1.0%/month [−3.7, +5.9]; cf05 +4.0%/month.
- **No half-life exists for NegRisk.** The 2026 fee removes 24% of YES gross and 45-55% of NO gross (2026Q2-Q3).

**By event-liquidity quintile** (monthly event volume; YES actual $/1M volume, 2026):

| quintile | Q1 | Q2 | Q3 | Q4 | Q5 |
|---|---|---|---|---|---|
| YES actual $/1M volume | 1,687 | 1,299 | 84 | 43 | 24 |
| median event volume | $89 | $929 | $5.5k | $27k | $97k |
| median distinct makers | 3 | 12 | 44 | 136 | 306 |

Per-$ yield falls about 70× from the thinnest to the thickest quintile, but the thin quintiles hold very few dollars.

### 2c. Kalshi mutually-exclusive ladders

**Print-confirmed baskets are an artefact-prone upper bound.**
- Two-leg game events dominate the raw count: 99% of the $1.2M of "net baskets" is sports.
- 47% of those dollars come from >10c edges. These are prints on two books seconds apart, straddling an in-play jump; for example, a 92c "sell basket" at a walk-off.
- After dropping sports and incomplete-leg series (2,779 events), there is no significant trend: buy net +9.7%/month [−2.9, +23.8], sell net +7.8%/month.

**Synchronous quotes (1-min candle closes of every leg, 218 quoted events)** are the measure to trust:

| quarter | events | quoted minutes | buy net % | sell gross % | sell net % |
|---|---|---|---|---|---|
| 2025Q2 | 25 | 4,406 | 0.09 | 7.3 | 1.04 |
| 2025Q3 | 33 | 8,345 | 0.05 | 5.9 | 0.48 |
| 2025Q4 | 30 | 13,841 | 0 | 9.0 | 0.79 |
| 2026Q1 | 41 | 16,173 | 0.09 | 8.9 | 0.54 |
| 2026Q2 | 42 | 16,709 | 0.03 | 4.9 | 0.25 |
| 2026Q3 | 27 | 13,921 | 0 | 1.3 | 0.01 |

- **Fits:** sell net −8.7%/month, HL **7.6 [4.3, 32.6]**; buy net HL 11.1 [3.2, none]; sell gross HL 75 [14, none].
- **Persistence:** quote arbs last a median of one minute close; 58-83% are seen at exactly one close.
- **Edge size:** median 0.4-0.8c, on books where the minimum order is one contract.
- **Kalshi fees did not change in the window,** so this decay is competition.

## 3. Intra-opportunity decay

**Real books, Oct 2026 (Kaplan-Meier, censored at window end):**

| cross | n | median | S(50 ms) | S(250 ms) | S(1 s) | S(5 s) | S(30 s) | S(300 s) |
|---|---|---|---|---|---|---|---|---|
| gross, all | 5,653 | 4.9 s | 0.93 | 0.90 | 0.83 | 0.50 | 0.30 | 0.16 |
| gross, games | 3,385 | 2.0 s | 0.92 | 0.88 | 0.85 | 0.44 | 0.27 | 0.14 |
| gross, Fed | 459 | 1.8 s | 0.99 | 0.93 | 0.64 | 0.29 | 0.14 | 0.09 |
| net, all | 940 | 11.3 s | 0.96 | 0.90 | 0.82 | 0.61 | 0.44 | 0.29 |
| net, games | 581 | 6.8 s | 0.97 | 0.90 | 0.79 | 0.54 | 0.35 | 0.19 |
| net, futures | 334 | 129 s | 0.93 | 0.89 | 0.85 | 0.71 | 0.60 | 0.44 |

**NegRisk (Probalytics, 7 days):** median duration by group-liquidity quintile 4.0 s / 0.32 s / 0.47 s / 1.2 s / 0.64 s. The Tardis first-of-month days (Jun-Oct 2026, prior report) show no monthly trend in medians (70-1,000 ms).

**Change over time (prints, cross-venue, const fees; durations have a 1-2 s floor):**

| half-year | episodes | single print-pair share | S(1 s) | S(5 s) | S(30 s) | median edge |
|---|---|---|---|---|---|---|
| 2024H2 | 258 | 0.82 | 0.15 | 0.13 | 0.03 | 1.0c |
| 2025H1 | 1,581 | 0.71 | 0.25 | 0.19 | 0.07 | 0.7c |
| 2025H2 | 2,002 | 0.65 | 0.28 | 0.19 | 0.06 | 0.7c |
| 2026H1 | 661 | 0.55 | 0.36 | 0.23 | 0.07 | 0.7c |
| 2026H2 | 284 | 0.46 | 0.44 | 0.30 | 0.15 | 0.4c |

The median gap between the two legs is 3 s in every quarter from 2024Q2 to 2026Q4, with 12-24% of legs within 1 s.

**Reading.** Opportunities did **not** get shorter-lived. The ones that remain are smaller (median net edge 1.0c → 0.4c) and fewer, and those that do appear survive a little longer. That is consistent with fewer, slower competitors chasing sub-cent edges after fees, not a faster race.

## 4. Liquidity scaling

**Elasticities** (log-log, family or month fixed effects, cluster-robust 95% CIs):

| data | y | x | β [95% CI] |
|---|---|---|---|
| books, net (859 ep., 42 pairs) | duration | ADV (24 h, min venue) | **−0.52 [−0.64, −0.40]** |
| | duration | touch depth | +0.13 [0.00, 0.26] |
| | edge | ADV | +0.26 [0.07, 0.45] |
| | $/opportunity | ADV / touch depth | +0.29 [0.09, 0.49] / +0.96 [0.78, 1.15] |
| | frequency per book-hour (Poisson, 374 pairs) | ADV | +0.18 [−0.15, 0.51] |
| books, gross (5,055 ep., 145 pairs) | duration / edge | ADV | −0.03 [−0.29, 0.23] / −0.10 [−0.18, −0.03] |
| | frequency (gross) | ADV | +0.43 [0.24, 0.62] |
| prints 2025-04..2026-10 (4,342 ep., 479 pairs) | duration + 1 s | PM ADV / K ADV | +0.07 [0.03, 0.11] / +0.04 [0.00, 0.08] |
| | edge | PM ADV | −0.20 [−0.34, −0.06] |
| | episodes per pair-month (Poisson) | K vol / PM vol | +0.60 [0.51, 0.69] / −0.12 [−0.24, 0.00] |
| | $ per pair-month | K vol | +0.76 [0.57, 0.95] |
| NegRisk books (463 ep., 37 groups) | duration / edge | group 24 h vol | −0.14 [−0.53, 0.26] / −0.26 [−0.45, −0.07] |
| Kalshi ladders, prints (6,535 ep.) | duration / edge | event 24 h vol | +0.22 [0.15, 0.29] / −0.10 [−0.13, −0.07] |

Number of active makers was measured for NegRisk only (§2b quintile table): yield per $ falls about 70× from 3 to 306 makers.

**Secular decay by liquidity quintile:**
- **Cross-venue prints** (pair-month pre-game volume, fixed cut-offs): Q1-Q4 (< $820k/month) show no decay (+1% to +36%/month). **Q5 (> $820k) −9.4%/month, HL 7.1 [4.6, 14.9].**
- **Quotes, 2026:** Q5 HL **2.3 [1.5, 5.0]**; Q1-Q4 not significant (HL 6.6-22 months, CIs open).
- **Kalshi quote ladders:** net-arb share 2.4% of minutes in Q1, 1.7% Q2, 1.3% Q3, 0.06% Q4, 0% Q5. Every quintile decays (HL 2.2-9.4 months; significant in Q2 and Q4).
- **NegRisk:** no quintile decays.

**$/week for a $1k-$10k account by liquidity bucket × latency.** Real Kalshi↔Polymarket books, Oct 2026, pre-game plus outrights resolving ≤ 45 days. Matched legs go through the hold-to-resolution engine; leg-risk unwinds are walked on the t+L books.

| ADV bucket (24 h, min venue) | 0 ms | 50 ms | 250 ms | 1 s | 5 s | 30 s |
|---|---|---|---|---|---|---|
| Q1 $0-2 | +1.4 | +0.4 | −6.5 | −5.4 | −5.3 | −5.6 |
| Q2 $2-6 | +0.4 | +0.4 | +0.4 | +0.2 | −0.1 | −0.4 |
| Q3 $7-971 | +9.3 | +5.6 | +2.4 | **+3.4** | 0.0 | −5.6 |
| Q4 $1.0-5.2k | +20.6 | +6.4 | −2.4 | −9.2 | −11.7 | −21.8 |
| Q5 $5.6-56k | +19.6 | +17.2 | +7.2 | −4.7 | −5.4 | −6.7 |

These are at $2.5k-$10k. At $1k, Q4/Q5 at 0 ms are $17 and $6. The one-leg-gone rate at 1 s rises from 3% (Q1) to 27-38% (Q4-Q5).

**NegRisk, Probalytics 7 days, sell/convert (instant capital), $/week (same at $1k-$10k except Q5):**

| group 24 h vol | 0 ms | 50 ms | 250 ms | 1 s | 5 s | 30 s |
|---|---|---|---|---|---|---|
| Q1 $1-4k | +2.4 | +0.1 | −1.4 | −1.9 | −2.0 | −2.1 |
| Q2 $4-30k | +18.4 | +17.0 | **+15.9** | +3.2 | +2.1 | −0.5 |
| Q3 $30-39k | +2.1 | −0.1 | −1.4 | −3.6 | −3.8 | −3.8 |
| Q4 $40-58k | +14.9 | +10.2 | +10.1 | +5.6 | −13.1 | −14.1 |
| Q5 $59-546k (@$1k / @$10k) | +15.8 / +1.7 | +7.2 / −6.9 | +3.2 / −18.8 | −17.9 / −39.9 | −21.8 | −23.1 |

**Buy-all-YES** is positive up to 5 s only in Q1 (+$20-26/week) and Q5 (+$26-90/week). These are standing, long-dated mispricings: capital is locked a median of 86 days, which is about 2-3%/yr (prior report), not weekly flow.

**Slow traders (≥ 1 s), print-history upper bound.** Cross-venue, Apr-Oct 2026, actual fees, sample totals:
- Q4 (ADV $116-374k): **$119/week at 1 s, $103 at 5 s, $47 at 30 s**, net of impact-model unwinds.
- Other buckets: ≤ $43 at 1 s and ≤ $1.4 at 30 s.
- Population-weighted Q4: $88/week [35, 144] at 30 s.

This bound assumes we take every dollar that printed after onset + L. Those dollars were in fact taken by whoever printed them. The book replay, which lets competitors act, turns every bucket negative at ≥ 1 s except Q3 (+$3/week).

**Kalshi ladders:** print-based (complete series, non-sports) is about $10-20/week at 0 ms and negative at ≥ 250 ms for the sell side. On quotes, edges are 0.4-0.8c, seen at one minute close, and absent in Q4/Q5.

**Where a slow trader can still earn.** Nowhere material.
- A 1-5 s bot is break-even to slightly positive only in mid-liquidity buckets: cross-venue Q3 (+$3/week at 1 s), NegRisk Q2/Q4 (+$3-6/week at 1 s).
- At ≥ 5 s the only positive cells are the long-dated buy-side and futures standing crosses. Their median life is 129 s, and they pay about 1-3%/yr on locked capital.
- Thin markets keep a cross 2-3× longer: β = −0.52 means a 100× thinner market lasts about 11× longer. But $/opportunity ∝ depth^0.96, so the dollars available fall almost one for one.

## 5. Ledger entry (proposed; not written to the DB)

- **strategy_id:** `arb_alpha_decay_slow_taker`
- **status:** `rejected`
- **verdict:** Arb alpha per $ volume decays with half-life ~8.6 mo cross-venue (const fees; 4.1 mo sports era; 2.7 mo on 2026 quotes), ~7.6 mo Kalshi ladders (quotes), none for NegRisk (YES baskets +14%/mo). ~86% of the 2025H2→2026H2 cross-venue drop is the Polymarket taker fee. Crosses did not get shorter-lived (net median 11 s, games 6.8 s); they got smaller and rarer. Thin markets hold crosses longer (duration ∝ ADV^−0.52) but $/opp ∝ depth^0.96. On real books no liquidity bucket pays a $1k-$10k account at ≥1 s; best ≤$20/wk at ≤50 ms.
- **key_numbers:**
  - `hl_xvenue_const_mo=8.6 [5.5,19.8]`, `hl_xvenue_sports_mo=4.1 [2.7,8.6]`, `hl_xvenue_quotes2026_mo=2.7 [1.9,5.0]`, `hl_xvenue_gross_mo=18 [8.4,inf]`
  - `negrisk_yes_trend=+13.9%/mo`, `negrisk_no_trend=+1.0%/mo`
  - `hl_kalshi_ladder_quotes_sell_mo=7.6 [4.3,32.6]`
  - `fee_share_of_2026_drop=0.86`
  - `dur_net_median_s=11.3 (games 6.8)`, `dur_gross_median_s=4.9`
  - `elasticity_duration_adv=-0.52 [-0.64,-0.40]`, `elasticity_usd_depth=0.96`, `elasticity_freq_kvol=0.60`
  - `hl_Q5_xvenue_mo=7.1 [4.6,14.9]`, `Q1-Q4 no decay`
  - `best_book_wk_50ms=$17 (Q5)`, `best_book_wk_1s=$3.4 (Q3)`, `negrisk_sell_wk_250ms=$16 (Q2)`
- **gaps:**
  - Real-book sub-second data is 1 week (Oct 2026), so durations over time come from 1-2 s prints.
  - The 2026 cross-venue sample is 622 pairs; population weights reach 25×.
  - Kalshi quote sample is 218 events at 1-min resolution; >30-leg brackets are excluded.
  - Makers count is measured for NegRisk only.
  - Print-history $/week assumes winning every race.
  - The local Polymarket timestamp repair is interpolated (≈1-2 s error, occasionally more around chain halts).
- **next_tests:**
  - Repeat the Probalytics-style every-change book replay on a second week (e.g. Jan or Jun 2026) to measure sub-second survival change directly.
  - Regress cross-venue duration on PM active makers (the daily counts are already in `pm_local/daily.parquet`).
  - Fix or regenerate `data/polymarket/trades.parquet` timestamps for other studies.
- **retest_if:**
  - Polymarket cuts sports taker fees below 0.02, or Kalshi cuts its coefficient.
  - A new venue pair (e.g. Polymarket US) shows gross gaps > 2c pre-game for > 1% of time.
  - Measured end-to-end latency ≤ 50 ms becomes available.

## 6. Reproduce

```
set -a; . ./.env; set +a
venv/Scripts/python.exe scripts/research/alpha_decay_block_times.py 56438000 80978000 --dune   # ~5 credits
venv/Scripts/python.exe scripts/research/alpha_decay_pm_scan.py                                # local PM prints, repaired times
venv/Scripts/python.exe scripts/research/alpha_decay_fetch_2026.py sample
venv/Scripts/python.exe scripts/research/alpha_decay_fetch_2026.py kalshi                      # Kalshi API trades
venv/Scripts/python.exe scripts/research/alpha_decay_fetch_2026.py dune                        # ~290 credits
venv/Scripts/python.exe scripts/research/alpha_decay_fetch_2026.py yesoi
venv/Scripts/python.exe scripts/research/alpha_decay_xvenue.py --src both
venv/Scripts/python.exe scripts/research/alpha_decay_negrisk.py --fetch vol yes no              # ~35 credits
venv/Scripts/python.exe scripts/research/alpha_decay_kalshi_ladder.py --series-flags --run --check-complete
venv/Scripts/python.exe scripts/research/alpha_decay_kalshi_quotes.py
venv/Scripts/python.exe scripts/research/alpha_decay_books.py
venv/Scripts/python.exe scripts/research/alpha_decay_predexon.py
venv/Scripts/python.exe scripts/research/alpha_decay_report.py > data/vendor/alpha_decay/report_tables.md
venv/Scripts/python.exe -m pytest tests/unit/test_alpha_decay.py
```

**Resources used:**
- Data: 0.73 GB under `data/vendor/alpha_decay/`.
- Dune: about 330 credits; the billing period stands at about 700/2,500.
- No Predexon API calls; its files were only read.
- No orders. No commits.

`alpha_decay_kalshi_ladder_2026.py` (a print-based 2026 extension) was written but not used. Its print method is artefact-prone, and the quote sample covers 2026 instead.

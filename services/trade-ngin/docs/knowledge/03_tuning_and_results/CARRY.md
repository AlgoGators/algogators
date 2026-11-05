# Carry

Carry is the return a futures position earns if prices do not move: the slope between the held contract and its
neighbour on the curve. Carver adds it to trend as a second family of rules (Advanced Futures Trading Strategies, AFTS,
strategy ten, basic carry; strategy eleven, combined carry and trend), because the two are paid for different things,
price moves against the shape of the curve, and a blend of two nearly uncorrelated sources should earn more per unit of
risk than either alone.

Carry is built and shipped off because it has not yet been tuned on the data the book trades. It needs two real
contract prices on the same date, which only the per-contract bars give, and the second contract is missing on many
dates: over 16 years UB has one on 55.4% of dates and 6M on 66.0%, and 6L's longest gap is 78 dates (section 7.2). When
carry was measured (2026-10-26 and 10-27) the continuous series still had holes (AlgoGators/algogators#36) and the gold
and silver micros were stand-ins, and the final retune ran with carry off (AlgoGators/algogators#186).

On the data it was measured on, carry earned little. What is known (section 2): over the 36 instruments since 2011
carry on its own earned about nothing (unrounded 16-year Sharpe 0.02 against trend's 0.26), so the 60/40 blend kept
trend's Sharpe (0.25) at lower volatility and added a forecast that earned nothing here; carry roughly halves the round
trips; the traded blend tracks its unrounded book a little more closely than trend alone, with a similar dollar
shortfall to it on the one stored 16-year run, and runs well under the risk target; and in the final sets carry on
lowered the mean after-cost Sharpe on both overlay covariances (0.28 with carry off against -0.08 with it on, and 0.25
against -0.04). Why the traded blend's after-cost Sharpe falls is unmeasured. Why carry's own return was so low on these
instruments is not measured either; the candidate causes are in section 3, each marked unmeasured.

Carry is on main, built as Carver writes it and switched off: no template carries a `carry` block, and with no block the
strategy is the trend strategy to the last stored digit (AlgoGators/algogators commit `c7cec21205`, in the futures
release, AlgoGators/trade-ngin#282). It was set off on 2026-10-27 and stays off in the chosen setup. The formula, the setting,
what the code refuses when carry is on, and where the code sits are in
[`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "1.6a Carry: built, switched off"; how to switch
it on is in [`config_template/README.md`](../../../config_template/README.md), section "`carry`: carry rules in the trend
strategy". What would change the decision is a re-measurement on the final data, AlgoGators/algogators#186 (section 5).

Code citations are paths under `services/trade-ngin/` in AlgoGators/algogators at commit 4c92eb1b8d, main's merge of
the trade-ngin import (AlgoGators/algogators#98, which imports trade-ngin d6f998da). The trend strategy carry plugs into is in [TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md)
and [`src/strategy/README.md`](../../../src/strategy/README.md). Related documents: [LEVERS.md](LEVERS.md) (every lever
tried, carry among them), [TUNING_STORY.md](TUNING_STORY.md) (the tuning rounds in order), [RESULTS.md](RESULTS.md) and
[THE_BOOK.md](../01_the_book/THE_BOOK.md) (the book as it trades).

"The starting book of the first tuning round" below is the full-size book with full-size gold and silver, before the
data fill; most single-run carry measurements were made on its settings.

## 1. Carver's carry and what it does to a trend book

- **A second, nearly independent return.** Carry's daily correlation with trend here is 0.01 to -0.04 in every window
  (section 6.1). That is Carver's case for the blend: the 60/40 split, trend 60% and carry 40%, is the proportion he uses
  in his own trading (AFTS, strategy eleven, p.248). The forecast diversification multiplier (table 52) scales the blend
  back up for the diversification of its rules, so if carry earns, the blend's lower volatility at the same signal is
  turned back into return by the risk target, and the Sharpe rises.
- **Smaller forecasts when the parts disagree.** The two parts often disagree in sign, so the blended forecast is
  smaller: over 16 years its mean absolute value was 8.16 against trend's 9.05 (Carver's scale averages 10), and the
  unrounded blend ran at 14.7% volatility against trend's 19.4%, below the 20% target in every window (14.0% to 15.3%;
  section 6.1).
- **Less of trend's crisis payoff.** Trend's monthly returns are positively skewed, which is what a trend follower is
  paid for; the blend gives much of it up (16y account monthly skew 0.78 with carry against 1.20 without, in the sizing
  and carry grid; section 6.2).
- **Different risk limits.** Carry lowers the three risk limits and raises net leverage, so a carry-on book needs its own
  limits (section 6.3).
- **Equity index shorts on carry alone.** US equity index carry is mostly negative, because a futures price sits above
  spot when the short rate exceeds the dividend yield. With the equity slow rule applied to the trend part only, carry
  can turn an equity index short by itself: 2,556 tradeable signal bars over 16 years (section 7.4).

## 2. What is known about why carry earned little here

### 2.1 Carry's own return was about zero

On the unrounded books over 2011-08-21 to 2026-10-23, before costs, on the sizing capital of the starting book of the
first tuning round, carry alone made +45.3k at a Sharpe of 0.02, trend alone +259.6k at 0.26, and the 60/40 blend +202.3k
at 0.25 (section 6.1). With no return of its own, carry can only diversify the blend: vol-matched to trend, the blend made
+57.5% over 16 years (maximum drawdown 40.2%) against trend's +61.0% (40.0%).

Measured against Carver's own figures (AFTS, tables 46 and 47), carry on these instruments promised a Sharpe of 0.29 and
delivered 0.04 on the median instrument, 15% of the promise, where Carver's own carry-only figures deliver 115%. A
carry forecast is a bet that today's curve slope is paid out in return, and on these instruments since 2011 it mostly
was not. By class over 16 years carry alone made money in FX (+58.1k), energy (+53.2k), ags (+71.0k) and equities
(+16.4k), and lost in metals (-99.7k), bonds (-31.9k), bitcoin (-14.4k) and livestock (-7.4k). Ags (82% of the promise)
and energy (65%) delivered most; bonds and metals delivered less than nothing, and metals are the largest loss.

The grid of section 6.2 gives the same picture of carry's own contribution: 16-year unrounded Sharpe trend 0.26, carry
0.02, blend 0.25, and inside each of its three blends the carry part made +8.5k, +3.8k and +6.3k over 16 years.

### 2.2 The traded blend against its unrounded book

On the one stored 16-year whole-contract run of the blend (the starting book of the first tuning round's settings plus
the carry block):

| 16y, whole contracts | Trend only | 60/40 blend |
|---|---:|---:|
| Whole-contract book P&L against its own unrounded book | +65k of +260k | +9k of +200k |
| Tracking error to its own unrounded book | 8.3% | 7.0% |
| Account return | +4.43% | -4.54% |
| Account Sharpe after costs | 0.08 | 0.02 |
| Volatility, account / sizing capital | 13.1% / 18.1% | 9.8% / 13.6% |

The traded blend kept a smaller share of a smaller unrounded result (9k of 200k against 65k of 260k). The dollar gap
between the whole-contract and the unrounded book is about the same in both (-191k against -195k), and the blend tracked
its unrounded book more closely; the grid's median tracking errors agree (9.5%, 8.7% and 9.6% with carry against 9.8%,
9.3% and 11.3% without). The data do not show a larger rounding or tracking loss with carry. Why the traded blend's
after-cost Sharpe falls (in the grid, on the granularity buffer with the sample optimiser covariance, 0.34 without carry
against 0.04 with it; in the final sets 0.28 against -0.08) is unmeasured, and how many rows round to zero with carry on
was never counted. This is one start: the five 5y starts of the starting book of the first tuning round, two weeks apart,
spread 52.9 points.

### 2.3 Churn

Carry roughly halves the round trips. The source is the sizing and carry grid of 2026-10-27 (section 6.2), on its book
with gold and silver micros (the granularity buffer, sample optimiser covariance, micro stand-ins before the data fill):
152 to 216 round trips a year with carry off against 76 to 110 with it on, 12 runs per cell. The grid's two other sizing
setups and the final sets of the same day show the same:

| Book | Round trips a year, carry off | Carry on | Costs, carry off / on |
|---|---|---|---|
| Granularity buffer, sample optimiser covariance (grid) | 152 to 216 | 76 to 110 | 153k / 117k over 12 runs |
| Minimal buffer, Carver's optimiser covariance (grid) | 180 to 237 | 103 to 128 | 172k / 125k over 12 runs |
| Granularity buffer, Carver's optimiser covariance (grid) | 138 to 179 | 71 to 113 | 150k / 119k over 12 runs |
| Final sets, overlay on Carver's covariance | 149 to 209 | 88 to 115 | 0.04 to 0.05 / 0.04 to 0.05 Sharpe |
| Final sets, overlay on the sample covariance | 141 to 212 | 75 to 109 | 0.04 to 0.05 / 0.04 to 0.05 Sharpe |
| The one stored 16y run of the starting book of the first tuning round | 115 | 59 | 0.62% / 0.43% of 500k a year |

The cost saved is real in dollars, but the cost in Sharpe units does not fall, because the carry-on book also runs at
lower risk. Why carry halves the round trips is unmeasured; smaller blended forecasts (section 1) asking for smaller
positions that trade fewer contracts is a candidate, not measured.

### 2.4 The risk difference

Carry-on books run at lower risk: 13.6% to 15.9% volatility on the sizing capital in the final sets, against 18.6% to
21.4% for carry off, which runs at about the 20% target. In dollars carry on cost a mean of 65k (year), 69k (2y), 110k
(5y) and 220k (10y) of net P&L per run and added 10k on the 16y, but those dollars mix the lower risk with the return, so
the Sharpe is the comparison that separates them. On the Sharpe, carry on lowered the mean after cost in the final sets
on both overlay covariances (section 6.3), and in the grid in two of three pairs (section 6.2). Carry-on sets also
breached Carver's rule that costs stay under a third of the pre-cost Sharpe in 6 and 7 of 17 runs, because their pre-cost
Sharpe is small.

The chosen setup's limits (net leverage 5.50) were measured with carry off only. Carry moves the limits, so no carry-on
limit exists for the book that trades (section 6.3).

### 2.5 Carry's years

In the grid's carry-only records carry's strongest years are 2011-2012, 2015 and 2017 and its weakest 2019, 2021, 2024
and 2025 (section 6.5). The 5y and 10y miss its strong years (carry alone -28.8k and -136.6k unrounded), and the 16y is
the only window that holds them, which is why the 16y is the one window where carry was level or ahead. The timing does
not explain the year and the 2y, where carry alone was positive (+8.9k and +23.1k) and the final sets still lost with
carry on.

## 3. What is suspected and unmeasured

Each item below is measured as far as stated; its effect on carry's return has not been measured alone, and
AlgoGators/algogators#186 lists measuring each alone as the first step (section 5).

- **The final data (unmeasured).** Every carry measurement ran before 484 missing continuous-series rows were filled
  (AlgoGators/algogators#36) and with stand-in prices for micro gold and silver. The final retuning on the filled data
  with the real micros ran carry off only.
- **A noisy measured slope (unmeasured effect).** FX is the noisiest family (the daily change of annualised carry 0.5 to
  1.4 times its level), rates the quietest (0.17 to 0.57). Measured on this book, every carry span turns over 1.6 to 2.6
  times Carver's table 40 (section 7.3), so the forecast may follow noise in the slope and pay to trade it.
- **The neighbour and missing prices (unmeasured effect).** The neighbour is taken from the root's roll-cycle months only,
  and on a date with no second price the last known raw carry stands with no age limit, which happens on 7.2% of rows and
  about 40% of UB's (section 7.2).
- **Metals and bonds (unmeasured cause).** Both delivered less than nothing against Carver's figures, where he has them
  delivering more than promised; why is not measured.
- **Carry-driven equity shorts (unmeasured effect).** With the slow rule on the trend part only, carry turned equity
  indices short on 2,556 tradeable signal bars (section 7.4); what those shorts earned was not measured alone.
- **Carver's weights for this size (unmeasured).** Carver notes that the best mixture of carry and trend "might be
  different, depending on the size of your portfolio" (AFTS, strategy eleven, p.255). Only the 60/40 weights were run;
  the other rows of table 51 and strategy sixteen's equity split were not (section 6.6).

## 4. On main, switched off

The decision, carry off for the final setup, was taken on 2026-10-27 and stays in the chosen setup, because carry has
not yet been tuned on the data the book trades: it was measured before the continuous-series fill and with stand-in
gold and silver micros, and the final retune ran with carry off (section 3, the final data). Off by absence
changes nothing: carry off was proved identical to the trend book on every stored cell, so switched off it does not
touch the book.

Carry's arithmetic agrees with an independent reference to 1.5e-14 over every (day, instrument) row of 2024-10-23 to
2026-10-23, and the cases most open to doubt (a roll's confirming bar, a date with no second price, a capped day) were
each worked by hand and agree with the engine.

- Off by absence: templates with the block at weight 0 gave identical backtests and bodies, but the live run stores the
  whole config file, so the block itself was a stored difference. The templates therefore ship without the block; under
  `config_template/` the quoted key `"carry"` appears only in the README section.

## 5. What would change it: AlgoGators/algogators#186

Either route, or both, closes it.

**Route A, Carver's way.**
1. Re-measure the three books (trend only, carry only, 60/40) on the final settings, the filled data and the real micro
   gold and silver, over the standard window set.
2. Find why carry here delivers a Sharpe of 0.04 on the median instrument against the 0.29 it promises (section 2.1),
   measuring each candidate cause of section 3 alone before any change: span turnover, the last known raw carry kept with no age limit, the roll-cycle
   neighbour, bonds and metals, FX noise, equity index carry driving shorts.
3. Measure the carry weights that table 51 itself gives rows for, not a fitted search.
4. Strategy sixteen's equity split (table 70: 10% trend, 90% carry for equities), which first needs a per-class carry
   weight built behind a setting, off by default and identical to today when off.

**Route B, side by side.** Run the trend-only book and a trend-and-carry book on the same final settings and post the
paired table over the standard window set at each month end; no trading change comes from this route alone.

**Done when** includes a recommendation judged by a rule written before any result (for example, the five-window mean
after-cost Sharpe rises and no window falls by more than 0.1 against trend only), and any setting that would change the
traded book is agreed before it is used. A carry-on book also needs its own limits by the p99 method before it trades.

## 6. The measurements

### 6.1 Carry on its own, unrounded

Three unrounded books on the sizing capital of the starting book of the first tuning round, 2011-08-21 to 2026-10-23,
before costs: trend only, carry only, and the 60/40 blend. Trend only reproduces the tuning's trend-only figure (+259.6k
against +260k). Windows are slices of that book's 16-year capital path, so the dollars are smaller than a fresh 500,000
would give; Sharpe is on the daily P&L over that capital, before costs.

| Window | Trend only: P&L / Sharpe | Carry only: P&L / Sharpe | 60/40 blend: P&L / Sharpe | Blend's trend part / carry part |
|---|---:|---:|---:|---:|
| year | +27.9k / 0.56 | +8.9k / 0.11 | +25.4k / 0.58 | +19.0k / +6.5k |
| 2y | -11.7k / -0.06 | +23.1k / 0.17 | +6.9k / 0.08 | -7.3k / +14.2k |
| 5y | -14.3k / -0.01 | -28.8k / -0.10 | -21.0k / -0.06 | -9.8k / -11.2k |
| 10y | +195.0k / 0.30 | -136.6k / -0.24 | +82.4k / 0.18 | +136.7k / -54.4k |
| 16y | +259.6k / 0.26 | +45.3k / 0.02 | +202.3k / 0.25 | +177.5k / +24.8k |

- Vol-matched to trend only, the blend makes +57.5% over 16y (maximum drawdown 40.2%) against trend's +61.0% (40.0%).
- Mean absolute forecast over the 16 years: trend 9.05, carry 9.79, blend 8.16.
- On the one stored whole-contract run of the blend (section 2.2): round trips 59 a year against 115; costs 0.43% of 500k
  a year against 0.62%; account return -4.54% against +4.43%; account Sharpe after costs 0.02 against 0.08.

### 6.2 The sizing and carry grid

Six cells of 12 runs each (16y, 10y, five 2y starts, five one-year starts), carry off and on under three sizing setups,
on gold and silver micro stand-ins, with Carver's values (weight 0.4, spans 5, 20, 60, 120, scalar 30). Sharpe is the
account Sharpe after costs on the whole-contract book; the unrounded row is dollars of the unrounded book before costs on
each cell's own sizing capital.

| Sizing setup | Carry | Round trips a year | Mean Sharpe after costs, 12 runs | Median tracking error | Unrounded 16y book |
|---|---|---|---:|---:|---:|
| Granularity buffer, sample optimiser covariance | off | 152 to 216 | 0.34 | 9.8% | 306k |
| Granularity buffer, sample optimiser covariance | on | 76 to 110 | 0.04 | 9.5% | 235k |
| Minimal buffer, Carver's optimiser covariance | off | 180 to 237 | 0.13 | 9.3% | 245k |
| Minimal buffer, Carver's optimiser covariance | on | 103 to 128 | 0.17 | 8.7% | 204k |
| Granularity buffer, Carver's optimiser covariance | off | 138 to 179 | 0.19 | 11.3% | 287k |
| Granularity buffer, Carver's optimiser covariance | on | 71 to 113 | -0.15 | 9.6% | 223k |
| The starting book of the first tuning round | off | 115 to 192 | 0.12 | | 260k |

- Carry lowered the 12-run mean in two of the three pairs. The minimal buffer with carry is above its pair (0.17 against
  0.13) from the five year starts alone (0.48 against 0.06); it is below on the 2y (-0.13 against 0.14), the 10y and the
  16y. The granularity buffer with the sample covariance and carry is above its pair only on the 16y (0.42 against 0.38).
  Carry lowered the unrounded 16y book in all three pairs.
- Inside each blend the carry part makes +8.5k, +3.8k and +6.3k over 16 years and about -75k on the 10y.
- Carry-on books run at lower volatility (account volatility on the 16y 9.2% to 10.9% against 11.1% to 13.1%) and give
  up much of trend's positive monthly skew (16y account 0.78 with carry against 1.20 without, on the granularity buffer
  with the sample covariance).
- Net leverage p99 of the carry-on target book 5.64 to 5.76, with 36 to 41 cycles above the then limit of 6; without carry
  5.33 to 5.38, 14 cycles above.

### 6.3 The final sets: carry off against carry on, both overlay covariances

On the granularity buffer with gold and silver micros, each set on its own re-measured limits, 17 runs per set (the year
from 2025-10-05, five starts each of 2y, 5y and 10y, one 16y).

| Set | Mean Sharpe after costs | Median | Runs with a positive Sharpe |
|---|---:|---:|---:|
| Overlay on Carver's covariance, carry off | 0.28 | 0.24 | 16 of 17 |
| Overlay on Carver's covariance, carry on | -0.08 | -0.11 | 6 |
| Overlay on the sample covariance, carry off | 0.25 | 0.26 | 16 |
| Overlay on the sample covariance, carry on | -0.04 | -0.02 | 8 |

- On Carver's covariance carry on is below carry off on the year, 2y, 5y and 10y and level on the 16y (0.19 with carry
  against 0.17 without); on the sample covariance it is below on every window (0.17 against 0.44 on the 16y).
- The book's overlay reads Carver's covariance, his own estimator for the overlay (each contract's sizing volatility with
  weekly correlations exponentially weighted over 26 weeks; the reason is in [RESULTS.md](RESULTS.md), section "The setup
  the numbers belong to"); the carry result is the same on both estimators.
- On the sizing capital carry off runs at about the 20% target (18.6% to 21.4% by window) and carry on at 13.6% to 15.9%.
- Carry lowers the three risk limits on both covariances, leaves gross leverage at 7.25 and raises net leverage. On
  Carver's covariance the risk limits are 1.25 / 3.50 / 2.75 with carry against 1.75 / 4.75 / 3.00 without, and net
  leverage 6.00 against 5.75; on the sample covariance 1.75 / 3.25 / 3.50 against 2.25 / 4.50 / 4.00. These are the limits
  of the 2026-10-27 setup, not the chosen setup's: its limits (net leverage 5.50) were measured with carry off only.
- Round trips a year: 149 to 209 carry off and 88 to 115 carry on (Carver's covariance), 141 to 212 and 75 to 109
  (sample); costs in Sharpe units 0.04 to 0.05 in all four sets. Carry-on sets breach Carver's "costs under a third of
  pre-cost Sharpe" in 6 and 7 of 17 runs, because their pre-cost Sharpe is small.

### 6.4 How the measurements fit together

The unrounded measurement and the grid agree on carry's own contribution: 16y unrounded Sharpe trend 0.26, carry 0.02,
blend 0.25 in both. What differs between them is the traded book around carry. On the whole-contract book carry halves
the round trips and lowers the volatility (grid, section 6.2); the blend tracks its unrounded book a little more closely
than trend alone, and on the one stored 16-year run its dollar shortfall to that book is similar to trend's (about 191k
against 195k), while it keeps a smaller share of a smaller unrounded result (9k of 200k against 65k of 260k; section
2.2). Why the traded blend's after-cost Sharpe falls is unmeasured.

### 6.5 Carry by year

In the grid's carry-only records carry's strongest years are 2011-2012 (+55.5k, +67.4k), 2015 (+46.4k) and 2017 (+67.5k)
and its weakest 2019 (-65.7k), 2021 (-65.4k), 2024 (-44.1k) and 2025 (-34.1k). In the unrounded carry-only book it made
+28.0k in 2023 and +24.1k in 2026 to the cut. That accounts for the 5y and 10y, which miss its strong years (carry alone
-28.8k and -136.6k); it does not account for the year and the 2y, where carry alone was positive (+8.9k and +23.1k) and
the final sets were still lower with carry on.

### 6.6 Cells that were planned and not run

A carry pair under the high-volatility speed switch was added to the grid on 2026-10-27 with one fix: with both on, the
blend must take table 52's row for the speed pairs the switch leaves in, not all six (10 rules not 7 without the fix). The
pair was not run, because the setup was narrowed to the granularity buffer with gold and silver micros the same day; a
fixed no-trade band under carry and strategy sixteen's equity split (not settable by one sleeve-wide weight) were not run
either. Neither the high-volatility switch nor that pair count is on main.

## 7. Carry as built: what the system document does not hold

The formula, the setting, the refusals and the code map are in
[`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "1.6a Carry: built, switched off". This section
keeps Carver's reasons for the settings, the cases the design had to decide, and the measured detail behind section 3.

### 7.1 Carver's settings and worked examples

- The two prices are real contract closes of the same date, never the back-adjusted series (AFTS, strategy ten, p.243).
- The four spans (5, 20, 60 and 120 business days) are Carver's: intermediate spans correlate too highly with their
  neighbours and longer smooths add nothing (AFTS, strategy ten, pp.228-229). The scalar 30 follows from his average
  absolute carry forecast, about 0.33, against the target average of 10 (p.229). Table 40 gives each span's turnover a
  year: Carry5 5.75, Carry20 3.12, Carry60 1.82, Carry120 1.22 (AFTS, table 40, p.230).
- Within each style every rule weighs the same (AFTS, table 51, p.249), and the multiplier comes from table 52 by the
  number of rules (p.250; the engine's copy is `src/strategy/carry_estimator.cpp:122-128`). The speed limit already
  removes trend speeds on twelve contracts, so the weights and the multiplier differ by instrument:

| Trend speeds kept | Weight per trend speed | Weight per carry span | Rules | FDM |
|---|---|---|---|---|
| 2, 4, 8, 16, 32, 64 | 0.10 | 0.10 | 10 | 1.35 |
| 4, 8, 16, 32, 64 | 0.12 | 0.10 | 9 | 1.34 |
| 8, 16, 32, 64 | 0.15 | 0.10 | 8 | 1.32 |
| 16, 32, 64 | 0.20 | 0.10 | 7 | 1.29 |

Carver's two worked examples, one per branch:

| Step | S&P 500 micro, holding December (the front) | WTI crude, holding December (not the front) |
|---|---|---|
| Prices | December 4578.50, March 4572.00 | November 62.61, December 62.09 |
| Raw carry | 4578.50 - 4572.00 = 6.50 | 62.61 - 62.09 = 0.52 |
| Annualised | 6.50 / 0.25 = 26.0 | 0.52 / 0.0833 = 6.24 |
| Carry | 26.0 / (0.16 x 4578) = 0.035 | 6.24 / (0.28 x 62.09) = 0.359 |
| x 30 | +1.1 | +10.8 |

### 7.2 The design and how each case is handled

AlgoGators/trade-ngin#185 ("Carry in the Carver strategy: trend and carry in one forecast", opened 2026-10-10) is the
design: carry is a second family of rules inside the existing Carver strategy, blended with trend into one forecast per
instrument, not a second strategy or sleeve. Everything after the forecast (sizing, overlay, optimiser, buffer, holds,
rolls, fills, P&L) reads the one blended forecast. Every setting starts at Carver's value, and a per-class scalar,
seasonal correction, volatility-regime adjustment, per-instrument split, spot trend and cross-sectional carry stay off.
Carry needs two contract prices of the same date, so it waited for the per-contract bars and the roll rule, with no
proxy; the code refuses carry unless `per_contract_rolls` is on and the book has exactly one trend sleeve
([`config_template/README.md`](../../../config_template/README.md), the carry section).

| Case | How the code handles it | Decided |
|---|---|---|
| Which contract is the neighbour | only contracts in the root's roll-cycle months count; the nearer one is the latest-expiring cycle contract that expires before the held one and is alive on the date; if there is none, the first listed cycle contract expiring after it; the side is decided by which contracts are alive, never by which printed (`src/data/carry_quote.cpp:8-37`) | 2026-10-26 |
| The quote | the held contract's close and the neighbour's close of that date; the pair moves on the bar that confirms a roll | 2026-10-26 |
| A date with no second-contract price | the last known raw carry stands, with no age limit, divided by that day's own volatility | 2026-10-26 |
| An instrument with no carry yet | trend only, judged per bar: trend only while no raw carry is known anywhere in the 3,200-bar window; in the 16-year run no row was trend only | 2026-10-26 |
| The equity slow rule | acts on the trend part only; carry-driven equity shorts are allowed and counted on a `CARRY_DRIVEN_EQUITY_SHORTS` run line | 2026-10-26 |
| Where the parts are recorded | one `CARRY` log line per symbol and signal bar, no new table or column | 2026-10-26 |
| Overlay limits | carry-on books report new percentiles; the limits in force were set on the carry-off book | 2026-10-27 |
| Whether the traded book switches carry on | off | 2026-10-27, kept for the chosen setup |

With the neighbour taken from the roll cycle, every root has a second contract on most dates: the lowest coverage over
16 years is UB 55.4%, 6M 66.0%, 6N 71.8%, ZT 78.4% and 6L 80.3%, and the longest gaps are 6L 78 dates (2010-12 to
2011-04), 6N 50, UB 46, ZT 24. Taking the neighbour from the roll cycle rather than any listed month lifts the metals'
coverage (PL 48.8% to 87.2%, SI 81.2% to 94.0%) and halves their noise. "Quotes" here means the pair of closes; no bid,
ask or spread data is used, and no short-rate (spot) data is used; that data sits with the new-markets study,
AlgoGators/algogators#184. In the 16-year run 10,950 of 152,769 signal rows (7.2%) use a raw carry from an earlier date,
and UB does so on 1,731 of its 4,280 rows (about 40%).

### 7.3 The speed-limit check against measured turnover

Carver's speed limit removes a rule whose yearly cost is above 0.15 Sharpe units, and AlgoGators/trade-ngin#185 asks that
carry spans pass the same test. With carry on, every span needs a table 40 turnover
(`src/strategy/trend_following.cpp:231-251`), and on every contract the fastest kept trend speed must turn over at least
as fast as the fastest span (`:252-274`); otherwise the sleeve refuses carry, naming the contract and the span. The
committed list passes (ZR keeps EWMAC16, table 35 turnover 13.2 against 5.75). The check compares Carver's table
turnovers, not measured ones. Measured on this book every span turns over faster than table 40:

| Span | Table 40 | Measured forecast turnover | Measured position turnover |
|---|---:|---:|---:|
| Carry5 | 5.75 | 14.87 | 25.22 |
| Carry20 | 3.12 | 5.78 | 11.97 |
| Carry60 | 1.82 | 2.88 | 8.50 |
| Carry120 | 1.22 | 1.93 | 7.64 |

These are on the starting book of the first tuning round's settings (1.6 to 2.6 times table 40; the grid measured 1.5 to
2.6 times). No measurement compares them with the measured trend-speed turnovers (unmeasured).

### 7.4 A worked example, day by day: UB around dates with no second price

From the carry-on 16-year run's carry record. UB holds UBZ6 (the front) against the further cycle contract UBH7, 3 months
apart, so raw carry = held minus further and the expiry difference is 0.25.

| Signal bar | Own quote? | Held close used | UBH7 close used | Raw carry | Annualised (/0.25) | Divisor (bar close x forecast sigma) | Carry forecast | Carry5 smoothed | Carry5 (x30, cap 20) | Carry mean (4 spans) | Trend part | FDM (10 rules) | Blended F |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-10-16 | no | 107.34375 | 107.125 | 0.21875 | 0.875 | 11.4987 | 0.07610 | 0.16640 | 4.99 | 5.02 | -4.30 | 1.35 | -0.78 |
| 2026-10-18 | no | 107.34375 | 107.125 | 0.21875 | 0.875 | 11.3067 | 0.07739 | 0.13673 | 4.10 | 4.69 | -3.34 | 1.35 | -0.17 |
| 2026-10-19 | no | 107.34375 | 107.125 | 0.21875 | 0.875 | 11.5345 | 0.07586 | 0.11644 | 3.49 | 4.43 | -4.06 | 1.35 | -0.90 |
| 2026-10-20 | no | 107.34375 | 107.125 | 0.21875 | 0.875 | 11.4266 | 0.07658 | 0.10315 | 3.09 | 4.24 | -4.04 | 1.35 | -0.98 |
| 2026-10-21 | yes | 107.65625 | 106.4375 | 1.21875 | 4.875 | 11.4805 | 0.42463 | 0.21031 | 6.31 | 5.34 | -1.97 | 1.35 | 1.29 |
| 2026-10-22 | no | 107.65625 | 106.4375 | 1.21875 | 4.875 | 12.8672 | 0.37887 | 0.26650 | 7.99 | 5.98 | 1.09 | 1.35 | 4.11 |

- On a bar with no quote of its own the last known annualised raw carry stands but is divided by that bar's own divisor,
  so the carry forecast still moves with the day's close and volatility (10-22: 4.875 / 12.8672 = 0.37887).
- Carry5's smoothing on 10-22: 0.3333 x 0.37887 + 0.6667 x 0.21031 = 0.26650; x 30 = 7.99, under the cap.
- The blend on 10-22: 1.35 x (0.6 x 1.0889 + 0.4 x 5.9799) = 4.111, the stored forecast.
- The quote of 10-21 is the first in this stretch; UB's carry was carried forward on about 40% of its 16-year rows.

Carry-driven equity shorts: over the 16-year carry-on run on the starting book of the first tuning round's settings the
equity indices were short on the blend while their trend part was not on 2,556 tradeable signal bars (MES 564, MNQ 561,
MYM 544, M2K 503, and before the micros listed on 2019-05-06, ES 106, NQ 108, YM 114, RTY 56). The held book was short on
495 of those bars; on the settings with gold and silver micros, 989.

## 8. What not to repeat

- Do not judge carry on one start: the starting book of the first tuning round's 5y starts two weeks apart spread 52.9
  points (section 2.2).
- Do not search carry settings over this book's instruments: start from Carver's values and move one at a time, agreed
  before the move (AlgoGators/trade-ngin#185).
- Do not read the 16y alone: it is the only window where carry is level or ahead, because it holds carry's strong years
  (section 6.5).
- Do not compare carry-on and carry-off books on one set of limits: carry lowers the three risk limits and raises net
  leverage (section 6.3).
- Do not quote the carry-on limits in the close of AlgoGators/trade-ngin#185 as the chosen setup's: they are the
  2026-10-27 setup's.
- Do not bring back the high-volatility speed switch beside carry without the pair count of section 6.6.

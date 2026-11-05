# Results of the final futures book

The final futures book, the chosen setup, earns **0.300 Sharpe after costs on the one 16-year run** (+60.07% total,
3.15% a year, 25.88% maximum drawdown), **0.290 on the 10-year and 0.273 on the 5-year**, as means over five starts two
weeks apart, and **0.102 on the 2-year and 0.407 on the one-year window** (all on the account, after costs; section 3).
What the numbers say about the book: a trend follower is paid by a few large, persistent moves and pays for them with
long stretches of small losses. The long windows hold several such moves. The 2-year holds none of them and holds 2025,
the year the three fast speeds whipsawed. Its loss is a signal loss, not a cost, roll or rounding loss (section 4). The
six speeds stay because they are Carver's design, each catching trends of a different length, and he removes a speed
only on a contract where its cost is above his speed limit; on the unrounded target book over the 16 years the fast
speeds earned 196.1k of 270.2k (section 1.2). The book does not reach the target of 0.4 to 0.7 on every window, and no
setting measured in the last tuning rounds did either (see [`TUNING_STORY.md`](TUNING_STORY.md) and
[`LEVERS.md`](LEVERS.md)). The choices behind the book are described in
[`../01_the_book/THE_BOOK.md`](../01_the_book/THE_BOOK.md), and the numbers here are the data on them.

The chosen setup is the sample optimiser covariance with the fraction step, at a net leverage limit of 5.50, set on
2026-10-29. The other setups measured with it, among them Carver's optimiser covariance with the edge step and the
selective speed rules of section 5.6, are described in [`LEVERS.md`](LEVERS.md) by what each does and why it is not in
the book, with their data on every window. The measure throughout is the
account Sharpe after costs, the one every tuning round used. Sharpe, return and drawdown are on the account after costs
unless a table says otherwise; volatility is shown on both capitals, and the figure to compare with the 20 percent target
is the one on the sizing capital, 18 to 19 percent (18.6% on the 16y; section 6).

Code citations are paths under `services/trade-ngin/` in AlgoGators/algogators at commit 4c92eb1b8d, main's merge of
the trade-ngin import (AlgoGators/algogators#98, which imports trade-ngin d6f998da). The book itself is described in [`../01_the_book/THE_BOOK.md`](../01_the_book/THE_BOOK.md), its
departures from Carver in [`../02_decisions/CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md), carry (built,
switched off) in [`CARRY.md`](CARRY.md), and the instrument set in [`../01_the_book/THE_BOOK.md`](../01_the_book/THE_BOOK.md),
section "Why these 36 markets, and the gold and silver micros". How the engine
computes and stores each statistic is in [TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md), sections
"1.17 The statistics as they are stored" and "4. Reading the results".

## 1. What the numbers say about the book

### 1.1 The long windows hold the large trend years

On the chosen setup's one 16-year path the unrounded target book (the positions the forecasts ask for, before rounding and costs)
made 270k. Its three best years, 2014 (+130k), 2020 (+175k) and 2022 (+101k), made 406k; the other thirteen together lost
136k (section 5.6). That is the shape of trend following: the profit arrives in a few years of large, persistent moves,
and the years between pay for staying in the market.

The windows read according to which of those years they hold:

| Window | Holds | Account Sharpe after costs | What carries it |
|---|---|---:|---|
| 16y (from 2011-08) | 2014, 2020, 2022 | 0.300 | the signal: target book +270.2k, rounding gap +87.8k |
| 10y (from 2016-08) | 2020, 2022 | 0.290 | two of the three large years |
| 5y (from 2021-08) | 2022 only | 0.273 | the rounding gap: target book +417, gap +94,004 (section 4) |
| 2y (from 2024-08) | none; holds 2025 | 0.102 | neither: target book -33,030 |
| Year (starts 2025-09 to 2025-11) | the live year | 0.407 | the signal of one recent year |

The 5y reads differently from the other windows. Its signal is one good year against the rest (2022 +102,947, 2025 -56,927) and nets to
about zero; its 0.273 comes from the whole-contract book standing away from the target in the right places, almost all
one gasoline (RB) contract kept in spring 2022 (section 4.6). The 5y's figure therefore rests on one contract's holding,
and it is read beside the 10y and 16y, never alone.

Two more things bear on how the windows read:
- **Start-date noise.** Five 2y starts two weeks apart run from -0.173 to 0.542 in Sharpe, and the year starts from
  -0.078 to 0.859 (section 3). At 500,000 the book is a sequence of single-contract decisions, and moving the start deals
  them differently. The 16y and 10y average over many more of those decisions.
- **One period.** The five 2y starts all share 2024 to 2026, and the year starts lie inside the same period, so the 2y
  and the year describe that period, whatever the number of starts.

### 1.2 Why the 2-year is weak: the fast speeds in 2025

As the mean of five 2y runs from their own starts, the target book lost about 33k, while costs (about 4k a year), rolls
(roll-bar moves +13,594) and rounding (gap +37,488) did not cause the loss (section 4). By calendar year the target book
made 8,214 in 2024, lost 67,994 in 2025 and made 26,750 back in 2026: 2025 alone lost more than the whole window.

The loss sits in the three fast speeds (2/8, 4/16 and 8/32). Over the 2y they lost 44.9k while the three slow speeds made
11.9k (section 4.4), and on the 16-year path 2025 is the fast speeds' worst year in sixteen (-65.4k; the next worst is
2019, -17.9k).

The mechanism is whipsaw. A fast speed follows moves of a few weeks, so it enters after a short move and loses when the
move reverses. 2025 was a year of sharp reversals: the April tariff shock (the worst day, 2025-04-09, lost 8.7% with the
book short almost every risk market), Treasuries with no trend at all (the book lost all twelve months, late on every
turn), and equity indices churning through a flat fourth quarter. Splitting each month's P&L into the position held at
the start of the month and the changes made within it shows it directly: in 2025 the start-of-month positions earned
+7.76 points of sizing capital and the changes within months lost 21.13. The book added after moves that then reversed
(section 5.5). The markets were not trendless at a medium speed: SG's single medium-speed rule run on the book's own
markets made money in 2025 in every version rebuilt (section 5.4).

The fast speeds stay. The reason is Carver's design, and the data are given beside it:
- **Each speed catches trends of a different length.** The six speeds together diversify the forecast. Carver removes a
  speed only on a contract where its yearly cost is above his speed limit (0.15 of Sharpe), never on its recent
  results; the book applies that test (AFTS, strategy nine, table 35; the removals are in
  [`../01_the_book/THE_BOOK.md`](../01_the_book/THE_BOOK.md), section "The speed limit: rules removed by cost"). A cut of
  the fast speeds everywhere also removes them where they pay ([TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md),
  section "3. What was tried and not adopted", the row on blanket speed cuts).
- **Their record over the 16 years.** On the unrounded target book of the 16-year path the fast speeds made 196.1k of
  270.2k, and 4/16 is both their worst speed in 2025 and the highest-earning speed over the whole run (section 5.6).
- **How their years follow one another.** Fast-minus-slow relative performance is negatively autocorrelated across
  calendar years (-0.32). In a split fitted on 2011 to 2020 and tested on 2021 to 2026, the faster set is higher at every
  step in the fit and the slower set at every step in the test, with differences that are not significant (section 5.7):
  a speed set chosen on one half of the history did not hold on the other half.

Keeping only 16/64 and slower made +3.9% in 2025 against the baseline's -14.3%, and +45.0% over sixteen years against
+64.7% (unrounded, on the sizing capital, before costs; section 5.7).

### 1.3 What the book earns from, and what it pays

Over the sixteen years the signal comes mostly from energy, then bonds and metals; FX lost. Dollars on the chosen
setup's one 16-year capital path, before costs: the target book is unrounded, the gap is the whole-contract book less it.

| Class | Target book | Gap | Fast (2/8, 4/16, 8/32) | Slow (16/64, 32/128, 64/256) |
|---|---:|---:|---:|---:|
| FX | -55.4k | +34.1k | -38.4k | -17.0k |
| Equity | +27.6k | +75.7k | +24.1k | +3.5k |
| Bonds | +56.4k | +38.3k | -10.9k | +67.2k |
| Energy | +198.5k | +9.0k | +122.5k | +76.0k |
| Metals | +41.2k | -76.9k | +65.4k | -24.1k |
| Ags | -16.6k | -5.3k | +21.5k | -38.1k |
| Livestock | +1.2k | +20.8k | +3.2k | -1.9k |
| Bitcoin | +17.3k | -7.9k | +8.7k | +8.5k |
| All | +270.2k | +87.8k | +196.1k | +74.1k |

- Energy is most of the 16y signal, which is one reason the loss attribution did not recommend leaving out the lumpy
  energy contracts, even though the four energy contracts (CL, RB, HO, NG) carry about as much of the rounding gap as the
  other 28 instruments together.
- Over the 2y, equity indices and bonds lost in the signal and metals carried the window (section 4.3).
- Costs are a steady drag of about 4k a year, 0.8% of 500,000, which takes about 0.05 off the Sharpe on every window and
  does not separate one setting from another. Doubling every fill's cost takes another 0.03 to 0.06 (section 3).
- The rounding gap is the whole-contract book standing away from the target. It added on average (16y +87.8k, 5y +94,004,
  2y +37,488), and it is the part of the result that moves most from start to start (section 7).

## 2. The setup the numbers belong to

- **Settings.** Sample optimiser covariance on the fixed 756-bar window and Carver's fraction step; the granularity buffer and the sign close; the risk overlay on Carver's covariance; limits by Carver's p99 method, 1.75 /
  4.75 / 3.00 / 7.25 / net 5.50; gold and silver micros on the per-contract rolls, every other contract full size; carry off; the equity slow
  rule on; held; 500,000; data cut 2026-10-23. The configuration of record is the template at 4c92eb1b8d, which is the
  chosen setup (`config_template/portfolios/conservative/risk.json:8-9`, `:14-16`, `:19`;
  `config_template/portfolios/conservative/portfolio.json:33-34`, `:65`, `:67`; AlgoGators/algogators commit
  c42dbbe6f8).
- **Why the overlay reads Carver's covariance.** It is Carver's own estimator for the overlay (AFTS, tactic four): each
  contract's sizing volatility, the estimate its position is sized on, with correlations of weekly returns (Saturday to
  Friday) exponentially weighted over a 26-week span (`include/trade_ngin/risk/overlay.hpp:72`,
  `kCarverCorrelationWeeks`). Weekly returns avoid the low correlations that daily closes taken hours apart give, which is
  his reason for them. The 26-week span weights recent weeks most, so a rise in correlations shows within weeks, where an
  equal-weight sample of the last 252 dates spreads it over a year. Carver notes that the method "isn't too sensitive to
  how the covariance matrix is estimated" (AFTS, footnote 289, p.622); the limits are set by his 99th-percentile method on
  this same estimator, so estimator and limits go together. The data: in the final comparison of the two overlay
  covariances on 2026-10-27 (the granularity buffer with gold and silver micros, each overlay on its own p99 limits, 17
  runs a set), account Sharpe after costs was 0.40 / 0.33 / 0.20 / 0.32 / 0.17 on the year / 2y / 5y / 10y / 16y with
  Carver's estimator and 0.06 / 0.27 / 0.21 / 0.26 / 0.44 with the sample; on the older limits the 16y read 0.40 and 0.38,
  so the 16y difference comes with the limits. Every set of the later final grid ran Carver's estimator
  ([`LEVERS.md`](LEVERS.md), "Overlay covariance: sample or Carver's").
- **The lookback setting is not on main.** `sample_cov_window` was built and measured at 160, 252 and 504 bars on every
  base and not adopted. It was dropped from the release: its results are recorded in [`LEVERS.md`](LEVERS.md) and its code is on the archive
  branch `archive/keep-do-not-delete/levers` of AlgoGators/trade-ngin. The 756 window is `kOptimiserBars`, a literal 756 at
  `src/strategy/trend_following.cpp:665`.
- **Runs of record.** The chosen setup's runs are from the second tuning round, on the same settings as the release, on a data copy with
  the holes filled, real MGC and SIL bars. The release reproduced the chosen setup's three anchor runs (section 3.1): every
  export cell was identical at the head of the release, also when run from the template (`AlgoGators/trade-ngin#282`).
- **Windows.** The 16y is one run from first fill 2011-08-21; the 10y, 5y, 2y and year each have five starts (default
  first fills 2016-08-22, 2021-08-22, 2024-08-21, and year starts from 2025-09-07). The warm-up is `lookback_years` 16 /
  11 / 6 / 3 for the 16y / 10y / 5y / 2y, and the year on lookback 3 from its `first_traded_date`.
- **Measure.** Account Sharpe after costs: daily account returns (equity over the previous day's equity), weekend rows
  folded into Monday, mean x 260 over population standard deviation x sqrt 260. The engine stores its own statistics, which
  differ slightly (16y engine Sharpe 0.297066, engine maximum drawdown 26.6155%). Section 6.3 sets the two side by side;
  this document does not use the engine's figures elsewhere.

## 3. The final numbers, per window

Mean over the starts (16y: the one run), with the range across starts in brackets. Return, Sharpe, volatility, drawdown and
worst month are on the account (weekday closes, 260 days) unless the row says otherwise; costs are dollars.

| Measure | 16y | 10y | 5y | 2y | Year |
|---|---:|---:|---:|---:|---:|
| Account Sharpe after costs | 0.300 | 0.290 (0.145 to 0.394) | 0.273 (0.206 to 0.453) | 0.102 (-0.173 to 0.542) | 0.407 (-0.078 to 0.859) |
| Account Sharpe before costs | 0.352 | 0.340 | 0.321 | 0.153 | 0.457 |
| Return a year, account | 3.15% | 3.01% | 2.83% | 0.30% | 5.84% |
| Total return, account | 60.07% | 35.81% (11.26% to 57.06%) | 15.42% (10.25% to 29.69%) | 0.65% (-9.22% to 16.67%) | 6.46% (-2.52% to 15.64%) |
| Volatility a year, account | 13.28% | 13.45% | 13.69% | 16.85% | 16.67% |
| Volatility a year, sizing capital | 18.6% | 19.2% | 18.7% | 17.9% | 18.1% |
| Maximum drawdown, account (weekday closes) | 25.88% | 24.07% | 21.97% | 14.94% | 11.18% |
| Worst month, account | -8.18% | -9.46% | -8.18% | -5.45% | -5.17% |
| Round trips a year | 152.8 | 173.6 | 189.4 | 215.5 | 199.2 |
| Costs a year ($) | 4,019 | 4,022 | 3,874 | 4,272 | 4,207 |
| Costs in Sharpe units, account | 0.052 | 0.050 | 0.048 | 0.051 | 0.049 |
| Account Sharpe after costs at 2x costs | 0.255 | 0.241 | 0.222 | 0.052 | 0.359 |
| Tracking error of the whole-contract book against the target book | 7.69% | 7.89% | 8.81% | 8.36% | 9.94% |
| Margin, mean share of the account | 20.77% | 18.19% | 14.37% | 17.80% | 16.66% |

- The sizing-capital volatility row is the one to compare with the 20 percent target; the account row falls as profit is
  set aside (section 6.2). It runs at about 18 to 19 percent: whole-contract rounding and the buffer leave part of the
  target unfilled, and Carver caps the IDM at 2.5 "to avoid excessive leverage on highly diversified portfolios",
  noting that "if you do this you will undershoot your expected risk target" (AFTS, Appendix B, p.644).
- Costs take about 0.05 off the Sharpe on every window; doubling every fill's cost takes another 0.03 to 0.06.
- Drawdowns are on weekday closes; with the weekend troughs included the 2y raw maximum is 15.2%.
- Names held are each run's median over its days: 14 on the 16y, and as means of the five starts' medians 14.8 (10y), 14.6
  (5y), 16.0 (2y) and 15.8 (year); single runs run from 14 to 17. Positive months on the account run from 49% (16y) to 57%
  (year).

### 3.1 Every start

Every cell is account / sizing capital, on the tuning's measure (weekday closes, 260 days; section 6.3). Sharpe after and
before costs both divide by that basis's own volatility. Return a year is compounded on the account and mean points x 260
on the sizing capital.

| Window | Run | Return total, % | Return a year, % | Sharpe after costs | Sharpe before costs | Vol a year, % | Max drawdown, % |
|---|---|---:|---:|---:|---:|---:|---:|
| 16y | 16y (first fill 2011-08-21) | 60.1% / 83.0% | 3.1% / 5.5% | 0.30 / 0.29 | 0.35 / 0.35 | 13.3% / 18.6% | 25.9% / 34.5% |
| 10y | Default (first fill 2016-08-22) | 57.1% / 73.1% | 4.5% / 7.2% | 0.39 / 0.37 | 0.44 / 0.42 | 13.6% / 19.3% | 21.4% / 34.5% |
| 10y | From 2016-09-05 | 11.3% / 23.3% | 1.1% / 2.3% | 0.15 / 0.12 | 0.20 / 0.17 | 14.5% / 18.9% | 27.5% / 43.6% |
| 10y | From 2016-09-19 (first fill 2016-09-20) | 24.5% / 36.7% | 2.2% / 3.6% | 0.23 / 0.19 | 0.28 / 0.24 | 13.3% / 19.4% | 27.7% / 50.0% |
| 10y | From 2016-10-03 | 40.1% / 48.7% | 3.4% / 4.8% | 0.33 / 0.25 | 0.38 / 0.30 | 12.3% / 19.2% | 23.9% / 51.1% |
| 10y | From 2016-10-17 | 46.2% / 60.2% | 3.9% / 6.0% | 0.35 / 0.32 | 0.40 / 0.37 | 13.5% / 19.1% | 19.8% / 29.5% |
| 5y | Default (first fill 2021-08-22) | 11.0% / 15.7% | 2.0% / 3.0% | 0.22 / 0.17 | 0.27 / 0.22 | 13.8% / 18.3% | 19.9% / 29.1% |
| 5y | From 2021-09-05 | 10.3% / 14.9% | 1.9% / 2.9% | 0.21 / 0.16 | 0.26 / 0.21 | 13.9% / 18.3% | 20.0% / 29.1% |
| 5y | From 2021-09-19 | 13.1% / 15.5% | 2.4% / 3.0% | 0.25 / 0.16 | 0.30 / 0.21 | 13.0% / 18.6% | 27.0% / 45.1% |
| 5y | From 2021-10-03 | 29.7% / 36.0% | 5.3% / 7.1% | 0.45 / 0.37 | 0.50 / 0.42 | 13.3% / 19.2% | 21.8% / 35.2% |
| 5y | From 2021-10-17 | 13.0% / 18.4% | 2.5% / 3.7% | 0.24 / 0.19 | 0.29 / 0.24 | 14.4% / 19.1% | 21.2% / 29.5% |
| 2y | Default (first fill 2024-08-21) | -8.9% / -7.9% | -4.2% / -3.6% | -0.16 / -0.20 | -0.11 / -0.15 | 17.3% / 17.9% | 16.0% / 18.1% |
| 2y | From 2024-09-04 | -2.0% / -0.4% | -0.9% / -0.2% | 0.03 / -0.01 | 0.08 / 0.04 | 16.5% / 17.3% | 14.3% / 16.5% |
| 2y | From 2024-09-18 | -9.2% / -8.3% | -4.5% / -4.0% | -0.17 / -0.22 | -0.12 / -0.17 | 17.6% / 18.1% | 16.0% / 18.1% |
| 2y | From 2024-10-02 | 6.6% / 8.3% | 3.2% / 4.0% | 0.27 / 0.23 | 0.32 / 0.28 | 16.3% / 17.8% | 14.6% / 17.3% |
| 2y | From 2024-10-16 | 16.7% / 18.3% | 7.9% / 9.0% | 0.54 / 0.48 | 0.59 / 0.53 | 16.6% / 18.6% | 13.8% / 16.3% |
| Year | From 2025-09-07 | 15.5% / 16.8% | 13.7% / 14.8% | 0.86 / 0.79 | 0.90 / 0.84 | 16.4% / 18.7% | 12.8% / 15.5% |
| Year | From 2025-09-21 | 15.6% / 17.3% | 14.3% / 15.8% | 0.84 / 0.80 | 0.88 / 0.84 | 17.7% / 19.8% | 11.4% / 13.1% |
| Year | From 2025-10-05 | 0.4% / 1.8% | 0.3% / 1.7% | 0.11 / 0.09 | 0.16 / 0.13 | 19.1% / 20.0% | 13.8% / 15.3% |
| Year | From 2025-10-19 | -2.5% / -1.4% | -2.5% / -1.4% | -0.08 / -0.08 | -0.02 / -0.03 | 15.9% / 16.5% | 9.0% / 9.6% |
| Year | From 2025-11-02 | 3.3% / 4.0% | 3.4% / 4.1% | 0.30 / 0.26 | 0.36 / 0.32 | 14.2% / 15.6% | 8.8% / 9.4% |

All 21 account Sharpes were recomputed for this document from each run's stored equity curve, by the definition of
section 2 and independently of the tuning's own report tool; every one equals the stored value to every printed digit,
and the five-start means equal the stored means. Three runs are the release's reproduction anchors, by the sha256 of
their uncompressed exports: the 16y (+60.075%, Sharpe 0.299973), the default 2y (-8.86098%, -0.159552) and the year from
2025-10-05 (+0.352205%, 0.114435). On the 2y and the year the net limit at 5.50 binds on one cycle (2026-06-10, m
0.99837); on the 16y net leverage is the binding term on 54 of the 193 cut cycles. So the chosen setup's anchors, not
those of the 5.75 variant, are the ones to use.

## 4. What drives the 2y and the 5y: the loss attribution

Net P&L of every run splits into five parts that sum to the stored total to the cent: the target book (the unrounded
positions the forecasts ask for, priced exactly), the rounding gap (whole-contract book minus target book: rounding,
search, buffer, limits), a residual, strategy costs and roll costs. For the chosen setup, five-start means (16y: the one run). Dollars:
net is the account after costs; the target book is the unrounded target on each run's own sizing capital, before costs;
each window's run starts at 500,000, and the 16y is one run.

| Window | Net | Target book | Rounding gap | Residual | All costs |
|---|---:|---:|---:|---:|---:|
| 16y | +300.4k | +270.2k | +87.8k | | |
| 5y | +77,108 | +417 | +94,004 | +2,419 | -19,732 |
| 2y | +3,241 | -33,030 | +37,488 | +7,732 | -8,949 |
| Year | +32.3k | +31.2k | -1.0k | | |

### 4.1 Not costs, not rolls

The chosen setup pays about 4k a year in fill and roll costs together, a steady drag of about 0.8% of 500,000 a year (0.97% of the mean
sizing capital on the 16y) that does not separate the sets. Roll-bar moves are positive (+10,951 on the 5y, +13,594 on the
2y).

### 4.2 The signal: flat over the 5y, a loss over the 2y

- The 5y target book is one good year against the rest: 2022 +102,947, 2025 -56,927.
- The 2y target book by calendar year, mean of five starts: 2024 +8,214, 2025 -67,994, 2026 +26,750. 2025 alone lost more
  than the window's -33,030.
- The worst months are signal months: the chosen setup's five worst 5y months lose 23.8k to 37.0k in the target book and under 6.3k
  in the gap.

### 4.3 The 2y by asset class

The chosen setup, five-start means. Dollars, as in section 4: net on the account after costs, target book unrounded on each run's own
sizing capital.

| Class | Net | Target book | Gap | Costs |
|---|---:|---:|---:|---:|
| FX | +769 | -11,303 | +12,013 | -1,933 |
| Equity | -23,256 | -49,153 | +25,760 | -450 |
| Bonds | -13,003 | -23,445 | +11,504 | -1,734 |
| Energy | -15,574 | -11,242 | -8,214 | -803 |
| Metals | +50,792 | +53,067 | -1,989 | -555 |
| Ags | -12,993 | -5,421 | -5,687 | -1,446 |
| Livestock | +9,135 | +2,744 | +6,984 | -593 |
| Bitcoin | +7,370 | +11,724 | -2,883 | -1,436 |

Equity indices and bonds lost in the signal; metals carried the window (GC+MGC +34.2k, SI+SIL +18.0k). On the 5y, energy
is the chosen setup's best class (+75,080, of which +47,496 is gap) and equities the worst (-35,548).

### 4.4 The 2y by speed

Dollars of the unrounded target book before costs: the 2y row on five runs from their own starts, the 16y row on one
16-year capital path. The two are not comparable per unit of capital: the 2y's default start has a mean sizing capital of
452,640, the 16y path 415,356, and 388,789 over its last two years (section 6.6).

| Speed | 2/8 | 4/16 | 8/32 | 16/64 | 32/128 | 64/256 | Target book |
|---|---:|---:|---:|---:|---:|---:|---:|
| The chosen setup, 2y (mean of starts) | -17,518 | -17,292 | -10,109 | -2,101 | +4,311 | +9,679 | -33,030 |
| The chosen setup, 16y | +48,333 | +82,613 | +65,159 | +33,348 | +15,570 | +25,162 | +270,185 |

On the 2y the fast speeds lost 44.9k and the slow made 11.9k; over the 16y the fast made 196.1k of 270.2k, and 2025 is the
fast speeds' worst year in sixteen (-65.4k; next worst 2019, -17.9k). So the 2y loss is a period in which the fast rules
whipsawed, not a defect of the rules. Named instruments that lost on the 2y made money over the 16y in the target book (NQ+MNQ +50.6k, HO +80.2k).
Section 5.6 gives the split by class, year and instrument.

### 4.5 The 2y months

The chosen setup's worst 2y months, five-start means. Dollars, as in section 4: net on the account after costs, target book unrounded on
each run's own sizing capital.

| Month | Net | Target book | Gap | What lost it |
|---|---:|---:|---:|---|
| 2025-02 | -25,462 | -29,271 | +4,032 | slow-trend reversal (slow speeds -22.3k) |
| 2025-10 | -22,397 | -22,665 | +735 | fast-speed whipsaw (fast -15.5k) |
| 2025-01 | -21,646 | -12,909 | -8,389 | both: trend, and HO and CL under target as energy rose |
| 2026-08 | -21,419 | -15,394 | -5,659 | slow reversal plus size (MBT against the forecast, SI+SIL at 2.41x) |
| 2025-04 | -19,731 | -29,265 | +9,824 | fast-speed whipsaw (fast -21.4k) in the tariff shock |

"What lost it" is the attribution's reading of each month. Costs in each of these months are 0.2k to 0.5k.

### 4.6 The rounding gap: what separates starts and sets

- The gap is the whole-contract book's deviation from the target book, at a tracking error of about 8% a year; the four
  energy contracts (CL, RB, HO, NG) carry about as much of it as the other 28 instruments together.
- On average it added to the chosen setup's result (5y +94,004, 2y +37,488). The chosen setup loses on the 2y starts
  where the gap did not offset the signal (2024-08-21 -44.3k, 2024-09-18 -46.1k).
- Where the chosen setup's 5y differs most from the other setups measured with it is in self-locked positions (a lumpy
  contract the search no longer wants, kept because it sets its own buffer): +48k on the 5y mean, almost all one RB
  contract in spring 2022.

## 5. SG Trend, speeds and positions

The book is compared with two SG series: the SG Trend Index, an index of trend funds net of their fees and with cash
interest, and the SG Trend Indicator, a published pure-trend model (a 20-day against 120-day moving-average crossover,
always long or short, sized by inverse volatility, equal risk by sector). The chosen setup moves with the Index (monthly correlation
0.64 to 0.76 over the 5y to the 16y) but returns less than it over the same months, and it trails the Indicator's last two
years.

In October 2026 the gap was rebuilt independently three times on the book before the micros and the data fill: SG's rule
on the book's own markets (section 5.3), a second rebuild of the book and SG's rule made from the book's record and SG's
series alone (section 5.4), and a recomputation of every price, forecast and target of the last two years from the raw
contract bars (section 5.3). All three agree on 2025: no defect, no sizing choice explains it, and the three fast speeds
lost it on markets the book already held. They do not agree on 2026, where the result of SG's rule on the book's markets
depends on Bitcoin, the weights and the rule's mechanics. A slower speed set made money in 2025, made less over the
sixteen years, and, chosen on one half of the history, did not hold on the other; the six speeds stay as Carver's design
(section 1.2).

### 5.1 The chosen setup against SG

Monthly account returns against each SG series over the same full months, to 2026-09, from the default start of each
window. The book's returns here end at 2026-09 and compound whole months, so they differ from section 3, which ends at
2026-10-23.

| Window (months) | The chosen setup's return, account | SG Trend Index return | Correlation / beta with the Index | SG Trend Indicator return | Correlation / beta with the Indicator |
|---|---:|---:|---|---:|---|
| 16y (181) | 68.7% | 88.5% | 0.64 / 0.85 | -16.7% | 0.63 / 0.50 |
| 10y (121) | 63.9% | 65.8% | 0.64 / 0.81 | 0.2% | 0.64 / 0.48 |
| 5y (61) | 16.3% | 49.4% | 0.76 / 0.83 | 10.1% | 0.67 / 0.42 |
| 2y (25) | -6.2% | 20.1% | 0.58 / 0.71 | 21.0% | 0.55 / 0.37 |
| Year from 2025-10-05 (11) | 10.0% | 19.6% | 0.12 / 0.16 | 20.0% | -0.08 / -0.04 |

The Indicator lost 16.7% over the sixteen years in which the book made 68.7%. Its own record splits the same way the
book's speeds do: -37.8% over 2011-08 to 2020 and +29.4% over 2021 to
2026-10-06.

### 5.2 One book, several readings of 2025

The October rebuilds were made on the starting book of the first tuning round (full-size gold and silver, before the
data fill), and its 2025 appears with different figures because each used a different basis. They are the same book.

| Reading of 2025 | Figure | Basis |
|---|---:|---|
| Unrounded target book, compounded | -14.3% | daily P&L on the sizing capital, before costs |
| Unrounded target book, summed by month | -13.4 points | sizing capital, before costs |
| Unrounded target book, vol-normalised | -11.2% | scaled to the Indicator's 17.8% vol, 2011 to 2026, before costs |
| Capped unrounded target book | -10.8% | percent of 500,000, before costs |
| Whole-contract book, on mean sizing capital | -5.6% | before costs |
| Whole-contract book, on the account | -3.7% | engine equity, net of costs |
| SG Trend Indicator | +3.9% | as published; the second rebuild took it as net of a 2% fee with cash interest, which it estimated adds 1 to 2 points |

The whole-contract book made +12.6% over the 24 months against about 0% for the unrounded book, on mean sizing
capital. That difference is rounding and which contracts were held, not signal, so the signal analyses
below all use the unrounded book.

### 5.3 First rebuild: SG's rule on the book's markets (2026-10-26)

SG's rule was rebuilt on the book's own 36 adjusted series, and the book's forecasts went through the identical sizing
frame. Every line is scaled to 17.8% vol, with no costs.

| Book | 2024 | 2025 | 2026 to October | 16y sum | 16y Sharpe |
|---|---:|---:|---:|---:|---:|
| SG Trend Indicator (actual) | -9.5% | +3.9% | +14.3% | | |
| SG's rule on our 36 | -2.4% | +17.3% | -13.1% | -21% | -0.05 |
| Our combined forecast | +1.8% | -11.2% | +4.8% | +74% | 0.20 |
| Our combined, no attenuation | -5.9% | -5.3% | +5.4% | +48% | 0.13 |
| 2/8 alone | +5.2% | -27.4% | +2.8% | +73% | 0.17 |
| 4/16 alone | +4.0% | -31.8% | +13.1% | +91% | 0.25 |
| 8/32 alone | +0.4% | -18.2% | +9.2% | +69% | 0.21 |
| 16/64 alone | -6.8% | +5.8% | -5.2% | +50% | 0.15 |
| 32/128 alone | -9.0% | +13.6% | -1.1% | +27% | 0.06 |
| 64/256 alone | +7.4% | +10.9% | +1.3% | +24% | 0.05 |

So 2025 was the rule mix: SG's rule on the book's own markets made +17.3%, the three fast speeds lost 18% to 32% each, and
equal weight across six speeds let them sink the year. For 2026 this rebuild read the universe as the cause: SG's rule on
the book's markets lost 13.1% while the Indicator made +14.3%, and the book's own rules were 18 points above SG's rule on
the same markets. The second rebuild does not support that reading as it stands (section 5.4). Over sixteen years on this
universe the book's combined forecast made +74% (Sharpe 0.20) and SG's rule -21% (-0.05), and 4/16 alone made +91%
(0.25), all scaled to 17.8% vol with no costs.

Business-day spans (Sunday rows folded) took the 16y Sharpe from 0.21 to 0.27 on an independent rebuild outside the
engine, with no costs, but left 2025 at -10.2%. The book counts Sunday as its own day because the Sunday evening session
is a real exchange session that prints its own bar, and the runner trades on the bars the feed prints (decided on
2026-09-03, kept on 2026-10-28). Folding Sunday into Monday moves every volatility, forecast and limit, so it waits for a
full engine retune; whether the volatility estimators should count business days is an open design question
([`../06_open/FUTURE_WORK_ADVICE.md`](../06_open/FUTURE_WORK_ADVICE.md), section "Open design questions and their
issues", the question on volatility estimators on counted rows or business days).

A separate recomputation rebuilt every close, return, adjusted level, forecast and unrounded target of 2024-10-23 to
2026-10-23 from the raw contract bars, agreed with the engine to 1e-9, and found no data or engine defect. In 31 of the 33
symbol-quarters with a strong move, the forecast was on the right side by 2 or more.

### 5.4 Second rebuild, independent of the first (2026-10-26)

A second rebuild was made from the book's record and SG's series alone, without the first rebuild's explanations. All
figures are fractions of sizing capital, no costs, summed by month. It priced the book by speed and copied SG's rule on the
same prices, and its pricing reproduces the engine's equity curve (correlation 0.99998).

| Period | Ours | SG's rule on our markets, our weights | SG Trend Indicator | Ours minus the rule |
|---|---:|---:|---:|---:|
| 2025 | -13.37 | +5.09 | +4.61 | -18.46 |
| 2026 January to September | +11.44 | +14.20 | +14.79 | -2.76 |
| 24 months, 2024-10 to 2026-09 | +0.83 | +22.39 | +17.60 | -21.56 |

The Indicator's own months are largely from markets the book does not trade or sizes differently: the monthly correlation
with the book is 0.32, and with SG's rule on the book's markets 0.45. Its largest months (2024-10 -9.6%, 2026-02 +11.8%,
2026-03 -11.1%) do not appear when its rule runs on the book's markets. Over the 24 months market and weight differences
net +4.8 points in the book's favour, so they shape SG's path and do not explain the book's shortfall.

**The two rebuilds against each other.** Both rebuilt SG's rule on the book's markets and they differ, because the frame
differs: the first scales to 17.8% vol and uses equal risk by sector with Bitcoin as its own sector; the second uses a 15%
vol target and sums months, once with the book's risk weights and once with SG's four equal sectors on the 35 markets
without Bitcoin. Their mechanics differ too: daily sign and a 32-day EWMA against a monthly rebalance and a 0.97 decay.

| SG's rule on our markets | 2025 | 2026 |
|---:|---:|---:|
| First rebuild (equal risk by sector, 17.8% vol, 2026 to October) | +17.3% | -13.1% |
| First rebuild, no Bitcoin | +13.5% | -2.8% |
| Second rebuild, SG's four equal sectors, 35 markets, no Bitcoin (sum of months, 2026 January to September) | +10.93 | +0.82 |
| Second rebuild, the book's weights (sum of months, 2026 January to September) | +5.09 | +14.20 |

Every version is positive in 2025, so the 2025 finding stands in all of them: the rule made money on markets where the book
lost. In 2026 the two no-Bitcoin rows nearly agree, both about flat (-2.8% and +0.82). The range from -13.1% to +14.2% moves
with Bitcoin, the weights and the rule's mechanics: crypto is -10.4 points of the -13.1%, while the second rebuild has
Bitcoin at +1.40 in the same year. So neither rebuild settles how much of the Indicator's 2026 is the universe. The second
rebuild's own reading is narrower: SG's month-to-month path is largely driven by markets the book does not trade or sizes
differently, and its confidence on what drives SG's path is low to moderate.

SG's single medium speed sat near the best point of 2025. The same rule on the book's markets at other speeds:

| Rule on our markets, our weights | 2025 | 2026 January to September | 24 months |
|---|---:|---:|---:|
| 5/20 | -24.38 | +16.66 | -6.07 |
| 10/40 | +3.73 | +10.64 | +13.44 |
| 10/60 | +2.24 | +7.28 | +14.20 |
| 20/120 | +5.09 | +14.20 | +22.39 |
| 40/160 | +1.06 | +17.61 | +13.02 |
| 50/200 | -6.27 | +22.00 | +10.15 |

### 5.5 Positions by speed

The book's P&L splits exactly by speed (fractions of sizing capital, unrounded, no costs, summed):

| Period | Total | 2/8 | 4/16 | 8/32 | 16/64 | 32/128 | 64/256 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2024 Q4 | +2.76 | +1.19 | +0.86 | +0.96 | +0.46 | -0.59 | -0.13 |
| 2025 | -13.37 | -4.92 | -7.11 | -4.87 | -0.15 | +1.68 | +2.00 |
| 2026 January to September | +11.44 | +0.28 | +2.93 | +2.83 | +0.90 | +2.00 | +2.50 |

Splitting each month's P&L into the position held at the start of the month and the changes made within it shows how
2025 was lost. In 2025 the start-of-month positions earned +7.76 points and the changes within months lost 21.13: the book
added after moves that then reversed. In 2024 Q4 the split was -6.92 and +9.68, and in 2026 to September +12.94 and -1.50.

| 2025 by class | Total | 2/8 | 4/16 | 8/32 | 16/64 | 32/128 | 64/256 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bonds | -10.18 | -2.47 | -2.40 | -1.91 | -1.17 | -1.03 | -1.21 |
| Energy | -8.16 | -0.20 | -1.92 | -2.06 | -1.67 | -1.26 | -1.06 |
| FX | -7.22 | -1.73 | -2.48 | -1.42 | -0.38 | -0.42 | -0.79 |
| Equity index | -4.00 | -1.42 | -1.40 | -0.76 | +0.09 | +0.02 | -0.54 |
| Bitcoin | -1.83 | -1.04 | -0.75 | -0.23 | +0.25 | +0.07 | -0.12 |
| Grains and oilseeds | +0.74 | +0.12 | -0.15 | -0.58 | 0.00 | +0.45 | +0.90 |
| Livestock | +1.79 | 0.00 | +0.16 | +0.04 | +0.30 | +0.63 | +0.67 |
| Metals | +15.48 | +1.82 | +1.82 | +2.04 | +2.43 | +3.22 | +4.16 |

The worst day was 2025-04-09, the tariff reversal: the book was short almost every risk market and lost 8.7% (the
Indicator 3.6%). Market by market, Treasuries had no trend in 2025 and the book lost all twelve months, late on every turn;
MES sat flat through March (forecast 0 under the equity slow rule), was short at the April turn, then long and churning in
a flat fourth quarter; NG and RB were on the right side but small and late at turns.

### 5.6 The same split on the chosen setup

Sections 5.2 to 5.5 are on the book before the micros and the data fill. The chosen setup's own target book splits the same way. Every
figure here is dollars of the unrounded target book before costs; the split by speed is pro rata to each speed's capped
scaled forecast, exact except on days the combined forecast is capped or zeroed by the equity slow rule.

The 2y, by class and speed, mean of five runs from their own starts:

| Class | Target book | 2/8 | 4/16 | 8/32 | 16/64 | 32/128 | 64/256 |
|---|---:|---:|---:|---:|---:|---:|---:|
| FX | -11,303 | -3,549 | -4,764 | -1,448 | -161 | -503 | -878 |
| Equity | -49,153 | -9,753 | -11,434 | -8,881 | -5,391 | -6,053 | -7,640 |
| Bonds | -23,445 | -5,924 | -1,500 | -4,058 | -3,816 | -4,456 | -3,691 |
| Energy | -11,242 | +5,176 | -2,645 | -4,300 | -5,133 | -3,553 | -787 |
| Metals | +53,067 | -4,125 | +1,713 | +7,770 | +11,683 | +15,835 | +20,190 |
| Ags | -5,421 | +838 | +44 | -3,288 | -3,027 | -50 | +63 |
| Livestock | +2,744 | 0 | -990 | -654 | +578 | +1,790 | +2,019 |
| Bitcoin | +11,724 | -181 | +2,285 | +4,750 | +3,165 | +1,302 | +403 |
| All | -33,030 | -17,518 | -17,292 | -10,109 | -2,101 | +4,311 | +9,679 |

Metals and Bitcoin made money at every speed from 4/16 down, and metals made more the slower the speed. Equity and bonds
lost at all six speeds. Summing the month table behind section 4.5, 2025 on these means is fast -79.7k and slow +11.7k, so
the fast speeds' 2y loss of 44.9k is smaller than their 2025 loss: they made 34.8k back in the months around it.

The chosen setup's one 16-year path, by calendar year (a different capital path from the table above, section 6.6):

| Year | Target book | Fast (2/8, 4/16, 8/32) | Slow (16/64, 32/128, 64/256) | 2/8 | 4/16 | 8/32 | 16/64 | 32/128 | 64/256 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2011 | -29,928 | -15,557 | -14,371 | -4,214 | -5,580 | -5,763 | -5,576 | -2,837 | -5,958 |
| 2012 | -33,845 | +20,157 | -54,003 | -1,959 | +12,748 | +9,369 | -4,460 | -24,358 | -25,185 |
| 2013 | +28,101 | +11,216 | +16,885 | +2,210 | +3,664 | +5,342 | +2,672 | +3,495 | +10,718 |
| 2014 | +130,446 | +68,470 | +61,976 | +14,197 | +27,088 | +27,185 | +26,559 | +21,369 | +14,047 |
| 2015 | +10,469 | +338 | +10,131 | -1,926 | +3,749 | -1,485 | -6,146 | +988 | +15,288 |
| 2016 | -24,554 | +10,654 | -35,208 | +15,544 | +3,443 | -8,334 | -7,118 | -9,860 | -18,230 |
| 2017 | -16,663 | -2,274 | -14,389 | +9,915 | -1,205 | -10,984 | -10,574 | -5,045 | +1,229 |
| 2018 | +33,148 | +42,187 | -9,039 | +13,449 | +17,812 | +10,927 | +5,371 | -2,957 | -11,453 |
| 2019 | -38,310 | -17,911 | -20,399 | -9,199 | -4,445 | -4,267 | -8,871 | -7,402 | -4,127 |
| 2020 | +175,362 | +92,583 | +82,779 | +25,112 | +27,823 | +39,647 | +37,487 | +24,336 | +20,956 |
| 2021 | +4,706 | -15,707 | +20,413 | -13,673 | -4,499 | +2,465 | +5,937 | +12,114 | +2,361 |
| 2022 | +100,585 | +34,932 | +65,653 | +9,278 | +12,167 | +13,487 | +12,770 | +22,229 | +30,655 |
| 2023 | -13,780 | +11,516 | -25,296 | +5,481 | +4,598 | +1,437 | -1,210 | -11,204 | -12,882 |
| 2024 | -19,077 | +1,870 | -20,947 | +2,774 | +1,666 | -2,570 | -9,134 | -10,707 | -1,106 |
| 2025 | -57,352 | -65,449 | +8,098 | -19,050 | -27,126 | -19,273 | -2,153 | +4,454 | +5,796 |
| 2026 | +20,878 | +19,079 | +1,799 | +393 | +10,710 | +7,976 | -2,207 | +956 | +3,051 |
| All | +270,185 | +196,104 | +74,081 | +48,333 | +82,613 | +65,159 | +33,348 | +15,570 | +25,162 |

2025 is the fast speeds' worst year in the sixteen (-65.4k; the next worst is 2019, -17.9k), and 4/16 is both their worst
speed that year and the best speed over the whole run.

The positions behind the 2y, the named instruments, mean of five starts. Net and gap are of the whole-contract book; target
sign flips are counted per contract; held / target is the multiple of the target actually held:

| Instrument | Net | Target book | Fast | Slow | Gap | Target sign flips | Held / target |
|---|---:|---:|---:|---:|---:|---:|---:|
| NQ+MNQ | -12,562 | -4,815 | -2,884 | -1,932 | -7,989 | 2.0 | 1.60 |
| YM+MYM | -11,352 | -14,625 | -6,870 | -7,754 | +3,355 | 8.0 | 1.07 |
| ZN | -11,821 | -4,256 | -1,815 | -2,441 | -7,198 | 44.8 | 0.66 |
| ZS | -11,783 | -8,045 | -2,750 | -5,295 | -3,410 | 19.0 | 0.86 |
| NG | -9,008 | -22,124 | -11,791 | -10,333 | +12,650 | 23.4 | 0.54 |
| UB | -7,819 | -16,130 | -6,894 | -9,237 | +9,115 | 41.8 | 1.28 |
| HG | -7,641 | -17,169 | -11,123 | -6,047 | +9,622 | 49.6 | 0.37 |
| HO | -6,131 | +14,697 | +11,589 | +3,108 | -21,391 | 32.2 | 0.29 |
| HE | -6,624 | -3,014 | -2,566 | -448 | -3,432 | 15.2 | 0.42 |
| 6B | -5,693 | -9,174 | -3,805 | -5,369 | +3,471 | 37.8 | 0.30 |
| MBT | +7,370 | +11,724 | +6,854 | +4,870 | -2,883 | 25.8 | 0.96 |
| SI+SIL | +18,044 | +18,457 | +1,749 | +16,708 | -413 | 53.6 | 1.06 |
| GC+MGC | +34,242 | +44,724 | +14,785 | +29,938 | -10,420 | 19.0 | 0.94 |

Two kinds of loss show. ZN, UB, HG and 6B whipsawed, with 37.8 to 49.6 target sign flips, and rounding helped three of the
four. NQ+MNQ and HO are size: NQ+MNQ was held at 1.60x and the part above target lost 6,238, while HO's target book made
14,697 and the chosen setup held 0.29x of it and missed 18,446. The worst months of section 4.5 are not all fast: they include
fast-speed whipsaw (2025-04, 2025-10) and slow-trend reversal (2025-02, 2026-08).

Selective speed rules keep every speed but switch the fast ones off by class or by regime. What each does, and why none
is in the book:
- **The equity fast-speed cut** runs the equity indices on 16/64 and slower only (a narrower form drops only 2/8 and
  4/16). No equity speed is cut: Carver removes a speed only on a contract where its cost is above his speed limit, and
  the equity micros' 2/8 costs 0.018 to 0.049 a year against his 0.15 ([`../01_the_book/THE_BOOK.md`](../01_the_book/THE_BOOK.md),
  section "The speed limit: rules removed by cost"); a cut chosen on 2025's losses is the over-fitting Carver warns of
  (AFTS, strategy nine, p.207 to 208). The book's one asymmetry for equities is its equity slow rule, adopted on this
  book's record of fast-only equity shorts, which lets an equity short open only when the slow speeds agree. The cut
  removes the fast speeds on the long side as well, where Carver finds no bouncing-cat effect (strategy twelve, figure
  53, p.266). On the 16-year target
  book the equity fast speeds made +24.1k (section 1.3); with the cut the equity class's 16-year target book read +85.9k
  against +27.6k, its weight moving to the slow speeds.
- **The high-volatility switch** turns a contract's fast pairs off while its smoothed volatility quantile is above 0.75.
  The book already answers a high-volatility regime as Carver does, by attenuating every forecast as the quantile rises
  (AFTS, strategy thirteen; [TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md), section "1.5 The trend rules:
  EWMAC, scaling, capping"); the switch replaces that graded response with an on/off cut that Carver does not use. It
  was found on the period it fixes (2021 to 2026), and over 2011 to 2020 it lost (-3.1 volatility-matched points against
  the baseline, offline; [`LEVERS.md`](LEVERS.md), "Selective rules, offline").
- **Limits.** The p99 method gives each book its own limits from its own history, so a set read at the chosen setup's
  limits is not on the limits it would trade on. The equity fast-speed cut with the high-volatility switch has its own
  limits at R_shock 3.25 and net 5.75, against 3.00 and 5.50; at the chosen setup's limits the overlay cut it on 241 16y
  cycles against 193.

The data, as whole contracts (account Sharpe after costs, 16y one run, others five-start means; the third tuning round):

| Set | Limits | 16y | 10y | 5y | 2y | Year | Mean of the five |
|---|---|---:|---:|---:|---:|---:|---:|
| The chosen setup | its own | 0.300 | 0.290 | 0.273 | 0.102 | 0.407 | 0.274 |
| Equity indices drop 2/8 and 4/16 | the chosen setup's | 0.362 | 0.240 | 0.250 | 0.389 | 0.455 | 0.339 |
| The equity fast-speed cut (equity indices 16/64 and slower) | the chosen setup's | 0.370 | 0.337 | 0.122 | 0.379 | 0.517 | 0.345 |
| The equity fast-speed cut with the high-volatility switch | the chosen setup's | 0.440 | 0.440 | 0.214 | 0.244 | 0.320 | 0.332 |
| The same, on its own limits | its own p99 | 0.365 | 0.353 | 0.202 | 0.248 | 0.041 | 0.242 |
| The high-volatility switch alone, on its own limits | its own p99 | 0.313 | 0.308 | 0.143 | 0.231 | 0.246 | 0.248 |

Each lifts the 2y and gives something up on another window. On its own limits the equity fast-speed cut with the
high-volatility switch reads 0.242 against 0.332 at the chosen setup's limits, with its year at 0.041.

### 5.7 The choice analysis: is any design choice the cause?

The engine's trend sleeve was rewritten independently with every design choice a switch, and each was switched off alone.
The rewrite matches the engine on all 152,769 estimator rows (unrounded target within 7.7e-11 contracts over 2025-26, P&L
to the dollar each year 2021 to 2026). Returns are compounded on the sizing capital, unrounded, before costs.

| Variant | 2025 | 2026 to 10-23 | 2y | 16y | 16y Sharpe | Correlation with the Indicator, 24 months |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | -14.3% | +6.5% | -10.6% | +64.7% | 0.26 | 0.31 |
| All six speeds on every instrument | -16.4% | +6.2% | -13.3% | +61.8% | 0.26 | 0.32 |
| Attenuation off | -14.4% | +7.5% | -11.7% | +29.1% | 0.18 | 0.33 |
| Equity slow rule off | -11.0% | +4.3% | -9.5% | +44.6% | 0.22 | 0.32 |
| FX excluded | -9.1% | +6.9% | -9.7% | +117.0% | 0.35 | 0.15 |
| Volatility on weekday rows, x 16 | -19.4% | +6.4% | -16.2% | +54.7% | 0.24 | 0.29 |
| Volatility blend off (current EWMA only) | -15.0% | +4.6% | -12.7% | +88.1% | 0.31 | 0.30 |
| Equal instrument weights | -13.5% | +2.2% | -12.6% | +15.7% | 0.14 | 0.31 |
| IDM 3.13 | -18.1% | +7.8% | -14.2% | +70.5% | 0.26 | 0.31 |
| Without 2/8 | -11.1% | +8.4% | -6.6% | +62.1% | 0.26 | 0.41 |
| 8/32 and slower | -3.5% | +6.5% | -0.9% | +51.1% | 0.24 | 0.51 |
| 16/64 and slower | +3.9% | +4.2% | +3.7% | +45.0% | 0.22 | 0.57 |
| Book's class-and-group weights | -19.5% | +5.1% | -16.8% | +71.3% | 0.28 | 0.20 |
| Book's strategy 13 attenuation | -15.2% | +9.7% | -9.8% | +62.4% | 0.26 | 0.28 |
| SG Trend Indicator (to 2026-10-06) | +3.9% | +14.3% | +19.8% | -19.5% | | |

Only the speed set moves 2025 by more than 6 points, and only toward SG when fast pairs come out. Full compounding, the
table 52 FDM, the ten-calendar-year blend and the sizing volatility clamp move nothing (the first changes dollars, not
percent); IDM 3.13 is leverage, with the Sharpe unchanged. Volatility on weekday rows was lower on every window shown;
the book takes volatility on the bars the feed prints, Sunday sessions included, for the reason in section 5.3.

The speed sets were then put through a split, fitted on 2011-08-21 to 2020 and tested untouched on 2021 to 2026-10-23
(Sharpe on sizing capital):

| Speed set | 2011 to 2020 | 2021 to 2026 |
|---|---:|---:|
| All six | 0.386 | 0.059 |
| Today's, after the cost removals | 0.364 | 0.109 |
| Without 2/8 | 0.317 | 0.169 |
| 8/32 and slower | 0.244 | 0.223 |
| 16/64 and slower | 0.184 | 0.281 |

In the fit window the faster set is higher at every step, in the test window the slower set at every step, in the same
order, and the differences are not significant (16/64 and slower: t -1.1, then +0.8). A choice made in 2020 would have
been all six; a slower set chosen now is chosen on the last two years. The Indicator's own record reverses between the
same halves (section 5.1). Fitted jointly over 135 combinations, the best in-sample set (0.462) scored 0.015 out of
sample.

Fitting the speeds by class gave the same picture. In vol-matched points against the baseline, class-fitted speed weights
gave +1.9 and +2.8 over 2021-26, their 16y gains sit in the fit window, and refitted on 2011-2018 they gave -0.4 and +0.2
over 2019-26. The rank correlation of speed Sharpes between fit and test is -0.77 for the whole book. Strategy sixteen on
speed groups is below the baseline on every window (2021-26 -5.4 to -5.6, 16y -17 to -37); fast-minus-slow relative
performance is negatively autocorrelated (-0.32 on calendar years).

Two changes were above the baseline in both halves of the split. The volatility blend off (current EWMA only) went from
0.364 to 0.414 in sample and 0.109 to 0.126 out of sample, a difference with a t of 0.35 out of sample, and it makes
2025-26 slightly lower. The blend of the current estimate with its long-run average is Carver's, and the book keeps it
for his reason: volatility mean-reverts, and a short estimate alone builds very large positions in the quiet spell
before a crisis (AFTS, strategy three, p.89 to 90; [TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md), section "1.4 Volatility"). Excluding FX went from 0.364 to
0.447 and 0.109 to 0.206, and the FX sleeve lost in 10 of 16 years. That removes an asset class on its past results;
the book keeps every instrument because which market makes the next large move cannot be known in advance
([`../01_the_book/THE_BOOK.md`](../01_the_book/THE_BOOK.md), section "Why these 36 markets, and the gold and silver
micros"). Carver's own rules do not decide it: his 0.01 cost limit removes nothing, and his minimum capital removes every
FX pair and 34 of the 36 instruments at 500,000. Neither change was adopted, and FX stayed as it is (2026-10-26).

### 5.8 What was concluded

1. The engine computes what its design says; no data or engine defect explains the gap.
2. No sizing choice explains 2025. The largest move toward SG is 5.2 points, from dropping FX, against an 18-point gap.
3. 2025 was the fast speeds. The 2026 gap to the Indicator is small on the second rebuild's basis (3.4 points) and its
   cause is not settled: SG's rule on the book's markets ranges from -13.1% to +14.2% with Bitcoin, the weights and the
   mechanics, and is about flat without Bitcoin.
4. A slower speed set made money in 2025 and about 20 points less over sixteen years, and, chosen on one half of the
   history, did not hold on the other. The six speeds stay as Carver's design, with removal only by his speed limit.
5. The selective rules that kept every speed but used them by class or by regime were taken to whole-contract runs (the
   equity fast-speed cut, the high-volatility switch, and the two together). None is in the book: see section 5.6 and
   "Selective speed rules" in [LEVERS.md](LEVERS.md). Nothing measured closes the 2-year gap to the Indicator (+19.8%;
   the best selective rule that keeps every speed made +1.6%, SG's own rule through the book's sizing +4.6%, the baseline
   -10.6%, unrounded on sizing capital).
6. Whether to add the markets behind the Indicator's 2026 is a study, `AlgoGators/algogators#184` (see
   [`../01_the_book/THE_BOOK.md`](../01_the_book/THE_BOOK.md), section "Why these 36 markets, and the gold and silver
   micros").

## 6. Two ways of reading the book: sizing capital and account

The book's P&L is one series of dollars, read against two capitals. The **account value** V is 500,000 plus the cumulative
net P&L. The **sizing capital** E is 500,000 less the drawdown of that cumulative P&L from its running peak, never above
500,000: a loss comes off it at once and a profit rebuilds it only up to the start. It is the capital the book is sized on
(Carver's half compounding, applied here in the backtest as well as live), and the difference V - E is profit set aside.
Because risk is targeted on E, volatility on E sits near the 20 percent target while volatility on V falls as profit is set
aside, and return, Sharpe and drawdown read differently on each, so a figure means little until its capital is named.

The mechanics are in [TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md): section "1.9 The risk target
and the sizing capital" gives the formula, the code and a day-by-day table of stored live rows; "1.17 The statistics as
they are stored" defines every stored column and its capital; "4. Reading the results" says which capital each measure is
read on. The rule, set on 2026-10-08, is that drawdown is judged on the account and volatility on the sizing capital, and
every tuning round compared settings on the account Sharpe after costs. This section is about what the difference does to
the numbers of this book.

Figures marked "recomputed" come from the stored equity curves of the chosen setup's runs by a read-only script, which reproduces the
stored 16-year account Sharpe of 0.299973 to every printed digit.

### 6.1 The point of the rule, in one day

In the worked table of "1.9 The risk target and the sizing capital", on 2026-05-01 the account stands at 505,871.57, above
the start, and the book is sized on 494,865.61, because the 5,134.39 lost the day before came off the sizing capital at
once. The profit set aside that day is 11,005.96, the earlier peak of the cumulative P&L, although the account is only
5,871.57 above the start. The book shrinks after every loss and does not grow with profit beyond the start.

### 6.2 Why the two readings differ

**Volatility.** Every target and every overlay reading is on E, and the 20 percent target applies to it. V holds E plus
the profit set aside, so the same dollars are a smaller return on V: to first order, volatility on V is volatility on E
times E / V. On the chosen setup's 16-year run the two start close together, because little profit has been set aside, and they part as
it builds (recomputed; calendar years with at least 20 weekdays):

| Year | Mean E | Mean V | V / E | Vol on V, % | Vol on E, % | Net P&L | Points of E | % of 500k |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2011 | 465,869 | 494,059 | 1.06 | 17.4 | 18.2 | -20,217 | -4.2 | -4.0 |
| 2012 | 444,120 | 489,916 | 1.10 | 15.8 | 17.4 | -31,733 | -6.5 | -6.3 |
| 2013 | 416,359 | 467,583 | 1.12 | 12.7 | 14.3 | +25,227 | +7.2 | +5.0 |
| 2014 | 407,464 | 461,432 | 1.13 | 16.3 | 18.5 | +112,713 | +25.4 | +22.5 |
| 2015 | 423,273 | 555,468 | 1.31 | 16.5 | 21.4 | -25,147 | -4.1 | -5.0 |
| 2016 | 415,345 | 548,547 | 1.32 | 13.4 | 17.7 | -19,741 | -3.2 | -3.9 |
| 2017 | 384,403 | 517,623 | 1.35 | 11.1 | 15.0 | -18,541 | -3.8 | -3.7 |
| 2018 | 390,754 | 524,091 | 1.34 | 16.0 | 21.2 | +15,846 | +6.4 | +3.2 |
| 2019 | 384,958 | 518,124 | 1.35 | 11.0 | 14.7 | -25,009 | -5.3 | -5.0 |
| 2020 | 442,430 | 592,629 | 1.34 | 17.0 | 22.6 | +130,810 | +32.5 | +26.2 |
| 2021 | 480,688 | 722,056 | 1.50 | 12.8 | 18.5 | +48,711 | +10.3 | +9.7 |
| 2022 | 459,769 | 806,783 | 1.75 | 13.4 | 23.1 | +123,688 | +27.5 | +24.7 |
| 2023 | 392,635 | 781,158 | 1.99 | 8.2 | 16.2 | -60,830 | -13.9 | -12.2 |
| 2024 | 389,195 | 778,064 | 2.00 | 8.4 | 16.6 | +47,545 | +13.3 | +9.5 |
| 2025 | 368,275 | 756,763 | 2.06 | 9.4 | 19.2 | -48,968 | -11.7 | -9.8 |
| 2026 | 411,870 | 800,624 | 1.94 | 9.6 | 18.8 | +46,022 | +13.2 | +9.2 |

The first rows show V / E near 1.1 and the two volatilities within about 2 points; from 2023 V is about twice E and
volatility on V is about half volatility on E. Volatility on E is not flat at 20 percent: it runs from 14.3 percent (2013)
to 23.1 percent (2022) by year. The target is forward-looking and the stored forward reading is `overlay_risk`;
`volatility_sizing` is the realised figure (`migrations/036_live_results_statistics.sql:87`, `:98`). The same mechanism
sorts the windows: a short window starts with V = E = 500,000, so its two volatilities are close (2y mean 16.8 against
17.9 percent); a long window has years of profit set aside (16y 13.3 against 18.6 percent).

**A loss cuts E at once, while V can stay above 500,000.** So E tracks the distance from the peak and V the distance from
the start. On the chosen setup's 16-year run the account peaked at 888,746.91 on 2022-06-14 and E has not been at 500,000 since
mid-June 2022. The stored row of 2026-10-23 holds `account_value` 797,029.44 and `sizing_capital` 408,282.53: the account
is 59 percent above the start and the book is sized on 18 percent less than the start. The difference, 388,746.91, is
exactly the peak of the cumulative net P&L (888,746.91 - 500,000). The capped-account form, E = min(500,000, account), was tried and not
adopted because it takes nothing off while the account is above the start ([TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md), section "3.
What was tried and not adopted").

**Return differs in kind as well as in denominator.** The account total is compounded equity over the start; the sizing
total is the sum of the daily points, and its annual figure is their mean x 260. On the 16 years the same 300,375 dollars
are 60.07 percent of 500,000 and 83.0 points of E, at a mean E of 415,356. The annual 3.1 against 5.5 percent therefore
mixes two effects: the smaller denominator and compounding against plain summing.

**Sharpe is the closest of the readings.** It is a ratio, so the level of the capital largely cancels. What remains is the
weighting: on E each day counts by one over that day's E, so a path whose E is low in a losing stretch reads lower on that
form. The chosen setup's 16y reads 0.30 and 0.29; its 5y mean reads 0.27 and 0.21. Half compounding therefore costs account return and
not risk-adjusted return: on 2026-10-08 two 16-year runs measured half compounding taking 30.4 and 18.4 points of account
return and nothing from the Sharpe on the sizing capital. A later measurement shows the same thing from the other side:
the unrounded trend book on the starting book of the first tuning round makes +259.6k at Sharpe 0.26 on its own E path,
and +392.9k at 0.27 on a fixed 500,000.

**Drawdown is not comparable across the two.** On the account it is the fall of the equity level as a share of its peak; on
the sizing capital it is the fall of the running sum of the daily points, in points
(`migrations/036_live_results_statistics.sql:86`). The second is larger because the losing days are divided by a shrinking
E, which is why the account figure is the one read: a drawdown summed in points of a shrinking sizing capital overstates
the picture.

**The Sunday row.** Bars are stamped by UTC date and most contracts print a short Sunday bar, so a Sunday row carries the
Friday-to-Sunday move of the symbols that print. A Sunday's run has no new bar to size on, so it stores no E, and its P&L
counts in the next sized row's E. The stored sizing statistics divide a Sunday grid row's P&L by the last capital stored at
or before that date, Saturday's (`migrations/036_live_results_statistics.sql:86-88`). Whether Sunday P&L belongs in the
sizing series at all is open and not measured: see the question on Sunday sizing capital in the sizing-capital statistics
in [`../06_open/FUTURE_WORK_ADVICE.md`](../06_open/FUTURE_WORK_ADVICE.md), section "Open design questions and their
issues".

**Which columns are on which capital.** The stored statistics are on the account except `max_drawdown_sizing`,
`volatility_sizing` and `worst_day_sizing`, which exist on futures rows only; the overlay's readings (`overlay_risk`,
`overlay_gross_leverage`, `overlay_net_leverage`, `var_95_1d`) are on the sizing capital
(`migrations/036_live_results_statistics.sql:98-103`), while `portfolio_leverage` and `net_leverage` on the live row are
on the account value at the previous close, as the comments on the two overlay leverage columns state (`:101-102`).
The columns came in as migrations 036 (live) and 037 (backtest), with no backfill: rows written earlier hold NULL
(`migrations/036_live_results_statistics.sql:10-11`, `:21-22`, `migrations/037_backtest_statistics.sql:12`).

### 6.3 Two sets of numbers on one capital

The stored statistics and the tuning's report fold the weekend differently and take E from a different day, so even on one
capital there are two sets of numbers.

| | Stored statistics (`backtest.results`, `trading.live_results`) | Tuning report |
|---|---|---|
| Days | Sunday-to-Friday grid dates with at least 9 symbols; a Saturday row adds to the next return | weekend rows folded into Monday, weekday closes |
| Annualisation | K = 311.0574 | 260 |
| Standard deviation | sample | population |
| E on a day's return | the last stored row on or before the grid date: the day's own row, or Saturday's on a Sunday | the stored value of the previous weekday, one day older |
| Definition | [TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md), section "1.17 The statistics as they are stored"; `migrations/036_live_results_statistics.sql:87` | section 2 above |
| The chosen setup's 16y Sharpe on the account | 0.297066 | 0.299973 |
| The chosen setup's 16y vol on the account / on E | 13.54% / 18.88% | 13.28% / 18.55% |
| The chosen setup's 16y max drawdown on the account / on E | 26.62% / 34.31% | 25.88% / 34.5% |

The tuning report divides a day's P&L by the previous weekday's stored E, while the stored statistics use the day's own E.
That is one of the reasons 18.55 percent differs from 18.88 percent; how much of the gap it explains was not split.

The stored statistics of the default runs (engine statistics, read from each run's stored `backtest.results` row):

| Run | Total return | `volatility` | `volatility_sizing` | `sharpe_ratio` | `max_drawdown` | `max_drawdown_sizing` | `worst_day` | `worst_day_sizing` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 16y | 60.07% | 13.54% | 18.88% | 0.297 | 26.62% | 34.31% | -5.45% | -6.71% |
| 10y | 57.06% | 13.82% | 19.59% | 0.390 | 21.53% | 34.31% | -6.80% | -8.17% |
| 5y | 11.02% | 13.82% | 18.31% | 0.215 | 19.86% | 28.32% | -4.50% | -6.36% |
| 2y | -8.86% | 17.59% | 18.22% | -0.153 | 16.30% | 17.58% | -7.47% | -7.77% |
| Year from 2025-10-05 | 0.35% | 19.36% | 20.28% | 0.115 | 13.77% | 15.34% | -7.61% | -7.96% |

### 6.4 The chosen setup on both capitals, window means

Every cell is account / sizing capital, on the tuning's measure. 16y: the one run; the others: mean of five starts. The
runs one by one are in section 3.1.

| Measure | 16y | 10y | 5y | 2y | Year |
|---|---:|---:|---:|---:|---:|
| Return a year, %, account | 3.1 | 3.0 | 2.8 | 0.3 | 5.8 |
| Return a year, %, sizing capital | 5.5 | 4.8 | 4.0 | 1.1 | 7.0 |
| Sharpe after costs, account | 0.30 | 0.29 | 0.27 | 0.10 | 0.41 |
| Sharpe after costs, sizing capital | 0.29 | 0.25 | 0.21 | 0.06 | 0.37 |
| Sharpe before costs, account | 0.35 | 0.34 | 0.32 | 0.15 | 0.46 |
| Sharpe before costs, sizing capital | 0.35 | 0.30 | 0.26 | 0.11 | 0.42 |
| Vol a year, %, account | 13.3 | 13.5 | 13.7 | 16.8 | 16.7 |
| Vol a year, %, sizing capital | 18.6 | 19.2 | 18.7 | 17.9 | 18.1 |
| Max drawdown, %, account | 25.9 | 24.1 | 22.0 | 14.9 | 11.2 |
| Max drawdown, %, sizing capital | 34.5 | 41.7 | 33.6 | 17.2 | 12.6 |

- The risk target was held: volatility on E is 17.9 to 19.2 percent on the means, and 0.78 to 1.00 of 20 percent on single
  runs. Volatility on V runs from 13.3 to 16.8 percent on the means.
- The account Sharpe after costs is at or above the Sharpe on E on every window mean, so the tuning's measure is the higher
  of the two here. The largest gap on the means is 0.06, on the 5y.
- Costs take about 0.05 of Sharpe on either capital. In money they are 0.80 percent of 500,000 a year and 0.97 percent of
  the mean E over the 16 years.
- Drawdown reads deeper on E on every run, by 1.4 points (year) to 17.6 points (10y) on the means. By the reading rule the
  account row is the one quoted.

### 6.5 The capital size study read on both capitals

The study of 2026-10-08 ran the book at 500,000 to 10,000,000 with only the two capital keys changed, on the book before
the micros, so its numbers are not the chosen setup's. Its findings on size are in [`LEVERS.md`](LEVERS.md), section
"Capital size". What it teaches about reading the two capitals:

- **Risk reads as the same book only on E.** Volatility on the sizing capital is 18.7 to 19.2 percent at every capital, 0.94
  to 0.96 of the target, as the median of 18 five-year windows. Single runs spread wider, from 18.20 percent (the 5-year
  run at 500k) to 23.41 percent (the 2-year run at 2m).
- **Account return and Sharpe on E answer different questions, and they order the sizes differently.** On the 18-window
  medians the account return reads 5m above 10m above 2m (+10.88, +8.43, +6.77) while the Sharpe on E and the points read
  2m above 5m above 10m (0.078, 0.073, 0.047; +8.11, +7.39, +4.75). These three are one book, with account returns on the
  same window apart by a mean of 3.0 and 1.7 points; what the reordering shows is that the two columns are not
  interchangeable. The figures are medians taken column by column, so a Sharpe there is not the same
  window's return over the same window's volatility.
- **The numerator of the Sharpe on E is the points, not the account return.** At 500k on the 5-year medians, points a year
  over volatility is 2.86 / 18.69 = 0.153, close to the stored 0.155, while the account's annual +3.52 over the same
  volatility would give 0.188, a mixed figure with no meaning.
- **Drawdown nearly doubles on E.** 23.68 percent on the account against 43.01 points at 500k. As medians, E averages 0.819
  of the start over a window's days, and a window ends with the account at 1.196 times the start and E at 0.675.
- **A cost percentage depends on the denominator.** At 500k costs are 0.609 percent of the start a year and 0.75 points of
  E.

Mixing the two gives figures that belong to neither: 16-year Sharpes of 0.369 and 0.275, computed from the account's
daily returns and printed beside a volatility on the sizing capital, are 0.387 and 0.228 on the sizing capital.

### 6.6 A case where the difference mattered: the last two years

**The 16-year path was sized on much less than 500,000.** On the 16-year record of the starting book of the first tuning
round (before the micros and the data fill), half compounding sized the last two years on 291k to 355k, a mean of 63
percent of 500k, because that run had not been back at its peak since 2015. Recomputed from that run's stored capital:
291,045 to 354,616, mean 317,223 from 2024-10-23 to 2026-10-23; the mean over the 24 full months is the stored 316,018. A
fresh 2-year run starts at 500k. A comparison in percent of 500,000 of that slice therefore understates the book by about a
third. A vol-normalised comparison of the same slice is unaffected.

**The same 24 months read four ways.** From the record of that book's 16-year run:

| Period | Book | Dollars | % of mean E | % of 500k | Engine equity, net % |
|---|---|---:|---:|---:|---:|
| 2025 | unrounded target | -43,844 | -14.09 | -8.77 | |
| 2025 | whole-contract book | -17,428 | -5.6 | -3.49 | -3.74 |
| 2026 to 10-05 | unrounded target | +37,422 | +11.62 | +7.48 | |
| 2026 to 10-05 | whole-contract book | +47,973 | +14.89 | +9.59 | +9.25 |
| 2024-10 to 2026-09 | unrounded target | -770 | -0.24 | -0.15 | |
| 2024-10 to 2026-09 | whole-contract book | +39,727 | +12.57 | +7.95 | +6.92 |

The mean E of those periods is 311,159, 322,125 and 316,018. The dollar rows are before costs; the engine column is the
account's net change over its own equity at the start of the period. So the same 24 months of the whole-contract book read
+12.57, +7.95 or +6.92 percent. A fourth basis applies to the signal alone: the unrounded target's 2025 is -14.3 percent
compounded on E, and -11.2 percent when vol-normalised to 17.8 percent with no costs.

**Own start against the 16-year slice.** On the unrounded book a window run from its own start equals the 16-year slice
exactly in percent and differs only in dollars, through the capital. The baseline's dollars:

| Window | Own start at 500k | Slice of the 16-year path |
|---|---:|---:|
| Year | +41k | +23k |
| 2y | -52k | -37k |
| 5y | -2k | -7k |
| 10y | +210k | +200k |

Here each book is sized on its own half-compounded capital, so the 16-year path is sized on about 73 percent of a fresh
start. That is a different capital path from the 63 percent above, which follows the whole-contract book. Both are correct
for what they describe.

**On the chosen setup itself the slice is less low, and whole-contract paths do not line up.** The chosen setup's own 16-year path was sized on
340,242 to 443,656 from 2024-10-23 to 2026-10-23, a mean of 388,789 or 77.8 percent of 500,000 (recomputed). Comparing the chosen setup's
own starts with the slice from the same first fill:

| Window | First fill | Own start: net, mean E, points of E | 16y slice: net, mean E, points of E |
|---|---|---:|---:|
| 10y | 2016-08-22 | +285,285, 425,488, +73.1 | +261,108, 409,547, +69.7 |
| 5y | 2021-08-22 | +55,096, 425,832, +15.7 | +59,178, 408,636, +18.2 |
| 2y | 2024-08-21 | -44,305, 452,640, -7.9 | +22,983, 388,478, +8.4 |
| Year from 2025-10-05 | 2025-10-05 | +1,761, 466,807, +1.8 | +29,214, 401,996, +9.0 |

Own start and slice differ here by much more than the capital: on the whole-contract book the difference is the path, not
the capital, because the two runs hold different positions, and one whole-contract decision can carry tens of points of a
window. The 2y own start lost 44,305 while the slice from the same day made 22,983. The exact equality in percent holds for
the unrounded book only.

### 6.7 Which to quote

| The question | Quote | Capital to name |
|---|---|---|
| What did the account make? | total return, dollars | account, after costs |
| Was the risk target held? | `volatility_sizing` realised, `overlay_risk` forward | sizing capital |
| What does the account lose at worst? | `max_drawdown`, in percent and dollars | account |
| How do two settings compare? | Sharpe after costs on the same capital on both sides | account, as in the tuning; name it |
| How does the book compare with a benchmark over a slice of a long run? | a vol-normalised or points-of-E series, never dollars or % of 500,000 of the slice | vol-normalised, or sizing capital |
| What does a setting do to the signal? | the unrounded target book, before costs, with the capital path named | unrounded target on E |

### 6.8 The daily e-mail

The daily book e-mail is the Python report (`AlgoGators/trade-ngin#261`). Its Volatility, Sharpe ratio, Sortino ratio
and Max drawdown lines read the stored account columns
(`services/rpc/algogators_rpc/services/report/calc.py:239-242`,
`services/rpc/algogators_rpc/services/report/builder.py:421-426`), and it prints one figure of the other capital, the day's
sizing capital in dollars (`services/rpc/algogators_rpc/services/report/builder.py:404-410`, `:431`). No `_sizing`
statistic is printed, so the e-mail's volatility line is the account column, not the figure the target is read against.
The futures
release reports are the labelled form: "Every figure is of the account after every cost unless it says sizing capital",
with volatility and drawdown shown on the sizing capital as separate lines
(`scripts/futures_release/report/live_report.py:219`, `:223`, `:244`;
`scripts/futures_release/report/backtest_report.py:123`, `:127`).

## 7. Limits of the evidence

- **Carver's standard error of the Sharpe ratio.** By Carver's formula for the error of a Sharpe estimate (AFTS,
  strategy three), one standard error of the 16-year Sharpe is about 0.26 (about 0.45 on a 5y and 0.69 on a 2y). The
  formula assumes independent returns; trend-following returns are fat-tailed and autocorrelated (on the earlier
  full-size 2y, ten days carried 37% of the variance). The largest paired |t| between the sets of the second tuning
  round is 1.14.
- **The spread across start dates.** A different quantity from the standard error: the chosen setup's Sharpe runs
  from 0.206 to 0.453 across its five 5y starts and from 0.145 to 0.394 across its 10y starts (section 3). The 16y is one
  run, so its spread across start dates is not measured.
- **The 16y is one run.** A quarter step in the net limit moves a set's 16y Sharpe by -0.020 to +0.092, as much as
  several set differences.
- **Start-date noise.** Across five starts two weeks apart, the chosen setup's 5y net ranges over 97k and its 2y net over 129k, mostly
  gap. The 2y Sharpe runs from -0.173 to 0.542 and the year from -0.078 to 0.859 (section 3). The starts share almost all
  of their days, so they are close to one draw, not five.
- **One period.** The five 2y starts all share 2024 to 2026, and the year starts lie inside the same period, so the 2y
  and the year describe that period.
- **Conventions.** Drawdowns are on weekday closes and weekend margin dates divide by Monday's equity, inherited from the
  tuning's report tool. Margins are today's applied to every year, so the margin row is indicative.
- **Limits are rounded.** The chosen setup's net p99 is 5.5926, 0.032 under the rounding midpoint; 5.75 would have given 0.339 on the
  16y, 0.280 on the 10y and 0.245 on the 5y, identical on the 2y and year.
- **The year window is the live year.** The five year starts run from 2025-09-07 to 2025-11-02; they say most about the
  live period and least about the book over the long run.

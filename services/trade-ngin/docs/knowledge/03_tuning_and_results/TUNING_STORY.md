# The tuning story

From the first volatility and churn measurements on 2026-09-24 to the settings of the final book on 2026-10-29, the
futures book went through thirty-three rounds of measurement. This file tells, round by round, what each established
about how a 500,000 book of whole contracts behaves: the mechanism it showed, the data that shows it (book and window
named), and what in the book follows from it. The long form of every setting is in [`LEVERS.md`](LEVERS.md) (by lever
number) and [`../02_decisions/CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) (by section number).

## What the rounds established

1. **One contract decides a window.** A 20% risk target on a 500,000 account asks for a fraction of one contract in the
   largest instruments (gold, silver, heating oil, gasoline, copper, platinum). Whether the book holds that one contract
   carried 40 to 90 points of a window's return, and a start date, an end date, a data correction or a fee can tip that
   decision either way (rounds 11, 13, 16, 18). Gold and silver on micro contracts remove most of it (round 20).
2. **Churn rises with capital.** The no-trade band is as wide as the risk of one contract of the lumpiest name, so as
   contracts become small against the account the band shrinks to its floor, and the book holds more names and trades
   more at the same risk: 143 round trips a year at 500,000 against 2,369 at 10 million (round 12). Account size alone
   does not explain the churn of Carver's fixed buffer at 500,000; what does is unmeasured (round 16). What cut churn on
   this book did it by holding fewer names or less risk (rounds 7, 8).
3. **Results depend on the start date.** A two-month shift in the start moved a 5-year result by 46 points and a 10-year
   result by 97, because the whole-contract holdings differ from the first traded day (round 13); the book is read
   across several starts.
4. **The rounding gap, not the signal, separates setups and starts.** Setups that change the optimiser or the limits
   share one unrounded target book; what differs is what whole-contract rounding keeps or adds (rounds 20, 29, 31).
5. **One symbol's data reaches the whole book.** The optimiser builds one covariance on the dates every symbol shares,
   so a hole in one series moves every position (rounds 23, 24, 27).
6. **Risk does not change with capital.** A larger account holds more names and trades more; it runs the same risk
   (round 12).
7. **The fast speeds carry most of the signal's return and its 2025 loss.** A speed set chosen on one half of the
   history did not hold on the other (round 21).

The final book is **the chosen setup: the sample covariance in the optimiser over 756 closes, the fraction step, the
granularity buffer and the forecast-sign close, the risk overlay on Carver's covariance, and overlay limits 1.75 / 4.75 /
3.00 / 7.25 / net 5.50**, with gold and silver on micros, carry off and the equity slow rule on (round 33). Its account
Sharpe after costs is 0.300 on the 16-year run and 0.290, 0.273, 0.102 and 0.407 as five-start means on the 10-year,
5-year, 2-year and one-year windows. The book is set out in [`../01_the_book/THE_BOOK.md`](../01_the_book/THE_BOOK.md),
its numbers in [`RESULTS.md`](RESULTS.md), carry in [`CARRY.md`](CARRY.md), and the instrument set in
[`../01_the_book/THE_BOOK.md`](../01_the_book/THE_BOOK.md), section "Why these 36 markets, and the gold and silver micros".

Code citations are paths under `services/trade-ngin/` in AlgoGators/algogators at commit 4c92eb1b8d, main's merge of
`AlgoGators/algogators#98`, which imports trade-ngin d6f998da. How the loop works is in [`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md) and
[`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md); costs in [`COST_MODEL.md`](../../COST_MODEL.md); rolls in
[`FUTURES_ROLLS.md`](../../FUTURES_ROLLS.md).

## Terms used in this file

| Term | What it is |
|---|---|
| the chosen setup | the final book (round 33) |
| the starting book | the book the rounds of 2026-10-14 to 10-26 started from: full-size contracts, the granularity buffer, the overlay on the sample covariance with the limits of that time |
| the micros book | the starting book with gold and silver on micro contracts (MGC, SIL); the base of the final grid |
| the granularity buffer | the book's no-trade band, as wide as the risk of one contract of the lumpiest held or searched name ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 1.1) |
| Carver's fixed buffer | Carver's band of 1 percent of capital at a 20 percent target (AFTS, strategy twenty-five, p.431) |
| the minimal buffer | the granularity buffer with held-only lumpy contracts no longer allowed to set its width |
| the sign close | a holding against its forecast's sign is closed in one fill before the search, deferred while the opposite forecast is under 2 |
| the equity fast-speed cut | the equity indices run only the 16/64 speed and slower |
| the high-volatility switch | every instrument switches its fast speeds off when its volatility quantile is above 0.75 |
| the reference model | a reference model of the redesigned one-pass loop, run outside the engine (rounds 6 to 9) |
| p99 limits | the overlay's five limits set at the 99th percentile of the book's own uncapped 16-year readings, Carver's method |
| R, R_jump, R_shock | the overlay's three risk readings, in multiples of the risk target: expected risk, jump risk, risk under a correlation shock ([`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The overlay in brief") |
| B_sigma | the width of the optimiser's no-trade buffer, in tracking error |
| 2/8 ... 64/256 | the six trend speeds, by the spans of their two moving averages |
| the rounding gap | the whole-contract book's P&L minus the unrounded target book's |

## How to read the numbers

- **The measure.** From 2026-10-26 the figures are the account Sharpe after costs: daily account returns (equity over
  the previous day's equity), weekend rows folded into Monday, mean x 260 over population standard deviation x sqrt
  260. The engine stores its own Sharpe, which differs slightly; where an engine figure is quoted it is named as such.
  The starting book's 16-year Sharpe is 0.09 on the engine's measure and 0.08 on the weekday measure.
- **Windows.** Until 2026-10-08 the "2y" and "5y" were lookbacks 2 and 6 ending on a frozen date (2026-05-03). From
  2026-10-08 the windows end on the latest data with lookbacks 3, 6, 11 and 16 for the 2y, 5y, 10y and 16y, labelled by
  first traded day. From 2026-10-26 the year, the 2y, the 5y and later the 10y ran from five starts two weeks apart, and
  the 16y as one run.
- **Engine and reference model.** Rounds 6 to 9 ran the reference model, not the engine; rounds 1 to 5 and everything
  from round 10 are the engine. A reference-model result is labelled as one and does not carry to an engine book: the
  micros measured there in round 8 are not the gold and silver micros of round 20.
- **Two capitals.** Sharpe, return and drawdown are on the account after costs unless a table says otherwise. Volatility
  is 18 to 19 percent on the sizing capital, the figure to compare with the 20 percent target. The two capitals are
  explained in [`RESULTS.md`](RESULTS.md), section "Two ways of reading the book: sizing capital and account".
- **Run sets.** The micros book was measured on three run sets (17 runs, 12 runs, 7 runs); its mean is quoted with its
  set.

## The rounds at a glance

| # | Round | Dates | What it established |
|---:|---|---|---|
| 1 | The cost penalty sweep | 09-24 | the multiplier buys lower churn only with tracking error |
| 2 | The vol waterfall | 09-24 | the volatility gap is the IDM table value and whole-contract granularity |
| 3 | The churn variants and the correlation gate | 09-24 | the loop's damping and its cut were one mechanism; the max-pair correlation term is blind to size and direction |
| 4 | The corrections, the cut, the universes, the first micro phase | 09-25 to 09-26 | a risk cut must be delivered in whole contracts; micros on every name triple churn |
| 5 | The cut that survives the next lap | 09-27 | a cut that stands raises trading on the same signal |
| 6 | Speeds and leverage on a reference model | 10-02 to 10-04 | cutting the fast speeds saves little cost; the net limit has no smooth optimum |
| 7 | The roll lineage and the churn remedies | 10-04 | the fall came from turning shorts on, not from roll accounting; low churn came only by starving sectors |
| 8 | The deep dive on a reference model | 10-04 to 10-05 | where churn and drawdown come from |
| 9 | Tau, the capital rule and the position limit | 10-05 | half compounding flattens churn; a lower tau drops names |
| 10 | IBKR fees | 10-08 | the fee is a search input, so a fee change is a new book |
| 11 | The 16-year Sharpe inspection | 10-08 | a whole run's gap was one gold contract |
| 12 | Capital size study | 10-08 | risk is the same at every capital; granularity and churn are not |
| 13 | Latest data and the window convention | 10-08 | the windows' names and their traded spans |
| 14 | Contracts trade from their listing date | 10-08 to 10-09 | how to switch from an E-mini to its micro |
| 15 | Knobs on IBKR fees and the per-contract speed limit | 10-08 to 10-09 | a sizing rule cannot change the target book; Carver's speed limit applied |
| 16 | The live year and the sizing study | 10-14 to 10-15 | a one-contract position can sit at twelve times target inside its own buffer |
| 17 | The first tuning round: limits and the starting book | 10-26 | the p99 limits; Carver's table values fit the data |
| 18 | Why the earlier high results did not reproduce | 10-26 | they were single contracts held far over target |
| 19 | Micros and Carver's instrument selection | 10-26 | at 500,000 Carver's selection stops at seven instruments |
| 20 | The sizing matrix and the book switches | 10-26 | gold and silver micros hold the book near target; the sign close ends wrong-sided holding |
| 21 | Signal choices, selective speeds, the slow rule | 10-26 to 10-27 | 2025 is in the speeds; speed sets do not carry across halves of the history |
| 22 | Copper micro, carry, and the overlay on the micros book | 10-26 to 10-27 | carry earned about nothing on these instruments; it halves round trips and tracks a little closer |
| 23 | Real micro metals and the crude micro | 10-27 to 10-28 | one symbol's data holes move the whole book |
| 24 | The data fill | 10-28 | the holes were the book's own ingestion's |
| 25 | The overlay-covariance switch | 10-25 | Carver's estimator built into the overlay |
| 26 | The one-pass optimiser as Carver's strategy twenty-five | 10-26 to 10-27 | Carver's buffer tracks better and trades far more; why the band is as wide as it is |
| 27 | The step rule: fraction or edge | 10-28 | a step can narrow the book's sensitivity to small data differences only partly |
| 28 | The final tuning grid on complete data | 10-29 | the limits matter as much as several setup differences |
| 29 | The loss attribution | 10-29 | costs and rolls did not lose the money; the signal and the rounding gap did |
| 30 | Two new levers | 10-29 | how many returns a sample-covariance window really reads |
| 31 | Fast-speed rules and the minimal buffer on the chosen setup | 10-29 | the fast-speed rules lift the signal and rounding hands part back |
| 32 | The same rules on their own limits, and the covariance lookback | 10-29 | a book's result moves with where its limits sit |
| 33 | The chosen setup: net 5.50 and the 756-close covariance | 10-29 | the final book and the reason for each setting |

Rounds 25 to 27 ran beside rounds 20 to 24 in time. They are told after the data fill because the switches they built
entered the final grid (round 28) on the filled data.

Basis of rounds 1 to 5 unless stated: engine, whole contracts, long-only, the raw spliced price series, the window
2024-05-03 to 2026-05-03 with fills from 2025-02-27; counts scaled by 365.25 / 429; volatility and Sharpe on weekday
account returns; spread-free figures (the estimated roll spread removed) in brackets. An execution is one fill. The
engine then ran the search and the risk gate in up to five laps a day (the lap loop).

## Round 1: the cost penalty sweep (2026-09-24)

**What it established.** The cost multiplier prices a trade against the tracking error it removes, so raising it lowers
churn only by letting the book drift further from its own signal. The book then traded about 330 times a year; the
multiplier alone reached the low to mid 100s of executions only between about 300 and 500, with 25 to 60 percent more
tracking error than before the cost term was put in the right units (one contract's cost over that contract's notional,
no longer over capital).

**The data.** Eighteen runs on the 2y with nothing else changed (full table: [`LEVERS.md`](LEVERS.md), lever 30):

| Multiplier | Executions a year | Mean tracking error |
|---:|---:|---:|
| 50, before the units fix | 315.0 | 0.007093 |
| 0 | 315.9 | 0.006907 |
| 50 | 283.5 | 0.007226 |
| 100 | 279.3 | 0.007591 |
| 300 | 181.3 | 0.008877 |
| 500 | 137.9 | 0.011315 |
| 1000 | 47.7 | 0.020959 |

**What follows in the book.** The multiplier is the book's price of trading against tracking, and the one lever that
reliably cuts this book's churn toward its trade bar. It stands at 100, the top of Carver's 10 to 100 (AFTS, strategy
twenty-five, p.428), and stops there because above that range trading collapses onto the cheapest instruments, as
Carver warns (p.430): on the reference model at 300 livestock was in a position on 2% of days against 68% at 50 (round
8; set on 2026-10-05). In the objective both terms are divided by capital, so the trade-off is the same at any account
size ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 1.4; lever 30).

## Round 2: the vol waterfall (2026-09-24)

**What it established.** The book realised far less than its 20 percent volatility target, and two steps account for
most of the gap: the instrument diversification multiplier (IDM) at Carver's table value, and whole-contract
granularity at 500,000. Granularity is the account's size, not a setting.

**The data.** A step-by-step ex-ante reading of the reference book on its 368 stored days. Realised volatility is 0.1165
on weekday rows (spread-free 0.1108); only the weekday figure compares with the target, because trading rows spread the
same variance over more rows (0.1066 on trading rows, 0.0819 on all rows). The steps are sequential, so each depends on
the book the last one left.

| Step | Vol after | Points | Nature |
|---|---:|---:|---|
| target | 0.2000 | | configuration |
| IDM 2.4 and the sector-equal weights at forecast 10 | 0.1495 | -5.05 | Carver's table value |
| the actual forecast (mean absolute 9.08, RMS 10.82) | 0.1766 | +2.71 | the signal |
| the 250,000 notional concentration cap | 0.1734 | -0.32 | configuration |
| instrument vol: the estimate runs low | 0.2045 | +3.11 | a defect in part |
| the long-only drop (the book was long-only then) | 0.1970 | -0.76 | configuration then |
| the strategy's buffer and whole contracts; 17 symbols never reach one contract | 0.1416 | -5.54 | granularity |
| the optimiser, lap 1 | 0.1397 | -0.19 | the search |
| the gate and the loop | 0.1182 | -2.15 | configuration plus a loop property |
| sizing on the constant 500,000, not the equity | 0.1082 | -1.00 | configuration |
| realised, weekday | 0.1165 | +0.83 | residual |

At 2,500,000 with the search's iteration cap lifted, the same book realises 0.1671 (spread-free 0.1588), +5.06 points
against the granularity step's 5.54. A smaller contract for the 17 names is worth at most +5.52 points carried to
realised, and the three proportional levers (IDM, risk target, forecast scalar) recover about 0.57 of a point for each
point of added risk. The volatility estimate runs at 0.65 to 1.10 of realised, lowest on energy and equity indices;
correcting it lowers realised volatility, so it is a defect, not a lever.

**What follows in the book.** The IDM went to 2.5 in round 4, Carver's table value for 30 or more instruments (AFTS,
strategy four, table 16). Carver's target is meant to be realised on average; the final book runs about 18 to 19 percent
on sizing capital against 20, the gap left by whole-contract rounding and the buffer. Carver himself caps the IDM at 2.5
"to avoid excessive leverage on highly diversified portfolios" and notes that the cap undershoots the risk target (AFTS,
Appendix B, p.644). Raising the IDM would scale every
position, including those already at their target, which raises the volatility target rather than filling the rounding
gap. The 3.2 that would have made this window hit its target is the target divided by what this one window's book
realised, not an estimate of diversification. Carver's formula on the instruments' trading sub-strategy returns gives
3.13 on the later filled data (round 17); on plain price returns the same formula gives 1.87 to 2.32 over every
756-date window since 2011 (lever 33; [`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md), "The table
values"). The annualisation defect was fixed in rounds 4 and 5.

## Round 3: the churn variants and the correlation gate (2026-09-24)

**What it established.** In the lap loop, the refusal of lap 2 against the held book was both what damped churn and the
only route by which the gate's cut reached the book. And the gate's correlation term, the largest absolute pair
correlation over held names, does not see how big or which way the positions are.

**The data.** Fourteen loop variants and two gate variants on the 2y (full tables: [`LEVERS.md`](LEVERS.md), levers 31 and 32).
- **Anchoring later laps on the previous lap's book** delivered requests the strategy would have withdrawn, which then
  filled and reversed: executions 333 to 517, and days over the leverage limit 14 to 51.
- **A trigger** (trade only when the gap exceeds 1.25 or 1.5 contracts, then land at the rounded target) cut requests,
  not fills (455 to 317 to 239 requests), because landing at the rounded target makes multi-lot requests that the loop
  delivers one lot a day. The deadband applied once after the loop is churn-neutral (338 against 333 executions).
- **Reaching the churn target took a smaller book:** multiplier 500 alone 137.9, trigger 1.25 with multiplier 300 158.4,
  multiplier 400 alone 171.1, 300 alone 181.3 executions a year. Per contract of gap to the target, the multiplier alone
  at 300 removed the most executions (76 per contract of gap, against 46 to 47 for the trigger pairs and 38 at 500), and
  it stopped 6L and ZC trading.
- **The correlation term** (0.85 / rho above 0.85) bound on 204 of 368 days on this window (3,321 of 4,726 days on the
  16y long-only), with a near-constant haircut (median 12.8 percent; MES/MNQ on 200 days, ZF/ZN on 77). Three ZN/ZF books
  on 2025-11-21 of 2%, a hedged 0.5% and 12% of capital all got 0.8768. A stressed volatility of the actual book in its
  place cut on fewer days (189 and 164 against 318) but at L 0.30 traded more (331.2 against 283.5 executions).

**What follows in the book.** The redesign of 2026-09-27 replaced the max-pair term with the overlay's three risk
readings in capital terms, which scale with the positions held ([`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md),
section "The overlay in brief"). How churn moves with account size is round 12's finding: it rises with capital, as the
band shrinks to its floor when contracts become small against the account.

## Round 4: the corrections, the cut, the universes and the first micro phase (2026-09-25 to 09-26)

**What it established.** A risk cut on a whole-contract book has to be delivered as whole contracts: scaling and
rounding the book, or keeping it fractional, leaves it over the limit. Micro contracts on every name remove most of the
rounding loss and nearly triple the trading.

**The data.** Twenty-seven commits, each measured on the 2y first; the first 16-year and 5-year readings on this loop
(the 5-year run covers 2020-05-03 to 2026-05-03 with trading from 2021-03-01). Full tables: [`LEVERS.md`](LEVERS.md),
levers 31 (the cut), 34 (universes), 33 (IDM) and 35 (the first micro phase).
- **The cut.** Rounding the scaled book or keeping it fractional left 132 and 126 days over the limit. An explicit
  whole-contract cut delivered it; anchored on the held book and ordering removals inside a class by tracking error it
  made 283 executions with 1 day over. Without a class order the cut re-bought (670 executions); withholding every added
  contract froze the book (50 executions, 13 names, 4.5% volatility).
- **Universes.** All 36 names against 18, 9, the 36 with the budget moved to diversifying singletons, and the 18 and 9
  on Carver's formula IDM. The 18-name set traded more (369.5 times a year against 278.4), at an extra cost near 0.2% of
  the account a year; the 9-name sets lost money; the budget-moved set traded less and returned -8.3%.
- **Calibrations.** IDM 2.5 against 2.4: 265.6 executions a year against 278.4, 23 names traded against 22. The engine
  then re-estimated each speed's forecast scalar every day as 10 over the mean absolute raw forecast of the loaded
  history, and on this window those scalars sat above Carver's table-29 values on every speed (12.29 against 10.6 on
  2/8, 3.47 against 1.87 on 64/256). Carver's table-29 scalars in their place made smaller forecasts: the book's weekday
  volatility went from 12.1 to 7.4 percent on this loop and window, at a return of -1.28%. They were left out of this
  round's combinations only. A 10 percent proportional band traded
  543.2 times a year, nearly twice the reference; below 5 contracts a proportional band is rounding. The stressed-volatility
  gate term over 2010-08-06 to 2026-08-06 traded 372.6 times a year against 276.8.
- **The gate's window.** Over 16 years the gate had read a zero-filled fallback window on 453 stored days, and 0 after
  the fix.
- **The first micro phase** put 20 CME-verified contracts on micro or mini rows with IBKR fees per row. The rounding loss
  fell from 8.00 to 1.31 points of volatility on the 2y, and executions rose from 265.6 to 758.6 a year (1,048.9 with a
  10 percent band).

**What follows in the book.** IDM 2.5, the gate window fix, the cost model read from metadata and all 36 names. All 36
stay because trend following is paid by a few large, persistent moves, and which market will make the next one cannot
be known in advance, so each market left out is a market where such a move can no longer be caught; Carver's own answer
for a small account is dynamic optimisation over the full set ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md)
4.1; lever 34). Carver's table-29 forecast scalars have been the book's design since 2026-09-27, in the redesigned loop: table
values avoid fitting scalars to the book's own history, and on the later data the average absolute scaled forecast was
9.18 to 9.88 against Carver's 10 (round 17). The explicit cut lived until the redesign replaced the lap loop with one
pass and an overlay trim. Micros on every name were not kept: for a contract the book holds at one or none, a micro
means several contracts, and every one-lot adjustment becomes a trade (taken up again in rounds 8 and 16 to 22).

## Round 5: the cut that survives the next lap (2026-09-27)

**What it established.** A cut the next lap can undo is not a cut: after a cut, the next lap re-optimised with the buffer
anchored on the held book and put contracts back. Once the cut stands, the requests the lap used to refuse now fill, so
the book trades more on the same signal.

**The data.** Before the change, 14 of 342 cut days on the 2y delivered above the request (2 fully undone) and 158 of
1,233 on the 5y (25 fully undone); after it, 0 of 341 and 0 of 1,229, with a lap that delivers a cut ending the loop.
The volatility annualisation now reads the trailing 256 bars in live and backtest alike. On the 2y, executions rose from
312 to 395 a year (265.6 to 336.3 annualised) and cost from 1,320.94 to 1,692.90 dollars. Four weaknesses remained: the
whole-contract removal is greedy by tracking error, not a best fit (a better book existed on 199 of 341 cut days on the
2y); two-day reversal pairs drive churn (9.7 to 25.5 a year on the 5y); on four cut days in five the gate cut gross
against a term a gross cut cannot move; and the volatility blend's long-run part over all loaded history made live, 2y
and 5y estimates differ. Binding term on the 2y: correlation on 245 of 341 cut days, jump 29, leverage 67 (lever 31).

**What follows in the book.** These weaknesses are why the lap loop was replaced: the redesign (2026-09-27 to 10-01)
wrote the overlay in capital terms and one pass from the held book in place of laps, tested on the reference model in
rounds 6 to 9 ([`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md)).

## Round 6: speeds and leverage on a reference model (2026-10-02 to 10-04)

**What it established.** Removing the two fastest speeds on every instrument saves far less cost than the return it
moves, so the return difference does not come from their cost. And the net leverage limit has no level at which the
book's result changes smoothly.

**The data.** On the reference model, long and short, fixed sizing equity 500,000 (full tables:
[`LEVERS.md`](LEVERS.md), levers 36 and 37). Six, five and four speeds (FDM 1.26, 1.19, 1.13): dropping 2/8 and 4/16 on
every instrument cut strategy fills from 607 to 329 a year on the 16y and saved 2,079 dollars a year of strategy cost
(about 0.4 percent of the account), against a return gap of 3.1 points a year; the 16y Sharpe was 0.30 without the two
speeds and 0.15 with them, and on the 5y 0.14 against 0.07. On the engine's unrounded target book over 16 years, by
contrast, the fast speeds earned 196k of the book's 270k (round 29): the two figures are different books and windows.
Net limits 2 to 6, gross = net + 2, 16 runs: results jump between neighbouring settings (5y long and short: 0.63, -0.03,
0.48 at net 3, 4, 5).

**What follows in the book.** The six speeds stay. They are Carver's design, each catching trends of a different
length; he removes a speed only on a contract where its cost is above his speed limit (0.15 of Sharpe a year), and the
book applies that test (round 15; AFTS, strategy nine, table 35). A blanket cut removes speeds also where they pay. A
speed set chosen on one half of the history did not hold on the other half (round 21). The net limit is set by Carver's
p99 method, not by a grid (round 17; [`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 2.2; lever 36).

## Round 7: the roll lineage and the churn remedies (2026-10-04)

**What it established.** The fall in the backtests at the third roll commit came from the one-line share test that
turned shorts on under the old lap loop, not from the roll accounting. And on the redesigned loop, every remedy that
brought churn to about 150 round trips a year did it by starving part of the book.

**The data.** The lineage of heads with a four-way split of the third roll commit (lever 20), and the churn remedies on
the reference model (lever 21).
- **Rolls.** Long-only, roll commits 1 and 2 raised end equity on every window. Roll legs at cost 0 moved no equity cent;
  roll costs took 1,070.18 / 3,888.95 / 13,743.98 dollars on the 2y, 5y and 16y, and shorts on took 108,555.88 /
  208,965.07 / 294,247.34 under the lap loop. Under the redesigned loop shorts were positive over 16 years and churned
  at about twice the target.
- **Remedies.** Only the cost penalty at 250 to 300 reached the target with positive returns, and it did so by starving
  grains and livestock (penalty 250: grains trade on 16% of days, livestock 5%), which removes a sector's
  diversification; 300 is also outside Carver's 10 to 100. Wider buffers (x1.5, x2) under-risked the book; Carver's
  strategy-eight buffer in place of the search or on top of it doubled churn; moving a symbol fully only when the gap is
  at least 1.5 or 2.5 contracts, or trading to the edge of a one-contract zone, stopped energy and metals opening or lost
  money. A carry proxy cut round trips without lifting Sharpe.

**What follows in the book.** The roll work stays. The book is long and short: Carver's trend strategies are long and
short from strategy six on (AFTS, "Strategy six: Slow trend following, long and short"), and a long-only trend book is a
different strategy with a standing market bet in it. No churn remedy that removes a sector's trading is used; the
multiplier and the deferral band went to round 8. The bar of about 150 round trips a year is set near what the
long-only book already traded (111 to 167 round trips a year), because round trips per traded instrument (3 to 11 a
year) were already within Carver's expected range and the excess trading was resizes.

## Round 8: the deep dive on a reference model (2026-10-04 to 10-05)

**What it established.** Where churn and drawdown come from. Churn comes from positions of 0 to 2 contracts, a combined
forecast that changes sign 15 to 17 times per name a year, and a buffer applied to the whole book. Drawdown is in the
signals, not the loop. What cuts churn on this book does it by holding fewer names or less risk.

**The data.** 258 reference-model runs, long and short, 500,000, full size unless stated, on the 2y, 5y and 16y with
start-date probes, including a 96-run matrix of cost penalty, deferred sign close, equity slow rule and a one-step trim
(every knob: [`LEVERS.md`](LEVERS.md), lever 22).
- Micros on every name tripled round trips (950 / 1,059 a year on the 5y / 16y) and turned the 5y negative (-18%);
  every partial micro subset raised round trips on the 2y and 5y.
- The full-size book's 5y profit was gold, heating oil and gasoline.
- Moving only the start date by 10 to 40 days moved the 5y total from -13% to +51%.
- The deferred sign close's own effect on 16y round trips was -4. The equity slow rule did not cut churn (+29 round
  trips on the 16y) and raised the equity P&L in every pairing.
- The fractional target book alone drew down 38.8% at 15.9% volatility.
- Sector caps held fewer names (sector cap 2: 11% to 12% volatility), and hysteresis and penalties of 200 or more cut
  livestock to a fraction of days or stopped it.

**What follows in the book.** The setup taken forward on 2026-10-05: penalty 100, the sign close with its deferral band,
the equity slow rule on, the one-step trim off (158 / 208 / 292 round trips on the 2y, 5y and 16y). A sector cap changes
the risk the book runs, not how it trades, and hysteresis decides which groups of instruments the book can hold at all,
so neither is used. The equity slow rule, adopted on this book's record of fast-only equity shorts, is the one rule in
the book that goes against Carver's advice (round 21;
[`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 3.1). The sign close and its band are in round 20.

## Round 9: tau, the capital rule and the position limit (2026-10-05)

**What it established.** Under full compounding, round trips rose with history (155 to 486 a year from 2013 to 2024),
chiefly one-contract trims as equity grew; half compounding, which stops the sizing capital growing with profits,
removes them. With whole contracts a lower risk target does not scale the book down evenly: it rounds more targets to
zero, so the book holds fewer names.

**The data.** 54 reference-model runs on the setup of round 8 (full table: [`LEVERS.md`](LEVERS.md), lever 38).
- **Half compounding** held round trips at 118 to 198 a year over 16 years (172 / 155 / 158 on the 2y / 5y / 16y). On
  the capital at risk the 5y and 16y drawdowns are 28.9 and 36.7 points against 36.8 and 44.2 with full compounding, and
  the sizing capital ratchets down (about 361,000 by 2025 against an account of about 884,000).
- **Tau.** Names held fell from 17.9 to 12.1 to 10.0 on the 16y as tau went down, and tau 0.10 and 0.15 turned a window
  negative.
- **Fixed capital** made a 5y Sharpe of 0.72.
- **The position limit** at a 10 percent nominal weight changed the held book once in 16 years (heating oil, 2 to 1
  contracts, 2022-03-08; with half compounding, that run's worst day went from -10.9% to -6.7%) and clipped three target
  rows; at 20 percent the
  5y and 16y results were identical with and without it.
- **Benchmarks.** Monthly correlation with AQMIX, DBMF and KMLM is 0.47 to 0.64, and monthly skew is above every
  benchmark's.

**What follows in the book.** Half compounding at tau 0.20. Half compounding is Carver's rule for an account whose
profits are taken out: the sizing capital never rises above its starting value, and losses come off it in full (AFTS,
tactic three, p.598). Fixed capital keeps the position the same size after losses, so it grows against a shrinking
account, which is why Carver says never to use it in a live account (p.596). Tau stays at 0.20 (kept on 2026-10-06),
Carver's own risk target, a "relatively conservative" value under the 23 percent his half-Kelly estimate gives (AFTS,
strategy two, p.72); at 500,000 a lower tau rounds names out of the book, so drawdown is lowered through the book's
construction, not a smaller target. Carver's per-instrument position limit is not built, by design and not on its
numbers (decided on 2026-10-05): the overlay's per-name cap of 2, Carver's own example value for a leverage limit (AFTS,
tactic four, p.619), already bounds each name, so his limit at a chosen 10 or 20 percent weight (p.620 to 621) would be
a second, overlapping per-name cap on a picked parameter. The cap bounds a name's notional, where Carver's limit bounds a
name's share of risk (a 40 percent volatility contract at weight 2 carries 0.8 of risk against a target of 0.2), and the
overlay's risk limits bound the book's risk as a whole. The open-interest part of his limit does not bind at 500,000,
and the book holds no open-interest data. The data above are support; leaving his limit out is a departure
([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 2.4).

## Round 10: IBKR fees (2026-10-08)

**What it established.** The fee is an input to every trade decision: the search weighs a trade's cost against the
tracking error it removes, so a different fee makes a different whole-contract book from the first traded day, and each
window follows its own holdings. The one systematic effect was on contracts whose old fee misstated their cost most.

**The data.** The engine against the fee migration on the windows of that time, then eighteen 5-year windows with a
third setup (only the four equity micros back at 1.50), 45 runs in all. The windows moved a long way both ways (2y +6.50%
to +10.10%, 5y -6.96% to +17.54%, 16y +93.50% to +48.43%) while the fees actually paid changed by +358 / +938 / +178
dollars. Across the eighteen windows the paired difference had a standard deviation of 15.12 points, and the old fills
repriced at IBKR fees cost only +127 dollars a year. The equity micros trade about 7.8 more round trips a year on IBKR
fees, because at a flat 1.50 they looked more than twice as expensive as they are.

**What follows in the book.** IBKR's per-contract fee on all 40 metadata rows, adopted on 2026-10-10
(`AlgoGators/trade-ngin#172`, migration 026): the book charged a flat 1.50 dollars a contract, which is not what the
account pays, and a wrong fee makes a wrong book. The fee model is in [`COST_MODEL.md`](../../COST_MODEL.md).

## Round 11: the 16-year Sharpe inspection (2026-10-08)

**What it established.** The 500,000 book is the unrounded target book plus its whole-contract holdings. The whole gap
between the flat-fee and the IBKR 16-year runs was one gold contract held in one run and never in the other.

**The data.** A decomposition of the two stored 16y fee runs. On the sizing capital the runs made 0.387 (flat fee) and
0.228 (IBKR). The unrounded target book made 0.286 before costs. The gold contract was worth +218,150 dollars. Costs
took 0.04 of Sharpe; on 62% of days a contract with a target held nothing. SG Trend made 0.385 over the same span, a
figure that includes its cash interest (about 0.23 over cash) and is after its managers' fees.

**What follows in the book.** No defect; nothing changed. How the target book and the rounding gap make up the book's
results is in [`RESULTS.md`](RESULTS.md).

## Round 12: the capital size study (2026-10-08)

**What it established.** The risk the book runs does not change with capital. What changes is granularity (how many
names the book can hold), how much a result depends on its start date, and churn, which rises with capital as the book
holds more names and more contracts of each.

**The data.** 100 backtests with only the two capital keys changed: 500,000 and 1, 2, 5 and 10 million on eighteen
5-year windows and the 2y and 16y (full table: [`LEVERS.md`](LEVERS.md), lever 11). Volatility on the sizing capital
18.7% to 19.2% at every size; symbols held 12.9 to 34.3; standard deviation over the monthly 5-year windows 17.5 points
at 500k and about 6 to 7 from 2m up; 143 to 2,369 round trips a year, with costs 0.61% to 1.99% of capital a year. Over
the eighteen 5-year windows the median account return was +19.6% at 500k, +23.5% at 1m and +6.8% to +10.9% from 2m up
(Sharpe on sizing capital 0.155, 0.262, then 0.047 to 0.078).

**What follows in the book.** The book is sized on the account it trades, 500,000. The sizing-capital reading of the
same study is in [`RESULTS.md`](RESULTS.md), section "Two ways of reading the book: sizing capital and account".

## Round 13: latest data and the window convention (2026-10-08)

**What it established.** The window names did not match what the windows traded, and a short window's volatility can
be one contract's.

**The data.** History to 08-06 reproduced on bars refreshed to 2026-10-07. The old "5y" traded 5.2 years and the old
"2y" 1.2 years. The 2y's 26% volatility on the sizing capital was one full-size gold contract; ten days carried 37% of
the variance. A two-month start shift moved a 5y by 46 points and a 10y by 97.

**What follows in the book.** From 2026-10-08 a 5-year backtest trades five years and ends on the latest data: lookbacks
3 / 6 / 11 / 16 ending 2026-10-07.

## Round 14: contracts trade only from their listing date (2026-10-08 to 10-09)

**What it established.** A backtest that trades a contract before it existed shows a book nobody could have held: the
equity micro symbols carried E-mini bars from 2010 although CME listed the micros on 2019-05-06. At the switch, the way
the E-mini holding is carried into the micro decides how far the book sits from its signal.

**The data.** Four switch-day rules on both fee tables and the windows of that time (lever 23). Exiting the E-mini and
entering the micro at the day's rounded target left a gap to the signal of 9k to 16k dollars. Letting the micro open from
zero left 106k to 170k. Carrying ten micros for each E-mini left 180k to 281k, because every E-mini held on the day was
one contract against a target of 0.10 to 0.47, so ten for one brought 10 micros where the signal wanted 2 to 5. Exit and
entry for held pairs only left 78k to 138k. The vendor's id relabel on 2026-02-22 booked a false roll; an
`instrument_id_relabels` list fixed it.

**What follows in the book.** At the switch the predecessor is exited and the micro entered at the day's rounded target,
since 2026-10-08 ([`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "Listing dates and the
switch").

## Round 15: knobs on IBKR fees and the per-contract speed limit (2026-10-08 to 10-09)

**What it established.** A sizing rule only scales the target book; it cannot change the unrounded target book, so
whatever it does to a window's result comes through which whole contracts the book holds. Differences of a few tenths of
Sharpe on one window arise from holdings alone: halving the roll cost moved the four windows +0.00, +0.25, -0.01, -0.09.

**The data.** 131 runs of twenty variants on the four windows, then eighteen 5-year start dates for some (every variant:
[`LEVERS.md`](LEVERS.md), lever 24): five sizing forms (proportional drawdown, half rate, a floor at 0.75, the capped
account, a yearly reset) and fixed 500k; equity-index shorts off; three blanket speed sets; a contract-size guard at 0.50
and 0.35 of the risk target; cost weight x0.5 and x2; a buffer twice as wide; roll cost x0.5; Carver's per-contract speed
limit; and shorts off with three partners. On single windows the sizing forms read 5y 0.718, 0.717, 0.675 and 0.681
against 0.129; on the unrounded target book shorts off moves the Sharpe by about 0.06 and the speed limit by 0.029. Over
eighteen 5-year starts the median difference with the speed limit was -0.01. The proportional drawdown form was deeper in
drawdown in 13 of 18 windows. The guard removes heating oil, gasoline, crude, gold, silver and copper when they are most
volatile; on these windows those were the contracts and spells that carried the wins.

**What follows in the book.**
- **The per-instrument speed limit**, adopted as Carver's procedure (AFTS, strategy nine): a rule is removed on a
  contract where its cost is above 0.15 of Sharpe a year on Carver's table-35 turnovers; twelve contracts, eighteen
  rules ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md), "Removing expensive rules by cost"; lever 12).
- **Half compounding stays as Carver wrote it** (round 9). The proportional form cuts sizing by the drawdown's share of
  the peak account, where half compounding cuts it by the full dollar loss, so the proportional form keeps more risk on
  in a drawdown from a high peak (levers 13 and 24).
- **Equity-index shorts stay on.** A long-only equity sleeve is a standing long bet on equity indices. The book's one
  asymmetry for equity shorts is the slow rule (round 21), which lets an equity short open only when the slow trend
  agrees.

## Round 16: the live year and the sizing study (2026-10-14 to 10-15)

**What it established.** A one-contract position can sit far above its target inside its own buffer: the granularity
buffer's width is set by the lumpiest held or searched contract, so a held lumpy contract can set the band that
protects it. Carver's fixed buffer holds such a contract near target and makes the book trade on nearly every cycle.
Carver's overlay covariance trims such a position without raising churn.

**The data.** The live year ended +42.3%, and one full-size silver contract made +181,138 of the +211,714, held at 1
contract for 117 days against a median target of 0.08; without silver the year was +6.1%. A backtest from 2025-10-06
matched the live year to the cent. Then seven setups on one build, on the live year from five starts and the 2y, 5y, 10y
and 16y, prices to 2026-10-13 (full table: [`LEVERS.md`](LEVERS.md), lever 25):
- Carver's fixed buffer, with and without four stand-in micros (silver, gold, crude, copper), holds silver near target
  but makes the book trade on 88% to 95% of searched cycles against the starting book's 62% to 78%. Carver's method as
  written (fixed buffer, micros, Carver's overlay at a 30 percent expected-risk ceiling) churned 539 / 527 / 476 / 419
  round trips a year on the 2y, 5y, 10y and 16y. The four fixed-buffer setups of the October sizing round together
  churned 338 to 544.
- The stand-in micros alone added the least churn but left the buffer coarse (a held-only name set it on 103 of 336
  cycles in the year).
- Carver's overlay covariance alone trimmed silver on 2025-12-28 and made +28.8% in the year at the starting book's
  churn.
- The starting book's year ranged +7.3% to +42.3% by start date.
- On Carver's covariance the book's 16y p99 of expected risk is 1.66 tau; on the sample covariance the same rule gives
  the limits of that time. A 25 percent expected-risk ceiling cut the book on 412 of 4,734 16y cycles, about 8 percent,
  where Carver's calibration aims at about 1 percent for each limit and just under 5 percent for all together. One
  setup's 5y +84.8% was gold held 520 bar dates for +283k.

**What follows in the book.** Carver's overlay covariance came into the book (round 25) and gold and silver went to
micros (round 20); the copper micro did not (round 22). Carver's fixed buffer is not used: it trades the
book on nearly every cycle, and what makes it churn here is unmeasured. Account size alone does not explain it: in
Carver's own test the 1 percent buffer at 500,000 turned over about as much as his 50 million book (48.8 against 46.5;
AFTS, strategy twenty-five, table 124, p.433); his test searched 100 instruments, not 36, blended carry, and charged
the current cost per contract adjusted for the volatility of each historical period (p.428; strategy five), where the
book adds an impact term ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 1.1; lever 1). The overlay
limits of this round were replaced by the p99 set measured on newer data (round 17).

## Round 17: the first tuning round, limits and the starting book (2026-10-26)

**What it established.** The overlay limits by Carver's 99th-percentile method on this book's own 16 years; Carver's
table values fit the book's data; and the switch bar (`held` or `vendor`) moves single windows both ways through single
contracts.

**The data.** On the per-contract rolls with the micros on the roll rule and data cut at 2026-10-23. The backtest first
reproduced 55 dated live runs with no differences.
- The p99 row (16y, `held`, Carver's overlay covariance): R 1.75 tau, R_jump 4.75, R_shock 3.00, leverage 7.25 gross /
  5.75 net. On the last 5 years of the 16y alone R is 1.50 and R_jump 4.25, a step lower.
- The speed-limit list held except ZW 4/16 at 0.1483, just under the line.
- Carver's table values against the new data:

| Value | Carver's table | Implied on the new data |
|---|---|---|
| IDM | 2.5 (table 16, 30+ instruments; capped at 2.5, Appendix B, p.644) | 3.13 |
| FDM, 6 pairs | 1.26 | 1.25 to 1.33 |
| FDM, 5 pairs | 1.19 | 1.17 to 1.24 |
| FDM, 4 pairs | 1.13 | 1.13 to 1.18 |
| FDM, 3 pairs | 1.08 | 1.09 |
| Average absolute scaled forecast | 10 | 9.18 (64/256) to 9.88 (2/8) |

- The starting book's 16y was +4.43% (engine Sharpe 0.09); its 5y ranged -2.94% to +49.92% across starts. `held`
  against `vendor`: year -7.64% / -7.53%, 2y -14.59% / -4.86%, 5y +29.17% / +21.08%, 10y +69.63% / +51.82%, 16y +4.43% /
  +96.72%, the books parting on a single RB contract in 2011. Without the speed-limit list the same 16y made +73.98%.

**What follows in the book.** Carver's p99 set became the limits (net later 5.50, round 33;
[`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 2.1; lever 8). The table values stand (round 2 for the
IDM). The speed-limit list stays. `held` stays: it prices the book on the contract it actually holds, which is why the
per-contract rule exists; neither setting showed a consistent direction (`vendor` higher on the 2y and 16y, lower on the
5y and 10y) (lever 26).

## Round 18: why the earlier high results did not reproduce (2026-10-26)

**What it established.** The earlier high results were single contracts held far above target, not a property of the
system. Every large move between October's results and these was which coarse contract a run held.

**The data.** Five controls; the pre-roll code on several data copies and end dates; a replica of SG's trend rule on the
book's own contracts (full controls table: [`LEVERS.md`](LEVERS.md), lever 26).
- The first control reproduced an earlier +98.88% cell for cell; moving its end date three weeks gave +5.10%. The
  +98.88% was one silver contract held at a median 15 times its target, +391,162 dollars.
- The earlier 62% to 66% 2y on the pre-roll code was one gold contract: +212k, 42 points of 64. The same code at end
  2026-10-23 gave +18.4%. Data corrections moved a fixed window 2 to 11 points.
- The roll switch has no consistent direction: off minus on is +25.0, +32.9, -13.3, -29.9 and +61.1 points on the year,
  2y, 5y, 10y and 16y, and where off is higher, RB carries most of it.
- The unrounded book over 16 years is +260k unrounded, +65k whole, +69k stored. An earlier "about zero" reading priced
  roll steps as P&L and is superseded.
- 2025 is the rule mix (round 21) and 2026 is the universe. Over 16 years the book's rule set on the book's 36 contracts
  made +74% against -21% for SG's rule (both scaled to 17.8 percent volatility, no costs).

**What follows in the book.** Every contract is kept and traded: which market makes the next large move cannot be known
in advance (round 4). The task became holding the coarse contracts near target, which rounds 19 and 20 took up.

## Round 19: micros and Carver's instrument selection (2026-10-26)

**What it established.** No instrument set removed the start-date effect, and micro sets raised churn. Carver's own
selection for a 500,000 account stops at seven instruments, because no eighth fits without a member failing his
four-contract minimum capital (AFTS, strategy two).

**The data.** Three sets on the starting book: micro FX; micro FX, metals and crude; and Carver's own selection for the
account size; plus Carver's greedy selection run eight ways offline (an expected Sharpe from his procedure, not a
backtest; full table: [`LEVERS.md`](LEVERS.md), lever 10). Long windows did not order one way (micro FX 16y +95.1%). Micro
FX, metals and crude made 278 to 376 round trips a year. Carver's seven-instrument selection lost 50.7% over the 10y. The
measured pre-cost subsystem Sharpe was 0.044 against the 0.3 Carver's procedure assumes.

**What follows in the book.** All 36 under dynamic optimisation. Micro FX is not used: micros are used where the
full-size contract cannot be held at 500,000, FX is not where the book is coarse, and a micro there turns one-lot
adjustments into trades. Carver's own data show what a small set gives up: in
his slow trend strategy the average single instrument made a Sharpe of 0.21 and the full Jumbo portfolio 1.01 (AFTS,
strategy six, tables 21 and 24, p.156 and p.159), and his IDM table gives 2.10 for seven instruments against 2.50 for 30
or more (table 16). His answer for a 500,000 account is dynamic optimisation over the full set: a Sharpe of 1.22 on
100 instruments at 500,000 against 1.27 at 50 million (strategy twenty-five, table 124, p.433)
([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 4.1).

## Round 20: the sizing matrix and the book switches (2026-10-26)

**What it established.** Gold and silver on micros let the book hold its two lumpiest instruments near target. Carver's
fixed buffer roughly halves tracking error and churns at full size. Without the sign close, wrong-signed positions stay
against their forecast for long stretches, and the book holds gold and silver far over target.

**The data.** Buffer (the granularity buffer, Carver's fixed buffer) by instruments (full size, gold and silver micros,
micro FX, metals and crude), 17 runs per cell (full table: [`LEVERS.md`](LEVERS.md), lever 1); and two switches against
the starting book on 7-run screens.
- Carver's fixed buffer churned 337 to 454 round trips a year at full size.
- The micros book held gold at 0.9 and silver at 1.1 times target over 16 years; its mean after-cost Sharpe was 0.31
  (0.35 before costs) on the 17-run matrix, against the starting book's 0.18 (0.22).
- Over the 16y the unrounded book made +260k to +306k before costs in every cell; the micros book's whole-contract book
  made +419k against the starting book's +65k, more than the unrounded book: these figures measure what rounding kept or
  added.
- **The sign close off** (the buffer alone moving a wrong-signed row): wrong-sided stretches of a median 4 cycles
  (longest 217) against 1 (longest 14) with the close; 32% of name-days over twice their target (gold at 2.2 times,
  silver at 2.7); tracking error 20.9% on the screen (13.6% over its own 17 runs); 100 to 165 round trips; a mean Sharpe
  of 0.38 after costs and 0.41 before against -0.02 and 0.02 for the starting book on the screen windows.
- **The equity slow rule off**, on the full-size screen: the equities themselves made less without the rule (16y net
  +28k against +49k).

**What follows in the book.** The micros book became the base. Micros are used where the full-size contract cannot be
held at 500,000: gold and silver want about 0.05 contracts at an average forecast, so only their micros hold them near
target. Carver's minimum-capital advice is to use smaller contracts when capital is limited (AFTS, strategy two), and
gold and silver were the book's coarsest holdings (one GC contract is 12.9 of Carver's buffers) ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 4.2; lever 9).
The sign close stays. In Carver's full method the full close at a sign change is the turnover floor his buffer solves
(AFTS, strategy twenty-five, p.430), and his buffer moves a wrong-signed row toward zero like any other. Here the buffer
is one lumpy contract wide and the search starts from the held book, so without the close a wrong-signed row sits
against its forecast, and a position against its own forecast is a position the system expects to lose on. Turnover did
not rise with the close (140 round trips a year without it on the 16y, 115 with it; round 26). The band waits while the
opposite forecast is under 2 (on Carver's scale, average absolute forecast 10), a weak reading of a sign change; its
measured effect on round trips is near zero ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 1.3; lever
2). The slow rule stays (round 21).

## Round 21: signal choices, selective speed rules and the slow rule (2026-10-26 to 10-27)

**What it established.** The 2025 loss is in the speed mix, not in sizing: every sizing choice leaves 2025 at -9% to
-20%, and only the speed set moves it. A speed set or speed weighting chosen on one half of the history does not hold on
the other, and fast-minus-slow relative performance mean-reverts from year to year. On the full-size book the selective
speed rules rode on single gold holdings; on the micros book their effect was small.

**The data.** Two offline analyses on the unrounded book (before costs, return on sizing capital, an independent
re-implementation matching the engine's target to 1e-10 contracts over 2025-26, windows to 2026-10-23), split into fit
2011-2020 and test 2021-2026; then the real book on 7-run screens. Full tables: [`LEVERS.md`](LEVERS.md), levers 27
(signal choices), 28 (FX), 29 (selective rules), 15 (the real-book screens) and 14 (the slow rule).
- **Signal choices.** Seventeen choices switched one at a time: all six speeds without the cost removals, attenuation
  off, the slow rule off, FX excluded, weekday-only volatility, the volatility blend off, a ten-year blend, equal weights,
  IDM 3.13, full compounding, FDM from table 52, the sizing clamp off, dropping 2/8, dropping 2/8 and 4/16, 16/64 and
  slower only, the book's handcrafted weights and strategy thirteen's attenuation as written. The speed mix that brings
  2025 to SG's +3.9% (16/64 and slower) has the lowest Sharpe in the fit half (0.184) and costs 19.7 points over 16
  years; the joint fit-half best of 135 combinations made 0.462 in the fit half and 0.015 in the test half.
- **The volatility blend off**: 0.414 in the fit half and 0.126 in the test half against 0.364 and 0.109; the test-half
  difference has a t of 0.35, and it does not move 2025-26.
- **FX.** FX excluded made 0.447 and 0.206 in the two halves, and the FX sleeve lost in 10 of 16 years. Carver's screens
  do not single FX out: his cost limit removes nothing; his minimum capital removes every FX pair and also 34 of the 36
  instruments, so at 500k it is a whole-book rule; his greedy selection picks a few pairs (6J in every run), a
  concentration whose test-half gain rests on 6J's 2022 and 6M's 2023 and 2025-26.
- **Selective rules.** Refit on 2011-2018, the class weightings' gains vanished (-0.4 and +0.2 points). Carver's
  strategy-sixteen timing of speed groups does not hold on this data, because fast-minus-slow relative performance
  mean-reverts (-0.32 on calendar years). Three regime and class rules, in volatility-matched points against the
  baseline over 2021-2026 / 2011-2020 / 16y, t in brackets: the equity fast-speed cut (equities run only 16/64 and
  slower) +8.7 (0.94) / +3.6 (0.31) / +12.3 (0.83); the high-volatility switch (fast speeds off above the 0.75
  volatility quantile) +20.4 (1.85) / -3.1 (-0.29) / +17.2 (1.11); and rule 3c (a fast speed counts only when it agrees
  in sign with the slow trend, for all instruments) +19.0 (1.03) / -8.9 (-0.39) / +10.1 (0.34); no t reaches the 5
  percent level. The high-volatility switch's test window is where the fast-whipsaw idea came from; its placebos (fast off in low volatility, a static cut, slow off in high
  volatility) lost.
- **Real book, full size.** The two equity and volatility rules together leaned on one gold contract held about 450
  days at 6 to 8 times its target; the equity fast-speed cut's first 2y start is a gold holding of 414 days at 5.8
  times. The high-volatility switch's tracking error was 18.7% against the starting book's 9.8%.
- **Real book, micros book**, 7-run screen: the high-volatility switch 0.16 and both rules together 0.31 against the
  micros book's 0.30; the slow rule off 0.17.

**What follows in the book.** Nothing between the signal and the sizing changed.
- **The equity slow rule stays**, the one rule in the book that goes against Carver's advice. The rule lets an equity
  short open only when both slow speeds (32/128 and 64/256) are negative. It was adopted on 2026-10-05 on this book's
  record: 113 of 190 equity-index shorts were opened on the fast speeds alone, and equity shorts as a whole lost 121,403
  over 16 years. That fits Carver's finding that equities are the weakest trend class (median Sharpe 0.03 in his
  multiple trend strategy; AFTS, strategy nine, table 37, p.206). It is a declared departure: Carver finds no statistical
  difference in risk-adjusted return across forecast ranges once the data are cut to one asset class, so he advises
  against asset-class adjustments (strategy twelve, p.267) and against any asymmetric adjustment (p.268). As an economic
  reading only, not a measurement of the rule, it also fits his "bouncing cat", a rally after strong negative fast
  forecasts in equity indices (figure 53, p.266) ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 3.1;
  lever 14).
- **Rule 3c is not in the book**: it applies the slow-rule idea to every instrument, and Carver's evidence is for
  equities only.
- **The volatility blend stays on**, for Carver's reason: volatility mean-reverts, and a short estimate alone builds very
  large positions in the quiet spell before a crisis, so he blends the short estimate with its long-run average, which
  also damps trading (AFTS, strategy three, p.89 to 90). The test-half gain of the blend off has a t of 0.35.
- **Volatility stays on the bars the feed prints**, Sunday sessions included: the Sunday evening session is a real
  exchange session that prints its own bar, and the runner trades on the bars the feed prints (2026-09-03). The
  weekday-only form was also lower on every window shown.
- **FX stays.** The book chooses instruments by cost, liquidity and diversification, and the FX pairs meet those; a
  sleeve's past return does not say which market makes the next move (round 4).
- **The six speeds stay**, with removal only by Carver's per-instrument cost test (rounds 6 and 15). No equity speed is
  cut: that test keeps them (the equity micros' 2/8 costs 0.018 to 0.049 of Sharpe a year against his 0.15), and a cut
  chosen on 2025's losses is the over-fitting Carver warns of (AFTS, strategy nine, p.207 to 208).

## Round 22: copper micro, carry, and the overlay on the micros book (2026-10-26 to 10-27)

**What it established.** Carry earned about nothing on these instruments, so blending it with trend adds a forecast
that earned nothing here. The blend roughly halves the round trips and tracks its unrounded book a little more closely;
why its traded after-cost Sharpe falls is unmeasured. A copper micro adds trades. Rounding each overlay limit up or
down moves a run's result by about as much as the start date does.

**The data.** Gold, silver and copper micros on a 7-run screen; a 12-run grid of the micros book and two
strategy-twenty-five cells (the minimal buffer with Carver's optimiser covariance, and the granularity buffer with
Carver's optimiser covariance), each with carry off and on; then 17 runs per set of the micros book with the overlay on
Carver's covariance or the sample covariance, each on its own p99 limits, carry off or on (limits: [`LEVERS.md`](LEVERS.md),
lever 8; carry: [`CARRY.md`](CARRY.md)).
- Gold, silver and copper micros: mean 0.13 against the micros book's 0.30 on the 7-run screen.
- Carry alone earned an unrounded 16y Sharpe of 0.02 against trend's 0.26. On the 12-run grid the micros book with
  carry made 76 to 110 round trips a year against 152 to 216 without, at median tracking errors of 9.5 / 8.7 / 9.6
  against 9.8 / 9.3 / 11.3 percent; on the one stored 16y run 7.0 against 8.3 percent. On that run the whole-contract
  blend kept 9k of its unrounded book's 200k against 65k of 260k without carry, a similar dollar shortfall (about 191k
  against 195k). On the 12-run grid carry on moved the
  micros book from 0.34 to 0.04 and the granularity-buffer cell from 0.19 to -0.15; the minimal-buffer cell went from
  0.13 to 0.17, from the five year starts alone. In the 17-run sets carry was lower on both covariances; its dollar cost
  (65k to 220k of net P&L per run on the year to 10y) also includes its lower risk.
- On the 16y one start, the overlay covariance alone moved the Sharpe by 0.02 (0.38 sample, 0.40 Carver's, both on the
  limits of that time); Carver's tighter p99 limits lowered it by 0.23. Rounding each limit up or down shifted a run's
  Sharpe by up to about 0.2 either way, and rounding down nearly doubles the cut cycles. Doubling every fill's cost lowers
  Sharpe by 0.04 to 0.05. Mean margin is 12% to 20% of the account, at most 65.7%.

**What follows in the book.** Carry is built and switched off, because it has not yet been tuned on the data the book
trades. It needs two real contract prices on the same date, which only the per-contract bars give, and the second
contract is missing on many dates (over 16 years UB has one on 55.4% of dates, 6M on 66.0%; 6L's longest gap is 78
dates). These measurements ran while the continuous series still had holes (AlgoGators/algogators#36) and with stand-in
gold and silver micros, and the final retune ran with carry off (AlgoGators/algogators#186). What they show: Carver's
case for carry is a second return beside trend, from the shape of the futures curve (AFTS, strategies ten and eleven),
and on these instruments carry alone earned about nothing (unrounded 16y Sharpe 0.02 against trend's 0.26), so the blend
moves 40 percent of the forecast to a rule that earned nothing here and adds no second return. Why the traded blend's
after-cost Sharpe falls is unmeasured ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 3.2;
[`CARRY.md`](CARRY.md)). Gold and silver only on micros: copper is not the coarse contract (full-size copper wants about
0.34 contracts at an average forecast against gold's and silver's 0.05), so a copper micro means several contracts where
the book held one or none, and every one-lot adjustment becomes a trade; the copper micro has only been listed since
2022-05-02. The overlay runs Carver's covariance
with its p99 limits (rounds 25, 17). This book was re-tuned on filled data (rounds 24, 28).

## Round 23: real micro metals, an independent check, and the crude micro (2026-10-27 to 10-28)

**What it established.** The optimiser builds one covariance for all symbols on the dates every symbol shares, so one
symbol's missing bars reach the whole book. The gap between stand-in micros (full-size bars under micro names) and real
micro bars was in the stand-ins' data, not in the real micros.

**The data.** The year from 2025-10-05 with real bars; an independent check that swapped calendar, prices and volumes
one at a time (lever 9); a hybrid series; Carver's optimiser covariance on the same comparison; seven one-tick tests;
then a 21-window screen of the crude micro. The year went from +6.20% (stand-in) to -9.25% (real). The stand-ins were
built from production's continuous series, which lacks SI.v.0 bars from 2025-03-24 to 04-04 and three other weekdays,
and some GC.v.0 and SI.v.0 Sundays. With the calendars aligned the real data gives +7.22%; the calendar accounts for
-16.5 of the -15.45 points; one tick does nothing. On 17 windows the real data was a mean 4.49 points lower (12 of 17).
The hybrid (twin prices until the micro's volume reached half the twin's) keeps the micros' own calendar and gave
-11.45%. Carver's optimiser covariance closes the year's gap (0.6 points) but not the 2y's (16.7 points).
The crude micro: CL was held at 2.3 to 10.3 times its target on the days it was held, and the micro brought crude nearer
target at about 297 dollars a year of extra cost. Over 21 windows the paired account Sharpe after costs moved -0.001
(mean) and -0.051 (median); crude's own net P&L rose (a mean +19,550) and the rest of the book fell (a mean -39,201,
led by RB, HO and NG), through the shared search and overlay. The cause of that transfer is unmeasured.

**What follows in the book.** Fix the data (round 24), not the micros. The book still builds the covariance on shared
dates; a per-instrument covariance would remove the coupling and is not built.

## Round 24: the data fill (2026-10-28)

**What it established.** A missing bar removes a real price from every run and, through the shared covariance, moves the
whole book. The holes in the continuous table were the book's own ingestion's, not the vendor's.

**The data.** Production's continuous table lacked 476 days its own contract table has. The fill rule reproduces
Databento's own `.v.0` contract on all 42 checked dates. A daily pull started at the whole table's latest date plus one
day, until 2026-10-12, so a day one symbol lacked, once another symbol's bars had moved the table past it, was never
pulled again. On the overlay limits of round 22 the year from
2025-10-05 went from -9.25% unfilled to +0.352205% filled (engine Sharpe 0.115127), a data result; 3,418 of 3,428 rolls
were unchanged.

**What follows in the book.** Every later round runs on the filled data. The production fill is a release step
(`scripts/futures_release/README.md`; `AlgoGators/algogators#180`). The data rules are in
[`../01_the_book/ROLLS_AND_DATA.md`](../01_the_book/ROLLS_AND_DATA.md).

## Round 25: the overlay-covariance switch (2026-10-25)

**What it established.** Carver's overlay estimator and the sample estimator make different books on the same data, and
Carver's can be added as a switch that leaves the sample book unchanged.

**The data.** Three commits ported from the sizing study plus one test fix (`AlgoGators/trade-ngin#257`), checked on the
2y, 5y, 10y, 16y and a dated live chain from 2026-10-15 to 10-22, roll switch off and on. `sample` equals the old book on
every compared stored cell and every e-mail body; `carver` equals the October build on the 2y and 5y. On the pre-micros,
unfilled data with the old limits the 2y made 21.13% with 192 trades on `carver` and 99.07% with 134 on `sample`.

**What follows in the book.** The overlay runs Carver's own estimator (AFTS, tactic four), set on 2026-10-27:
each contract's sizing volatility, the estimate its position is sized on, with correlations of weekly returns (Saturday
to Friday) exponentially weighted over a 26-week span (`kCarverCorrelationWeeks`,
`include/trade_ngin/risk/overlay.hpp:72`). Weekly returns avoid
the low correlations that daily closes taken hours apart give, which is Carver's reason. The 26-week span weights recent
weeks most, so a rise in correlations shows within weeks, where an equal-weight sample of the last 252 dates spreads it
over a year. Carver notes that the overlay method "isn't too sensitive to how the covariance matrix is estimated" (fn
289, p.622); the limits are set by his
99th-percentile method on this same estimator, so estimator and limits go together. `overlay_covariance` is a template
key, set to `carver`, and the code requires it. It is the overlay's covariance, not the optimiser's (round 26; lever 7).

## Round 26: the one-pass optimiser as Carver's strategy twenty-five (2026-10-26 to 10-27)

**What it established.** Why the book's buffer is as wide as it is and what each of the book's departures in the
optimiser does. Carver's fixed buffer tracks the target much better and trades far more. A minimal buffer is still set by
one lumpy contract, so it tracks like the granularity buffer. Without the sign close the buffer alone leaves rows against
their forecast.

**The data.** On `archive/keep-do-not-delete/s25-one-pass`, with `held`, the limits of that time and data cut at
2026-10-23, one run per window (full table: [`LEVERS.md`](LEVERS.md), lever 1): the fixed buffer at 0.05, 0.10, 0.15 and
0.20 tau, the minimal buffer, wrong-signed rows left to the buffer, Carver's optimiser covariance alone, and
combinations.
- Carver's 1 percent fixed buffer halves the 16y tracking error (8.3% to 4.5%) and churns 337 to 454 round trips a year;
  widths of 2% to 4% trade churn for tracking smoothly.
- The minimal buffer makes 139 round trips on the 16y and 186 on the 2y and tracks like the granularity buffer (8.2%),
  because B_sigma is then set by the lumpiest searched contract, a median 0.055, about 5.5 times Carver's 1 percent.
- Wrong-signed rows left to the buffer give the same 16y book as the sign close off: 140 round trips against 115,
  tracking error 9.1% against 8.3%, and 5.9% of free name-days held against the target's sign.
- Carver's optimiser covariance alone widens B_sigma in volatility spikes (2025-04-08: 0.129 against 0.071). The tracking
  error the band is compared with scales with volatility too, so a broad spike moves both.
- The minimal buffer with Carver's covariance and the blended sigma made 150 round trips on the 16y at 7.5% tracking
  error (granularity buffer 8.3%), and 192 on the 2y at 10.7% (9.5%).
- Returns on this pre-micros, unfilled book swing widely between settings (16y: the starting book +4.4%, the others +24%
  to +89%).

**What follows in the book.** The granularity buffer, the sign close and the sample optimiser covariance stay; the
strategy-twenty-five switches live on their archive branch, and the book needs none of them
([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 1.1, 1.3 and 1.5). Carver's optimiser covariance and the
minimal buffer were measured again on the filled data (rounds 28, 31).

## Round 27: the step rule, fraction or edge (2026-10-28)

**What it established.** Because the shared optimiser couples every symbol, a small data difference (here, a calendar
alignment) changes many positions, and a single decision can turn on the last digit of a float. An edge step (walk one
contract at a time toward the search's answer, best tracking error first, until inside the buffer) narrowed the result
gap on this pair only partly: the positions that differ fell by about a fifth.

**The data.** The switch `optimization.buffer_step_rule` (`fraction`, the default, or `edge`) on
`archive/keep-do-not-delete/step-edge`: the real micro data against the same data aligned to the stand-ins' calendar,
on the year from 2025-10-05 and the first 2y start. Total return on the account after costs, whole-contract book, unfilled
micro data:

| Window | Rule | Real | Aligned | Gap (points) |
|---|---|---:|---:|---:|
| Year from 2025-10-05 | fraction | -9.25% | +7.22% | 16.5 |
| | edge | +8.70% | +10.50% | 1.8 |
| 2y first start | fraction | -2.66% | -5.99% | 3.3 |
| | edge | -2.98% | -0.09% | 2.9 |

The positions where the two books differ fell from 189 to 154 on the year and from 433 to 350 on the 2y; the aligned 2y
moved 4.9 points on one decision decided by the last digit of a float on 2026-04-12. Round trips fall 8% to 22%;
strategy-fill costs rise 8% to 35% and roll costs fall 22% to 30%, so the total cost is about the same. These figures are
on the unfilled data: the fraction year of -9.25% is the year the data fill moved to +0.35%.

**What follows in the book.** The book keeps Carver's fraction step ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md),
"The fraction step"); the edge step was measured on the filled data in round 28 (lever 3).

## Round 28: the final tuning grid on complete data (2026-10-29)

**What it established.** On complete data, with real gold and silver micros, the limits a setup runs on move its result
as much as several of the differences between setups: moving each setup onto the chosen setup's limits shifts its 16y
by -0.020 to +0.092. The edge step with Carver's optimiser covariance moves the recent and the long windows in opposite directions.

**The data.** The book at its final state on the filled data: real gold and silver micros on the per-contract rolls, the
granularity buffer and the sign close, Carver's overlay covariance, carry off, the slow rule on. Each
setup's limits by Carver's p99 method on its uncapped 16y; four setups (sample or Carver's optimiser covariance, by
fraction or edge step), 21 runs each (16y one start; 10y, 5y, 2y and year five starts each); the chosen setup at net 5.75;
a reference book (the crude-micro screen's book without the crude micro); and each setup on the chosen setup's limits
(lever 17).
- The chosen setup: 0.300 on the 16y; 0.290, 0.273, 0.102 and 0.407 on the 10y, 5y, 2y and year. Its 2y is the
  reference book's: same fills, positions and results.
- Carver's optimiser covariance with the edge step raises the 2y above the reference book (+0.143, 4 of 5 starts) and
  lowers the 5y (-0.163, all five) and the 16y (-0.099). The 2y starts all share 2024 to 2026. Round 29 traced its 2y
  rise and its 5y fall to one energy episode each.
- Carver's optimiser covariance alone is about level with the sample covariance on the 2y (-0.03), 10y (+0.02) and year
  (+0.03), and lower on the 5y (-0.13) and the 16y (-0.15).
- Net 5.50 against 5.75: the 2y and year are identical; 5.75 is higher on the 16y (0.339 against 0.300), 5.50 on the 10y
  and 5y.

What separates this book from the round-22 book: real micros replaced the stand-ins, whose data had the holes (round
23); and the data fill put back 326 consumed bars in the anchor year (+0.352205%, engine Sharpe 0.115).
The chosen setup's own p99 net leverage is 5.5926; the micros' 2019 equity-micro legs move two of the reference book's
10y starts (-0.082, +0.045).

**What follows in the book.** Each setup is read on its own p99 limits, since the limits move a result as much as the
setup does. The settings of the final book are in round 33; the four setups in full are in lever 17.

## Round 29: the loss attribution (2026-10-29)

**What it established.** Costs and rolls did not lose the money. The trend signal itself made nothing over the 5y and
lost over the 2y in every setup, and everything that separates the setups and the starts is the rounding gap. A step
that fills the buffer with a few large contracts concentrates the book in one episode, and a lumpy contract can keep
itself by setting its own buffer.

**The data.** Round 28's stored runs, net P&L split into five additive parts (target book, rounding gap, residual,
strategy costs, roll costs) that sum to each run's stored total to the cent.
- The unrounded target book made nothing over the 5y and lost about 33k over the 2y, in every setup.
- The rounding gap carries what separates setups and starts (5y -74.3k of -73.8k; 2y +27.6k of +26.9k).
- Carver's optimiser covariance with the edge step: its 5y fall and its 2y rise are each one energy episode, from the
  edge step filling the buffer with a few large contracts.
- The chosen setup's 5y rests partly on self-locked positions (a lumpy contract kept because it sets its own buffer).
- On the unrounded target book over 16 years, the fast speeds earned 196k of the book's 270k.

**What follows in the book.** The 2y loss is in the signal, so no change to rounding or limits is expected to make it
positive. The minimal buffer, aimed at the self-lock, was measured in round 31. The full attribution of the final book is
in [`RESULTS.md`](RESULTS.md) (lever 18).

## Round 30: two new levers, the sample-covariance window and the size-neutral edge (2026-10-29)

**What it established.** A sample-covariance window of N closes reads only about 0.8 N returns, because each symbol keeps
its own last N closes and the search uses only the dates all symbols share; a short window therefore reads too few
returns for a 36-instrument covariance.

**The data.** `optimization.sample_cov_window` (40 to 756, absent = 756) and the `edge_risk` step (ranks moves by error
removed per unit of a contract's own risk, built to undo the edge step's concentration in large contracts), on
`archive/keep-do-not-delete/levers`; both change nothing at their defaults (the merged code reproduces round 28's anchor
year cell for cell). One working 2y run each: the chosen setup at 126 bars +0.067 against -0.160 at 756; Carver's
covariance with `edge_risk` -0.284 against +0.048 under edge.

**What follows in the book.** At 63 bars the window reads about 50 returns for a 36-instrument covariance, too few to
estimate it, so the shortest lookback measured in round 32 is 160 bars (about Carver's six months after the shared-date
thinning). The size-neutral step was not run as a full set (levers 4, 6).

## Round 31: fast-speed rules and the minimal buffer on the chosen setup (2026-10-29)

**What it established.** The equity fast-speed cut and the high-volatility switch together lift the unrounded target
book on every window and every start, and the rounding gap hands part of that back on the recent windows. A book's own
p99 limits differ from the chosen setup's once its signal changes. The minimal buffer changes which lumpy positions the
book keeps, not how closely it tracks.

**The data.** On round 28's base and the filled data, five setups on the chosen setup's limits: the minimal buffer; the
equity indices without 2/8 and 4/16; the equity fast-speed cut; the equity fast-speed cut with the high-volatility
switch; and Carver's optimiser covariance with the edge step and the minimal buffer (full tables:
[`LEVERS.md`](LEVERS.md), levers 15, 1 and 17). Account Sharpe after costs, 16y one run, others five-start means.
- **The two fast-speed rules together**: 16y 0.440, 10y 0.440, 5y 0.214, 2y 0.244, year 0.320, against the chosen
  setup's 0.300, 0.290, 0.273, 0.102, 0.407. The target book rises on every window and start (16y +168.1k, 2y +65.1k);
  the rounding gap gives back part on the recent windows (5y -104.0k, 2y -36.5k); part of the gain is outside the equity
  indices (metals +44.0k of the 16y's +168.1k). 5y account maximum drawdown 27.2% against 22.0%, worst month -11.8%
  against -8.2%. At the chosen setup's limits the overlay cut it on 241 of about 4,738 16y cycles, 5.1 percent, about
  Carver's rate (he expects "just under 5%" for strategy eleven; AFTS, tactic four, p.613), against the chosen setup's
  193 (4.1%). Its own p99 limits are looser (R_shock 3.25 and net 5.75).
- **The equity fast-speed cut alone**: 0.370, 0.337, 0.122, 0.379, 0.517. **The equity indices without 2/8 and 4/16**:
  0.362, 0.240, 0.250, 0.389, 0.455. Every such setup's own net p99 rounds to 5.75.
- **The minimal buffer**: 0.236, 0.360, 0.166, 0.227, 0.162. The band stays about 5.5 times Carver's (round 26), so it
  brings no mechanism gain; it tracks like the granularity buffer, and what it changes is which held lumpy positions the
  book keeps.
- **Carver's covariance with the edge step and the minimal buffer**: 0.152, 0.286, 0.132, 0.388, 0.747; its year
  correlation with the SG Trend Index was -0.073.

**What follows in the book.** The rules were measured again on their own limits (round 32). The minimal buffer is not
used: when held contracts stop setting the band, the lumpiest searched contract sets it instead (a median 0.055, still
over five times Carver's 1 percent), so the band barely narrows and tracking did not improve (8.2% against 8.3% on the
16y, round 26) ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 1.1).

## Round 32: the same rules on their own limits, and the covariance lookback (2026-10-29)

**What it established.** Moving a book onto its own, looser limits leaves its signal unchanged and changes its result
through the rounding-and-limits gap: the overlay rarely cuts, and the book holds different whole contracts. The
optimiser covariance's lookback shows no smooth response.

**The data.** 231 runs and 11 uncapped runs on the merged code (full tables: [`LEVERS.md`](LEVERS.md), levers 15 and 6).
- The two fast-speed rules together on their own limits (1.75 / 4.75 / 3.25 / 7.25 / 5.75): 16y 0.365, 10y 0.353, 5y
  0.202, 2y 0.248, year 0.041. Against the chosen setup's limits they lose 0.076 on the 16y and 0.279 on the year with the
  signal unchanged (year target book +59.9k against +60.3k); the rounding-and-limits gap takes the loss (-59.9k against
  -37.8k), and the looser limits rarely cut (3.2 cycles on the 2y against 21.8). At its own limits the 16y cut 149 cycles.
- The high-volatility switch alone on its own limits (1.50 / 4.75 / 3.00 / 7.25 / 5.75): 0.313, 0.308, 0.143, 0.231,
  0.246.
- Sample-covariance lookbacks of 160, 252 and 504 closes on the chosen setup gave five-window means of 0.240, 0.124 and
  0.199 against 0.274 at 756; results jumped between neighbouring lookbacks (largest jump 0.587 on the chosen setup's
  year), and none raised its base's mean on the three bases. Each lookback ran on its base's limits while its own p99
  differs on one to three terms, so part of the lookback differences may be a limits effect (unmeasured).

**What follows in the book.** The six speeds stay with Carver's per-instrument removal by cost. No equity speed is cut:
Carver's per-contract cost test keeps them (0.018 to 0.049 of Sharpe a year against his 0.15; round 15), and a cut
chosen on 2025's losses is the over-fitting Carver warns of (AFTS, strategy nine, p.207 to 208). A blanket cut removes
speeds also where they pay (round 6). The equity fast-speed cut also removes the fast speeds from equity longs, where
Carver finds no bouncing-cat effect (strategy twelve, p.266). The high-volatility switch is not Carver's form: for
volatility's effect on a rule's profitability his answer is strategy thirteen's attenuation (p.268), which the book
applies to every speed, and he attenuates rather than switching speeds off; the switch was found on the period it fixes
(round 21) and lost over 2011 to 2020. The optimiser keeps the 756-close sample (round 33).

## Round 33: the chosen setup, net 5.50 and the 756-close covariance (2026-10-29)

**The book.** The chosen setup, set on 2026-10-29: the sample optimiser covariance over 756 closes, the fraction step,
the granularity buffer and the sign close, the overlay on Carver's covariance, limits 1.75 / 4.75 / 3.00 / 7.25 / net
5.50, gold and silver micros, carry off, the slow rule on. Account Sharpe after costs 0.300 on the 16y; five-start means
0.290 / 0.273 / 0.102 / 0.407 on the 10y, 5y, 2y and year. The setup was picked by pass rules fixed before any result
was seen: a change had to raise the mean of the five window values and drop no window by more than 0.1, because a rule
chosen after the results would fit the period.

**Each setting, and why.**
- **Net 5.50.** The chosen setup's own p99 net leverage is 5.5926, 0.032 under the 5.625 midpoint, so Carver's method
  gives 5.50. Net leverage is Carver's gross-leverage reading applied to direction: like his, it needs no volatility
  estimate (AFTS, tactic four, p.610 to 613). It is a plain signed sum of notional weights, so low-volatility rates and FX dominate it, and it
  does not measure the book's one-way market risk; Carver's finer form, limits by asset class (fn 292, p.622), is not
  built. It is a limit enforced by a cut on the days it binds, never a target: the fifth term of the daily multiplier. It
  was the binding term on 54 of the 193 cut cycles of the chosen setup's 16y
  ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 2.2; lever 8).
- **The 756-close sample covariance in the optimiser.** The window was set at 756 closes on 2026-09-21: an equal-weight
  sample uses every return it is given, so its length moves its answer, and 756 (`kOptimiserBars`) kept the optimiser on
  the prices the trend sleeve then held, whose history was capped at 756 at the time. The three-year sample moves slowly,
  so the search's substitutions and the one-contract risks that set the buffer change little from day to day; Carver's
  estimator (32-day volatility, weekly correlations over six months; AFTS, strategy twenty-five, p.417; Appendix B,
  p.641) follows recent risk. Keeping the sample over Carver's estimator is a measured result: no mechanism shows that
  either trades less on this book, and Carver's covariance was lower on the 5y and 16y, alone and with the edge step
  (round 28); the lookbacks of 160, 252 and 504 closes raised no base's mean (round 32)
  ([`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md) 1.5; levers 5, 6).
- **The fraction step.** Carver's step inside the band. The edge step fills the buffer with a few large contracts, and
  its swings on the 2y and 5y were one energy episode each (rounds 28, 29; levers 3, 17).
- **The granularity buffer, the sign close, the overlay covariance, the micros, carry off and the slow rule**: rounds
  20, 25, 22 and 21.

**What it needs in code.** No strategy-twenty-five switch and no lookback setting: the 756 is `kOptimiserBars`, a literal
at `src/strategy/trend_following.cpp:665`; the lookback results are in [`LEVERS.md`](LEVERS.md) (lever 6) and its code on
`archive/keep-do-not-delete/levers`. The tracked conservative template at 4c92eb1b8d is the chosen setup
(AlgoGators/algogators commit c42dbbe6f8):
1.75 / 4.75 / 3.0 / 7.25 / 5.5, `overlay_covariance` carver, the MGC and SIL pairs, `per_contract_rolls` true and
`per_contract_switch_bar` held. At 5.50 the net limit binds on one cycle of the 2y (2026-06-10, m 0.99837) and changes one
equity-curve row and two run metadata cells without moving a contract.

## What moved the numbers between rounds

| From | To | Main cause of the move |
|---|---|---|
| Round 1 | Round 3 | loop variants and the multiplier, on one 2y window, so executions and not equity are the measure |
| Round 3 | Round 4 | IDM 2.5, the explicit cut and the corrections; the head moves 333 to 312 executions |
| Round 4 | Round 5 | the cut now stands: 312 to 395 executions on the same signal |
| Round 5 | Round 6 | the reference model of the redesigned loop replaces the lap loop; shorts on; speed set or net limit changes per row |
| Round 6 | Round 7 | nothing new ran; the roll lineage and the churn remedies are collected |
| Round 7 | Round 8 | measurement corrections; the reference model's start-date spread; micros measured |
| Round 8 | Round 9 | half compounding stops the sizing capital growing, so 5y and 16y churn falls on the same signal |
| Round 9 | Round 10 | engine instead of the reference model; the fee is a search input, so a new fee is a new book |
| Round 10 | Round 13 | window convention and two more months of data; one gold contract |
| Round 13 | Round 15 | listing dates (long windows trade E-minis from day one); the speed-limit list |
| Round 15 | Round 16 | new data to 10-13, pinned end, merged engine; start date |
| Round 16 | Round 17 | new data copy, roll switch on, cut 2026-10-23; which coarse contract is held |
| Round 17 | Round 20 | gold and silver on micros: the two coarsest holdings near target |
| Round 20 | Round 21 | the selective speed rules' full-size gains were one gold contract; small on the micros book |
| Round 21 | Round 22 | carry on costs P&L on every window but the 16y; limits moved to the Carver p99 set |
| Round 22 | Round 23 | stand-ins carried continuous-table holes, real micros did not |
| Round 23 | Round 24 | holes filled in the data every run reads |
| Round 24 | Round 28 | real micros plus filled data; the chosen setup's own p99 net limit 5.50 |
| Round 28 | Round 29 | nothing ran: the rounding gap, not the signal, separates setups and starts |
| Round 28 | Round 31 | the fast-speed rules change the signal; the rounding gap hands part back on recent windows |
| Round 31 | Round 32 | the fast-speed rules at their own limits: same signal, larger rounding-and-limits loss, far fewer cuts |

Rounds 25 to 27 built switches on older data; their figures do not carry into the later rounds. Items that were planned
and never run are listed at the end of [`LEVERS.md`](LEVERS.md).

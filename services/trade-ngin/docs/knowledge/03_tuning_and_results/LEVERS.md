# Levers tried on the futures book

Every lever measured on the futures book between 2026-09-24 and 2026-10-29 is recorded here in one form: what it does to
the book; the economic reason it is or is not in the book, for a 500,000 book of whole contracts, tied to Carver's
Advanced Futures Trading Strategies (AFTS) where he applies; and the data, with its book and window named. An option
that is not in the book is described within its lever: what it does, why it does not fit this book, and its data. A
lever with several options holds them as rows of its tables.

Most of these levers turn on the size of the contracts against the account. A 20 percent risk target on 500,000 asks for
a fraction of one full-size contract in gold, silver, gasoline and heating oil, so a whole-contract book can hold such a
contract at many times its target, and a window's result can rest on that one holding. That is why the levers are also
read on the unrounded target book, across several start dates and after gold and silver went to micros, and why each
choice rests on what it does to the book, with the results as data.

Sharpe, return and drawdown are on the account after costs unless a table says otherwise; volatility is on the account in
the tables and 18 to 19 percent on the sizing capital, which is the figure to compare with the 20 percent target. Each
performance table names its basis; the two capitals are explained in [`RESULTS.md`](RESULTS.md), section "Two ways of
reading the book: sizing capital and account". Window values are five-start means except the 16y (one run), unless a
table says otherwise. Where an engine Sharpe is quoted it is named as such. A mean for the gold and silver micro book is
always quoted with its run set (the 17-run sizing matrix, the 12-run grid or the 7-run screens). Rows marked "reference
model" ran on a reference model of the redesigned loop, run outside the engine; they describe that model, not an engine
book.

Code citations are paths under `services/trade-ngin/` in AlgoGators/algogators at commit 4c92eb1b8d, main's merge of
`AlgoGators/algogators#98`, the import of trade-ngin d6f998da. The rounds named below are told in order in
[`TUNING_STORY.md`](TUNING_STORY.md); the final numbers are in [`RESULTS.md`](RESULTS.md); carry is in
[`CARRY.md`](CARRY.md); the instrument set is in [`THE_BOOK.md`](../01_the_book/THE_BOOK.md), section "Why these 36
markets, and the gold and silver micros"; the book is in [`../01_the_book/THE_BOOK.md`](../01_the_book/THE_BOOK.md);
departures from Carver are in [`../02_decisions/CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md). How each
mechanism works is in [`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md),
[`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), [`RISK_MODULES.md`](../../RISK_MODULES.md),
[`COST_MODEL.md`](../../COST_MODEL.md) and [`FUTURES_ROLLS.md`](../../FUTURES_ROLLS.md).

## Names used in this file

| Name | What it is |
|---|---|
| the chosen setup | the final book: the sample optimiser covariance with the fraction step, on its own p99 limits (net leverage 5.50); the conservative template at 4c92eb1b8d |
| the final grid's other three sets | the sample covariance with the edge step; Carver's optimiser covariance alone (with the fraction step); Carver's optimiser covariance with the edge step |
| the grid's reference book | the book of the crude-micro screen without the crude micro |
| the starting book | the starting book of the first tuning round (2026-10-14 to 10-26): full-size contracts, today's buffer, the overlay on the sample covariance with the limits of that time |
| the gold and silver micro book | the granularity buffer with gold and silver micros (MGC, SIL): the starting book with gold and silver on micros, the base of the final grid |
| the minimal buffer | today's buffer rule without the held-only rows |
| the sign close off | the forecast-sign close and its band switched off, so a wrong-signed position is moved only by the buffer |
| own limits | a set run on its own p99 limits, not on the chosen setup's |
| p99 limits | the overlay's five limits at the 99th percentile of the book's own uncapped 16-year readings, Carver's method |
| R, R_jump, R_shock | the overlay's three risk readings, in multiples of the risk target tau: expected risk, jump risk, risk under a correlation shock |
| B_sigma | the width of the optimiser's no-trade buffer, in tracking error |
| 2/8 ... 64/256 | the six trend speeds, by the spans of their two moving averages |
| reference model | a reference model of the redesigned loop, run outside the engine |
| the deep dive's chosen settings | the reference model's loop chosen in the deep dive of 2026-10-04 to 10-05: cost penalty 100, sign close deferred, equity slow rule on, one-step trim off (lever 22) |

## Summary

The second column says whether AFTS holds the lever; the last gives the economic reason in a few words.

| Lever | In AFTS? | In the book? | Economic reason |
|---|---|---|---|
| 1. Buffer width: granularity, fixed 1%, minimal | fixed 1% is strategy twenty-five's; granularity and minimal are not | granularity: yes | the band is set to the smallest trade the book can make, one contract of its lumpiest name; under the minimal variant the lumpiest searched contract sets the band instead, still over five times Carver's 1%, so the band barely narrows and tracking did not improve |
| 2. Wrong-sign rule: close or buffer | the buffer form is strategy twenty-five's; the separate close is the book's own | the close with its band: yes | a position against its own forecast is one the system expects to lose on, and a one-contract buffer searched from the held book leaves it there |
| 3. Step rule: fraction or edge | fraction is strategy twenty-five's; edge is not | fraction: yes | Carver's step spreads the move over every row; the edge step favours large contracts and concentrates the book |
| 4. Size-neutral edge | not in AFTS | no | it rests on the edge step, which is not in the book, and still leans to large contracts |
| 5. Optimiser covariance: sample or Carver's | Carver's is strategy twenty-five's; the 756-close sample is not | sample: yes | an equal-weight sample's answer moves with its length, so the window was set at 756 closes on 2026-09-21 to keep the optimiser on the prices the trend sleeve then held; keeping the sample over Carver's estimator is a measured result: his was lower on the 5y and 16y |
| 6. Sample-covariance lookback | partly (Carver uses about six months) | no | the 756 window was set for the length reason of lever 5; fewer returns give a noisier 36-instrument estimate |
| 7. Overlay covariance: sample or Carver's | Carver's is the overlay's own estimator | Carver's: yes | weekly correlations avoid the closing-time bias, on the volatility the positions are sized on, and the limits are measured on it |
| 8. Overlay limits by the p99 method | the method is Carver's; the net leverage reading and the fixed quarter rounding are not | p99 limits, net 5.50: yes | each limit binds only in its reading's tail, so the overlay acts on about 4 percent of cycles, as Carver intends |
| 9. Gold and silver micros | Carver's minimum capital (strategy two) | yes | micros are used where the full-size contract cannot be held at 500,000: gold and silver want about 0.05 contracts at an average forecast, and only their micros hold them near target |
| 10. Other micro sets and Carver's selection | Carver's minimum capital and selection | no | micro FX does not solve the coarse-contract problem (FX is not where the book is coarse) and turns one-lot adjustments into trades; the copper micro adds trades where copper is not the coarse contract; Carver's small-account answer is dynamic optimisation over the full set |
| 11. Capital size | not a lever in AFTS | 500,000: yes | the book is sized on the account it trades |
| 12. Per-contract speed limit | strategy nine | yes | a speed whose cost on a contract is over Carver's limit is removed there |
| 13. Proportional drawdown, equity shorts off | not in AFTS | no | half compounding is Carver's own form; no equity shorts puts a standing long bet into a long-and-short strategy |
| 14. Equity slow rule | not in AFTS (a departure) | yes | adopted on 2026-10-05 on this book's record: 113 of 190 equity-index shorts were opened on the fast speeds alone, and equity shorts as a whole lost 121,403 over 16 years; equities are Carver's weakest trend class (median Sharpe 0.03); a declared departure from his advice against asymmetric rules |
| 15. Selective speed rules | not in AFTS | no | Carver's per-contract cost test keeps every equity speed, and a cut chosen on 2025's losses is the over-fitting he warns of; the high-volatility switch is not his form (he attenuates), was found on the period it fixes and lost over 2011 to 2020 |
| 16. Carry | strategies ten and eleven | built, switched off | not yet tuned on the data the book trades: carry needs two real contract prices on the same date, it was measured on series with holes and stand-in micros, and the final retune ran with carry off; carry alone earned about nothing on these instruments |
| 17. The final grid's four sets | mixed (levers 3 and 5) | the chosen setup: yes | Carver's fraction step on the sample covariance (levers 3 and 5) |
| 18. The loss attribution's ranked fixes | mixed | no | each fix is a lever with its own reason; the 2y loss sat in the signal |
| 19. The data fill | a data correction | yes | a missing bar removes a real price from every run and spreads through the shared covariance |
| 20. Roll accounting and the share-test line | Carver's adjusted prices (strategy one) | roll work: yes | a roll is not a market move |
| 21. Churn remedies on the redesigned loop (reference model) | mixed | no | the remedies that brought churn under about 150 round trips a year stopped whole sectors trading |
| 22. The deep-dive catalogue (reference model) | mixed | the chosen settings: yes | the other knobs cut churn only by shrinking the book or deciding which sectors it can hold |
| 23. Listing switch rules | not in AFTS | rule C: yes | it lands the switch at the day's rounded target |
| 24. The IBKR knob list | mixed | speed limit only | a sizing-capital rule moves risk over time, not the target book's signal; the guard removes contracts in their most volatile spells |
| 25. The October sizing setups | mixed | superseded | the fixed buffer trades nearly every cycle; a 25% ceiling made the overlay a standing constraint |
| 26. `held` or `vendor`, and the roll switch | not in AFTS | `held`, switch on: yes | the book is priced on the contract it holds |
| 27. Offline signal choices | mixed | no | each choice is Carver's form or has its own lever; the volatility blend stays because volatility mean-reverts, and a short estimate alone builds large positions in the calm before a crisis; volatility is taken on the bars the feed prints, Sunday sessions included |
| 28. FX out, half weight, subsets | Carver's minimum capital, cost limit and selection | no | instruments are chosen on cost, liquidity and diversification, not on their record |
| 29. Selective rules, offline | mixed | no | class or regime speed weighting is the asset-class fitting Carver declines |
| 30. The cost penalty multiplier | Carver's range 10 to 100 | 100: yes | the one lever that reliably cuts this book's churn toward its trade bar; it stops at 100, the top of Carver's range, because above it trading collapses onto the cheapest instruments, as he warns |
| 31. Lap-loop churn variants and the cut | not in AFTS | the loop was replaced | a risk cut must reach the stored book |
| 32. The gate: correlation term and window | Carver's shock form is the idea's source | window fix: yes; stressed term: no | the window fix is a correctness fix; the gate was later replaced by the overlay |
| 33. IDM | Carver's table 16 | 2.5: yes | a leverage scalar: a higher IDM raises the volatility target, not the rounding gap |
| 34. Universes | Carver's handcrafting | all 36: yes | which market makes the next large move cannot be known in advance; dynamic optimisation over the full set is Carver's small-account answer |
| 35. The first micro phase (engine, lap loop) | Carver's minimum capital | no | micros on twenty rows turned one-lot adjustments into trades |
| 36. Blanket speed cuts (reference model) | Carver's rule is per instrument | no | a blanket cut removes speeds also where they pay |
| 37. Net leverage grid (reference model) | limits by Carver's p99 method came later | no single value | the level is set by the p99 method (lever 8) |
| 38. Tau, capital rule, position limit, penalty 150 (reference model) | half compounding and the position limit are Carver's | half compounding and tau 0.20: yes; position limit: no | half compounding is Carver's own form; tau 0.20 is Carver's own target, and a lower tau rounds names to zero; the position limit is not built by design: the per-name cap of 2 already bounds each name, so his limit would be a second, overlapping cap on a picked weight |

## 1. Buffer width: granularity, fixed 1%, minimal

**What it does to the book.** It sets the width B_sigma of the optimiser's no-trade buffer: the book trades only when its
held positions drift from the search's answer by more than this tracking error. `granularity` (today): the larger of
the floor and the largest one-contract risk among the names searched non-zero or held. `fixed`: B_sigma =
`b_sigma_floor` x tau, the book's 1% at 0.05. `minimal`: today's rule without the held-only rows. The switch
`b_sigma_rule` was tested on `archive/keep-do-not-delete/s25-one-pass` and is not on main. Mechanics:
[`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The granularity buffer, the rounding and
the clip".

**Why granularity is in the book.** Carver's buffer stops the book trading on changes too small to pay for: a 5%
asymmetric tracking-error buffer of the 20% risk target, "the optimal buffer B sigma is 1%" (AFTS, strategy
twenty-five). On this book one contract of most futures carries more tracking error than that band, so a 1% band is
crossed whenever a target moves by part of a contract, and the book then trades a whole contract. Setting the band to the
risk of one contract of the lumpiest name sets it to the smallest trade the book can make: the book trades only when a
whole-contract trade closes a gap worth that trade, which is Carver's purpose for the buffer with contracts that cannot
be split. The fixed 1% is his width as written. Its churn is measured below in two figures: at full size on the sizing
matrix, 337 to 454 round trips a year; the four fixed-buffer setups of the October sizing round together (lever 25),
338 to 544. Account size alone does not explain that churn: Carver's own 500,000 test at 1% turned over about as much as
his 50 million book (48.8 against 46.5, AFTS, table 124, p.433). His test searched 100 instruments, not 36, and blended
carry; it charged the current cost per contract, adjusted for the volatility of each historical period (AFTS, strategy
twenty-five, p.428, with strategy five's method), and the book adds an impact term on the bar's volume. Which difference
drives the churn here is unmeasured.

The minimal variant aims at the self-lock: a held lumpy contract that sets its own band, so the book never trades it
down. It removes the self-lock but not the width: when held contracts stop setting the band, the lumpiest searched
contract sets it instead (a median of 0.055, still over five times Carver's 1%), so the band barely narrows and tracking
did not improve (8.2% against 8.3% on the 16y); it brings no mechanism gain over the granularity buffer.

**The data.** The sizing matrix of 2026-10-26, 17 runs per cell, data cut 2026-10-23, roll switch on, the starting book's
limits. Sharpe is the weekday account Sharpe after and before costs, mean over 17 runs; the last column is dollars on the
whole-contract book before costs, on each run's own sizing capital. The unrounded book itself made +260k to +306k in
every cell, so a whole-contract figure above it is rounding adding to the book, not a share kept.

| Cell | Median tracking error to the unrounded book | Round trips a year | Mean Sharpe after / before costs | Whole-contract 16y before costs |
|---|---:|---|---|---:|
| the starting book (granularity, full size) | 10.9% | 115 to 192 | 0.18 / 0.22 | +65k |
| granularity, gold and silver micros | 10.7% | 141 to 216 | 0.31 / 0.35 | +419k |
| granularity, micros for FX, metals and crude | 7.2% | 278 to 376 | 0.17 / 0.23 | +358k |
| fixed, full size | 5.5% | 337 to 454 | 0.16 / 0.25 | +362k |
| fixed, gold and silver micros | 4.5% | 389 to 476 | 0.15 / 0.23 | +335k |
| fixed, micros for FX, metals and crude | 3.8% | 568 to 667 | 0.04 / 0.13 | +248k |

The strategy-twenty-five measurements (engine, today's limits, whole contracts, total return on the account and engine
Sharpe, after costs; tracking error to the unrounded book). On the 16y: today 115 round trips, 8.3% tracking error,
0.09; fixed 1% 337, 4.5%, 0.28; minimal 139, 8.2%, 0.31. In the table, "sign rule to the buffer" is the buffer form of
lever 2 and "Carver's optimiser covariance" is lever 5's.

| Setting | Window | Round trips | TE | Return | Sharpe |
|---|---|---:|---:|---:|---:|
| fixed 0.10 tau (2%) | 2y / 16y | 357 / 280 | 6.2% / 5.2% | -6.1% / 36.1% | -0.06 / 0.21 |
| fixed 0.15 tau (3%) | 2y / 16y | 308 / 236 | 6.6% / 5.5% | -3.6% / 59.0% | 0.00 / 0.30 |
| fixed 0.20 tau (4%) | 2y / 16y | 269 / 194 | 7.6% / 6.2% | 16.0% / 56.2% | 0.47 / 0.28 |
| minimal + sign rule to the buffer + Carver's optimiser covariance | 16y | 151 | 8.3% | 50.2% | 0.28 |
| the same | 2y | 187 | 11.2% | 18.8% | 0.53 |
| today's buffer + sign rule to the buffer + Carver's optimiser covariance | 16y | 128 | 9.9% | 57.4% | 0.30 |
| the same | 2y | 126 | 14.5% | 5.7% | 0.23 |
| minimal + Carver's optimiser covariance, blended | 16y / 2y | 150 / 192 | 7.5% / 10.7% | 47.6% / 9.7% | 0.27 / 0.34 |
| Carver's optimiser covariance alone, blended | 16y / 2y | 130 / 163 | 8.9% / 11.0% | 30.1% / -10.2% | 0.19 / -0.22 |

Widths of 2% to 4% trade churn for tracking smoothly, and none reaches 150 round trips. The two rows with the sign rule
left to the buffer carry an option that is not in the book (lever 2); the two blended rows replaced them. Over 17 runs
each, the fixed 1% made 0.16 after costs against the starting book's 0.18 at full size, and 0.15 against 0.31 on gold and
silver micros. In October, Carver as written (lever 25) churned 539 / 527 / 476 / 419 round trips a year and returned
less than the starting book on every long window.

The minimal buffer on the filled data, in the targeted round on the chosen setup's limits; account Sharpe after costs,
16y one run, others five-start means.

| Set | 16y | 10y | 5y | 2y | Year | Mean of five |
|---|---:|---:|---:|---:|---:|---:|
| the chosen setup | 0.300 | 0.290 | 0.273 | 0.102 | 0.407 | 0.274 |
| the chosen setup with the minimal buffer | 0.236 | 0.360 | 0.166 | 0.227 | 0.162 | 0.230 |
| Carver's optimiser covariance with the edge step and the minimal buffer | 0.152 | 0.286 | 0.132 | 0.388 | 0.747 | 0.341 |

The last set's year correlation with the SG Trend Index was -0.073. The 1% buffer was not taken on 2026-10-26.

## 2. Wrong-sign rule: close or buffer (the sign close off, strategy twenty-five's form)

**What it does to the book.** It decides what happens to a position whose forecast has changed sign. `close` (today): the
position is closed before the search once the opposite forecast reaches 2, and held while it is weaker (the deferral
band `sign_close_band`, with its freeze). `buffer`: no close, no band, no freeze; a wrong-signed row is free and the
buffer moves it like any row. The sign close off was the sizing round's form of the same idea. The switch
`wrong_sign_rule` was tested on `archive/keep-do-not-delete/s25-one-pass` and is not on main. Mechanics:
[`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The deferral band and the forecast-sign
close".

**Why the close is in the book.** The separate close is the book's own departure from Carver. In his full method the
full close at a sign change is the turnover floor that his buffer solves (AFTS, strategy twenty-five, p.430): his buffer
moves a wrong-signed row toward zero like any other. Here the buffer is one lumpy contract wide and the search starts
from the held book, so without the close a wrong-signed row sat against its forecast a median 4 cycles (up to 217)
against 1 (up to 14) with it. A position held against its own forecast is a position the system expects to lose on, so
the book closes it in full. Turnover did not rise with the close (140 round trips a year without it on the 16y, 115 with
it). The band: the close waits while the opposite forecast is under 2 (on Carver's scale, where the average absolute
forecast is 10), a weak reading of a sign change; its measured effect on round trips is near zero (the deferral's own
effect on the reference model's 16y round trips was -4, lever 22).

**The data.** On the 16y, today's limits; return is the engine's total return on the account after costs; Sharpe is the
engine's.

| Setting | Round trips | Tracking error | Return | Engine Sharpe | Free name-days stored against the target's sign |
|---|---:|---:|---:|---:|---:|
| Today (`close`) | 115 | 8.3% | 4.4% | 0.09 | 0 |
| The buffer form alone (`buffer`) | 140 | 9.1% | 88.5% | 0.41 | 8,826 (5.9%) |

The sign close off against the starting book: 0.38 / 0.41 after / before costs against -0.02 / 0.02 on the 7-run screen
windows, 0.38 over its own 17 runs, at 100 to 165 round trips; tracking error 20.9% on the screen (13.6% over 17), 32% of
name-days over twice their target (GC at x2.2, SI at x2.7), and wrong-sided stretches of a median 4 cycles (longest 217)
against 1 (14). The buffer form alone is the same book as the sign close off on the 16y, cell for cell. The loss
attribution found positions held against the forecast small in the chosen setup and negative with Carver's optimiser
covariance and the edge step. The sign close off was dropped on 2026-10-26 and the buffer form not used on 2026-10-27;
the switch stays on the archive branch.

## 3. Step rule: fraction or edge

**What it does to the book.** When the held book's tracking error exceeds B_sigma, `fraction` (today) moves every row a
fraction of the way from held to the search's answer and rounds; `edge` starts from the held book and adds or removes one
contract at a time, in the row whose move gives the lowest tracking error against the answer, until at or inside B_sigma.
Key `optimization.buffer_step_rule`, on `archive/keep-do-not-delete/step-edge`, not on main.

**Why the fraction step is in the book.** `fraction` is Carver's method: strategy twenty-five scales every required trade
by an adjustment factor so that the tracking error equals the buffer, then rounds. Every row moves in proportion to its
gap, so the adjustment is spread over the book. `edge` takes the stated aim, trade "until the tracking error is equal to
the buffer" (AFTS, strategy twenty-five), literally, contract by contract, which is not his formula. Each step picks the
single contract that removes the most tracking error, and on a lumpy book that is usually a large contract, so the walk
fills the buffer with a few large positions: the book holds fewer names, each nearer its own risk limit, which is a
concentrated bet rather than the diversified book the weights describe. The edge step was tried in the hope of a book
less sensitive to small data differences (round 23 found that the real micro data and the same data on the stand-ins'
calendar give very different books) and of fewer contracts traded.

**The data.** The final grid, paired by start; differences in account Sharpe after costs.

| Pair | 16y | 10y | 5y | 2y | Year |
|---|---:|---:|---:|---:|---:|
| edge minus fraction, on the sample covariance | -0.05 | -0.05 | -0.22 | -0.31 | +0.14 |
| edge minus fraction, on Carver's optimiser covariance | +0.09 | -0.00 | -0.06 | +0.17 | +0.20 |

The loss attribution measured the concentration: the Spearman correlation of one-contract risk against held/target was
+0.50 on the 5y and +0.64 on the 2y for Carver's optimiser covariance with the edge step, against -0.07 and -0.09 for the
chosen setup, and that set held 12 names a day against 15 to 17. Its 5y fall and its 2y rise were each traced to one
energy episode. On the real/aligned micros pair (unfilled data), edge narrowed the year's gap from 16.5 to 1.8 points and
the first 2y start's from 3.3 to 2.9, but the positions where the books differ fell only by about a fifth;
strategy-fill costs rose 8% to 35% while roll costs fell 22% to 30%. On the stand-ins, the year under edge was +11.63%
against fraction's +6.20%.

## 4. Size-neutral edge (`edge_risk`)

**What it does to the book.** Edge's walk, start, tolerances and stop, but the move picked is the largest (TE - TE_move)
/ (u_i sqrt(Sigma_ii)): tracking error removed per unit of the contract's own risk; cost plays no part. It replaced the
planned cost-aware edge (`edge_cost`), which the loss attribution said would deepen the concentration. On
`archive/keep-do-not-delete/levers`, not on main.

**Why it is not in the book.** It keeps edge's contract-by-contract walk while dividing out the contract's size, the loss
attribution's second-ranked fix; it is not in AFTS. It rests on the edge step, which is not in the book (lever 3), and on
its one working run the ranking still leaned to large contracts (+0.45) and held fewer names than the fraction step (12.9
against 16.8 on this start), so it does not remove the concentration that keeps the edge step out.

**The data.** One working run, Carver's optimiser covariance with the edge step, on the first 2y start; account Sharpe
after costs; return is total return on the account.

| Run | Account Sharpe | Return | Names held a day | Spearman, one-contract risk against held/target |
|---|---:|---:|---:|---:|
| 2y, edge | +0.048 | -2.06% | 12.0 | +0.61 |
| 2y, size-neutral edge | -0.284 | -13.51% | 12.9 | +0.45 |

The full 21-run set with it was not run.

## 5. Optimiser covariance: sample or Carver's (strategy twenty-five's form)

**What it does to the book.** It sets the covariance the optimiser uses to measure tracking error. `sample` (today): the
equal-weight sample covariance of each free row's last 756 closes (`kOptimiserBars`, a literal at
`src/strategy/trend_following.cpp:665`). `carver`: sigma_i sigma_j rho_ij, with sigma strategy three's blended standard
deviation x 16 and rho from the overlay's weekly exponentially weighted correlations, 26-week span, no shrinkage. The
switch `optimiser_covariance` is on `archive/keep-do-not-delete/s25-one-pass`, not on main.

**Why the sample is in the book.** The window is 756 closes by design (decided on 2026-09-21): an equal-weight sample
uses every return it is given, so its length moves its answer, and 756 kept the optimiser on the prices the trend sleeve
then held, whose history was capped at 756 at the time. What it does: a three-year equal-weight sample of daily
adjusted returns moves slowly, so the search's substitutions (which contract stands in for which) and the one-contract
risks that set the buffer change little from day to day. Carver's estimator, a 32-day volatility with correlations from
"the last six months of weekly returns" (AFTS, strategy twenty-five, p.417; Appendix B, p.641, weights them exponentially
over six months), follows recent risk. Keeping the sample over Carver's estimator is a measured result, not a mechanism:
the buffer and the tracking error it is compared with both scale with volatility (B_sigma is the largest one-contract
risk, and the tracking error is measured on the same covariance: `src/optimization/one_pass.cpp:231-245`, `:257-259`), so
a volatility rise across the book moves both together, and no mechanism shows that either estimator trades less on this
book. The measured comparison below is the support: Carver's form was lower on the 5y and the 16y.

**The data.** The final grid; account Sharpe after costs; 16y one run, others five-start means.

| Set | 16y | 10y | 5y | 2y | Year |
|---|---:|---:|---:|---:|---:|
| the chosen setup (sample) | 0.300 | 0.290 | 0.273 | 0.102 | 0.407 |
| Carver's optimiser covariance alone | 0.151 | 0.308 | 0.147 | 0.072 | 0.432 |
| Carver's minus sample, paired | -0.15 | +0.02 | -0.13 | -0.03 | +0.03 |

On the 12-run grid, today's buffer with Carver's optimiser covariance made 0.19 against the gold and silver micro book's
0.34. In the strategy-twenty-five measurements Carver's optimiser covariance alone widened B_sigma in volatility spikes
(2025-04-08: 0.129 against 0.071). In the real-micros check it closed the year's real/aligned gap (0.6 points) but not
the 2y's (16.7 points). With the edge step it is lever 17's fourth set.

## 6. Sample-covariance lookback (`sample_cov_window`)

**What it does to the book.** Each searched row's closes are cut to its own last N closes (40 to 756, absent = 756)
before the unchanged sample covariance; the search then uses the shared dates, so the window reads about 0.8 N returns
(median 100, 202, 405 and 609 at 126, 252, 504 and 756). The default 756 is the trend sleeve's last 756 consumed bars:
`kOptimiserBars`, a literal at `src/strategy/trend_following.cpp:665`. The key is on `archive/keep-do-not-delete/levers`
and not on main.

**Why it is not in the book.** A shorter window follows a change in correlations sooner; Carver uses about six months
(AFTS, risk overlay in Part Six, tactic four; AFTS, strategy twenty-five), and the 160 setting is about his six months
after the shared-date thinning. Its cost is a noisier estimate: a 36-instrument covariance has 666 entries, and fewer
returns estimate them less precisely. The book keeps 756 closes for the reason in lever 5: an equal-weight sample's
answer moves with its length, and 756 was set on 2026-09-21 to keep the optimiser on the prices the trend sleeve then
held. The data below are the support: no lookback raised its base's mean. The
63-bar window was dropped before any run: after the shared-date thinning it
reads about 50 returns for a 36-instrument covariance, too few to estimate it; 126 was replaced by 160.

**The data.** One working run first: the chosen setup's 2y at 126, +0.067 against -0.160 at 756. Then the targeted
round's second set of runs: 160, 252 and 504 on the chosen setup and on the equity fast-speed cut with the
high-volatility switch and the high-volatility switch alone (both on their own limits, lever 15), 21 runs each, at each
base's limits. Account Sharpe after costs; 16y one run, others five-start means.

| Window N | 16y | 10y | 5y | 2y | 1y | Mean of five |
|---:|---:|---:|---:|---:|---:|---:|
| 756 (the chosen setup) | 0.300 | 0.290 | 0.273 | 0.102 | 0.407 | 0.274 |
| 504 | 0.167 | 0.328 | 0.060 | 0.191 | 0.250 | 0.199 |
| 252 | 0.217 | 0.269 | 0.078 | 0.073 | -0.016 | 0.124 |
| 160 | 0.109 | 0.268 | 0.156 | 0.094 | 0.571 | 0.240 |

On the other bases (the two together on own limits, mean 0.242; the high-volatility switch on own limits, mean 0.248):

| Set | Limits | 16y | 10y | 5y | 2y | 1y | Mean |
|---|---|---:|---:|---:|---:|---:|---:|
| the two together, 160 | that base's own | 0.396 | 0.343 | 0.165 | 0.239 | 0.027 | 0.234 |
| the two together, 252 | that base's own | 0.326 | 0.337 | 0.119 | 0.123 | -0.029 | 0.175 |
| the two together, 504 | that base's own | 0.340 | 0.357 | 0.120 | 0.297 | -0.025 | 0.218 |
| the high-volatility switch, 160 | that base's own | 0.271 | 0.320 | 0.342 | -0.118 | -0.204 | 0.122 |
| the high-volatility switch, 252 | that base's own | 0.245 | 0.228 | 0.201 | -0.067 | -0.457 | 0.030 |
| the high-volatility switch, 504 | that base's own | 0.327 | 0.355 | 0.142 | 0.309 | -0.106 | 0.206 |

Results jump between neighbouring lookbacks: the largest neighbour jump was 0.587 (the chosen setup's 1y) and the largest
move against a base -0.703; no lookback raised its base's mean. Each lookback ran its base's limits while its own p99
differs on one to three terms (the chosen setup's lookbacks all round net to 5.75), so part of these differences may be
a limits effect. The lookback key is not in the release; its code is
on the archive branch.

## 7. Overlay covariance: sample or Carver's

**What it does to the book.** It sets the covariance the risk overlay uses for R, R_jump and R_shock: `sample` (the
equal-weight sample covariance of the last 252 dates) or `carver` (each contract's sizing standard deviation, the
estimate its position is sized on, with correlations of weekly returns, Saturday to Friday, exponentially weighted over
a 26-week span: `include/trade_ngin/risk/overlay.hpp:60-72`, `kCarverCorrelationWeeks`). The carver estimate reads each
symbol's last 300 bars (`src/strategy/trend_following.cpp:658-664`, `kOverlayBars = 300`). Mechanics:
[`RISK_MODULES.md`](../../RISK_MODULES.md), section "The Carver overlay".

**Why Carver's estimator is in the book.** It is the overlay's own estimator in AFTS: the existing volatility estimates
combined with weekly, exponentially weighted correlations over about six months (AFTS, tactic four, footnote 289,
p.622). Weekly returns avoid the low correlations that daily closes taken hours apart give: they reduce the bias "caused
by markets closing at different times" (same footnote). The volatility is the one each position is sized on, so the
overlay reads the risk the sizing assumed. The 26-week span weights recent weeks most, so a rise in correlations shows
within weeks, where the equal-weight sample of the last 252 dates spreads it over a year. Carver notes that the method
"isn't too sensitive to how the covariance matrix is estimated" (footnote 289, p.622); the limits are set by his
99th-percentile method on this same estimator (lever 8), so estimator and limits go together. Set on 2026-10-27;
`overlay_covariance` stays a template key set to `carver`, and the code requires it
(`src/risk/risk_module_config.cpp:205`).

**The data.** The gold and silver micro book's final, each overlay on its own p99 limits, 17 runs per set; account Sharpe
after costs, window means.

| Overlay (own limits) | Year | 2y | 5y | 10y | 16y (one start) |
|---|---:|---:|---:|---:|---:|
| carver | 0.40 | 0.33 | 0.20 | 0.32 | 0.17 |
| sample | 0.06 | 0.27 | 0.21 | 0.26 | 0.44 |

On the 16y the covariance alone moves little: the stored gold and silver micro book on the sample covariance made 0.38
and the same book on Carver's overlay covariance 0.40, both on the older limits; Carver's own tighter p99 limits take it
to 0.17 (lever 8). In October, Carver's overlay alone (lever 25) was the one setup that trimmed the live year's silver
contract at the book's churn (+28.8% in the year). The switch was ported with checks and no tuning
(`AlgoGators/trade-ngin#257`): `sample` reproduces the old book exactly.

## 8. Overlay limits by the p99 method, and net 5.50 against 5.75

**What it does to the book.** It sets the overlay's five limits (R, R_jump, R_shock in tau; gross and net leverage). Each
is the 99th percentile of the overlay's reading of the target book over the complete cycles after the warm-up of the
set's uncapped 16y run, rounded to the nearest 0.25. Mechanics:
[`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The overlay in brief".

**Why the p99 limits are in the book.** Carver sets each limit at the 99% quantile point of its reading, "find the 99%
quantile point", so each limit's multiplier works about 1% of the time; with four such multipliers he expects the overlay
to be active "around 4% of the time", about two weeks a year (AFTS, risk overlay in Part Six, tactic four, p.605 to 606),
and on his strategy eleven it acts "just under 5%" of the time (p.613). The overlay then cuts only when a reading is in
its tail and leaves the signal's book alone otherwise, and limits taken from the book's own history fit this book's
instruments and contract sizes rather than his 100-instrument portfolio. The chosen setup's five limits cut 193 of 4,738
16y cycles, 4.1%, Carver's rate. Carver rounds each 99th percentile to a round, neat number (his 76% becomes 75%, 3.75
times tau; his 67% becomes 65%, 3.25 times tau: p.608 to 610); the book writes that rounding as a fixed rule, the nearest
0.25, so the same history always gives the same limits.

The net leverage limit is this book's own reading. Like Carver's gross reading it needs no volatility estimate (tactic
four, p.610 to 613), applied to direction: it is the plain signed sum of the positions' notional weights. At equal risk
a low-volatility contract carries many times the notional of a volatile one, so the rates and FX positions dominate the
sum, and it does not measure the book's one-way market risk; Carver warns that leverage "varies radically by instrument"
(p.611), and his finer form, limits by asset class (footnote 292, p.622), is not built. It is a limit enforced by a cut
on the days it binds, never a target: the fifth term of the daily multiplier, with its level from his 99th-percentile
method. It was the binding term on 54 of the 193 cut cycles of the chosen setup's 16y.

**The data.** The final grid's limits (raw p99, rounded):

| | R | R_jump | R_shock | Gross | Net |
|---|---|---|---|---|---|
| the chosen setup's p99 (rounded) | 1.68 (1.75) | 4.82 (4.75) | 3.06 (3.00) | 7.18 (7.25) | 5.59 (5.50) |
| the other three sets (rounded) | 1.75 | 4.75 | 3.00 | 7.25 | 5.75 |

The chosen setup's net p99 is 5.5926, 0.032 under the 5.625 midpoint, so the rule gives 5.50; the limit was set at 5.50
on 2026-10-29. The chosen setup at 5.75 minus the chosen setup (account Sharpe after costs): 16y +0.039, 10y -0.010, 5y
-0.028, 2y and year 0.000; plain means 0.275 against 0.274. On every 5y start the 5.75 run's fills are the grid's
reference book's exactly. Each set re-run on the chosen setup's limits (the one 16y run):

| Set | At 5.50 | At 5.75 | Change |
|---|---:|---:|---:|
| the chosen setup | 0.300 | 0.339 | +0.039 |
| sample covariance, edge step | 0.272 | 0.252 | -0.020 |
| Carver's optimiser covariance alone | 0.121 | 0.151 | +0.030 |
| Carver's optimiser covariance, edge step | 0.148 | 0.240 | +0.092 |

The 2y moves nothing. The quarter step moves each set's 16y by -0.020 to +0.092. The gold and silver micro book's final
limits, rounded (raw p99 in brackets):

| Limit | Carver overlay, carry off | Carver overlay, carry on | sample overlay, carry off | sample overlay, carry on |
|---|---|---|---|---|
| R | 1.75 (1.68) | 1.25 (1.28) | 2.25 (2.24) | 1.75 (1.71) |
| R_jump | 4.75 (4.81) | 3.50 (3.56) | 4.50 (4.51) | 3.25 (3.32) |
| R_shock | 3.00 (3.04) | 2.75 (2.64) | 4.00 (3.96) | 3.50 (3.41) |
| Gross | 7.25 (7.23) | 7.25 (7.19) | 7.25 (7.23) | 7.25 (7.19) |
| Net | 5.75 (5.66) | 6.00 (5.91) | 5.75 (5.66) | 6.00 (5.91) |

On the 16y one start: the stored gold and silver micro book +73.8%, 0.38; the same on Carver's overlay covariance with
the older limits +83.9%, 0.40; Carver's overlay with carry off on its own limits +22.2%, 0.17; the sample overlay with
carry off on its own limits +94.1%, 0.44. So the covariance alone is +0.02, Carver's tighter risk limits cost 0.23, and
the leverage limits alone add 0.06. Rounding each limit up (1.75 / 5.00 / 3.25 / 7.25 / 5.75) or down (1.50 / 4.75 / 3.00
/ 7.00 / 5.50) shifted a run's Sharpe by up to about 0.2 either way, and rounding down nearly doubles the cut cycles; the
nearest rounding stayed. Doubling every fill's cost lowers Sharpe by 0.04 to 0.05 (Carver's overlay with carry off at 2x:
0.24). Mean margin is 12% to 20% of the account, at most 65.7%. On 2026-10-26 the first measure on the new data (16y,
`held`, Carver's overlay covariance) gave R 1.75, R_jump 4.75, R_shock 3.00, leverage 7.25 / 5.75, and on the sample
covariance the method reproduced the older limits 2.25 / 4.50 / 4.00. In the targeted round every option's own net p99
on the chosen setup rounded to 5.75; that was reported, not used. October's limits (lever 25) are superseded: a 25%
expected-risk ceiling cut the book on about 8% of days, where Carver sets each limit to bind about 1% of the time and
expects all of them together to act on under 5%; Carver's set measured on the new data replaced them on 2026-10-26.

## 9. Gold and silver micros

**What it does to the book.** Gold and silver micros (MGC, SIL) replace GC and SI from their listing dates, on today's
granularity buffer.

**Why they are in the book.** Micros are used where the full-size contract cannot be held at 500,000. A 20% target on
500,000 asks for about 0.05 of one full-size gold or silver contract at an average forecast, so at full size the whole-contract decision held them well off their target (the plain full-size book held GC on 1,137
days at 1.7 times its target) and a window's result could rest on that one holding. A micro is a tenth of the size, so the book can hold them close to
target. Carver's basis: own at least four contracts when you begin trading, and use smaller contracts when capital is
limited (AFTS, strategy two). Gold and silver are the book's coarsest holdings: one GC contract carries 12.9 of Carver's
1% buffers (lever 1). The listing dates and the universe are in [`THE_BOOK.md`](../01_the_book/THE_BOOK.md), section
"Why these 36 markets, and the gold and silver micros".

**The data.** On the 17-run sizing matrix (lever 1), the gold and silver micro book made 0.31 / 0.35 after / before costs
against the starting book's 0.18 / 0.22, held GC at 0.9x and SI at 1.1x their targets over 16y, and its whole-contract
16y made +419k before costs against the starting book's +65k. Then real MGC and SIL bars replaced the stand-ins
(full-size bars under micro names). The year from 2025-10-05 on the settings of Carver's overlay with carry off; return
is the engine's total return on the account, Sharpe the engine's.

| Run | Calendar | Return | Sharpe |
|---|---|---:|---:|
| stand-in | stand-in's | +6.20% | 0.39 |
| real MGC/SIL | real | -9.25% | -0.48 |
| real, calendar aligned to the stand-in | stand-in's | +7.22% | 0.44 |

The independent check, variant by variant, same basis:

| Run | Calendar | Prices | Volumes | Return | Sharpe |
|---|---|---|---|---:|---:|
| real, twin volumes | real | real | GC/SI | -12.18% | -0.67 |
| real, twin prices | real | GC/SI | real | -10.55% | -0.56 |
| twin both | real | GC/SI | GC/SI | -9.93% | -0.53 |
| junk off | real | real | real | -9.92% | -0.51 |
| aligned + GC/SI volumes | stand-in's | real | GC/SI | +6.20% | 0.39 |
| aligned + GC/SI prices | stand-in's | GC/SI | real | +6.20% | 0.39 |
| less SI's 2025 hole dates only | partly | real | real | +5.63% | 0.38 |
| less the 2026 Sundays only | partly | real | real | -10.15% | -0.54 |
| less the older dates only | partly | real | real | -11.46% | -0.62 |

Seven one-tick tests changed no book. On 17 windows: real -4.49 mean paired points (12 of 17 lower), the hybrid -1.87
(8 of 17), aligned +0.92 (8 of 17). The hybrid (twin prices until the micro's median weekday volume reached half the
twin's) does not help the year (-11.45%), because it keeps the micros' own calendar. Carver's optimiser covariance on the
same comparison closes the year's gap (0.6 points) but not the 2y's (16.7 points).

The real-data drop was not the micros: the stand-ins were built from the continuous series, which had holes the real
micro bars do not, and the optimiser's shared-date covariance spread them to the whole book; the calendar accounts for
-16.5 of the -15.45 points. The fix was to fill the data (lever 19); after the fill the real-data year is +0.35%, a data
result. A per-instrument covariance would remove the coupling; it is a finding, not built. Adopted on 2026-10-27, on real
data from the final grid onward. The earlier micro sets ran on other loops: the wide sets on the lap loop (lever 35) and
on the reference model (lever 22).

## 10. Other micro sets and Carver's instrument selection

**What it does to the book.** Micro FX puts FX on micros; the wide set puts FX, metals and crude on micros; the metal
micros put gold, silver and copper on micros (MGC, SIL, MHG); the crude micro replaces CL by MCL from 2021-07-12;
Carver's own selection for the account size replaces the universe by the instruments his selection picks at 500,000.

**Why they are not in the book.** Micros are used where the full-size contract cannot be held at 500,000, as in gold
and silver (lever 9). Elsewhere a micro means the book holds several contracts where it held none or one, and every
one-lot adjustment becomes a trade: the wide set churned 278 to 376 round trips a year, and micros on twenty rows nearly
tripled the churn on the earlier loop (lever 35). Micro FX does not solve the coarse-contract problem, because FX is not
where the book is coarse, and it turns one-lot adjustments into trades. The copper micro adds trades where copper is not
the coarse contract: full-size copper wants about 0.34 contracts at an average forecast against gold's and silver's
0.05; the gold, silver and copper set made 0.13 against 0.30 on the 7-run screen, and the copper micro (MHG) has only
been listed since 2022-05-02.

The crude micro was also tried: CL was held at 2.3 to 10.3 times its target on the days it was held,
and MCL brought crude nearer target at about 297 dollars a year of extra cost. Over 21 windows crude's own net P&L
improved (a mean +19,550 dollars) while the rest of the book's fell (a mean -39,201, led by RB, HO and NG), through the
shared search and overlay; the cause is not traced.

Carver's own selection is his static answer for a small account: choose the instruments the capital can hold at four
contracts each (AFTS, strategy two; his greedy selection, strategy twenty-five). At 500,000 it stops at 7 instruments.
His other answer for exactly this case is dynamic optimisation over the full set (strategy twenty-five), which the book
runs (lever 34), and his own data favour it: a 500,000 account trading 100 instruments under dynamic optimisation made a
Sharpe of 1.22, against 1.18 for 27 instruments chosen by static selection and 1.27 for the full portfolio at 50 million
(AFTS, table 124, p.433).

**The data.** Returns are engine total returns on the account after costs; Sharpe figures are the account Sharpe after
costs.

| Set | What was measured | Result |
|---|---|---|
| micro FX | 16y return | +95.1% (long windows did not order one way) |
| FX, metals and crude on micros | round trips a year; mean Sharpe after costs, 17-run sizing matrix | 278 to 376; 0.17 against the gold and silver micro book's 0.31 |
| Carver's selection for the account size | instruments picked at 500k; 10y return; 16y Sharpe | 7; -50.7%; 0.12 against the starting book's 0.08 |
| metal micros (gold, silver, copper) | mean Sharpe after costs, 7-run screen | 0.13 against the gold and silver micro book's 0.30 |
| crude micro | paired Sharpe after costs, 21 windows (Carver overlay, carry off, filled data) | mean -0.001, median -0.051; ahead on 8 of 21; the 5y lower on all five starts (-0.29) |

Carver's selection ran eight ways offline (an expected Sharpe from his procedure, not a backtest), on the 36 instruments
plus 10 stand-ins. Every run stopped at 7 because no eighth instrument fits without a member failing its minimum capital.
Run 1's set was the one run on the engine.

| Run | SR* | Weights | T | Selected | Expected SR |
|---|---:|---|---|---|---:|
| 1 | 0.300 | equal | measured | M2K, 6J, KE, HE, MCL, MBT, 6M | 0.684 |
| 2 | 0.300 | equal | 7 | M2K, 6J, KE, 6L, HE, MBT, MCL | 0.724 |
| 3 | 0.300 | sector | measured | M2K, 6J, KE, MCL, MBT, MGC, ZT | 0.660 |
| 4 | 0.300 | sector | 7 | M2K, 6J, KE, MCL, MBT, MGC, ZT | 0.688 |
| 5 | 0.044 | equal | measured | M2K, 6J, 6E, ZF, ZM, MGC, 6M | 0.067 |
| 6 | 0.044 | equal | 7 | M2K, 6J, MCL, MBT, ZM, MGC, HE | 0.090 |
| 7 | 0.044 | sector | measured | M2K, 6J, 6E, 6A, ZT, 6M, 6B | 0.064 |
| 8 | 0.044 | sector | 7 | M2K, 6J, MCL, MBT, ZM, MGC, ZN | 0.089 |

The measured pre-cost subsystem Sharpe was 0.044 against Carver's working 0.3. Micro FX, the wide set and Carver's
selection were superseded by the sizing matrix before a set was chosen.

## 11. Capital size

**What it does to the book.** Only the two capital keys change: 500,000 against 1, 2, 5 and 10 million.

**Why 500,000 is in the book.** 500,000 is the account the book trades, and the book is sized on the capital it trades;
the capital is a fact of the account, not a setting chosen on results. A larger account would hold every target closer
in whole contracts and depend less on which coarse contract it happens to hold; it is not a lever in AFTS, where his
minimum capital (strategy two) is the related idea. The study shows how the same rules behave at other sizes.

**The data.** 100 backtests on the eighteen 5-year windows, the 2y and the 16y, on the book before the micros. Medians
over the 18 five-year windows, except the last column, the dispersion over twelve monthly windows.

| Capital | Account return | Sharpe (sizing capital) | Max drawdown (account) | Round trips a year | Costs % a year | Symbols held | Monthly-start standard deviation (points) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 500,000 | +19.6% | +0.155 | 23.7% | 143 | 0.61 | 12.9 | 17.5 |
| 1,000,000 | +23.5% | +0.262 | 24.4% | 359 | 0.87 | 20.3 | 10.2 |
| 2,000,000 | +6.8% | +0.078 | 29.2% | 789 | 1.18 | 27.3 | 5.8 |
| 5,000,000 | +10.9% | +0.073 | 28.8% | 1743 | 1.69 | 32.5 | 7.2 |
| 10,000,000 | +8.4% | +0.047 | 30.1% | 2369 | 1.99 | 34.3 | 6.6 |

Risk is the same at every size (volatility on the sizing capital 18.7% to 19.2%). A bigger book holds more symbols (12.9
to 34.3) and depends less on the start date (17.5 to 6.6 points), and it churns more (143 to 2,369 round trips a year;
costs 0.61% to 1.99% of capital a year). Over the eighteen 5-year windows the median return did not rise with size: 1m
was above 500k (account +23.5% against +19.6%, Sharpe 0.262 against 0.155) and 2m, 5m and 10m below it (+6.8% to +10.9%,
0.047 to 0.078). Mechanics: [`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "Account size".

## 12. Per-contract speed limit

**What it does to the book.** A trend speed is not run on a contract when its yearly cost there exceeds 0.15 Sharpe-ratio
units; the speeds left weigh equally with table 36's multiplier. The list: no 2/8 on 6M, GF, HE, LE, NG, ZL, ZS; no 2/8
or 4/16 on 6L, KE, ZC, ZW; no 2/8, 4/16 or 8/32 on ZR: twelve contracts, eighteen rules. Mechanics:
[`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "Rules removed from a contract by cost".

**Why it is in the book.** A speed whose cost on a contract uses up too much of what it can earn there pays the broker
more than the book. Carver: "on an instrument by instrument basis, I remove trading rules that are too expensive", with a
limit of 0.15 SR units of cost a year for a trading rule (AFTS, strategy nine, p.200 to 201), where yearly cost =
(turnover + 2 x rolls a year) x risk-adjusted cost per trade, on table 35's turnovers. The book applies his procedure as
written, so a speed is removed only where its own cost on that contract is over his limit.

**The data.** Eighteen 5-year start dates, change against the engine; median Sharpe change on the sizing capital;
drawdown on the account.

| Change | Windows ahead | Median Sharpe change | Median change in max account drawdown | Round trips a year |
|---|---|---|---|---|
| Speed limit by cost | 9 of 18 | -0.01 | +1.1 points | -5 |
| Proportional drawdown | 8 of 18 | -0.01 | +1.9 points (deeper in 13) | +9 |
| Speed limit + proportional | 13 of 18 | +0.15 | +0.7 points | +5 |

On the unrounded target book net of costs the speed limit adds 0.029 (lever 24). Adopted on 2026-10-09, and in the
conservative template at 4c92eb1b8d. The list uses Carver's table-35 turnovers; the book's own turnovers are 23% to 52%
higher, and on them eight more rules would go. Two cells sit on the line: ZW 4/16 0.1504 (removed) and HE 4/16 0.1477
(kept); on the tuning data ZW 4/16 read 0.1483 and the list was kept. The equity micros' 2/8 costs 0.018 to 0.049 a year
against the 0.15 limit, so no equity speed is removed by cost. The list was measured on full-size GC and SI; MGC and SIL
are unmeasured against it.

## 13. Proportional drawdown and equity-index shorts off

**What it does to the book.** Proportional drawdown sizes on the start capital times the account over its peak, a
percentage-of-account form, in place of Carver's half compounding (formulas in lever 24). Equity-index shorts off allows
no short position in the equity indices. Mechanics:
[`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The sizing capital".

**Why neither is in the book.** Half compounding is Carver's own form for his own money: profits above the start are not
added to the notional capital, and losses are taken off it "to ensure my risk targeting is correct" (AFTS, tactic three,
p.598 to 600). The proportional form cuts sizing by the drawdown's share of the peak account, a smaller cut than half
compounding's full dollar loss after a large gain, so it keeps more risk on in a drawdown than the capital at risk
supports. Neither the proportional form nor the other sizing forms of lever 24 is in AFTS.

Carver's trend strategies are long and short from strategy six on (AFTS, "Strategy six: Slow trend following, long and
short"). A book with no equity shorts is a different strategy with a standing long bet in equities. The book's one
asymmetry for equity shorts is the equity slow rule (lever 14), adopted on this book's record of fast-only equity
shorts.

**The data.** Proportional drawdown alone: ahead in 8 of 18 windows, median Sharpe change -0.01 (sizing capital),
drawdown deeper in 13 (table in lever 12). The pair with the speed limit was ahead in 13 of 18 (median +0.15) and below
either part on the 16y standard window, with no mechanism found. On single windows both forms were above the baseline
(lever 24: 5y 0.718 and 0.327 against 0.129). A sizing rule changes only the capital the positions are sized on, so on
the unrounded target book it has no reading; equity shorts off adds about 0.06 there.

## 14. Equity slow rule

**What it does to the book.** An equity-index short (MES, MNQ, MYM, M2K) stands only when both slow speeds (32/128,
64/256) are negative; otherwise that negative forecast is zero. The book shorts an equity index only when the slow trend
agrees. Mechanics: [`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The equity slow rule".

**Why it is in the book.** Adopted on 2026-10-05 on this book's record: 113 of 190 equity-index shorts were opened on the
fast speeds alone, and equity shorts as a whole lost 121,403 over 16 years. The rule removes the shorts the fast speeds
alone would take. It fits Carver's finding that equities are the weakest trend class: in his multiple trend strategy the
median equity instrument made a Sharpe of 0.03, against 0.19 to 0.51 for every other asset class (AFTS, strategy nine,
tables 37 and 38, p.206 to 207), and equities "do even worse when trading quicker filters" (p.207). It is a declared
departure: Carver advises against any asymmetric adjustment, because class-level results are not statistically
significant (strategy twelve, p.267 to 268). As an economic reading only, not a measurement of this rule, it also fits
Carver's "bouncing cat", a rally after strong negative fast forecasts in equity indices (strategy twelve, figure 53,
p.266). Rule 3c, the same idea for all instruments, is not in the book: Carver's evidence is for equities only (lever
29).

**The data.** The deep-dive matrix (reference model): the rule did not cut churn (+29 round trips on the 16y) and raised equity P&L in
every pairing. Offline on the unrounded book it is worth about 20 points over 16 years, and only 2025 would have been
higher without it (+3.9 points, all in the equities). The 7-run screen; each cell is total return on the account, then
weekday account Sharpe after costs:

| Window | Gold and silver micro book | Slow rule off (full size) | Slow rule off (gold and silver micros) |
|---|---|---|---|
| 16y (from 2011-08-21) | +73.8%; 0.38 | +46.2%; 0.24 | +58.9%; 0.30 |
| 10y (from 2016-08-22) | +32.1%; 0.26 | +21.4%; 0.20 | +28.8%; 0.24 |
| 2y first start | +4.2%; 0.19 | +24.8%; 0.64 | -3.5%; -0.03 |
| Mean Sharpe after costs, 7 runs | 0.30 | 0.39 | 0.17 |

On full size the starting book's 7-run mean was -0.02 after costs (0.02 before), against 0.39 / 0.42 with the rule off;
there the gain sat in the metals, and the equities' own 16y net was lower without the rule (+28k against +49k with it).
On the gold and silver micro book, the rule off gave 0.17 against 0.30. It is in the conservative template at 4c92eb1b8d;
with carry it would apply to the trend part only. It was not made a lever of the targeted round.

## 15. Selective speed rules: the equity speed cuts and the high-volatility switch

**What it does to the book.**
- The equity 2/8-and-4/16 cut: the equity indices drop 2/8 and 4/16.
- The equity fast-speed cut: the equity indices run only 16/64 and slower (drop 2/8, 4/16 and 8/32) via
  `trading_rule_removals`, config only.
- The high-volatility switch (`fast_off_high_vol`): on a bar whose smoothed volatility quantile is above 0.75, a symbol
  with fast and slow pairs uses its slow pairs only. It was code written for the tuning and is not on main or on a pushed
  branch.
- The two together: the equity fast-speed cut plus the high-volatility switch.

**Why they are not in the book.** No equity speed is cut. The six speeds are Carver's design, each catching trends of a
different length; he removes a speed only on a contract where its cost is above his speed limit (lever 12), and his
per-contract cost test keeps every equity speed: the equity micros' 2/8 costs 0.018 to 0.049 of Sharpe a year against
his 0.15. A cut chosen on 2025's losses is the over-fitting Carver warns of: he notes that equities do worse with quicker
filters and calls a lower trend allocation in equities potentially over fitting (AFTS, strategy nine, p.207 to 208). An
offline analysis traced the equity fast-speed cut to Carver's published equity figures (AFTS, footnote 190), but his own
equity change (AFTS, table 70) moves equity weight from trend to carry, and this book runs carry off. The high-volatility
switch is not Carver's form: it borrows strategy thirteen's top-quartile volatility regime as an on/off rule, where
Carver attenuates forecasts smoothly and never switches them off; it was found on the period it fixes (2021 to 2026),
and over 2011 to 2020 it lost (lever 29). None of the four is in AFTS. The p99 method gives every book its own limits from its own history, so a set is read on its own limits, the ones
it would trade on.

**The data.** The targeted round; account Sharpe after costs; 16y one run, others five-start means.

| Set | Limits | 16y | 10y | 5y | 2y | 1y | Mean |
|---|---|---|---:|---:|---:|---:|---:|
| the chosen setup | the chosen setup's | 0.300 | 0.290 | 0.273 | 0.102 | 0.407 | 0.274 |
| + equity 2/8-and-4/16 cut | the chosen setup's | 0.362 | 0.240 | 0.250 | 0.389 | 0.455 | 0.339 |
| + equity fast-speed cut | the chosen setup's | 0.370 | 0.337 | 0.122 | 0.379 | 0.517 | 0.345 |
| + the two together | the chosen setup's | 0.440 | 0.440 | 0.214 | 0.244 | 0.320 | 0.332 |
| + the two together | own: 1.75 / 4.75 / 3.25 / 7.25 / 5.75 | 0.365 | 0.353 | 0.202 | 0.248 | 0.041 | 0.242 |
| + the high-volatility switch | own: 1.50 / 4.75 / 3.00 / 7.25 / 5.75 | 0.313 | 0.308 | 0.143 | 0.231 | 0.246 | 0.248 |

The two together on their own limits are looser than the chosen setup's (R_shock 3.25 and net 5.75 against 3.00 and
5.50). At the chosen setup's limits the overlay cut them on 241 of about 4,738 16y cycles (5.1%, about Carver's rate: he
expects "just under 5%" for strategy eleven, p.613) against the chosen setup's 193; at their own limits on 149. Moved
from the chosen setup's limits to their own, with the signal unchanged, they were 0.076 lower on the 16y and 0.279 lower
on the 1y: the looser limits rarely cut (3.2 cycles on the 2y against 21.8) and the book held different whole contracts.
At the chosen setup's limits they had a 5y account maximum drawdown of 27.2% against 22.0%, a 5y worst month of -11.8%
against -8.2%, large per-start losses, and part of their gain outside the equity indices (metals +44.0k of the 16y's
+168.1k). No lookback changed this (lever 6).

Earlier, on the 7-run screens (weekday account Sharpe after costs): the two together on full size 0.56; the
high-volatility switch with gold and silver micros 0.16 and the two together with gold and silver micros 0.31, against
the gold and silver micro book's 0.30; the starting book's full-size mean was -0.02. Full size, weekday account Sharpe
after / before costs:

| Cell | 16y | 10y | 2y first start | Mean of 7, after / before | Round trips |
|---|---|---|---|---|---|
| equity fast-speed cut | +113.3%; 0.51 / 0.55 | +62.3%; 0.43 / 0.46 | +28.3%; 0.70 / 0.72 | 0.24 / 0.28 | 107 to 150 |
| high-volatility switch | +98.9%; 0.41 / 0.45 | +86.1%; 0.48 / 0.51 | +16.0%; 0.44 / 0.46 | 0.45 / 0.48 | 106 to 120 |

The equity fast-speed cut's 2y spread of 40.0 points is the widest, and its first start is a gold holding (414 days at
5.8 times target). The high-volatility switch's tracking error on full size was 18.7% against the starting book's 9.8%.
On full size these gains came with one gold contract held about 450 days at 6 to 8 times its target; on the gold and
silver micro book they were not there. The rules do raise the unrounded book on every run. Offline, their 16y
t-statistics were 0.83 (the equity fast-speed cut), 1.11 (the high-volatility switch) and 1.34 (the two together), and
the volatility-regime rule's test window is where the idea came from (lever 29). Neither rule is in the book. A one-at-a-time speed sweep was not run, because sweeping speeds to find the
best would fit the period.

## 16. Carry

**What it does to the book.** A carry forecast beside trend (weight 0.4 in the grid), one combined forecast.

**Why it is built and switched off.** Carry has not yet been tuned on the data the book trades. It needs two real
contract prices on the same date, which only the per-contract bars give, and the second contract is missing on many
dates: over 16 years UB has one on 55.4% of dates and 6M on 66.0%, and 6L's longest gap is 78 dates. When carry was
measured (2026-10-26 and 10-27) the continuous series still had holes (AlgoGators/algogators#36) and the gold and silver
micros were stand-ins, and the final retune ran with carry off (AlgoGators/algogators#186). What the measurements show:
Carver's case for carry is a second return beside trend, from the shape of the futures curve (AFTS, strategies ten and
eleven), and on these instruments since 2011 carry alone earned about nothing
(unrounded 16y Sharpe 0.02 against trend's 0.26), so the blend kept trend's Sharpe at lower volatility and added no
second return: 40 percent of the forecast moved to a rule that earned nothing here. Why the traded blend's after-cost
Sharpe falls is unmeasured: the data do not show a larger rounding or tracking loss with carry, and the number of rows
that round to zero was never counted. Carry is on main, switched off (AlgoGators/algogators commit c7cec21205, the carry
commit of `AlgoGators/trade-ngin#282`). The full record is in [`CARRY.md`](CARRY.md).

**The data.** The 12-run grid (the gold and silver micro book, the minimal buffer with Carver's optimiser covariance,
and today's buffer with Carver's optimiser covariance, each carry off and on), then the gold and silver micro book's
final, 17 runs per set; mean account Sharpe after costs:

| Set | Mean | Median | Runs positive |
|---|---:|---:|---:|
| Carver overlay, carry off | 0.28 | 0.24 | 16 of 17 |
| Carver overlay, carry on | -0.08 | -0.11 | 6 |
| sample overlay, carry off | 0.25 | 0.26 | 16 |
| sample overlay, carry on | -0.04 | -0.02 | 8 |

On the 12-run means carry on moved the after-cost Sharpe from 0.34 to 0.04 on the gold and silver micro book and from
0.19 to -0.15 on today's buffer with Carver's optimiser covariance, and from 0.13 to 0.17 on the minimal buffer with
Carver's optimiser covariance, from the year starts alone. The dollar change (a mean 65k to 220k of net P&L per run on
the year to 10y) mixes the blend's lower risk with its results, so the Sharpe is the comparison used. On the grid's gold
and silver micro book carry roughly halved the round trips (76 to 110 a year with it against 152 to 216 without, 12 runs
per cell), and the blend tracked its unrounded book a little more closely (median tracking error 9.5 / 8.7 / 9.6 against
9.8 / 9.3 / 11.3 percent; on the one stored 16y run 7.0 against 8.3). On that run, on the starting book's settings, the
whole-contract blend kept a smaller share of its unrounded book (9k of 200k against trend alone's 65k of 260k) at a
similar dollar shortfall (about 191k against 195k).

## 17. The final grid's four sets

**What it does to the book.** The two-by-two of levers 3 and 5, each set on its own p99 limits, on the filled data with
real micros: the sample or Carver's optimiser covariance, with the fraction or the edge step.

**Why the chosen setup is in the book.** It is Carver's fraction step (lever 3) on the sample optimiser covariance
(lever 5). The edge step concentrates the book in a few large contracts (lever 3). The 756-close window was set
for its length, since an equal-weight sample's answer moves with it (lever 5); keeping the sample over Carver's optimiser covariance
is a measured result, on the data below. The grid tested the two strategy-twenty-five changes that might lift the 2y; no set raises the 2y without
lowering a longer window.

**The data.** 84 runs plus 6 limits-split runs and 21 runs at net 5.75. Account Sharpe after costs; 16y one run, others
five-start means.

| Set | 16y | 10y | 5y | 2y | Year | Plain mean |
|---|---:|---:|---:|---:|---:|---:|
| the chosen setup (sample, fraction) | 0.300 | 0.290 | 0.273 | 0.102 | 0.407 | 0.274 |
| sample, edge | 0.252 | 0.244 | 0.057 | -0.206 | 0.550 | 0.180 |
| Carver's optimiser covariance, fraction | 0.151 | 0.308 | 0.147 | 0.072 | 0.432 | 0.222 |
| Carver's optimiser covariance, edge | 0.240 | 0.308 | 0.082 | 0.245 | 0.636 | 0.302 |
| the grid's reference book | 0.339 | 0.288 | 0.245 | 0.102 | 0.407 | 0.276 |

Carver's optimiser covariance with the edge step carries the edge step's concentration: it held 12 names a day against
15 to 17, and its 5y fall and 2y rise were each traced to one energy episode (lever 3). Its 2y and year figures sit in
the 2024 to 2026 period that all the 2y starts share; its 5y was 0.082 against 0.273 and its 16y 0.240 against 0.300,
and its mean 5y and 2y drawdowns were 30.9% and 20.7% against 22.0% and 14.9%. The largest paired t on the 16y is 1.14.
The chosen setup was set on 2026-10-29.

## 18. The loss attribution's ranked fixes

**What it does to the book.** Nothing ran; it ranks fixes from a decomposition of the final grid's runs into target book,
rounding gap, residual and costs.

**Why none is in the book.** Each ranked fix is a lever with its own economic reason, and none is in the book for that
reason: the minimal buffer keeps the width (lever 1), the size-neutral edge rests on the edge step (lever 4), net 5.75 is
not what the p99 rule gives (lever 8), and a slower speed mix is a blanket speed cut (lever 36). The decomposition put
the 2y loss in the signal, not in the rounding, and on the unrounded target book over 16 years the fast speeds earned
196k of 270k, so a fix aimed at 2025's fast-speed losses would also remove the speeds that earned most of the long-run
return. Its reading rule stands for later tests: judge a fix first on the target book, the tracking error and the gap's
spread, then on window means, because the gap's tracking error of about 8% a year is a standard deviation of roughly 89k
over five years.

**The data.**

| Fix | Order in the attribution | What happened next |
|---|---|---|
| Minimal buffer on the chosen setup | 1 | measured on the chosen setup's limits in the targeted round (lever 1) |
| Size-neutral edge (rank by error removed per unit of the contract's risk) | 2, for Carver's optimiser covariance with the edge step | built as `edge_risk` (lever 4); its full set was not run |
| Net 5.75 | 3 | measured; 5.50 kept (lever 8) |
| Cost-aware edge | 4; would deepen concentration | replaced by `edge_risk` |
| Slower speed mix or dropping fast speeds | not recommended: fits 2025 | not run as a speed sweep |
| Leaving out lumpy energy, the wrong-sign buffer, a lower `b_sigma_floor` | not recommended | not run |

## 19. The data fill

**What it does to the book.** It inserts the 476 continuous-table rows (484 with real micros) that production's contract
table has but its `.v.0` table lacks, in both continuous tables, by bracket (B), else carry (C), else volume (V). The
data rules are in [`../01_the_book/ROLLS_AND_DATA.md`](../01_the_book/ROLLS_AND_DATA.md).

**Why it is in the book.** The engine grids every per-contract series on the `.v.0` dates, so a hole removes a real bar
from every run, and the optimiser's shared covariance spreads one symbol's hole to the whole book. It is a data
correction, not a lever in AFTS: the book must trade on the prices that existed.

**The data.** The engine year from 2025-10-05 on the old limits of Carver's overlay with carry off; total return on the
account and engine Sharpe.

| | Unfilled | Filled |
|---|---:|---:|
| Total return, year from 2025-10-05 | -9.25308% | +0.352205% |
| Engine Sharpe | -0.481294 | 0.115127 |
| `opt_bars_per_year`, first traded cycle | 244.74 | 251.34 |

The holes were the book's own ingestion's, from a daily pull that restarted at the whole table's latest date. The filled
rows match Databento's contract on all 42 checked dates, and 3,418 of 3,428 rolls are unchanged. Every later round tunes
on filled data. On production the fill is a step of the futures release (`scripts/futures_release/README.md`,
`AlgoGators/algogators#180`); the release scripts are on main since `AlgoGators/algogators#124`, and the fill's issue is
`AlgoGators/algogators#36`.

## 20. Roll accounting and the share-test line

**What it does to the book.** The roll work: adjusted series for every return, no P&L on a change bar, roll legs with
their costs; and, split out on 2026-10-03, the one-line share test that turned shorts on. Mechanics:
[`FUTURES_ROLLS.md`](../../FUTURES_ROLLS.md), section "13. With the switch off: the vendor's series" (change and flip
bars on the vendor's series) and section "6. The two ROLL legs".

**Why it is in the book.** A roll is not a market move: booking the switch jump as P&L puts a false return in the book.
Trend on adjusted prices is Carver's default (AFTS, strategy one). The share test is a code line, not a lever.

**The data.** Engine end equity on a 500,000 start, whole contracts, after costs, on the 2y, 5y and 16y of that time,
except the last two rows, which are net P&L on the reference model.

| Run | 2y | 5y | 16y | 16y Sharpe |
|---|---:|---:|---:|---:|
| before the roll work, long-only | 560,165 | 556,647 | 698,318 | 0.28 |
| roll commit 1 | 653,311 | 623,064 | 626,695 | 0.20 |
| roll commits 1+2 | 671,670 | 596,252 | 757,525 | 0.34 |
| shorts off, roll cost 0 | 671,669.64 | 596,251.80 | 757,525.02 | 0.3368 |
| shorts off, roll cost on | 670,599.46 | 592,362.86 | 743,781.04 | 0.3228 |
| shorts on, roll cost 0 | 563,113.76 | 387,286.73 | 463,277.68 | 0.0337 |
| shorts on, roll cost on | 561,780.01 | 377,315.34 | 462,869.35 | 0.0340 |
| reference model, long and short, net P&L | 5,943.33 | 44,018.18 | 255,032.78 | |
| reference model, long-only, net P&L | 14,261.59 | 228,151.05 | 209,417.91 | |

Long-only, commits 1 and 2 raised end equity on every window; commit 1 alone was lower on the 16y because it changed
positions while still booking the raw switch jump as P&L. Roll legs at cost 0 moved no equity cent; roll costs alone took
1,070.18 / 3,888.95 / 13,743.98; shorts on under the lap loop took 108,555.88 / 208,965.07 / 294,247.34. The fall was the
lap loop's handling of shorts, which the redesign replaced. Nothing here was adopted as an option.

## 21. Churn remedies on the redesigned loop (reference model)

**What it does to the book.** Each cause-level fix for the redesigned loop's churn. The control is the one-pass loop as
specified, with the search's cost penalty at 50 (the penalty multiplies the cost of trading away from the held book in
the search's objective). The first sign-close remedy moves the forecast-sign close behind the whole-book buffer, so a
wrong-signed position closes in full only on a day the buffer trades; the second is the same with penalty 100. The
deadband holds a position whose forecast has flipped until the opposite forecast reaches the band. The B_sigma multiples
widen the one whole-book buffer. The static buffer replaces the search with Carver's strategy-eight per-instrument
buffer; the per-symbol version keeps the search and adds that buffer on top. P1(K) moves a symbol fully to the search's
answer on a trade day when the gap is at least K contracts and otherwise holds it, openings included. P2 trades to the
edge of a one-contract zone. P3 is P1(1.5) plus the deferred sign close.

**Why none is in the book as such.** Each aims at a cause of needless trades (small reversals, a forecast hovering near
zero, one-lot trims) against a bar of about 150 round trips a year. The bar is set near what the long-only book already
traded (111 to 167 round trips a year), because round trips per traded instrument (3 to 11 a year) were already within
Carver's expected range and the excess trading was resizes. The penalty range 10 to 100 and the strategy-eight
buffer are Carver's; the rest are not in AFTS. The remedies that brought round trips to about 150 or under with positive
returns, penalties of 250 to 300, did it by starving grains and livestock, which removes a sector's diversification from
a book whose return comes from moves nobody can place in advance; 300 is also outside Carver's range. The wider buffers
under-risk the book, the static buffer doubles churn, and the one-contract triggers stop energy and metals opening. The
deep dive took up the penalty and the deferral (lever 22).

**The data.** On the reference model, long and short, 500,000, 5y / 16y, after costs, one run per window.

| Remedy | Round trips | Return | Sharpe | What it did |
|---|---:|---:|---:|---|
| control, penalty 50 | 272 / 316 | +51% / +69% | 0.48 / 0.27 | control |
| sign close behind the buffer | 281 / 330 | | 0.55 / 0.26 | the buffer is open about 80% of days |
| the same + penalty 100 | 182 / 273 | +30% | 0.35 | over 150 round trips |
| penalty 100; 200 | 189 / 250; 167 / 195 | | 0.35 / 0.26; 0.44 / 0.29 | over 150 round trips |
| penalty 250 | 147 / 165 | +43% / +80% | 0.43 / 0.30 | near 150 round trips; grains 16%, livestock 5% of days |
| penalty 300 | 138 / 121 | +48% / +58% | 0.48 / 0.25 | under 150 round trips; outside Carver's 10 to 100 |
| penalty 300 + deadband | 126 / 135 | +47% / +90% | 0.45 / 0.31 | under 150 round trips; same sector starvation |
| deadband abs F below 2; below 4 | 234 / 350; 191 / 302 | | | over 150 round trips |
| B_sigma x1.5; x2 | 135 / 208; 92 / 109 | | -0.31 / 0.14; 0.13 / 0.08 | under-risks the book |
| static strategy-8 buffer | 364-592 / 427-637 | | | doubles churn |
| per-symbol strategy-8 on the search | 250-309 / 262-389 | | -0.41 to 0.23 | over 150 round trips |
| P1 K 1.5 | 161 / 226 | -25% / +27% | -0.32 / 0.18 | energy and metals stop opening |
| P1 K 2.5 | 77 / 77 | -16% / -11% | -0.26 / -0.02 | negative on both windows |
| P2 one-contract zone | 182 / 170 | -18% / -16% | -0.18 / -0.01 | negative on both windows |
| P3 | 164 / 294 | -14% / +80% | -0.12 / 0.30 | over 150 round trips, 294 on the 16y |

A carry proxy cut round trips and left the Sharpe where it was; carry waited for its data (lever 16). The deep dive later
found the deadband's own effect on 16y round trips was -4.

## 22. The deep-dive catalogue (reference model)

**What it does to the book.** Every knob below, one at a time or in the 96-run matrix (penalty x deferral x slow rule x
trim). The deferred sign close holds a position whose forecast has flipped while the new forecast is weaker than 2, and
closes it once the opposite forecast reaches 2; the deferral at 4 does the same at 4. Carver's width fixes the buffer at
0.05 x tau = 1%, where the design's width is the largest one-contract risk. Risk inertia and hysteresis make the search
pay T times a contract's own risk to trade it, with moves delivered in full once they are large enough in risk. The
one-step trim touches only partial reductions: it goes to the answer in one trade when the gap is 2 or more and holds
when it is 1. Reductions in full deliver at once any reduction the search asks for. The collar never holds more than the
target rounded up to the next whole contract; the second form adds a risk allowance. Carver's own risk limits replace
0.45 / 0.90 / 0.80 with 0.30 / 0.75 / 0.65. The per-name risk cap limits contracts x weight x sigma to 10% of equity,
never under one contract. The sector cap lets at most K names of a sector hold a position, and a held name keeps its
slot; in the weighted form the held names get the sector's whole weight. Partial micros put a named subset on micro or
mini rows with their own multiplier, tick and fee.

**Why one setting is in the book and the others are not.** Each knob attacks a cause the deep dive found: positions of 0
to 2 contracts, a combined forecast that changes sign 15 to 17 times per name a year, a whole-book buffer, and drawdown.
Carver's own risk limits, his 1% width and his position limit are his; the rest are mixed. The chosen settings keep the
cost penalty inside Carver's range, defer the sign close on a weak opposite forecast, and keep the equity slow rule.
The other knobs cut churn only by shrinking the book: a sector cap changes the risk the book runs instead of how it
trades; hysteresis and high penalties decide which groups of instruments the book can hold at all; delivering every
reduction in full ratchets the book down. Drawdown is in the signals, not the loop: the fractional target book drew down
38.8% at 15.9% volatility. Micros on this loop raised round trips on every subset; they are the whole universe on
another loop and bear on gold and silver micros on the engine only through lever 9.

**The data.** 258 runs on the reference model, 2026-10-04 to 10-05; round trips a year / total return after costs, one
run per window. Under 150 round trips on the 16y: 4 of the 32 matrix settings, all at penalty 300.

| Knob | 2y | 5y | 16y | What it did |
|---|---:|---:|---:|---|
| penalty 50 | 235 / +9% | 272 / +51% | 316 / +69% | control |
| penalty 100 | 214 / +11% | 189 / +31% | 250 / +66% | in the chosen settings |
| penalty 200 | 154 / +7% | 167 / +44% | 195 / +78% | livestock trades on 29% of days |
| penalty 250 | | 147 / +43% | 165 / +80% | livestock 13% |
| penalty 300 | 102 / -12% | 138 / +48% | 121 / +58% | livestock stops |
| deferral at 4 | | 191 / +19% | 302 / +47% | more round trips than the deferral at 2 |
| no sign close | | | 422 | churns |
| penalty 300 + deferral + slow rule | | 137 / +53% | 202 / +126% | penalty 300 stops a sector |
| Carver's 1% width on the chosen settings | 436 / -3% | 511 / +4% | 550 / +52% | 436 to 550 round trips |
| risk inertia RI20; RI30 | | 163 / +0%; 125 / -4% | 181 / +14%; 132 / +74% | same idea as hysteresis |
| one-step trim alone | 221 / -8% | 251 / +56% | 341 / +68% | the chosen settings have it off |
| reductions delivered in full | 236 / -6% | 235 / +0% | 367 / +70% | a ratchet: the book shrinks |
| collar | 181 / +5% | 250 / +63% | 330 / +70% | 3 of 5 starts negative on the 5y |
| collar with risk allowance | 167 / -6% | 178 / -17% | 315 / +100% | 5y loss |
| Carver's own risk limits | 164 / +6% | 265 / +71% | 272 / +80% | drawdown unchanged; the control's 5y turns negative |
| per-name risk cap 0.10 | 153 / +2% | 247 / +17% | 299 / +65% | drawdown not lower |
| sector cap 3 | 143 / -6% | 170 / +3% | 284 / +122% | a smaller book |
| sector cap 2 | 92 / +3% | 111 / +6% | 154 / +53% | a smaller book, 11% to 12% volatility |
| sector cap 1 | 54 / +12% | 68 / +6% | 62 / +8% | a smaller book |
| cap 2, weights follow | 192 / +61% | 257 / +52% | 516 / +136% | 50.5% 16y drawdown |
| hysteresis 0.20 | 89 / +4% | 98 / +8% | 116 / +81% | livestock nearly stops |
| hysteresis 0.35 | 45 / +19% | 59 / +10% | 70 / +101% | livestock stops |
| reductions + risk cap | 220 / -1% | 242 / -9% | 312 / +41% | churn and return both lower |
| full reductions + risk cap + 2 per sector | 133 / -4% | 135 / -9% | 165 / +32% | churn and return both lower |

Micros on the whole book, 5y / 16y: P1(1.5) 616 / 701, -15% / -4%; P3 582 / 704, -12% / +6%; penalty 250 471 / 483, -11%
/ +58%; penalty 300 396 / 379, -18% / +56%; per-instrument buffer 10% 1,215 / 1,347, -11% / +8%; 25% 787 / 854, -12% /
-1%; with the deferral and slow rule 779 / 843, +0% / +18%. Partial micros on the chosen settings (round trips, return,
max drawdown):

| Micros on | 2y | 5y | 16y |
|---|---|---|---|
| none | 158, +7%, 14.7% | 208, +52%, 33.2% | 292, +80%, 38.7% |
| SI, GC, RB | 307, +23%, 10.8% | 262, -16%, 36.4% | 444, +71%, 42.4% |
| SI, GC, RB, CL, UB, HG | 349, +6%, 14.7% | 380, -11%, 41.9% | 508, +48%, 43.7% |
| GC, SI, PL, HG | 255, +9%, 14.1% | 317, +19%, 43.3% | 454, +72%, 37.7% |
| CL, NG, RB | 223, +3%, 14.3% | 276, -5%, 35.1% | 533, +104%, 42.1% |
| 6A, 6B, 6C, 6E, 6J, 6S | 223, +5%, 11.6% | 258, -1%, 43.4% | 397, +69%, 39.3% |
| ZC, ZS, ZW, KE, ZL, ZM | 197, +11%, 10.9% | 228, +6%, 37.5% | 289, +66%, 38.4% |
| UB | 186, +3%, 16.9% | 212, +46%, 30.9% | 328, +85%, 39.4% |

The deep dive's chosen settings: penalty 100, sign close deferred, equity slow rule on, one-step trim off; 158 / 208 /
292 round trips and +7.2% / +51.5% / +79.5% on the 2y, 5y and 16y. On this loop every micro subset raised round trips on
the 2y and 5y, and micros everywhere tripled them and turned the 5y negative. The deferral's own effect on 16y round
trips is -4, and the slow rule raised equity P&L in every pairing without cutting churn.

## 23. Listing switch rules

**What it does to the book.** It decides what happens on the day the equity micros replace the E-minis (CME listed the
micros on 2019-05-06). Mechanics: [`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "Listing
dates and the switch".

**Why rule C is in the book.** A backtest that trades a contract before it existed shows a book nobody could have held,
so the switch happens on the listing date, and on that day the book should land as close to the signal as it can. Rule C
closes the E-mini and opens the micro at the day's rounded target, which is by construction the position the signal
asks for on that date. Not in AFTS. Adopted on 2026-10-08.

**The data.** An engine experiment on both fee tables and the standard set, 2026-10-08 to 10-09; dollars of gap to the
signal. Rule A closes the E-mini and lets the micro open through the normal pass from zero: 106k to 170k. Rule B carries
ten micros for each E-mini: 180k to 281k; every E-mini held on the day was one contract against a target of 0.10 to 0.47,
so ten for one brought 10 micros where the signal wanted 2 to 5. Rule C closes and opens at the day's rounded target: 9k
to 16k. Rule D is an exit and an entry for a held pair and leaves a pair with nothing held to the ordinary pass: 78k to
138k.

## 24. The IBKR knob list

**What it does to the book.** Twenty variants, one change each. Write S_0 for 500,000, V for the account, C = V - S_0
and P for the running peak of C floored at 0. Today's half compounding sizes on S_0 - (P - C). The capped account sizes
on min(S_0, V); proportional drawdown on S_0 x V / (S_0 + P); the floor on max(f x S_0, today's figure), with f = 0.75;
half rate on S_0 - 0.5 x (P - C); the yearly reset is today's rule with the peak reset on the first row of each calendar
year; fixed is S_0 always, a diagnostic. Equity-index shorts off allows no short position in the equity indices. The
speed variants drop pairs on every instrument and use table 36's multiplier for the pairs left, 1.19, 1.13 and 1.08. The
guard refuses a contract whose stand-alone risk is above 0.50 or 0.35 of the risk target. Cost weight is 50 or 200
against today's 100, and the buffer is twice today's width. Roll cost at half bounds an assumption and is not a
strategy.

**Why only the speed limit is in the book.** Each was a candidate to raise the IBKR Sharpe on the sizing capital by 0.1
to 0.2. The speed limit is Carver's per-contract cost rule (lever 12). The sizing forms change only how much capital the
positions are sized on, so they move the book's risk over time and leave the signal's return per unit of risk unchanged:
on the unrounded target book they have no reading. Half compounding is Carver's own form (lever 13); the other forms are
not in AFTS, the capped account contradicts his wording, and fixed capital is the form he calls extremely dangerous for
a live account. Equity shorts off is a standing long bet (lever 13), and the speed cuts are blanket cuts (lever 36). The
guard refuses a whole contract when its stand-alone risk is high, where the sizing already scales the position down by
its volatility; on this book it removes heating oil, gasoline, crude, gold, silver and copper in their most volatile
spells. The cost weight is lever 30's multiplier, and the buffer width is lever 1.

**The data.** 131 runs on IBKR fees on the four standard windows ending 2026-10-07; Sharpe on the sizing capital, after
costs, one run per window. The ideal-book column is the variant's own unrounded 16y target, net of a cost estimate, the
reading that does not depend on which whole contracts a run happened to hold. A sizing rule cannot change it, so those
rows have no reading.

| Variant | 2y | 5y | 10y | 16y | Ideal book, net change | Why it is not in the book |
|---|---:|---:|---:|---:|---:|---|
| baseline | 1.057 | 0.129 | 0.277 | 0.287 | 0.212 | |
| proportional drawdown | 1.075 | 0.718 | 0.503 | 0.492 | no reading | not in AFTS; keeps more risk in a drawdown (lever 13) |
| half rate | 1.273 | 0.717 | 0.581 | 0.340 | no reading | a tuned sizing number, not in AFTS |
| floor at 0.75 | 1.057 | 0.231 | 0.547 | 0.341 | no reading | a tuned sizing number, not in AFTS |
| capped account | 1.346 | 0.675 | 0.618 | 0.433 | no reading | contradicts Carver's wording; drawdown 30% on 16y |
| yearly reset | 1.119 | 0.681 | 0.491 | 0.415 | no reading | a tuned sizing number, not in AFTS |
| fixed 500k | 1.304 | 0.802 | 0.509 | 0.505 | no reading | diagnostic; Carver warns against it live |
| equity shorts off | 1.217 | 0.327 | 0.425 | 0.371 | +0.060 | a standing long bet (lever 13) |
| five speeds | 0.148 | 0.284 | 0.199 | 0.311 | +0.006 | blanket cut (lever 36) |
| four speeds | 0.496 | 0.053 | 0.290 | 0.366 | +0.002 | blanket cut |
| three slowest | -0.215 | 0.316 | 0.308 | 0.208 | +0.020 | blanket cut |
| guard 0.50 | 0.117 | -0.242 | 0.115 | 0.015 | no reading | removes contracts in their most volatile spells |
| guard 0.35 | -0.369 | -0.158 | 0.090 | 0.162 | no reading | same |
| cost weight x0.5 | 1.077 | 0.159 | 0.160 | 0.242 | no reading | lever 30 |
| cost weight x2 | 1.158 | 0.384 | 0.232 | 0.439 | no reading | lever 30 |
| buffer width x2 | 0.451 | -0.063 | 0.214 | 0.114 | no reading | lever 1 |
| roll cost x0.5 | 1.057 | 0.382 | 0.265 | 0.197 | +0.018 | a bound, not a strategy |
| speed limit | 1.197 | 0.301 | 0.367 | 0.391 | +0.029 | in the book (lever 12) |
| shorts off + proportional | 1.122 | 0.612 | 0.667 | 0.588 | +0.059 | +0.07 and +0.10 before 2025; as its parts |
| shorts off + half rate | 1.057 | 0.823 | 0.573 | 0.492 | +0.060 | as its parts |
| shorts off + proportional + cost weight x2 | 0.977 | 0.266 | 0.646 | 0.621 | +0.060 | as its parts |

Halving the roll cost, which changes no signal, moved the windows +0.00, +0.25, -0.01 and -0.09. Half compounding and
equity shorts stayed as they were, and cost weight, buffer, guard and roll cost are unchanged.

## 25. The October sizing setups

**What it does to the book.** Seven setups on one build, then four overlay setups:
- the starting book: today;
- the fixed-buffer setup: Carver's fixed 1% buffer in place of today's, nothing else changed;
- the four-micros setup: today's buffer with silver, gold, crude and copper on micros (stand-ins: full-size prices under
  micro symbols);
- the fixed buffer with four micros: the two before together;
- the Carver-overlay setup: Carver's overlay covariance;
- Carver as written at 30%: fixed buffer, four micros, Carver's overlay at a 30% expected-risk ceiling; and the same at
  25%;
- then three p99 setups: the Carver-overlay setup with Carver's p99 limits on the book's own 16y, differing only in the
  ceiling (35%, 25% and 30% of capital); and the 25% p99 setup with the jump and leverage limits at today's. A first p99
  matrix (R_shock 4.0, leverage 8 / 6) is kept as a diagnostic and was not reported.

**Why none stands as it was.** The live year had been carried by one full-size silver contract held at twelve times its
target, and each setup attacks a cause: a buffer narrow enough to trim it, smaller contracts so it can be held near
target, an overlay estimator that sees the risk, and limits that cap the book's expected risk. Carver as written and the
p99 method are Carver's. On this 36-instrument book of whole contracts the 1% buffer makes the book trade nearly every
cycle and its costs take back the tracking it buys (lever 1). Micros alone on four instruments left the buffer coarse.
Carver sets each overlay limit to bind about 1% of the time and all together under 5%, so the overlay acts only in the
tail; a 25% expected-risk ceiling cut the book on about 8% of cycles, which makes it a standing constraint on the book
rather than a tail limit. The whole limits chain was superseded on 2026-10-26 by Carver's set measured on the new data
(lever 8). Two parts came back on later evidence: the Carver-overlay setup's covariance (lever 7) and gold and silver as
micros (lever 9).

**The data.** Total return on the account after costs, engine, end pinned at 2026-10-14; the live year from five starts
for the starting book ranged +7.3% to +42.3%.

| Setup | Buffer, contracts, overlay | Year | 2y | 5y | 10y | 16y | Round trips a year (2y, 5y, 16y) |
|---|---|---:|---:|---:|---:|---:|---|
| the starting book | granularity, full, sample at R_max 45% | 42.3% | 52.1% | 22.7% | 53.6% | 115.4% | 134, 136, 125 |
| fixed buffer | fixed, full, today | 5.4% | 4.0% | 10.8% | | 79.4% | 422, 397, 338 |
| four micros | granularity, four micros, today | 20.5% | 8.2% | -11.4% | | 61.9% | 261, 239, 181 |
| fixed buffer, four micros | fixed, four micros, today | 8.2% | 0.2% | 7.1% | | 43.3% | 537, 527, 415 |
| Carver overlay | granularity, full, carver at R_max 30% | 28.8% | 58.1% | 13.0% | 109.9% | 111.9% | 139, 137, 126 |
| Carver as written, 30% | fixed, four micros, carver at R_max 30% | 14.6% | -0.4% | 6.9% | 38.8% | 58.1% | 539, 527, 419 |
| Carver as written, 25% | fixed, four micros, carver at R_max 25% | 12.5% | 2.2% | 4.1% | | 63.6% | 544, 525, 420 |
| p99, 35% | carver, p99 limits, R_max 35% | 24.4% | 51.3% | 26.6% | 44.7% | 44.8% | |
| p99, 25% | as above, R_max 25% | 31.2% | 37.1% | 35.0% | 52.6% | 110.0% | |
| p99, 30% | as above, R_max 30% | 29.0% | 60.1% | 84.8% | 62.2% | 67.3% | |
| p99, 25%, jump and leverage at today's | as p99 25% | 31.2% | 36.8% | 37.2% | 55.7% | 107.3% | |

The four fixed-buffer setups of the October sizing round together churned 338 to 544 round trips a year and traded on
88% to 95% of searched cycles against the starting book's 62% to 78%; they returned less than the starting book on every
long window. In the four-micros setup a held-only name set B_sigma on 103 of 336 cycles in the year, and it lost 11.4%
over the 5y. Carver as written at 25% was the only one of the seven whose held book never expected more than 25% in the
year (max 24.2%), and its overlay cut the target on 355 of 4,734 16y cycles; the 25% p99 setup was the only overlay
setup that never expected more than 25% in any of its nine runs, cut on 412 of 4,734 against 158 for the 35% setup and
227 for the 30% setup. In the year and the 2y the binding term of all three was mostly R_shock, which the Carver-overlay
setup's 4.0 never hit. Long-window returns did not order with the ceiling: the 30% p99 setup's 5y +84.8% is gold held
520 bar dates for +283k.

## 26. `held` or `vendor`, and the roll switch

**What it does to the book.** `per_contract_switch_bar` (`held` or `vendor`) picks the bar handed out on a vendor switch bar
the next bar has not yet confirmed: `held` gives the held contract's own bar, `vendor` the bar of the contract the vendor
switched to. The booked P&L and the roll legs are the same either way; only the levels read on that one bar differ.
`per_contract_rolls` turns the per-contract roll rule on or off. Both
are on main. Mechanics: [`FUTURES_ROLLS.md`](../../FUTURES_ROLLS.md) and
[`../01_the_book/ROLLS_AND_DATA.md`](../01_the_book/ROLLS_AND_DATA.md).

**Why `held` and the switch on are in the book.** `held` keeps that bar's levels on the contract the book
actually holds, so on that bar too sizing, marks, notional and the overlay read the price of the position the book
owns, which is the reason the per-contract rule exists. The per-contract rule rolls on the book's own
calendar instead of the vendor's. Not in AFTS. Settled on 2026-10-26. Whether to remove the setting is listed in
[`FUTURE_WORK_ADVICE.md`](../06_open/FUTURE_WORK_ADVICE.md), section "Open design questions and their issues".

**The data.** The starting book, 17 `held` and 5 `vendor` runs, and five controls, 2026-10-26; engine, total return on
the account after costs. `held` / `vendor`: year -7.64% / -7.53%, 2y -14.59% / -4.86%, 5y +29.17% / +21.08%, 10y +69.63%
/ +51.82%, 16y +4.43% / +96.72%, with first differing fills as small as one RB contract. Neither setting showed a
consistent direction (`vendor` higher on the 2y and 16y, lower on the 5y and 10y). Without the speed-limit list the same
16y made +73.98%, Sharpe 0.38 (a cost check, not an option). The controls (engine Sharpe):

| Run | What | Return | Sharpe |
|---|---|---:|---:|
| third control | the year, switch off | 17.39% | 1.04 |
| third control | the 2y, switch off | 18.35% | 0.55 |
| fourth control | October's year start, switch on | 78.08% | 2.69 |
| fifth control | 16y, switch off | 65.50% | 0.33 |
| fifth control | 10y, switch off | 39.69% | 0.30 |
| fifth control | 5y, switch off | 15.90% | 0.29 |

Off minus on is +25.0, +32.9, -13.3, -29.9, +61.1 points on the year, 2y, 5y, 10y and 16y; where off is higher, RB
carries most of it. Two corrections to the SG replica moved nothing in 2025 (Bitcoin in metals 16y Sharpe 0.19,
business-day spans 0.27, both 0.25, against 0.21).

## 27. Offline signal choices

**What it does to the book.** One design choice at a time between the signal and the sizing, on an independent Python
re-implementation of the trend sleeve that matches the engine's target to 1e-10 contracts over 2025-26.

**Why none is in the book.** 2025 was a losing year where SG Trend made money; if one choice between the signal and the
sizing were wrong, switching it would move 2025 toward SG without hurting the long run. Each choice the book keeps is
Carver's form or has its own lever. The volatility blend stays for Carver's reason: volatility mean-reverts, and a short
estimate alone builds very large positions in the quiet spell before a crisis, so he blends the short estimate with its
long-run average, which also damps trading (AFTS, strategy three, p.89 to 90). Volatility is taken on the bars the feed
prints, Sunday sessions included: the Sunday evening session is a real exchange session that prints its own bar, and
the runner trades on the bars the feed prints (2026-09-03); the weekday-only form (e) was also lower on every window
shown. The FDM and IDM are Carver's table values (lever 33),
the speed set is Carver's six speeds with his per-contract cost removals (levers 12 and 36), the slow rule is lever 14,
FX is lever 28, half compounding is lever 13, and the sector-equal weights keep Carver's top-down layer of equal risk to
each asset class (AFTS, strategy four, p.122 to 126). The attenuation follows strategy thirteen with the book's own
quantile input. Every sizing choice left 2025 between -9% and -20%; only the speed set moved it toward SG.

**The data.** Offline, 2026-10-26: unrounded book, before costs, return on sizing capital, windows to 2026-10-23. None
of it is a whole-contract result.

| Variant | What | 2025 | 2026 | Year | 2y | 5y | 10y | 16y | 16y Sharpe | Corr SG |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | | -14.3% | +6.5% | +7.3% | -10.6% | -7.1% | +40.2% | +64.7% | +0.26 | +0.31 |
| a | all six speeds | -16.4% | +6.2% | +6.9% | -13.3% | -11.9% | +32.1% | +61.8% | +0.26 | +0.32 |
| b | attenuation off | -14.4% | +7.5% | +10.6% | -11.7% | -10.3% | +18.5% | +29.1% | +0.18 | +0.33 |
| c | slow rule off | -11.0% | +4.3% | +4.6% | -9.5% | -9.3% | +33.3% | +44.6% | +0.22 | +0.32 |
| d | FX excluded | -9.1% | +6.9% | +8.0% | -9.7% | -2.2% | +74.9% | +117.0% | +0.35 | +0.15 |
| e | weekday sigma x16 | -19.4% | +6.4% | +6.2% | -16.2% | -13.7% | +28.7% | +54.7% | +0.24 | +0.29 |
| f1 | vol blend off | -15.0% | +4.6% | +4.0% | -12.7% | -5.6% | +55.8% | +88.1% | +0.31 | +0.30 |
| f2 | ten-calendar-year blend | -14.2% | +6.4% | +7.1% | -10.8% | -7.3% | +39.0% | +63.3% | +0.26 | +0.31 |
| g | equal weights | -13.5% | +2.2% | +1.3% | -12.6% | -16.8% | +1.7% | +15.7% | +0.14 | +0.31 |
| h | IDM 3.13 | -18.1% | +7.8% | +8.7% | -14.2% | -11.5% | +43.2% | +70.5% | +0.26 | +0.31 |
| i | full compounding | -14.3% | +6.5% | +7.3% | -10.6% | -7.1% | +40.2% | +64.7% | +0.26 | +0.31 |
| j | FDM table 52 | -14.4% | +6.4% | +7.2% | -10.8% | -7.0% | +40.8% | +65.6% | +0.27 | +0.31 |
| s | sizing clamp off | -14.3% | +6.5% | +7.3% | -10.6% | -7.0% | +41.3% | +66.1% | +0.27 | +0.31 |
| k | no 2/8 | -11.1% | +8.4% | +8.9% | -6.6% | -4.0% | +38.7% | +62.1% | +0.26 | +0.41 |
| l/m | no 2/8 or 4/16 | -3.5% | +6.5% | +7.0% | -0.9% | -0.5% | +35.3% | +51.1% | +0.24 | +0.51 |
| n | 16/64 and slower | +3.9% | +4.2% | +4.7% | +3.7% | +6.2% | +34.2% | +45.0% | +0.22 | +0.57 |
| x | Carver's weights | -19.5% | +5.1% | +0.9% | -16.8% | -9.8% | +43.8% | +71.3% | +0.28 | +0.20 |
| x | Carver's attenuation | -15.2% | +9.7% | +10.3% | -9.8% | -6.6% | +42.6% | +62.4% | +0.26 | +0.28 |

Each code in words. "In" is the Sharpe for 2011-2020 and "out" for 2021-2026; the baseline is 0.364 in and 0.109 out.
Moves in 2025 are additive points of sizing capital.

| Code | What it changes | What it did | Where the book stands |
|---|---|---|---|
| a | runs all six speed pairs on every instrument, without the cost removals | 2025 -16.4% against -14.3%; 0.386 in sample, then 0.059 out of sample | Carver removes a speed on a contract whose cost is over his limit (lever 12) |
| b | sets the attenuation multiplier to 1 | 16y +29.1% against +64.7%; no attenuation form gives 0.263 in, 0.065 out | attenuation is Carver's strategy thirteen |
| c | removes the equity slow rule | +3.9 points in 2025, all in the equities, -2.0 in 2026; -20 points over sixteen years | lever 14 |
| d | drops FX and re-spreads the weights by the sector rule over six sectors | +5.9 points in 2025; 16y +117.0%; 0.447 in, 0.206 out | removes an asset class on its record (lever 28) |
| e | estimates volatility on weekday rows only, weekend returns folded into Monday, annualised by 16 | 2025 lower by 5 points, mostly metals, equity and rates in April; lower on every window shown | volatility is taken on the bars the feed prints, Sunday sessions included, as the runner trades them |
| f1 | sizing and forecast volatility on the current EWMA only, no long-run blend | 16y +88.1%, 2025 -15.0% and 2026 +4.6%; 0.414 in, 0.126 out (t 0.35) | Carver's blend, for the reason above |
| f2 | the book's blend over ten calendar years, not 2,520 rows | 16y +63.3% | moves nothing |
| g | 1/36 weight each | 16y +15.7%; 2026 lower by 4.1 points; 0.251 in, -0.029 out | Carver's top-down equal risk by asset class |
| h | IDM 3.13, the implied value, against the capped 2.5 | volatility 24.2% against 19.3%, Sharpe unchanged | a leverage scalar (lever 33) |
| i | full compounding | same book in percent; dollars only, 16y +337k against +292k | half compounding (lever 13) |
| j | FDM from table 52 (1.27 / 1.25 / 1.23 / 1.03) against table 36 | 16y +65.6% against +64.7% | moves nothing |
| s | sizing volatility clamp at 100% off; it binds on NG and MBT in high-volatility months | 16y +66.1% against +64.7% | moves little |
| k | drops 2/8 | +3.6 points in 2025; 0.317 in, 0.169 out | a blanket speed cut (lever 36) |
| l/m | drops 2/8 and 4/16, the same set as 8/32 and slower | +11.7 points in 2025; 0.244 in, 0.223 out | a blanket speed cut (lever 36) |
| n | runs 16/64 and slower only | +19.1 points in 2025 across every sector, landing on SG's +3.9%; lowest in sample (0.184), 16y -19.7 points, out-of-sample t 0.8 | a blanket speed cut (lever 36) |
| x weights | Carver's handcrafted class, group and instrument weights, Bitcoin in metals | -6.3 points in 2025: precious metals cut to 1.85% each; 0.404 in sample, then 0.078 out against 0.109 | sector-equal weights keep Carver's top layer |
| x attenuation | strategy 13 as written: the quantile of relative volatility in the instrument's whole history | +3.1 points in 2026, -1.0 in 2025; 0.350 against 0.364 in, 0.117 against 0.109 out | moves little |

The split, fit 2011-08-21 to 2020-12-31, test 2021-01-01 to 2026-10-23 (Sharpe):

| Knob | In sample | Out of sample | Chosen in sample | The in-sample choice out of sample, against the baseline's 0.109 |
|---|---|---|---|---|
| Speed set | all 0.386, ours 0.364, no 2/8 0.317, 8/32+ 0.244, 16/64+ 0.184 | all 0.059, ours 0.109, no 2/8 0.169, 8/32+ 0.223, 16/64+ 0.281 | all six | 0.059 |
| Attenuation | none 0.263, ours 0.364, Carver's 0.350 | 0.065, 0.109, 0.117 | ours | 0.109 (the baseline's own) |
| Vol blend | current 0.414, ours 0.364, Carver's 0.361 | 0.126, 0.109, 0.107 | current only | 0.126 |
| Weights | equal 0.251, ours 0.364, Carver's 0.404 | -0.029, 0.109, 0.078 | Carver's | 0.078 |
| Joint best of 135 | 0.462 | 0.015 | | 0.015 |
| FX excluded | 0.447 | 0.206 | | 0.206 (one variant, not a choice from a range) |
| c + d + Carver's attenuation + 16/64+ | 0.191 | 0.364 | | not chosen in sample (assembled by looking at 2025-26) |

Combinations: the first row is every choice that helped the 2y, the second adds the speeds, the third is every choice
that helped the 16y Sharpe (FX out, blend off, table 52, clamp off), and the fourth is f1 with FX out.

| Set | 2025 | 2026 | Year | 2y | 5y | 10y | 16y |
|---|---:|---:|---:|---:|---:|---:|---:|
| c + d + Carver's attenuation | -6.4% | +7.9% | +8.1% | -7.4% | -4.6% | +68.9% | +84.0% |
| the same + n | +10.4% | +11.3% | +11.7% | +10.2% | +9.8% | +61.8% | +63.8% |
| d + f1 + j + s | -10.2% | +3.7% | +3.0% | -13.6% | -1.7% | +99.9% | +156.3% |
| split survivor + FX excluded | -10.0% | +3.8% | +3.2% | -13.3% | -1.6% | +96.5% | +151.8% |
| joint in-sample best | -23.4% | +1.1% | -4.5% | -24.3% | -16.8% | +45.0% | +80.4% |

The second combination was assembled by looking at 2025-26 and has an in-sample Sharpe of 0.19 against 0.36.

## 28. FX out, half weight, subsets

**What it does to the book.** Fewer FX pairs, chosen on Carver's own screens.
- **FX out (d).** All nine pairs removed and their weight re-spread by the sector rule over six sectors.
- **FX half weight (d2).** The FX sector's weight halved.
- **Minimum capital (d3).** Carver's test, 4 x multiplier x price x sigma% / (IDM x weight x tau), at the engine's FX weight
  of 1.72%, IDM 2.5 and tau 20%.
- **The selection-run subsets (d4).** Each keeps only the FX pairs one of Carver's eight selection runs picked (lever 10),
  keeps the other 27 instruments, and gives the pairs the sector's weight: two pairs 7.74% each, one pair capped at 7.14%,
  three 5.16%, five 3.10%. Runs 1 and 2 use SR 0.30 and equal weights, with T measured or 7; runs 3 and 4 use SR 0.30 and
  sector weights; run 5 uses SR 0.044, equal, T measured; runs 6 and 8 use SR 0.044 and T 7; run 7 uses SR 0.044, sector,
  T measured.
- **The cost limit (d5).** Carver's 0.01 SR per trade.

**Why none is in the book.** The book chooses instruments on cost, liquidity and diversification, not on their record,
and removing an asset class because it lost is a fit to that record. The question was whether Carver's own screens
(minimum capital, AFTS strategy two; the cost limit; his greedy selection) lead there for a reason other than the
record. They do not give one: at 500,000 his minimum capital removes nearly the whole book, his cost limit removes
nothing, and his selection is a seven-instrument book whose FX picks are the period's winners. On Carver's grounds FX
out and FX at half weight are the two forms that could be argued; neither was run on the whole-contract book, and on
2026-10-26 no FX rounds were run.

**The data.** FX excluded was above the baseline in both halves of the split, and the FX sleeve lost in 10 of 16 years
(-12.5 points in all, 6S alone -10.9). Offline, same basis as lever 27. None ran on the whole-contract book: the
confirmation cells were dropped.

| Variant | 2025 | 2026 | Year | 2y | 5y | 10y | 16y | Sharpe 2011-20 (t) | Sharpe 2021-26 (t) | 16y Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline = d5 | -14.3% | +6.5% | +7.3% | -10.6% | -7.1% | +40.2% | +64.7% | 0.364 | 0.109 | 0.265 |
| d = d3 | -9.1% | +6.9% | +8.0% | -9.7% | -2.2% | +74.9% | +117.0% | 0.447 (+1.11) | 0.206 (+1.02) | 0.353 |
| d2 FX weight halved | -11.6% | +6.6% | +7.5% | -10.3% | -4.9% | +57.0% | +90.0% | 0.410 (+1.16) | 0.157 (+0.97) | 0.311 |
| d4 run 1: 6J, 6M | -6.7% | +6.8% | +12.1% | -5.6% | +36.4% | +76.6% | +152.4% | 0.385 (+0.35) | 0.430 (+2.52) | 0.403 |
| d4 run 2: 6J, 6L | -12.0% | +5.2% | +7.4% | -8.5% | +24.5% | +77.8% | +151.4% | 0.417 (+0.62) | 0.381 (+2.03) | 0.403 |
| d4 runs 3, 4, 6, 8: 6J | -10.7% | +2.3% | +5.1% | -14.0% | +18.8% | +79.5% | +144.9% | 0.406 (+0.67) | 0.364 (+2.25) | 0.390 |
| d4 run 5: 6E, 6J, 6M | -10.2% | +7.8% | +11.2% | -7.9% | +20.0% | +70.7% | +131.6% | 0.412 (+0.78) | 0.321 (+2.63) | 0.377 |
| d4 run 7: 6A, 6B, 6E, 6J, 6M | -13.4% | +6.1% | +8.4% | -12.2% | +0.4% | +53.8% | +96.0% | 0.412 (+1.21) | 0.179 (+1.61) | 0.322 |

The cost limit removes nothing: the dearest instrument is ZR at 0.00483 and the dearest FX pair 6L at 0.00252, so d5 is
the baseline. Minimum capital removes every FX pair on every date checked (the cheapest needs 1.18m at the cut, 6L;
1.54m in 2015, 6M; 1.28m in 2020, 6L), so d3 is d; the same test passes only 2 of the 36 instruments at the cut (MBT
336k, M2K 488k), so applied as written it removes 34 of 36 and is a whole-book rule, not an FX rule. The d4 sets are a
concentration: run 1 puts 4.5 times today's weight on 6J and 6M, and its out-of-sample gain rests on 6J's 2022 (+15.7
points) and 6M's 2023 and 2025-26. Their in-sample gain is small (t 0.35 to 0.78) and out-of-sample gain large (t 2.0 to
2.6), but the picks are the pairs that made money here and the selection's inputs were measured over the untouched half,
so that t is not a property of the rule. None comes near SG in 2025 (best -6.7% against +3.9%).

## 29. Selective rules, offline

**What it does to the book.** Speed weighting by asset class, Carver's strategy sixteen on speed groups, and regime rules
that switch fast speeds.

**Why none is in the book.** If fast speeds lost in some classes or regimes, weighting them down there would raise the
long-run return. Carver rules out asset-class fitting of trend forecasts because the class-level results are not
statistically significant (AFTS, strategy twelve, p.267 to 268), and the class weightings here are that fitting.
Strategy sixteen's allocation rests on relative performance persisting, and here fast-minus-slow relative performance is
negatively autocorrelated. The on/off regime rules are not in AFTS, where strategy thirteen attenuates smoothly. Rule 3c,
a fast speed that counts only when it agrees in sign with the slow trend, is the equity slow rule's idea for every
instrument; Carver's evidence for that idea is in equities only (lever 14), so it stays out. 1e (the equity fast-speed
cut), 3a (the high-volatility switch) and the two together went to the real book (lever 15).

**The data.** Offline, 2026-10-26, same basis as lever 27; points are volatility-matched against the baseline, t in
brackets; fit 2011-2020, test 2021-2026, refit 2011-2018. Costs are estimated and overstated.

| Code | Method | 2021-26 pts (t) | 2011-20 pts (t) | 16y pts (t) | 16y Sharpe |
|---|---|---:|---:|---:|---:|
| 1a | class, positive-Sharpe speeds | +1.9 (+0.14) | +45.8 (+2.58) | +47.7 (+2.14) | +0.42 |
| 1b | class, proportional, shrunk | +2.8 (+0.30) | +35.1 (+2.75) | +37.9 (+2.38) | +0.39 |
| 1c | whole book positive-Sharpe | identical | identical | identical | +0.26 |
| 1d | whole book proportional | -3.6 (-0.63) | +8.3 (+1.27) | +4.7 (+0.54) | +0.28 |
| 1e | equity 16/64 and slower | +8.7 (+0.94) | +3.6 (+0.31) | +12.3 (+0.83) | +0.31 |
| 2a | strategy 16, three groups, thirds | -5.5 (-0.30) | -31.0 (-1.84) | -36.5 (-1.47) | +0.14 |
| 2b | strategy 16, baseline shares | -5.6 (-0.31) | -21.3 (-1.32) | -26.8 (-1.10) | +0.17 |
| 2c | strategy 16, two-way | -5.4 (-0.31) | -12.1 (-0.78) | -17.5 (-0.75) | +0.21 |
| 2d | control, static thirds | +2.4 (+0.34) | -7.8 (-0.89) | -5.4 (-0.47) | +0.25 |
| 3a | fast off, Q above 0.75 | +20.4 (+1.85) | -3.1 (-0.29) | +17.2 (+1.11) | +0.32 |
| 3b | fast only if abs(slow) 10 or more | +18.1 (+1.03) | -33.1 (-1.77) | -15.0 (-0.58) | +0.21 |
| 3c | fast counts if it agrees with slow | +19.0 (+1.03) | -8.9 (-0.39) | +10.1 (+0.34) | +0.30 |
| 4a | 16/64 and 32/128 only | +10.2 (+0.47) | -31.1 (-1.20) | -20.9 (-0.62) | +0.19 |
| 4b | 16/64 and slower | +20.2 (+0.78) | -32.9 (-1.08) | -12.7 (-0.32) | +0.22 |
| 4c | SG's 20/120 | -12.0 (-0.35) | -92.9 (-2.23) | -105.0 (-1.95) | -0.09 |

Each code in words:

| Code | What it changes | What it did |
|---|---|---|
| 1a | for each of six classes, equal weight over the speeds with a positive pooled class Sharpe after costs, fit 2011-2020; 36 binary choices | its 16y gain is all in the fit window; refit gives -0.4; turnover rises from 35 to 37-41 |
| 1b | weights proportional to the pooled class Sharpe, shrunk halfway to equal; 30 weights | same: refit gives +0.2 |
| 1c | whole book pooled, Carver's way, positive-Sharpe speeds: all six are positive | identical to the baseline |
| 1d | whole book pooled, Sharpe-proportional, shrunk halfway | -3.6 out of sample |
| 1e | Carver's published class difference, equity instruments on 16/64 and slower only; nothing fitted | +0.9 on 2019-26; sent to the real book as the equity fast-speed cut |
| 2a | Carver's strategy 16 allocation on fast, medium and slow groups, neutral at one third each | below the baseline in both halves and on the 16y; relative performance mean-reverts |
| 2b | the same, neutral at the baseline's shares of 1/2, 1/6, 1/3 | same |
| 2c | the same two-way, fast against slower, Carver's formula exactly | same |
| 2d | control for 2a: static one third per group, no timing | below the baseline on the 16y, and shows what the timing adds |
| 3a | fast speeds off on bars where strategy 13's volatility quantile is above 0.75; nothing fitted | t 1.85 on 2021-26; its test window is where the idea came from, and it lost over 2011-2020; sent to the real book as the high-volatility switch |
| 3b | not in AFTS: fast speeds only when abs(slow forecast) is 10 or more | -15.0 against the baseline on the 16y |
| 3c | not in AFTS: a fast speed counts only when it agrees in sign with the slow trend | above the baseline on 2021-26 and the 16y, with 2011-20 -8.9 and 16y t 0.34; not taken to the real book |
| 4a | medium blend, 16/64 and 32/128 only | a speed choice; below the baseline on the 16y |
| 4b | 16/64 and slower, the reference | 2021-26 +20.2 but 16y -12.7 |
| 4c | SG's 20/120 crossover, always long or short, through the book's sizing | 16y -105; a yardstick |
| 1a' to 1d' | 1a to 1d refit on 2011-2018 | the dependence check: only 1b' is above the baseline on 2019-2026, at +0.2 |
| 1e + 3a | both rules together | 16y Sharpe 0.36 against 0.26; sent to the real book as the two together |

Refit on 2011-2018, tested on 2019-2026, points against the baseline: 1a' -0.4 (-0.03), 1b' +0.2 (+0.02), 1c' -3.4
(-0.23), 1d' -10.7 (-0.67), so the 1a and 1b gains sit in the fit window. Fit-against-test rank correlation of speed
Sharpe is -0.77 for the whole book. Fast-minus-slow relative performance is negatively autocorrelated, -0.32 on calendar
years. 1e + 3a: 2021-26 +25.7 (t +1.75), 2011-20 +3.1 (t +0.20), 16y +28.8 (t +1.34), Sharpe 0.36. Out of sample the
speed weightings fitted by asset class read t +0.14 (1a) and +0.30 (1b), and the three rules above the baseline in
2021-2026 without a lower 16y read 2021-26 t +0.94 (1e), +1.85 (3a) and +1.03 (3c).

Compounded standard windows on sizing capital:

| Code | Year | 2y | 5y | 10y | 16y | 16y max DD | Turnover |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | +7.3% | -10.6% | -7.1% | +40.2% | +64.7% | -39.8% | 35.2 |
| 1a | +11.4% | -15.9% | -5.3% | +60.6% | +164.6% | -41.3% | 37.4 |
| 1b | +8.6% | -13.2% | -5.6% | +57.6% | +140.7% | -40.4% | 36.8 |
| 1d | +8.4% | -13.1% | -9.2% | +40.0% | +73.2% | -39.6% | 39.3 |
| 1e | +8.9% | -4.4% | -4.4% | +46.6% | +86.5% | -39.7% | 30.9 |
| 2a | +4.4% | -11.2% | -15.1% | +2.8% | +13.8% | -47.1% | 28.9 |
| 2b | +5.5% | -11.3% | -13.5% | +8.9% | +25.7% | -46.5% | 34.5 |
| 2c | +6.3% | -10.2% | -10.6% | +15.3% | +38.3% | -45.8% | 36.0 |
| 2d | +5.9% | -8.0% | -8.3% | +37.2% | +56.2% | -40.4% | 27.5 |
| 3a | +9.6% | -3.4% | +8.9% | +62.0% | +93.4% | -33.8% | 31.7 |
| 3b | +6.6% | -0.8% | +9.6% | +28.3% | +40.3% | -37.3% | 22.6 |
| 3c | +11.2% | -3.9% | +5.8% | +59.8% | +85.3% | -44.7% | 23.7 |
| 4a | +2.6% | -0.9% | -8.8% | +32.7% | +33.7% | -41.3% | 13.6 |
| 4b | +4.7% | +3.7% | +6.2% | +34.2% | +44.9% | -39.1% | 11.4 |
| 4c | -2.3% | +4.6% | -18.1% | -7.9% | -34.8% | -47.0% | 7.1 |

The volatility-regime rule, its variants and placebos:

| Variant | 2021-26 pts (t) | 2011-20 pts (t) | 16y pts (t) |
|---|---:|---:|---:|
| fast off, Q above 0.75 (3a) | +20.4 (+1.85) | -3.1 (-0.29) | +17.2 (+1.11) |
| fast halved, Q above 0.75 | +7.2 (+1.80) | -2.4 (-0.63) | +4.8 (+0.87) |
| fast off, Q above 0.50 | +29.9 (+1.70) | -5.9 (-0.37) | +23.9 (+1.00) |
| fast off, Q above 0.90 | +8.6 (+1.47) | -2.7 (-0.39) | +6.0 (+0.66) |
| only 2/8 and 4/16 off, Q above 0.75 | +13.9 (+1.91) | -2.7 (-0.40) | +11.2 (+1.13) |
| fast off, book's relative-volatility quantile | +19.9 (+1.92) | -4.1 (-0.38) | +15.9 (+1.06) |
| placebo, fast off in low volatility | -15.2 (-1.79) | -13.0 (-0.89) | -28.3 (-1.67) |
| placebo, static x 0.74 | +2.9 (+0.70) | -5.6 (-1.16) | -2.7 (-0.42) |
| placebo, slow off, Q above 0.75 | -11.8 (-1.55) | +6.0 (+0.91) | -5.7 (-0.57) |
| medium + slow off, Q above 0.75 | -16.1 (-1.45) | +10.1 (+1.02) | -6.0 (-0.41) |

## 30. The cost penalty multiplier

**What it does to the book.** It sets the multiplier on the search's cost term, which prices trading away from the held
book against the tracking error the trade removes: 0 to 1,000 on the engine here; 50 to 300 on the reference model
(levers 21 and 22); 100 in the deep dive's chosen settings and in the book, with 150 tested on those settings (lever
38). Mechanics: [`COST_MODEL.md`](../../COST_MODEL.md), section "Where cost enters decisions".

**Why 100 is in the book.** The cost penalty is the one lever that reliably cuts this book's churn toward its trade bar,
and it stops at 100, the top of Carver's range: Carver uses 50 and says "any number between 10 and 100" gives similar
results (AFTS, strategy twenty-five, p.428), and above that range trading collapses onto the cheapest instruments, as he
warns: a higher penalty "only results in trading being limited to the very cheapest instruments" (p.430). On the
reference model at 300 livestock was in a position on 2% of days against 68% at 50 (2026-10-05). The multiplier sets the
book's cost budget: how much tracking error the search will accept to save a dollar of cost. A higher multiplier makes
the search trade only where the tracking gain is worth that much more, which cuts churn and moves the book further from
its target. The account size does not set the choice: in the
objective the tracking term moves by one contract's weight and the cost term by one contract's cost, each divided by
capital, so the trade-off between them is the same at any capital (details in
[`CARVER_DEPARTURES.md`](../02_decisions/CARVER_DEPARTURES.md), section "1.4 The cost multiplier"). The book's budget is
a churn of about 150 round trips a year, the bar every loop design was measured against, set near what the long-only
book already traded (111 to 167) because the excess trading was resizes (lever 21); on the reference model 100 made
fewer round trips than 50 on every window. Above his range the count falls
only as the book gives up tracking: on the engine the low to mid 100s of executions a year are reached by the multiplier
alone only between about 300 and 500, at 25 to 60 percent more tracking error, and on the reference model penalties of
200 or more starved livestock (lever 22). 100 was set in the deep dive's chosen settings of 2026-10-05; 50 had been the
setting before.

**The data.** On the reference model, with the multiplier the only change, round trips a year at 50 / 100 were 235 / 214
(2y), 272 / 189 (5y) and 316 / 250 (16y). Eighteen engine runs on the 2y of 2026-09-24, after the cost term was put in
the right units. Basis: engine, whole contracts, raw spliced series, long-only, one run per setting; counts scaled to a
year; spread-free equity in brackets.

| Multiplier | Executions a year | Round trips a year | Resizes a year | Cost, dollars | Mean tracking error | End equity (spread-free) |
|---:|---:|---:|---:|---:|---:|---:|
| 50, before the units fix | 315.0 | 68.1 | 170.3 | 1,474.18 | 0.007093 | 557,434.45 (563,727.58) |
| 0 | 315.9 | 70.7 | 166.0 | 1,514.08 | 0.006907 | 559,542.55 (565,468.18) |
| 1 | 316.7 | 71.5 | 165.2 | 1,506.92 | 0.006830 | 562,203.65 (567,161.80) |
| 3.571 | 306.5 | 69.8 | 158.4 | 1,438.69 | 0.006727 | 564,464.56 (569,589.46) |
| 5 | 306.5 | 69.8 | 158.4 | 1,439.33 | 0.006747 | 564,558.92 (569,683.82) |
| 6.849 | 299.7 | 67.3 | 156.7 | 1,406.46 | 0.006980 | 563,525.35 (569,985.23) |
| 10 | 305.7 | 67.3 | 162.6 | 1,399.25 | 0.007041 | 564,738.75 (571,198.63) |
| 20 | 294.6 | 63.0 | 160.1 | 1,335.29 | 0.007183 | 557,641.83 (563,796.47) |
| 35 | 287.8 | 67.3 | 144.7 | 1,336.73 | 0.007218 | 558,207.52 (564,741.55) |
| 50 | 283.5 | 63.0 | 149.8 | 1,273.07 | 0.007226 | 557,730.30 (563,999.34) |
| 75 | 277.6 | 62.2 | 145.6 | 1,240.09 | 0.007520 | 556,121.85 (562,884.01) |
| 100 | 279.3 | 57.9 | 155.8 | 1,225.65 | 0.007591 | 547,101.48 (554,287.76) |
| 150 | 257.1 | 54.5 | 141.3 | 1,072.68 | 0.007431 | 553,790.32 (558,666.12) |
| 200 | 225.6 | 45.1 | 127.7 | 931.94 | 0.007328 | 549,902.50 (554,518.91) |
| 300 | 181.3 | 35.8 | 103.0 | 732.93 | 0.008877 | 546,568.88 (552,448.65) |
| 400 | 171.1 | 33.2 | 99.6 | 667.82 | 0.009307 | 544,256.87 (550,544.60) |
| 500 | 137.9 | 28.1 | 77.5 | 514.55 | 0.011315 | 535,353.01 (544,808.56) |
| 1000 | 47.7 | 12.8 | 19.6 | 168.67 | 0.020959 | 503,893.58 (517,914.81) |

On the deep dive's chosen settings, 150 raised the 16y churn to 325 round trips against 292 (lever 38).

## 31. Lap-loop churn variants and the cut

**What it does to the book.** On the old lap loop (search and gate in up to five laps a day): how lap 2 anchors, a
trigger on trades, the deadband's place; and how a risk cut reaches the book.

**Why the loop is not in the book.** The book traded about twice the target count, and the gate's cut did not always
reach the stored book. A risk limit only limits risk if its cut reaches the positions the book stores, and a churn
remedy only helps if it removes trades the signal does not want. The anchor refresh delivered requests the strategy
would have withdrawn, which then reversed, and it stopped the cut reaching the book; the trigger cut requests, not
fills, because landing at the rounded target makes multi-lot requests delivered one lot a day; what reached the churn
target did it with a smaller book. The explicit cut, ordered by tracking error, then cut once, so that a risk cut
stands, landed (chosen from seven designs on one window, an in-sample choice recorded as one); the higher count is the
cost of a cut that stands. The lap loop itself was replaced by the one-pass design. Not in AFTS.

**The data.** Engine, 2y of the time, raw spliced series, long-only, one run per variant, weekday account figures,
2026-09-24 to 09-27. The loop variants:

| Variant | What it changes | Executions a year | Round trips a year | Days over the net limit | Realised vol weekday (spread-free) | Sharpe weekday (spread-free) | End equity (spread-free) |
|---|---|---:|---:|---:|---:|---:|---:|
| reference | the head at multiplier 50 | 283.5 | 63.0 | 14 | 0.1165 (0.1108) | 0.827 (0.946) | 557,730.30 (563,999.34) |
| anchor refresh | from lap 2 the deadband anchors on the previous lap's book before the gate cut it | 440.2 | 76.6 | 51 | 0.1222 (0.1164) | 0.674 (0.799) | 548,063.35 (555,577.35) |
| both anchors refreshed | the same, the search's cost anchor too | 517.7 | 92.8 | 103 | 0.1233 (0.1175) | 0.672 (0.798) | 548,269.26 (556,040.11) |
| trigger 1.25 | the strategy trades only when the gap exceeds 1.25 contracts, then lands at the rounded target | 280.1 | 51.9 | 3 | 0.1303 (0.1246) | 0.471 (0.616) | 533,416.02 (543,906.96) |
| trigger 1.5 | the same at 1.5 | 257.1 | 50.2 | 10 | 0.0860 (0.0859) | 0.587 (0.418) | 529,472.39 (520,210.13) |
| anchor + 150 | anchor refresh, multiplier 150 | 383.1 | 59.6 | 103 | 0.1243 (0.1186) | 0.679 (0.788) | 549,063.57 (555,652.88) |
| anchor + 300 | anchor refresh, multiplier 300 | 313.3 | 46.8 | 10 | 0.1215 (0.1160) | 0.642 (0.725) | 545,176.24 (549,688.62) |
| anchor + trigger 1.5 + 150 | all three | 321.8 | 51.1 | 19 | 0.0881 (0.0878) | 0.723 (0.564) | 537,994.15 (528,817.46) |
| deadband once after the loop | the search alone inside each lap, the deadband once after the loop against the held book | 287.8 | 60.4 | 15 | 0.1177 (0.1120) | 0.742 (0.870) | 551,593.10 (558,803.33) |
| lap-1 deadband only | the deadband on lap 1, later laps the search alone | 396.8 | 83.4 | 29 | 0.1194 (0.1135) | 0.692 (0.824) | 548,351.68 (556,188.11) |
| lap-1 deadband + trigger 1.25 | | 341.4 | 68.1 | 7 | 0.1306 (0.1249) | 0.408 (0.557) | 528,028.68 (539,067.81) |
| lap-1 deadband + trigger 1.5 | | 330.3 | 63.9 | 14 | 0.0850 (0.0849) | 0.655 (0.488) | 532,977.56 (523,821.64) |
| trigger 1.5 + 150 | no anchor change | 231.6 | 46.0 | 4 | 0.0849 (0.0847) | 0.683 (0.509) | 534,447.97 (524,903.81) |
| trigger 1.5 + 300 | no anchor change | 191.6 | 34.9 | 0 | 0.0826 (0.0815) | 0.646 (0.487) | 531,596.77 (522,888.31) |
| trigger 1.25 + 300 | no anchor change | 158.4 | 33.2 | 0 | 0.1240 (0.1185) | 0.314 (0.445) | 519,144.03 (528,270.11) |
| multiplier 150 alone | lever 30's row | 257.1 | 54.5 | 15 | 0.1186 (0.1132) | 0.765 (0.860) | 553,790.32 (558,666.12) |
| multiplier 300 alone | lever 30's row | 181.3 | 35.8 | 0 | 0.1169 (0.1112) | 0.680 (0.788) | 546,568.88 (552,448.65) |
| multiplier 500 alone | lever 30's row | 137.9 | 28.1 | 0 | 0.1058 (0.0998) | 0.584 (0.758) | 535,353.01 (544,808.56) |

The cut designs (2y, 2026-09-25; days over the limit by the last reading and on equity):

| How the cut is delivered | Delivers it? | Executions | Days over the limit, last reading / on equity | Five-lap days | Vol weekday (spread-free) | Names traded |
|---|---|---:|---|---:|---:|---:|
| reference: scaled book in place | through lap 2's refusal | 333 | 16 / 14 | 16 | 0.1165 (0.1108) | 19 |
| round the scaled book | no | 494 | 132 / 132 | 132 | 0.1244 (0.1184) | 19 |
| keep it fractional | no | 562 | 126 / 126 | 126 | 0.1243 (0.1186) | 19 |
| explicit whole-contract cut, anchor the cut book | yes | 392 | 0 / 0 | 7 | 0.1188 (0.1125) | 18 |
| explicit cut, anchor the held book | yes | 316 | 2 / 0 | 4 | 0.1174 (0.1116) | 18 |
| the same, ordered inside a class by tracking error | yes | 283 | 1 / 0 | 3 | 0.1143 (0.1087) | 19 |
| explicit cut with no class order | yes, re-buys | 670 | 0 / 0 | 17 | 0.1161 (0.1165) | 19 |
| withhold every added contract on a cut day | yes, freezes | 50 | 0 / 0 | 0 | 0.0449 (0.0442) | 13 |

Cut once, 2026-09-27 (a lap that delivers a cut ends the loop):

| Window | Measure | Before | After |
|---|---|---:|---:|
| 2y | executions (a year) | 312 (265.6) | 395 (336.3) |
| 2y | cost, dollars | 1,320.94 | 1,692.90 |
| 2y | end equity (spread-free) | 564,540.87 (565,684.69) | 560,165.10 (562,665.63) |
| 2y | realised vol weekday (spread-free) | 0.1227 (0.1171) | 0.1256 (0.1197) |
| 2y | Sharpe weekday (spread-free) | 0.8721 (0.9224) | 0.8042 (0.8683) |
| 5y | executions; end equity | 1,280; 550,682.53 | 1,614; 556,647.46 |

The anchor refresh took executions from 333 to 517 and days over the limit from 14 to 51.

## 32. The gate: correlation term and window

**What it does to the book.** The max-pair correlation term (the largest absolute pair correlation over held names, 0.85
/ rho above 0.85) against a stressed volatility of the actual book; and which symbols form the gate's window.

**Why the window fix is in the book and the stressed term is not.** The max-pair term is blind to size and direction:
three ZN/ZF books on 2025-11-21 of 2%, a hedged 0.5% and 12% of capital all got 0.8768. A stressed volatility of the
actual book charges risk only where the book carries it; Carver's shock form, the book's volatility under shocked
correlations, is the idea's source. The window fix is a correctness fix: the gate had read a zero-filled fallback window
on 453 stored days over 16 years, then on 0. The stressed term was not taken into the gate, and the redesign then
replaced the gate with the overlay, whose R_shock reading is Carver's form of the same idea.

**The data.** Engine, 2y, weekday account figures; days over the net limit on the stored book.

| | Reference | Stressed vol, L 0.194 | Stressed vol, L 0.30 |
|---|---:|---:|---:|
| days cut | 318 | 189 | 164 |
| median request on cut days | 0.870 | 0.676 | 0.800 |
| delivered on cut days | 0.747 | 0.734 | 0.770 |
| executions a year | 283.5 | 274.2 | 331.2 |
| days over the net limit | 14 | 11 | 27 |
| realised vol weekday (spread-free) | 0.1165 (0.1108) | 0.1145 (0.1085) | 0.1217 (0.1160) |

At L 0.20 the stressed term made a 2y Sharpe of 0.848 against the reference's 0.733. Over 2010-08-06 to 2026-08-06 it
traded 372.6 times a year against 276.8, returned 39.71% against 41.85%, Sharpe 0.29 against 0.30. With the window fix
the 16y weekday Sharpe went from 0.292 to 0.260.

## 33. IDM

**What it does to the book.** The instrument diversification multiplier scales every position up for the diversification
across instruments: 2.4, 2.5, the 3.2 that would have made one window's book hit its target, or the 3.13 Carver's
formula gives on the newer data. Mechanics:
[`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "Instrument weights and the IDM".

**Why 2.5 is in the book.** It is Carver's table value for the book's instrument count, 2.5 for 30 or more instruments,
the table's highest (AFTS, table 16), and he recommends capping the IDM at 2.5 "to avoid excessive leverage on highly
diversified portfolios", noting that the cap undershoots the risk target (Appendix B, p.644). An IDM is a leverage
scalar: it scales every position, so it changes the volatility, not the Sharpe. Carver's target is meant to be realised
on average; the book runs about 18 to 19 percent on the sizing capital against 20, the gap left by whole-contract
rounding and the buffer, with Carver's cap on the IDM. Raising the IDM would scale every position, including those
already at their target, which raises the volatility target rather than filling the rounding gap. Carver also notes that
an IDM estimated over a whole backtest is a weak form of in-sample fitting (AFTS, strategy four, footnote 85, p.136),
and the table value avoids fitting it to the one history the book has. The 3.2 is a different object: the target divided
by what one window's continuous book realised under a biased volatility estimate, not an estimate of diversification.

**The data.** In the waterfall the IDM and the sector-equal weights at forecast 10 cost 5.05 points of realised
volatility against the target, and hitting the target on that window would have needed 3.2 (the full waterfall:
[`TUNING_STORY.md`](TUNING_STORY.md), round 2). On the 2y of 2026-09-25, IDM 2.5 against 2.4: Sharpe weekday 0.872
against 0.733, 265.6 executions a year against 278.4, 23 names traded against 22. On the 16y run: return a year 2.37%
against 2.33%, Sharpe weekday 0.29 against 0.30. Carver's formula, 1/sqrt(w'Hw) with H the correlations of the
instruments' trading sub-strategy returns as he defines it (AFTS, strategy four, p.129), gives 3.13 over the 36
instruments on the filled data (equal weights, H floored at 0); the same formula on plain price returns with the book's
weights gives 1.87 to 2.32 over every 756-date window since 2011 (median 2.2). Offline, IDM 3.13 raised volatility from
19.3% to 24.2% with the Sharpe unchanged (lever 27).

The same calibration round tried three other calibrations. Carver's fixed forecast scalars per rule (table 29), in place
of the scalars the engine estimated at the time: on that 2y window the book's weekday volatility was 7.38% and its return
-1.28% (Sharpe -0.107), and they were left out of that round's combinations; the fixed table scalars became the book's
design on 2026-09-27, as Carver's table values. A 10 percent proportional band gave a 2y Sharpe of 0.993 against 0.733
at 543.2 executions a year, nearly twice the count; below 5 contracts a 10 percent band is narrower than half a contract,
so it does nothing beyond rounding. The trigger is in lever 31 and the stressed gate in lever 32.

## 34. Universes

**What it does to the book.** The instrument set and budget: all 36 names, 18, 9, the 36 with the budget moved to
diversifying singletons, and the 18 and 9 on Carver's formula IDM.

**Why all 36 are in the book.** Trend following is paid by a few large, persistent moves, and which market will make the
next one cannot be known in advance, so each market left out is a market where such a move can no longer be caught.
Carver's minimum capital and static selection are one answer for a small account; dynamic optimisation over the full set
is his other, written for this case: in his own test a 500,000 account trading 100 instruments under dynamic optimisation
made a Sharpe of 1.22, against 1.18 for 27 instruments chosen by static selection and 1.27 for the full portfolio at 50
million (AFTS, table 124, p.433). The diversification across 36 instruments is also what the IDM of 2.5 assumes (lever
33). Fewer names mean larger positions per name, so the smaller sets trade more. Carver's handcrafted weights and his IDM
table or formula are the basis of the variants.

**The data.** Engine, 2y, 2026-09-25.

| Measure | 36 names | 18 names | 9 names | 36 names, budget moved to diversifying singletons | 18 names, formula IDM | 9 names, formula IDM |
|---|---:|---:|---:|---:|---:|---:|
| names trading | 22 | 18 | 9 | 16 | 18 | 9 |
| IDM | 2.4 | 2.3 | 2.2 | 2.4 | 2.4297 | 2.3539 |
| executions a year | 278.4 | 369.5 | 437.6 | 235.0 | 370.4 | 419.7 |
| cost, dollars | 1,431.29 | 2,756.58 | 4,913.48 | 1,721.17 | 2,688.85 | 5,105.92 |
| return over the window (spread-free) | 0.1049 (0.1120) | 0.1117 (0.1020) | -0.0210 (-0.0017) | -0.0826 (-0.0947) | 0.0956 (0.0853) | -0.0341 (-0.0154) |
| Sharpe weekday (spread-free) | 0.733 (0.809) | 0.975 (0.908) | -0.115 (0.034) | -0.595 (-0.695) | 0.822 (0.749) | -0.206 (-0.068) |

The 18-name set traded 369.5 times a year against 278.4, and its cost rose from 1,431 to 2,757 dollars, about 1,300
dollars more over the window's fourteen months of trading, near 0.2% of the account a year. Only the 36 and the
budget-moved set traded a full universe with lower resizing and churn, and the budget-moved set returned -8.3%.

## 35. The first micro phase (engine, lap loop)

**What it does to the book.** Twenty CME-verified contracts on micro or mini rows, with IBKR fees per row, on the lap
loop; also a 10 percent band. Not the gold and silver micros of lever 9.

**Why it is not in the book.** Smaller contracts cut the rounding loss that granularity costs a 500,000 book (AFTS,
strategy two). On twenty rows, though, micros let the book hold several contracts where it held none or one, so every
one-lot adjustment became a trade: the churn nearly tripled. The question was measured again on the reference model
(lever 22) and settled for gold and silver alone (lever 9), the two contracts whose full-size target is a fraction of one
contract.

**The data.** Engine, 2y, 5y and 16y, 2026-09-26, with placeholder margins. The full-size column is at the flat 1.50
fee; at per-row fees its 16y Sharpe reads 0.290 (0.348), and the 2y and 5y barely move.

| Window | Measure | Full size | Micro and mini | With a 10 percent band |
|---|---|---:|---:|---:|
| 2y | executions a year | 265.6 | 758.6 | 1,048.9 |
| 2y | Sharpe weekday (spread-free) | 0.872 (0.922) | 1.187 (1.347) | 1.340 (1.622) |
| 2y | rounding loss, vol points | 8.00 | 1.31 | 0.64 |
| 5y | executions a year | 247.5 | 727.4 | 1,028.1 |
| 5y | Sharpe weekday (spread-free) | 0.256 (0.120) | 0.490 (0.561) | 0.482 (0.513) |
| 16y | executions a year | 283.8 | 745.9 | 959.8 |
| 16y | Sharpe weekday (spread-free) | 0.272 (0.306) | 0.338 (0.387) | 0.321 (0.417) |

## 36. Blanket speed cuts (reference model)

**What it does to the book.** It drops 2/8, or 2/8 and 4/16, on every instrument. On the engine the same idea is in lever
24 (five speeds, four speeds, three slowest) and, for the equity indices only, lever 15.

**Why it is not in the book.** The six speeds are Carver's design, each catching trends of a different length. He
removes a speed only on a contract where its cost is above his speed limit (0.15 of Sharpe a year), which the book
applies (lever 12; table 35 lists the turnovers behind the removals). He notes that "even EWMAC(2,8) is too expensive
for almost all futures instruments" (AFTS, strategy nine, p.188), and his answer is still the per-contract test. A
blanket cut removes the fast speeds also where they pay. Rejected on 2026-10-03. The same reason is in
[`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "Tried and not adopted".

**The data.** Reference model, long and short, fixed sizing equity 500,000, 2026-10-02 to 10-03; volatility and Sharpe on
daily P&L by sqrt 252.

| Speed set | Window | Strategy fills a year | Strategy cost a year | Vol | Return a year | Sharpe | Max drawdown | 2022 | Net P&L |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all six | 16y | 607 | 3,902 | 18.0% | 2.7% | 0.15 | 55.7% | +13.2% | 255,033 |
| drop 2/8 | 16y | 427 | 2,536 | 19.1% | 3.7% | 0.19 | 47.4% | +22.8% | 348,238 |
| drop 2/8 and 4/16 | 16y | 329 | 1,823 | 19.4% | 5.8% | 0.30 | 42.0% | +32.5% | 552,490 |
| all six | 5y | 534 | 3,025 | 17.5% | 1.2% | 0.07 | 53.3% | +15.9% | 44,018 |
| drop 2/8 | 5y | 384 | 2,011 | 17.3% | 2.5% | 0.14 | 57.8% | +17.6% | 90,942 |
| drop 2/8 and 4/16 | 5y | 310 | 1,556 | 18.5% | 2.5% | 0.14 | 53.6% | +40.5% | 94,300 |

On this model's 16y, cutting 2/8 and 4/16 everywhere saved 2,079 dollars a year of strategy cost (3,902 to 1,823, about
0.4% of the account), and the return a year went from 2.7% to 5.8%. Other books and windows: on the unrounded target book
over 16 years the fast speeds earned 196k of 270k (lever 18); a speed set chosen on one half of the history did not hold
on the other half, and fast-minus-slow performance mean-reverts from year to year (levers 27 and 29). Carver's
per-instrument cost test came back in round 15 (lever 12).

## 37. Net leverage grid (reference model)

**What it does to the book.** The net leverage limit, 2 to 6, gross = net + 2, with R, R_jump and R_shock fixed.

**Why no grid value is in the book.** A tighter net limit caps how much of the book's notional points one way; a looser
one lets the signal run. Carver does not set a limit by grid; he sets it at the 99% quantile of its reading (lever 8),
which binds only in the tail of the book's own history. Net 6 / gross 8 stayed until the p99 limits.

**The data.** 16 reference-model runs at own compounding equity, 2026-10-04.

| Book | Window | Today's lap loop | Net 2 | Net 3 | Net 4 | Net 5 | Net 6 |
|---|---|---:|---:|---:|---:|---:|---:|
| long-only | 5y Sharpe | 0.40 | 0.46 | 0.16 | 0.32 | 0.01 | 0.22 |
| long-only | 5y total return | +18% | +30% | +7% | +22% | -4% | +12% |
| long-only | 16y Sharpe | 0.32 | 0.35 | 0.23 | 0.18 | 0.21 | 0.20 |
| long-only | 16y total return | +49% | +62% | +38% | +27% | +34% | +32% |
| long and short | 5y Sharpe | -0.49 | -0.09 | 0.63 | -0.03 | 0.48 | 0.48 |
| long and short | 5y total return | -25% | -12% | +64% | -11% | +51% | +51% |
| long and short | 16y Sharpe | 0.03 | 0.09 | 0.30 | 0.27 | 0.29 | 0.27 |
| long and short | 16y total return | -7% | +4% | +77% | +66% | +77% | +69% |

Results jump between neighbouring settings (5y long and short 0.63, -0.03, 0.48 at net 3, 4, 5); the deep dive then read
such jumps as start-date spread.

## 38. Tau, capital rule, position limit, penalty 150 (reference model)

**What it does to the book.** On the deep dive's chosen settings: tau 0.10, 0.15, 0.175; capital fixed or half
compounded; Carver's per-instrument position limit at a 10% or 20% nominal weight; penalty 150. Mechanics:
[`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The sizing capital".

**Why half compounding and tau 0.20 are in the book.** Round trips on those settings rose with history (155 to 486 a
year from 2013 to 2024), chiefly one-contract trims as equity grew. Half compounding is Carver's own form for his own
money (AFTS, tactic three, p.598 to 600): size falls after every loss, as his risk scaling wants, and profits are not
added to the sizing capital, so the churn and the account drawdown stop growing with profits. Fixed capital does not cut
size after losses, which Carver calls extremely dangerous for a live account (p.598). Tau 0.20 is Carver's own risk
target, a "relatively conservative" value under the 23 percent his half-Kelly estimate gives (AFTS, strategy two, p.72).
On whole contracts at this capital a lower target shrinks every position, so more of them round to zero and the book
holds fewer names: it would trade diversification for lower risk, so drawdown is lowered through the book's
construction, not a smaller target. Carver also advises a lower target when the expected Sharpe is under 0.40, and
attaches conditions to 20 percent that this book does not meet.

**Why the position limit is not in the book.** It is not built, by design and not on its numbers (decided on
2026-10-05). The overlay's per-name cap of 2, Carver's own example value for a leverage limit (AFTS, tactic four,
p.619), already bounds each name, so Carver's position limit at a chosen 10 or 20 percent weight (p.620 to 621) would be
a second, overlapping per-name cap on a picked parameter. The two bound different things: the cap bounds a name's
notional, where Carver's limit bounds a name's share of risk (a 40 percent volatility contract at weight 2 carries 0.8
of risk against a target of 0.2), and the overlay's risk limits bound the book's risk as a whole (lever 8). The
open-interest part of his limit (p.619) does not bind at 500,000, and the book holds no open-interest data. Leaving the
limit out is a departure from Carver, made by design; the reference-model data below are support.

A penalty of 150 is above Carver's range of 10 to 100 (lever 30).

**The data.** 54 reference-model runs, 2026-10-05; account figures, one run per window but the 5y start probe; 2y / 5y /
16y.

| Setting | Round trips a year, 2y / 5y / 16y | Max drawdown, % | Total return, % | Sharpe | Where the book stands |
|---|---|---|---|---|---|
| the deep dive's chosen settings | 158 / 208 / 292 | 14.7 / 33.2 / 38.7 | +7 / +52 / +79 | 0.40 / 0.51 / 0.29 | base of this round |
| half compounding | 172 / 155 / 158 | 14.2 / 18.7 / 23.5 | +6 / +43 / +80 | 0.35 / 0.55 / 0.37 | in the book |
| fixed capital | 178 / 171 / 202 | 18.0 / 26.8 / 33.4 | +5 / +72 / +135 | 0.32 / 0.72 / 0.46 | Carver warns against fixed capital live |
| tau 0.15 | 124 / 134 / 145 | 11.3 / 24.0 / 33.9 | +3 / +51 / -5 | 0.25 / 0.59 / 0.04 | tau stays 0.20 |
| tau 0.10 | 76 / 82 / 102 | 5.5 / 15.5 / 23.7 | +9 / -8 / +21 | 0.70 / -0.17 / 0.18 | whole contracts drop names |
| half compounding + tau 0.175 | 148 / 144 / 137 | 11.9 / 21.2 / 21.2 | +10 / +8 / +62 | 0.54 / 0.17 / 0.33 | tau stays 0.20 |
| half compounding + tau 0.15 | 119 / 106 / 109 | 9.9 / 26.6 / 21.8 | +5 / -2 / +14 | 0.37 / 0.03 / 0.13 | tau stays 0.20 |
| fixed capital + tau 0.15 | 131 / 138 / 144 | 12.0 / 23.1 / 28.3 | +9 / -8 / +51 | 0.50 / -0.04 / 0.29 | Carver warns against fixed capital live |
| position limit, weight 10% | 165 / 211 / 291 | 14.7 / 33.2 / 40.8 | +7 / +51 / +70 | 0.41 / 0.50 / 0.27 | not built by design: the cap of 2 already bounds each name's notional, the overlay's limits the book's risk ([CARVER_DEPARTURES](../02_decisions/CARVER_DEPARTURES.md) 2.4) |
| position limit, weight 20% | 159 / 208 / 292 | 14.7 / 33.2 / 38.7 | +7 / +52 / +79 | 0.41 / 0.51 / 0.29 | the same book as the chosen settings on the 5y and 16y |
| half compounding + position limit, weight 10% | 177 / 135 / 160 | 14.2 / 25.3 / 23.5 | +8 / +27 / +117 | 0.42 / 0.42 / 0.46 | not built by design: a second, overlapping per-name cap ([CARVER_DEPARTURES](../02_decisions/CARVER_DEPARTURES.md) 2.4) |
| penalty 150 | 153 / 188 / 325 | 12.3 / 39.2 / 39.2 | +8 / +51 / +122 | 0.42 / 0.49 / 0.37 | penalty above Carver's 100 |
| half compounding + penalty 150 | 145 / 112 / 129 | 13.5 / 25.7 / 25.8 | -6 / +29 / +40 | -0.21 / 0.47 / 0.24 | penalty above Carver's 100 |

Start probe, 5y, five starts: the chosen settings 187 to 256 round trips, +9% to +52%, mean Sharpe 0.34; half
compounding 134 to 156, +12% to +85%, mean 0.60; with tau 0.175, 118 to 144, +8% to +27%, mean 0.36.

Half compounding flattened yearly churn (118 to 198 round trips over 16 years) and lowered account drawdown; on the
capital at risk the drawdowns are 28.9 and 36.7 points on the 5y and 16y against the chosen settings' 36.8 and 44.2, and
the sizing capital ratchets down (about 361,000 by 2025 against an account of about 884,000). Taken on 2026-10-05; tau
kept at 0.20 on 2026-10-06. Names held fell from 17.9 to 12.1 to 10.0 on the 16y as tau went from 0.20 to 0.15 to 0.10.
The position limit at a 10% weight changed the held book once in 16 years (heating oil, 2 to 1 contracts, 2022-03-08,
taking the worst day from -10.9% to -6.7%) and clipped three target rows; at 20% the 5y and 16y results were identical
with and without it. With half compounding at a 10% weight its 16y Sharpe was 0.46 against 0.37 and its 5y 0.42 against
0.55. It was measured and removed on 2026-10-05, by design and not on these numbers. Later challenges to half compounding are in levers 13 and 24.

## Planned and never run

These were planned and not measured; each is listed so it is not repeated without knowing it was planned.

| Item | What it was | Why it was not run |
|---|---|---|
| Three overlay setups after the first tuning round | Carver's limit set of round 17 on the Carver overlay, an R_max of 1.25, and October's 25% p99 setup with today's jump and leverage limits, 17 runs each | the sizing matrix replaced the question |
| The speed limit's size | 2 runs with the rule list out, on the 16y and the first 5y start of the setup chosen in the first tuning round | the list stayed as Carver's procedure, so its size did not bear on the decision |
| FX cells | micro FX under the two strategy-twenty-five combinations, and the FX weighting options | on 2026-10-26 no FX rounds were run; FX stayed as it was |
| The strategy-twenty-five combinations on the gold and silver micro book | those two combinations on that book | withdrawn until that build was finished and checked; a slow-rule run took their place |
| Three interaction screens under a carry-on cell | the 4% fixed buffer, equity weights, the high-volatility switch | carry stayed off; the equity-weight screen also cannot be set by config, because carry's weight is sleeve-wide |
| Carry with the two speed rules together | the equity fast-speed cut and the high-volatility switch with carry | the two together were not pursued at that date |
| A correlation-anchored FDM for the equity fast-speed cut | an FDM from the four equity instruments' own correlations, which would scale their forecasts by 0.98 to 1.04 | a change under 4% sits inside the buffer on most days |
| Part 1 of the carry measurement | 34 runs of carry alone and the 60/40 blend | dropped for the grid |
| 21-run sets of Carver's overlay with carry off and on | five 16y starts | the 16y was cut to one start |
| The size-neutral edge with Carver's optimiser covariance; `edge_cost` | that set on the size-neutral edge; a cost-aware edge | not run (levers 4 and 18) |
| Lookbacks 63 and 126 | | 63 too few returns for the covariance; 126 replaced by 160 (lever 6) |
| A one-at-a-time speed sweep | | it would fit the period (lever 15) |

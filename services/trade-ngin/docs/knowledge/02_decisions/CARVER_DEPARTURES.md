# Departures from Carver's Advanced Futures Trading Strategies

The futures book chosen on 2026-10-29 (the chosen setup: the sample optimiser covariance on each symbol's last 756
closes, Carver's fraction step, net leverage limit 5.50) is Carver's trend-following method from Advanced Futures Trading
Strategies (AFTS) in its signal, sizing, risk overlay and dynamic optimisation, with a short list of departures. Most of
them answer one fact: a 500,000 account holds whole contracts, and one contract of most of the 36 instruments is large
against the book's risk. That is why the dynamic optimisation of strategy twenty-five, the part Carver wrote for small
accounts, carries most of them: the no-trade buffer is as wide as the largest one-contract risk in the book instead of
Carver's fixed 1 percent, the search starts from the held book, and a position held against its forecast is closed in a
separate fill unless the opposite forecast is weaker than 2. The cost multiplier is 100, the top of Carver's range, which
sets how much tracking error a dollar of cost must buy; the optimiser's covariance is a three-year daily sample of 756
closes instead of Carver's 32-day volatility with six months of weekly correlations. The risk overlay uses
Carver's own method for its limits, at the book's own values, and adds a net leverage limit; the signal adds one rule
that goes against Carver's advice (an equity-index short needs both slow speeds negative) and runs trend without Carver's
carry; the instruments are all 36 under dynamic optimisation, which is Carver's own answer for a 500,000 account (AFTS,
strategy twenty-five, table 124, p.433), with micros for the equity indices, gold and silver. Each section gives what the departure does
to the book, its economic reason (how it keeps Carver's purpose or, where it does not, why it fits a 500,000 book of whole
contracts), and then the data, with the book and window named. Where Carver's own form was measured on this book and is
not used, the section says what it does, why it does not fit, and its data; section 7 lists those forms together.

Code citations are paths under `services/trade-ngin/` in AlgoGators/algogators at commit 4c92eb1b8d, main's merge of
`AlgoGators/algogators#98`, which imports trade-ngin d6f998da. AFTS is cited by strategy or tactic, table and printed page; quotes are short phrases. The
mechanics live in the tracked documents:
[OPTIMIZER_AND_RISK_DESIGN.md](../../OPTIMIZER_AND_RISK_DESIGN.md) (the loop, the overlay in brief, the one pass, and its
own table "Where the loop departs from Carver"), [RISK_MODULES.md](../../RISK_MODULES.md) (the Carver overlay),
[TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md) (the method end to end),
[COST_MODEL.md](../../COST_MODEL.md) (every cost constant) and [FUTURES_ROLLS.md](../../FUTURES_ROLLS.md) (rolls). This
document holds the why. Related knowledge files: [the levers document](../03_tuning_and_results/LEVERS.md) holds the full
results of every option named here, [the carry document](../03_tuning_and_results/CARRY.md) the carry story,
[the rolls document](../01_the_book/ROLLS_AND_DATA.md) the roll rule, [the decisions by area](DECISIONS_BY_AREA.md) every
dated decision, and [the future work advice](../06_open/FUTURE_WORK_ADVICE.md), section "Open design questions and their issues", the
questions without a decision.

## How to read this document

- **The book** is CONSERVATIVE_PORTFOLIO run as the chosen setup. The template at the code commit is the chosen setup:
  1.75 / 4.75 / 3.00 tau, gross 7.25, net 5.50, `overlay_covariance` carver
  (`config_template/portfolios/conservative/risk.json:8-19`), the MGC and SIL listing pairs, `per_contract_rolls` true
  and `per_contract_switch_bar` held (`config_template/portfolios/conservative/portfolio.json:33-34`, `:65`, `:67`). The
  chosen setup's results were run before the release on the same settings.
- **Options tested and not shipped.** The optimiser switches for the buffer width, the treatment of a wrong-signed
  holding, the optimiser covariance, the step rule, the size-neutral step and the covariance lookback are not on main.
  The 756 window is the literal `kOptimiserBars` (`src/strategy/trend_following.cpp:665`). The switches are kept on the
  archive branches of AlgoGators/trade-ngin: `archive/keep-do-not-delete/s25-one-pass` (buffer, sign and covariance),
  `archive/keep-do-not-delete/step-edge` (step rule) and `archive/keep-do-not-delete/levers` (lookback and size-neutral
  step).
- **The design** is the loop design locked on 2026-10-05. Where it said one thing and a later measurement another (the
  first limits, the sample overlay covariance, "no micros", vendor-series change bars), the older line is marked
  superseded where it comes up and listed at the end.
- **The reference model** is a model of the design run outside the engine, used to measure rules before the engine was
  built to them. **The starting book of the first tuning round** (the tuning of 2026-10-14 to 10-26) is full-size
  contracts, the granularity buffer, and the overlay on the sample covariance with the limits of that time. It is not the
  chosen setup: a comparison with it is a comparison with that book. **The gold and silver micro book** is the
  granularity buffer with gold and silver micros, the base of the final grid.
- **Sharpe** is the account Sharpe after costs on the whole-contract book: five-start means except the 16y (one run),
  with the chosen setup's five-window mean 0.274. Comparisons with the starting book of the first tuning round use the
  sizing and strategy-twenty-five rounds' own Sharpe, named where they appear. A **round trip** is an opening and a
  closing trade; a **reversal pair** is a trade undone by the opposite trade in the same symbol one or two days later;
  **tracking error** is how far the held book's risk sits from the book the optimiser aims at. The **trade bar** is the
  book's churn budget of about 150 round trips a year, set near what the long-only book already traded (111 to 167),
  because the excess trading was resizes: the book's own round trips per traded instrument (3 to 11 a year) were already
  inside the range Carver expects; each round trip costs fees and spread with no expected return of its own.

## Summary

| Group | Departure | AFTS | The book (the chosen setup) | Why, in one line | Decision (date) |
|---|---|---|---|---|---|
| Dynamic optimisation | Buffer width | Strategy 25, p.430 to 431 | B_sigma = max(1 percent, the largest one-contract risk among held or searched names) | One contract is 1.1 (ZN) to 12.9 (GC) of Carver's buffers, so the band is set to the smallest trade the book can make, and the book trades only when a whole contract closes a gap worth that trade; Carver's fixed 1 percent at full size churned 337 to 454 round trips a year here, while his own 500,000 test turned over about as much as his 50 million book (table 124), so account size alone does not explain the churn | Granularity buffer 2026-10-05; 1 percent rejected 2026-10-26 |
| Dynamic optimisation | Search from the held book | Strategy 25, p.417 to 418 | Greedy from today's holdings, one contract per step, never past zero against the target's sign | Carver's cost term already prices trades against the held book; the difference is where the greedy path starts and stops: from zero a lumpy book lands on a different near-equal answer from day to day, from the held book it stays unless one step lowers the objective; 187 against 320 reversal pairs a year on the first reference model | 2026-09-27 |
| Dynamic optimisation | Forecast-sign close and its band | Strategy 25, p.430 | A wrong-signed row is closed in one fill before the search; held while abs(F) < 2 | The book's own departure: in Carver's full method the buffer moves a wrong-signed row like any other; here the buffer is one lumpy contract wide and the search starts from the held book, so without the close a wrong-signed row sat against its forecast a median 4 cycles against 1 with it, a position the system expects to lose on; turnover did not rise with the close (140 round trips a year without it, 115 with it, on the strategy-twenty-five round's 16y); the band waits while the opposite forecast is under 2, a weak reading of a sign change | Close 2026-09-27; band 2026-10-05 |
| Dynamic optimisation | Cost multiplier | Strategy 25, p.428 | 100 | The cost penalty is the one lever that reliably cuts this book's churn toward its trade bar; it stops at 100, the top of Carver's 10 to 100 range, because above it trading collapses onto the cheapest instruments, as Carver warns (at 300 livestock was in a position on 2 percent of the 16y's days against 68 percent at 50); reference model 16y round trips 316 at 50, 250 at 100; the account size does not enter, because both terms of the objective scale with capital | 2026-10-05 |
| Dynamic optimisation | Optimiser covariance | Strategy 25, p.417; Appendix B | Sample covariance of each symbol's last 756 daily closes | The window length was set on 2026-09-21 to keep the optimiser on the prices the trend sleeve then held (its history was capped at 756 at the time), because an equal-weight sample's answer moves with its length; keeping the sample over Carver's estimator, which follows recent risk, is a measured result: Carver's covariance alone was 0.13 lower on the 5y and 0.15 on the 16y and level elsewhere | 756 set 2026-09-21; kept 2026-10-29 |
| Risk overlay | Limit values | Tactic four, p.605 to 612 | 1.75 / 4.75 / 3.00 tau; gross 7.25; net 5.50 | Carver's method (the 99th percentile of the book's own history) applied to this book; his values were measured on his portfolio | 2026-10-26, 2026-10-27, 2026-10-29 |
| Risk overlay | Net leverage limit | Tactic four, p.610 to 613; footnote 292, p.622 | A net reading beside gross | Like Carver's gross reading, a notional check that needs no volatility estimate, applied to direction; a plain signed sum, so rates and FX dominate it and it does not measure one-way market risk; a limit cut on the days it binds, set by his 99th-percentile method; the binding term on 54 of the 193 cut cycles of the 16y | Design of 2026-10-05 |
| Risk overlay | Held rows unscaled; the trim | Tactic four, p.603 to 604, p.612; footnote 293, p.622 | The multiplier scales free rows only; the trim removes up to 5 contracts | A held row cannot trade that day; whole contracts can round a book back over a limit | 2026-09-30 |
| Risk overlay | No separate per-instrument position limit | Tactic four, p.614 to 621 | Only the per-name cap of 2 | Not built by design, not on its numbers: the cap of 2 already bounds each name, so Carver's limit at a chosen 10 or 20 percent weight would be a second, overlapping per-name cap on a picked parameter; the cap bounds notional where Carver's limit bounds a name's risk, and the overlay's risk limits bound the book's risk as a whole; the open-interest part does not bind at 500,000 and the book holds no open-interest data; on the reference model at a 10 percent weight it changed the held book once in 16 years and clipped three target rows, and at 20 percent it changed nothing on the 5y and 16y | Limit not built 2026-10-05; screen closed 2026-10-01 |
| Signal | Equity slow rule (against Carver's advice) | Strategy 12, p.266 to 268 | An equity-index short only when 32/128 and 64/256 are both negative | Adopted on this book's record: 113 of 190 equity-index shorts were opened on the fast speeds alone, and equity shorts as a whole lost 121,403 over 16 years, which fits Carver's finding that equities are the weakest trend class (median Sharpe 0.03, strategy nine, table 37, p.206); a declared departure from his advice against asymmetric adjustments, which he finds not statistically significant | 2026-10-05; kept 2026-10-29 |
| Signal | Carry off | Strategy 11, p.248 to 249, table 51 | Trend only (carry built, shipped off) | Not yet tuned on the data the book trades: carry needs two real contract prices on the same date, which only the per-contract bars give, and was measured while the continuous series still had holes and the gold and silver micros were stand-ins; the final retune ran with carry off. Known beside that: Carver's case is a second return; on these instruments since 2011 carry alone earned about nothing (16y Sharpe 0.02 before costs); the blend halves the round trips and tracks a little more closely, and why its traded after-cost Sharpe falls is unmeasured; on the gold and silver micro book the mean after-cost Sharpe over 17 runs was -0.08 with carry on and 0.28 off | 2026-10-27 |
| Signal | Attenuation, annualisation, volatility details | Strategies 3 and 13 | Carver's volatility formula; a counted sizing annualisation; attenuation ranked on the short volatility itself, not Carver's relative volatility | A flat series must not give zero volatility; a series with Sunday rows has more than 256 bars a year; one estimator serves the attenuation | Design of 2026-10-05 |
| Instruments | All 36 under dynamic optimisation | Strategy 25, table 124, p.433; strategy 2, p.79; strategy 3, p.102; strategy 4, p.126 to 127 | Every instrument, no minimum-capital screen | Carver's own answer for a 500,000 account; his static selection keeps 7 instruments on this book, and his IDM table puts 7 instruments at 2.10 against 2.50 for 30 or more | 2026-10-26 |
| Instruments | Micros for the equity indices, gold and silver | Strategy 2; Appendix C | MGC and SIL, and MES, MNQ, MYM and M2K; every other contract full size | Micros are used where the full-size contract cannot be held at 500,000: gold and silver want about 0.05 contracts at an average forecast, so only their micros hold them near target; micro FX does not solve that problem (FX is not where the book is coarse) and turns one-lot adjustments into trades, and the copper micro adds trades where copper is not the coarse contract | 2026-10-26, 2026-10-27 |
| Instruments | Sector-equal weights | Strategy 4, p.122 to 126 | Equal by sector, a symbol capped at half its sector, MBT its own sector | The sectors stand for Carver's asset classes, the layer of his method that matters most | Design of 2026-10-05 |
| Execution | Rolls as two outright legs | Tactic one, p.567; strategy 3, p.94 | Two outright fills at the cost model on the roll bar | No spread prices; the cost matches Carver's own conservative "two lots of costs" per roll | Two legs 2026-10-01; roll rule 2026-10-23 |
| Execution | The cost model per fill | Strategy 1, p.24; strategy 3 | Fee plus a tick spread with a volatility multiplier plus square-root impact, per fill | The engine books a dollar cost on every dated fill, and a thin day must cost more | Design of 2026-10-05 |
| Not built | Margin action, liquidity screen, trade limits | Tactic four, p.602 to 603, p.621; strategy 3, p.104 to 105 | Not built | Filed: `AlgoGators/algogators#172` (margin), `AlgoGators/algogators#184` part A (liquidity, trade limits) | Filed 2026-10-27 onward |

## Where the book follows Carver exactly

Several things people may assume are departures follow Carver as written.

| Element | AFTS | The book |
|---|---|---|
| Half compounding | Tactic three, p.598 to 600 | Sizing capital = 500,000 less the drawdown of the settled net P&L from its peak, live and backtest (2026-10-05) |
| Overlay covariance | Tactic four, p.622; Appendix B | Each symbol's sizing volatility with exponentially weighted weekly correlations, 26-week span (2026-10-27) |
| Buffer step | Strategy 25, p.432 | The fraction step: every row moves the same fraction toward the search answer, then rounds |
| Per-name cap of 2 | Tactic four, p.619 | No position above a weight of 2 on the sizing capital |
| Removing expensive rules by cost | Strategy 9, p.200 to 201; tables 35 and 36, p.202 and p.205 | Eighteen rules on twelve contracts removed at 0.15 Sharpe-ratio units (2026-10-09) |
| Table values | Table 16 (p.125 to 126), table 29 (p.189), table 36 (p.205) | tau 0.20, IDM 2.5, the table FDM and forecast scalars, re-checked on the book's data |

Also as written: one multiplier applied once, before the optimiser (Tactic four, p.612 and footnote 293, p.622); six
EWMAC speeds with equal weights and forecasts capped at 20 (strategy nine); long and short, as Carver's trend strategies
are from strategy six on ("Slow trend following, long and short"); the strategy publishes unbuffered, unrounded targets
and leaves buffering to the optimiser (strategy twenty-five); roll yield inside the trend signal through the adjusted
series; Panama back-adjustment with a same-day gap (Appendix B); and rounding the new book, which equals rounding the
trades because the held book is whole.

### Half compounding

The book sizes on 500,000 less its drawdown from the peak of settled net P&L, never above 500,000. Size falls after every
loss, as Carver's risk scaling wants, but profits are not reinvested, so the churn and the account drawdown stop growing
with profits. Carver uses this form on his own money (AFTS, Tactic three, p.598 to 600). It departs from strategy two's
fixed-capital backtests (p.76) and from its live advice to size on the current account value (p.77), not from his
method, and one rule serves live and
backtest alike, so a backtest sizes as the live book will. On the reference model (2026-10-05) half compounding
flattened the yearly churn to 118 to 198 round trips over 16 years and lowered the account drawdown.

Not in the book. Fixed capital does not cut size after losses, which Carver warns against for a live account; its 5y
Sharpe on the reference model was 0.72. Four other sizing forms were measured on the engine
(Sharpe on the sizing capital, one run per window, against the baseline's 5y 0.129): proportional drawdown cuts sizing by
the drawdown's share of the peak account, a smaller cut than half compounding's full dollar loss after a large gain, so it
keeps more risk on in a drawdown from a high peak (5y 0.718; deeper in drawdown in 13 of 18 windows); half rate cuts by
half the dollar loss, a tuned fraction that is not Carver's (5y 0.717); the capped account sizes on the account itself up
to 500,000, which contradicts Carver's wording and drew down 30 percent on the 16y (5y 0.675); the yearly reset restores
the peak each January, a calendar reset that is not Carver's (5y 0.681). A sizing rule scales every position of the
unrounded target book alike, so none of them changes that book's Sharpe; their differences are on the whole-contract
book. Tau below 0.20 is in section "The table values". Mechanics:
[OPTIMIZER_AND_RISK_DESIGN.md](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The sizing capital".

### The overlay covariance

The overlay reads Carver's own estimator for it (AFTS, tactic four): each symbol's sizing volatility (strategy three's
0.7 / 0.3 blend), the estimate its position is sized on, with exponentially weighted correlations of weekly returns, each
week Saturday to Friday, over a 26-week span (`include/trade_ngin/risk/overlay.hpp:65-72`, `kCarverCorrelationWeeks`).
Carver uses weekly returns because they reduce the bias "caused by markets closing at different times": daily closes
taken hours apart give correlations that are too low (footnote 289, p.622). The 26-week span weights recent weeks most, so
a rise in correlations shows within weeks, where the design's alternative, an equal-weight sample of the last 252 dates,
spreads it over a year. Carver notes that the method "isn't too sensitive to how the covariance matrix is estimated" (footnote 289, p.622); the limits are set
by his 99th-percentile method on readings from this same estimator, so estimator and limits go together. The estimator
reads each symbol's last 300 bars (`src/strategy/trend_following.cpp:658-664`, `kOverlayBars = 300`) where Carver uses
all data; at a 26-week span about 1 to 2 percent of the correlation weight lies beyond them.

Data, on the gold and silver micro book, each covariance on its own p99 limits, 17 runs: Carver's 0.40 / 0.33 / 0.20 /
0.32 against the sample's 0.06 / 0.27 / 0.21 / 0.26 on the year, 2y, 5y and 10y means, and 0.17 against 0.44 on the one
16y run. On the 16y the gap comes from the limits: on the same limits the covariance alone moves the 16y by 0.02, and
Carver's p99 limits on his estimator are tighter and take it down 0.23. In the October sizing round, Carver's overlay
alone was the one setup tested that trimmed the live year's silver contract at the book's existing churn. Chosen
2026-10-27; it supersedes the design's sample covariance for the overlay. `overlay_covariance` stays a required template
key set to carver (`src/risk/risk_module_config.cpp:205`). Mechanics: [RISK_MODULES.md](../../RISK_MODULES.md), section
"The Carver overlay".

### The fraction step

When the held book's tracking error TE_h exceeds the buffer B, every row moves the shared fraction a = (TE_h - B) /
TE_h toward the search answer and is rounded (`include/trade_ngin/optimization/one_pass.hpp:96-97`): strategy
twenty-five as written (p.432).

Not in the book: `edge`, one contract at a time to the buffer's edge, which takes Carver's stated aim literally but is
not his formula. It fills the buffer with a few large contracts and holds about 12 names a day where the fraction step
holds 15 to 17 (the loss attribution of the final grid, on Carver's covariance), so the book becomes a concentrated bet on
those contracts and gives up the diversification across 36 instruments that its IDM of 2.5 assumes. Data, the sample
covariance with the edge step against the chosen setup: -0.31 on the 2y, -0.22 on the 5y, -0.05 on the 10y and 16y,
+0.14 on the year; five-window mean 0.180 against 0.274. With Carver's covariance it is the setup in section 1.5.

### The per-name cap of 2

No position may exceed a weight of 2 on the sizing capital: the cap bounds the target the overlay reads, guards the
search and clips free rows of the rounded book; a held row is never clipped
(`include/trade_ngin/optimization/one_pass.hpp:34-36`). Two is Carver's own example, a leverage ratio "above two" for
any single instrument (AFTS, Tactic four, p.619). Applying it to the target first means the overlay never reads a
position the book cannot hold. The rest of Carver's position limits are in section 2.4.

### Removing expensive rules by cost

Twelve contracts drop their fastest one to three EWMAC pairs (eighteen rules) because the rule's yearly cost there
exceeds 0.15 Sharpe-ratio units, computed with table 35's turnovers and the book's own cost per trade: strategy nine,
"Removing expensive trading rules" (AFTS, p.200 to 201; tables 35 and 36, p.202 and p.205). Carver's limit is a cost budget per rule: a
speed whose cost on a contract takes more than that out of its expected return is not worth trading there. The list was
decided on 2026-10-09. On return it moved little (ahead on 9 of 18 five-year windows, median Sharpe change -0.01 on the
sizing capital); it is kept because it is Carver's procedure in exactly this setting. The book's own turnovers run 23 to
52 percent above table 35, and on them eight more rules would go; the list follows table 35. It was measured on
full-size GC and SI and has not been re-measured for MGC and SIL (an open design question).

Not in the book: blanket speed cuts. Dropping 2/8 and 4/16 on every instrument removes the fast speeds also from contracts
where their cost is under Carver's limit. The six speeds are Carver's design, each catching trends of a different length,
and he removes a speed only on a contract where its cost is above his speed limit, which the book applies above. Data,
each with its book and window: on the unrounded target book over 16 years the fast speeds earned 196k of the 270k; on the
reference model's 16y (long and short, fixed 500,000 sizing) the cut raised the Sharpe from 0.15 to 0.30 and saved 2,079
dollars a year of strategy cost (3,902 to 1,823, about 0.4 percent of the account); in the fit-and-test split, a speed
set chosen on one half of the history did not hold on the other half. Rejected on 2026-10-03. On 2026-10-29 an equity
speed cut was also not taken as a lever: no equity speed is over the cost limit (the equity micros' 2/8 costs 0.018 to
0.049 a year against 0.15), so Carver's test removes none of them, and a cut chosen on 2025's losses would be the
over-fitting Carver warns of (strategy nine, p.207 to 208).

### The table values

tau 0.20, IDM 2.5 (table 16, p.126: 30 or more instruments), the FDM of table 36 (1.26 for six pairs) and the forecast
scalars of table 29 (p.189) are Carver's table values, not estimates; using table values is Carver's own shortcut
(strategy four, p.125), and he notes that an IDM estimated over a whole backtest is a weak form of in-sample fitting
(footnote 85, p.136). Each was compared with the value implied by the book's own data, and nothing changed:

| Value | Table value used | Implied on the book's data |
|---|---|---|
| IDM | 2.5 (table 16, 30+ instruments) | 3.13 over 36 instruments; Carver recommends capping the IDM at 2.5 (Appendix B, p.644), so 2.5 |
| FDM, 6 pairs | 1.26 | 1.25 to 1.33 |
| FDM, 5 pairs | 1.19 | 1.17 to 1.24 |
| FDM, 4 pairs | 1.13 | 1.13 to 1.18 |
| FDM, 3 pairs | 1.08 | 1.09 |
| Forecast scalars (average absolute scaled forecast) | Carver's 10 | 9.18 (64/256) to 9.88 (2/8) |

**Volatility below target, and the IDM.** Carver's 20 percent target is meant to be realised on average. The book runs
at about 18 to 19 percent on its sizing capital (18.6 percent on the 16y), the gap left by whole-contract rounding and the
buffer. The IDM stays at 2.5: Carver recommends capping it there "to avoid excessive leverage on highly diversified
portfolios", and says a capped IDM undershoots the risk target (Appendix B, p.644). Raising it would scale every position,
those already at their target included, which raises the volatility target rather than filling the rounding gap: on a
rebuild of the trend sleeve outside the engine (the unrounded book, before costs), an IDM of 3.13 raised volatility from
19.3 to 24.2 percent at the same Sharpe.

**tau.** tau stays at 0.20 (kept 2026-10-04 and 2026-10-05). It is Carver's own risk target, a conservative value under
his half-Kelly estimate of 23 percent (AFTS, strategy two, p.72). At 500,000 a lower tau shrinks every target, and with
whole contracts more targets round to zero, so the book holds fewer names and gives up the diversification its IDM
assumes; drawdown is lowered through the book's construction, not a smaller target. Carver's conditions for 20 percent
are not all met here: he advises it only with at least one instrument in each of his seven asset classes, and sets tau
at about half the expected Sharpe, so 20 percent assumes a Sharpe of 0.40 or more (strategy three, p.101; strategy four,
p.133 to 134); this book has no volatility futures. Data, on the reference model's 16y: names held fell from 17.9 to
12.1 to 10.0 as tau was lowered from 0.20, and tau 0.15 and 0.10 each turned a window negative.

## 1. Dynamic optimisation (strategy twenty-five)

Mechanics: [OPTIMIZER_AND_RISK_DESIGN.md](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The one pass", and
[TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md), sections "The whole-contract search" and "The buffer, the
rounding, the clip and the trim".

### 1.1 The buffer width

**What it does to the book.** The book trades only when the held book's tracking error against the search answer
exceeds the risk of one contract of its lumpiest held or searched name, and never on less than Carver's 1 percent:
B_sigma = max(0.05 tau, the largest u_i sqrt(Sigma_ii) over free symbols with a non-zero search answer or holding)
(`include/trade_ngin/optimization/one_pass.hpp:88-94`). It trades far less than Carver's buffer, at a wider tracking
error: on the strategy-twenty-five round's 16y, about 115 round trips a year and 8.3 percent tracking error against
Carver's 337 and 4.5 percent.

**Why.** Carver's buffer B_sigma = 0.05 tau ("the optimal buffer Bσ is 1%" at a 20 percent target; AFTS, strategy
twenty-five, p.431) stops the book trading on changes too small to pay for. On this book one ZN contract is 1.1 of his
buffers, UB 3.0, CL 5.2 and GC 12.9, so a 1 percent band is narrower than one contract of most names. Sizing the band by
the lumpiest contract's risk sets it to the smallest trade the book can actually make, so the book trades only when a
whole-contract trade closes a gap worth that trade, which is Carver's purpose with whole contracts. The step inside the
band is still Carver's fraction.

**The numbers.** In the first reference model measurement the granularity buffer cut reversal pairs from 320 to 29 a
year. The chosen setup makes 153 round trips a year on the 16y.

**Carver's fixed 1 percent, not in the book.** It trades whenever the tracking error exceeds 1 percent. On this book one
contract of most names is wider than that band, so the band asks for changes the book can make only by trading a whole
contract, and each round trip costs fees and spread with no expected return of its own. Two measured figures: Carver's
fixed 1 percent buffer at full size churned 337 to 454 round trips a year (the strategy-twenty-five round and the
2026-10-26 sizing matrix) at 1.2 to 1.5 percent costs; the four fixed-buffer setups of the October sizing round together churned
338 to 544. Account size alone does not explain that churn: in Carver's own test the 1 percent buffer with multiplier 50
at 500,000 turned over about as much as his full portfolio at 50 million (48.8 against 46.5; AFTS, strategy twenty-five,
table 124, p.433). His test differs from this book in ways that bear on churn: it searched 100 instruments, so the
optimiser had many substitutes for a coarse contract, where this book has 36; it blended carry, a slow rule, with trend;
and it charged the current cost per contract, adjusted for the volatility of each historical period (p.428; strategy
five), where this book's cost adds an impact term on every fill. Which of these makes the 1 percent band churn here is unmeasured. The fixed buffer halved the tracking error, and
its costs took back the gain made before costs (0.08 to 0.09 of Sharpe). Data against the starting book of the first
tuning round (engine Sharpe): 0.28 against 0.09 on the 16y and 0.58 against -0.65 on the year, -0.48 against -0.35 on
the 2y (every start lower), and over the matrix's 17 runs 0.16 against 0.18; with the gold and silver micros, 0.15
against 0.31. Rejected on 2026-10-26. Wider fixed buffers (2 to 4 percent) trade churn for tracking smoothly and stay
above the trade bar (194 round trips on the 16y at 4 percent). Carver's static per-instrument buffer of strategy eight,
on the reference model, nearly doubled the churn (5y 592, 16y 637 round trips).

**The minimal variant, not in the book.** It lets no held-only lumpy contract set B_sigma. It removes the self-lock
(below) but not the width: when held contracts stop setting the band, the lumpiest searched contract sets it instead (a
median of 0.055, still over five times Carver's 1 percent), so the band barely narrows and tracking did not improve (8.2
against 8.3 percent on the 16y); what it changes is which held lumpy positions the book keeps. Data: on the chosen setup
a five-window mean of 0.230 against 0.274, with the 5y down 0.107 and the year down 0.245; on the starting book of the
first tuning round, the 16y 0.31 against 0.09.

**Known cost.** A held lumpy contract can set the band that protects it: GC on 2025-04-08 rounded back to one contract
where Carver's 1 percent would have exited.

**Decision.** The granularity buffer, approved 2026-09-30 and locked 2026-10-05; the fixed buffer rejected 2026-10-26.

### 1.2 The search from the held book

**What it does to the book.** The greedy search starts from today's holdings and tries one contract up and one down in
every free symbol, so a holding stays unless moving it lowers the objective net of its cost. It never steps past zero
against the target's sign, and a target of 0 steps only toward zero.

**Why.** Carver's greedy algorithm starts from "a zero weight in all instruments" and only adds contracts on the side
of the unrounded optimum (AFTS, strategy twenty-five, p.417 to 418). His cost penalty is already measured against the
contracts held (p.428), so both starting points price the same trades and keeping a holding costs nothing in either;
what differs is where the greedy path starts and where it stops. From zero the path adds one contract at a time and
stops at the first answer no single step improves; on a lumpy book two nearly equal answers lie close together, small
changes in the inputs send the path to one or the other on successive days, and the book rebuilds itself. From the held
book the path starts where the book is and stops there unless one contract's step lowers the objective, so a nearly
equal answer does not displace the held book. The objective is Carver's (tracking error plus the cost penalty); only
the starting point changes, and the sign guard keeps his rule that positions sit on the forecast's side. The engine's
search reaches a lower value of Carver's own objective than his greedy on 97 of 109 sampled days.

**The numbers.** On the first reference model, from the held book 187 reversal pairs a year against 320 from zero, with
a lower tracking error (0.047 against 0.059).

**Carver's start, not in the book.** The first design as written (search from zero, 1 percent buffer, fee-only costs),
on the first reference model: 2,107 fills and 588 reversal pairs a year against the engine's 395 and 28. On a lumpy book
each day's switch between near-equal answers is a trade that pays fees and spread; the redesign brought the search from
the held book and the granularity buffer.

**Decision.** The search from the held book, 2026-09-27, with the sign guard approved on 2026-09-30. On 2026-09-20 the
from-zero search had been held to be Carver's design and not a defect; the reference model's measurement reversed that.

### 1.3 The forecast-sign close and its deferral band

**What it does to the book.** A free symbol held against its forecast's sign is closed with one fill to flat before the
search (class SIGN), outside the buffer. While the opposite forecast is weaker than 2 (abs(F) < 2) the position is held
instead. The template sets `sign_close_band` 2 (`config_template/defaults.json:20`).

**Why.** Carver's optimisation only ever takes a position on the forecast's side, so "whenever a forecast changes sign"
the whole position is closed. He presents that full close as the problem: it sets "a lower bound below which turnover
can't be reduced", which a higher cost penalty cannot lower, and his answer is the buffer (AFTS, strategy twenty-five,
p.430). In his full method there is no separate close: the buffer moves a wrong-signed row a fraction of the way to zero
like any other row. The book's separate close is therefore its own departure. Its economics: a position held against its
own forecast is a position the system expects to lose on. Here the buffer is as wide as one lumpy contract (up to 12.9 of
Carver's) and the search starts from the held book with the cost term at 100, so the buffer alone leaves such a row in
place: when the sizing round let the buffer move wrong-signed rows (below), they sat against the forecast a median 4
cycles (longest 217) against 1 (longest 14) with the close. The close ends that on the day the sign changes. The turnover
Carver expects from a full close did not appear on this book: without the close the strategy-twenty-five round's 16y
made 140 round trips a year against 115 with it, and the reference model's 16y 422, more than with the close at either
cost multiplier (316 at 50, 250 at 100).

The band is the book's own: the close waits while the opposite forecast is under 2. On Carver's scale, where the average
absolute forecast is 10, a forecast under 2 is a weak reading of a sign change.

**The numbers.** The band's own effect on round trips is near zero (mean -12, -14 and -4 on the 2y, 5y and 16y over 16
pairings).

**Carver's treatment, not in the book.** In Carver's form there is no separate close; the buffer moves the wrong-signed
row like any other. Under this book's buffer that leaves rows against the forecast and metals far over target, so the
book holds positions its forecast expects to lose on and runs more risk than it was sized for, with the result of a
window resting on one or two gold and silver contracts. The sizing round ran it as the book with the sign close off (the
band off, the buffer moving the row): tracking error 20.9 percent on the seven screen windows and 13.6 percent over its
17 runs, 32 percent of name-days over twice their target, GC at 2.2 and SI at 2.7 times target, and the wrong-sided
stretches above. Its mean Sharpe was 0.38 after costs and 0.41 before, against -0.02 and 0.02 for the starting book of
the first tuning round on the seven screen windows, at 100 to 165 round trips; 0.38 over its own 17 runs against that
book's 0.18; and 0.15 against 0.42 on the 10y. It was dropped on 2026-10-26. The strategy-twenty-five round's change 2
(no close, no band, the buffer moves the row) is identical to it on the 16y: Sharpe 0.41 against 0.09, 140 round trips,
tracking error 9.1 against 8.3 percent, and 5.9 percent of free name-days stored against the target's sign. Its
wrong-signed holding comes mostly from the buffer width and the cost term against the held book, not from the search's
start point. Change 2 was not used (2026-10-27).

**Decision.** The close came with the search from the held book (2026-09-27); the band on 2026-10-05.

### 1.4 The cost multiplier

**What it does to the book.** The search weighs trading cost at 100 times one contract's cost, so a row moves only when
the tracking error it removes is worth that much. The book makes fewer, cheaper trades.

**Why.** Carver multiplies the cost by 50 and says any number "between 10 and 100" gives similar results (AFTS, strategy
twenty-five, p.428). The multiplier is the price the search puts on a dollar of cost against the tracking error a trade
removes, so it sets the book's cost budget: at 100 a trade must remove twice the tracking error per dollar that Carver's
50 asks. The account size does not set the choice: in the objective the tracking term moves by one contract's weight and
the cost term by one contract's cost, each divided by capital (`src/optimization/one_pass.cpp:153`, `:383`, `:543`), so
the trade-off between them is the same at any capital, and Carver set his 50 on a 500,000 example. The cost penalty is
the one lever that reliably cuts this book's churn toward its trade bar of about 150 round trips a year. It stops at
100, the top of Carver's range, because above it trading collapses onto the cheapest instruments, as Carver warns: a
higher penalty "only results in trading being limited to the very cheapest instruments" (p.430). At 300 livestock was in
a position on 2 percent of the 16y's days, against 68 percent at 50 (2026-10-05).

**The numbers.** On the reference model, with the cost multiplier the only change, round trips a year at 50 / 100 were
235 / 214 (2y), 272 / 189 (5y) and 316 / 250 (16y). On the chosen setup's 16y costs came to 4,019 a year (section 5.2).

**A higher multiplier, not in the book.** 300, outside Carver's range, met the trade bar on the reference model only by
starving grains and livestock (livestock stopped trading), so the book stopped following its signal in whole sectors. On
the reference model's chosen setup (multiplier 100 with the deferred sign close and the equity slow rule), 150 raised the
16y churn to 325 round trips against 292. On an earlier engine loop, a target of the low to mid 100s of executions a year
was reached by the multiplier alone only between about 300 and 500, at 25 to 60 percent more tracking error.

**Decision.** Multiplier 100, 2026-10-05.

### 1.5 The optimiser covariance (756 closes)

**What it does to the book.** The search measures how contracts stand in for each other with the equal-weight sample
covariance of daily adjusted returns over each free symbol's last 756 consumed closes, on the dates every kept symbol
shares. The 756 is `kOptimiserBars` (`src/strategy/trend_following.cpp:665`). Because the roots keep different
calendars about 0.8 of the 756 returns reach the covariance; the median is 609 returns on the chosen setup's 2y.

**Why.** Carver's optimiser covariance is a 32-day EWMA volatility with "the last six months of weekly returns" for
correlation (AFTS, strategy twenty-five, p.417; Appendix B, p.641, weights correlations exponentially with a six-month
span); it follows recent risk. The design used one sample estimator for every reading; since the overlay moved to
Carver's covariance on 2026-10-27, the sample stays for the optimiser. The window length was set on 2026-09-21 to keep
the optimiser on the prices the trend sleeve then held (its history was capped at 756 at the time), because an
equal-weight sample's answer moves with its length. What the sample does: a three-year equal-weight window moves slowly, so the search's choice of which
contract stands in for another, and the one-contract risks that set B_sigma, change little from day to day. The band and
the held book's tracking error both scale with volatility (`src/optimization/one_pass.cpp:237`, `:258`), so a broad
volatility spike moves both together under either estimator. Keeping the sample over Carver's estimator is a departure
and a measured result, with the data below.

**Carver's form, measured.** The variant tested used the book's blended sizing volatility with the overlay's 26-week
weekly correlations.

- Carver's optimiser covariance alone, with the fraction step, against the chosen setup: -0.03 on the 2y, +0.02 on the
  10y, +0.03 on the year, -0.13 on the 5y and -0.15 on the 16y; five-window mean 0.222 against 0.274.
- Carver's optimiser covariance with the edge step: the edge step fills the buffer with a few large contracts (section
  "The fraction step"), so the book becomes a concentrated bet on them. Data against the chosen setup: five-window mean
  0.302 against 0.274; 2y 0.245 against 0.102, 10y 0.308 against 0.290, year 0.636 against 0.407, 5y 0.082 against
  0.273, 16y 0.240 against 0.300; maximum drawdown 30.9 against 22.0 percent on the 5y and 20.7 against 14.9 on the 2y.
  The five 2y starts share 2024 to 2026, and the year starts lie inside them. With the minimal buffer the same setup's
  mean was 0.341, its 5y 0.132, and its correlation with the SG Trend Index over the year -0.073. The size-neutral step,
  on its one working run, -0.284 against +0.048. It was dropped on 2026-10-29.
- Shorter sample lookbacks of 160 bars (about Carver's six months after the shared dates thin it), 252 and 504: five-window
  means 0.240, 0.124 and 0.199 against 0.274. Single windows were higher (160 on the year, 0.571 against 0.407; 504 on the
  10y, 0.328, and the 2y, 0.191), and results jumped between neighbouring lookbacks (largest jump 0.587 on the year), so no
  lookback showed a smooth response. The lookback switch was dropped from the release on 2026-10-29.

**Decision.** The 756-close window, 2026-09-21; carried in the design of 2026-10-05; the chosen setup at 756 on
2026-10-29.

## 2. Risk overlay (Tactic four)

Mechanics: [RISK_MODULES.md](../../RISK_MODULES.md), section "The Carver overlay", and
[OPTIMIZER_AND_RISK_DESIGN.md](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The overlay in brief".

### 2.1 The limit values: Carver's method, the book's own values

**What it does to the book.** The overlay reads the capped target book and scales it by one multiplier when any of five
readings is over its limit: R_max 1.75, R_jump_max 4.75 and R_shock_max 3.00 (ratios to tau: 0.35, 0.95 and 0.60 at tau
0.20), gross leverage 7.25, net leverage 5.50. On the chosen setup's 16y it cut 193 of 4,738 cycles (4.1 percent, counted
from the first fill), split L_g 32, L_n 54, R 11, R_jump 51 and R_shock 45; the 2y and year cuts are mostly R_shock.

**Why.** Carver sets each limit at the 99th percentile of its reading in his own backtest, rounded to a neat number, so
the overlay acts only in the tail (AFTS, Tactic four, p.605 to 612). He warns that the leverage figure does not transfer
between universes and that a less diversified book needs a higher risk limit. Applying his method to this book's own
history is Carver as written; the values differ because the book differs (36 instruments in whole contracts at 500,000,
against his 100-instrument portfolio). Five readings, each at its own 99th percentile, bind on different cycles, so the
book is cut on about 4 percent of cycles while no single reading binds on more than about 1 percent (R_jump and net
leverage 1.1 percent of the 16y's cycles, R 0.2 percent). That is Carver's own expectation: four multipliers each
active 1 percent of the time give a combined multiplier active "around 4% of the time" (p.606), and his strategy eleven
was cut "just under 5%" of the time (p.613).

**The method.**

1. Run the set's 16-year backtest with the overlay limits off (the three risk limits at 100 tau, both leverage limits at
   10); the per-name cap stays at 2, so the readings are of the capped target.
2. Check that nothing binds on any cycle.
3. Take the 99th percentile of each reading over the complete cycles after the warm-up.
4. Round to the nearest 0.25 (Carver rounds by judgment; the fixed rule is the book's).

| Limit | Chosen setup p99 (unrounded) | Limit |
|---|---:|---:|
| R | 1.6847 tau | 1.75 tau = 0.35 |
| R_jump | 4.8233 tau | 4.75 tau = 0.95 |
| R_shock | 3.0581 tau | 3.00 tau = 0.60 |
| Gross leverage | 7.1847 | 7.25 |
| Net leverage | 5.5926 | 5.50 |

**5.50 or 5.75.** Net 5.5926 is 0.032 under the 5.625 midpoint, so the method gives 5.50. The other three sets of the
final grid rounded to 5.75. The chosen setup at net 5.75 against 5.50: +0.039 on the 16y, -0.010 on the 10y, -0.028 on the
5y, 0.000 on the 2y and the year; plain means 0.275 against 0.274. The limit is the method's rounding: 5.50, set on
2026-10-29.

**Against Carver's values.** Carver's are 1.5, 3.75 and 3.25 tau and gross leverage 20. R and R_jump here are looser (1.75
against 1.5; 4.75 against 3.75), R_shock is tighter (3.00 against 3.25), and gross leverage is far tighter (7.25 against
20). His values bind at his portfolio's frequency, not this book's. Carver's own values (with gross 8 and net 6) were
measured on the reference model on 2026-10-05: the 16y maximum drawdown was 37.4 against 38.7 percent.

**Tried before.** The first limits of 2026-10-01 (R 0.45, R_jump 0.90, R_shock 0.80, gross 8.0, net 6.0) came from a p99
of the reference model's long and short book with judgment rounding, on the sample overlay covariance; they are
superseded. An October cap of 25 percent expected risk met the risk target but, as a single reading, cut the book on
about 8 percent of days where Carver aims each reading at about 1 percent, and long-window returns did not order with
R_max; the method, not a return figure, set the replacement.

**Decision.** The Carver-method set accepted 2026-10-26, made the final setup 2026-10-27, and the chosen setup at net 5.50
on 2026-10-29.

### 2.2 The net leverage limit

**What it does to the book.** A fifth reading, net leverage (the signed sum of the weights), with its own limit of 5.50
beside gross 7.25. It is a limit, never a target: the fifth term of the daily multiplier, enforced by a cut only on the
days it binds. It was the binding term on 54 of the 193 cut cycles of the chosen setup's 16y. On the 2y it bound once
(2026-06-10, multiplier 0.99837) and moved no contract.

**Why.** Carver has one leverage reading, gross, "the crudest measure of risk"; in his backtest it was the multiplier
with the largest effect in 2012, when low volatility in rates pushed leverage up (AFTS, Tactic four, p.610 to 613). Like
Carver's gross reading, the net reading needs no volatility estimate; it applies the same check to direction: a cap on
signed notional, so it still binds when low estimates let notional build. It is as
crude as Carver's gross reading: a plain signed sum of notional weights (`include/trade_ngin/risk/overlay.hpp:77-82`), so
the low-volatility rates and FX positions, which carry the most notional per unit of risk, dominate it, and it does not
measure the book's one-way market risk. Carver warns that leverage "varies radically by instrument" and names limits by
asset class as the finer form (p.611; footnote 292, p.622); that form is not built. The net limit has no Carver
counterpart; its level comes from his method, the book's own 99th percentile, so it binds only where the book's own
history puts the net notional in its tail.

**Tried instead.** On the reference model a net leverage grid of 2 to 6 (gross = net + 2) showed no smooth response:
results jumped between neighbouring settings. Net 6 with gross 8 stayed until the p99 limits.

**Decision.** Kept in the design of 2026-10-05; the value 5.50 on 2026-10-29.

### 2.3 Held rows unscaled, and the trim

**What it does to the book.** The multiplier applies once to every free symbol's capped target; held rows (the bar
classifier's holds, a pending roll, the deferral band) count at their held quantity and are not scaled. After rounding,
if the stored book is still over a limit, the trim removes one contract at a time from free rows, the one that lowers the
first breached reading most, at most five a day.

**Why.** Carver applies one multiplier to all unrounded positions, before the optimiser under dynamic optimisation (AFTS,
Tactic four, p.603 to 604 and p.612; footnote 293, p.622). The book keeps his order and his single scalar, and never
applies a second. A held row cannot trade that day (its bar is not a usable print, or a roll is pending), so scaling it
would ask for a trade the book cannot make. Whole contracts can round a book back over a limit the target was inside; the
trim restores the limit with the fewest contracts.

**The numbers.** The trim removed 15 contracts on 14 days over the chosen setup's 16 years (CL 5, UB 3, HO 2, and NQ,
RB, 6B, HG and PL 1 each) and one contract in the 2y, and no stored day was left over a limit. It cleared every marked
day on the reference model.

**Tried instead.** On the earlier lap loop, rounding the scaled book did not deliver the cut (132 days over the limit in
the 2y), and keeping it fractional left 126; iterating the overlay and the optimiser was replaced by one pass on
2026-10-01.

**Also without a Carver counterpart.** Below 21 complete dates in the gate window the covariance readings are off and
leverage still applies, and a symbol with fewer than 120 returns is left out of R and R_jump (both 2026-09-30), so a new
listing cannot blind the book. The overlay reads the capped target (2026-10-01).

**Decision.** Held rows unscaled and the trim, approved 2026-09-30.

### 2.4 No separate per-instrument position limit

**What it does to the book.** The only per-name bound is the cap of 2 (section "The per-name cap of 2"). There is no
limit at the maximum forecast, no open-interest limit, and no screen for instruments too safe to trade.

**Carver's limit, and what leaving it out does.** Carver sets three limits and applies the tightest: one at the maximum
forecast with 50 percent headroom, a per-instrument leverage limit, and a share of open interest; and he excludes an
instrument too safe to trade (AFTS, Tactic four, p.614 to 620). Under dynamic optimisation he says "it's important that
you should still apply some position limits", with a nominal weight of 10 or 20 percent in the maximum-forecast formula,
"to limit the possible concentration of risk in any individual instrument" (p.620 to 621). The cap of 2 is his
per-instrument leverage limit, his own example value (p.619).

**Why it is not built.** By design, not on its numbers (2026-10-05): the cap of 2 already bounds each name, so his
position limit at a chosen nominal weight of 10 or 20 percent would be a second, overlapping per-name cap on a picked
parameter. The two bound different things: the cap bounds notional, where Carver's limit bounds a name's risk (a
contract with 40 percent volatility at a weight of 2 carries 0.8 of risk against a target of 0.2), and the overlay's
risk limits bound the book's risk as a whole. Leaving his limit out is a departure from his advice. The open-interest
part does not bind at 500,000, and the book holds no open-interest data. The cap also bounds a low-volatility contract every day, which is the
job of the too-safe screen, and no contract is removed from the 36.

**The data.** On the reference model with half compounding, at a 10 percent nominal weight the limit changed the held
book once in 16 years (heating oil, 2 to 1 contracts, on 2022-03-08, which took that run's worst day from -10.9 to -6.7
percent) and clipped three target rows; its 16y Sharpe was 0.46 against 0.37 without it, and its 5y 0.42 against 0.55. At
20 percent the 5y and 16y were identical with it and without it.

**Decision.** The per-name cap and no minimum-volatility screen closed on 2026-10-01; the position limit not built, by
design, on 2026-10-05, the day the design was locked.

## 3. Signal

Mechanics: [TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md), sections "Volatility", "The trend rules: EWMAC,
scaling, capping" and "The equity-index slow rule".

### 3.1 The equity slow rule: the one rule that goes against Carver's advice

**What it does to the book.** For MES, MNQ, MYM and M2K a negative combined forecast stands only when the 32/128 and
64/256 scaled forecasts are both negative; otherwise it is 0. The book shorts an equity index only when the slow trend
agrees.

**Why, and why it departs from Carver.** The rule was adopted on 2026-10-05 on this book's record: 113 of 190
equity-index shorts were opened on the fast speeds alone; equity shorts as a whole lost 121,403 over 16 years. That fits
Carver's finding that equities are the weakest trend class: across equity instruments his multiple-speed trend strategy
has a median Sharpe of 0.03, and he notes that equities "do even worse when trading quicker filters" (AFTS, strategy
nine, table 37, p.206 to 207). It is a declared departure: Carver finds the equity effect by asset class not
statistically significant and advises against "any kind of adjustment that is asymmetric" (strategy twelve, p.268), and
says lowering the trend allocation within equities "would potentially be over fitting" (strategy nine, p.208). As an
economic reading only, his "bouncing cat" (figure 53, p.266) shows equity indices rallying after strong negative fast
trends, though that bounce is in the two fastest speeds and the rule keys on the slow ones. The same idea for every instrument (a fast speed
counts only when it agrees in sign with the slow trend) is not in the book: Carver's evidence is for equities only, and
on this book's history that form lost 8.9 points over 2011 to 2020.

**The numbers.** On the
unrounded book the rule is worth about 20 points over 16 years, and 2025 is the one year that was higher without it (+3.9
points, all in the equities). It does not cut churn.

**Carver's form (the rule off), not in the book.** Off, the book opens equity shorts on the fast speeds alone, the
trades that lost on this book's record. At full size, the rule off made 0.39 against -0.02 mean Sharpe after costs for
the starting book of the first tuning round over the seven screen windows; the difference was in the metals, and the
equities themselves made less without the rule (16y net +28k against +49k). On the gold and silver micro book, the rule
off gave 0.17 against 0.30. No equity shorts at all is a second asymmetric form and a standing long bet on equities,
which symmetric trend following does not hold; it was measured at 5y 0.327 against 0.129 on the sizing capital. The
equity fast-speed cut and the high-volatility switch, alone or together, are not Carver's either: Carver's own equity
change (AFTS, table 70) moves equity weight from trend to carry, and this book runs carry off; an equity speed cut
chosen on 2025's losses is the over-fitting he warns of (strategy nine, p.207 to 208); and the high-volatility switch is
not his form (he attenuates and never switches off), was found on the period it fixes, and lost over 2011 to 2020. Those
options are in
[the levers document](../03_tuning_and_results/LEVERS.md); the speed-cut answer is in section "Removing expensive rules
by cost" above.

**Decision.** Rule on, 2026-10-05; kept, not made a tuning lever, 2026-10-29. With carry it would apply to the trend part
only.

### 3.2 Carry shipped off

**What it does to the book.** The book trades trend only. The carry code is on main, switched off (c7cec21205 in
`AlgoGators/trade-ngin#282`), to the design of `AlgoGators/trade-ngin#185`; no template carries a `carry` block, and it
turns on only by adding one.

**Why, and why it departs.** Carver's default blends trend 60 percent and carry 40 percent (AFTS, strategy eleven, p.248
to 249, table 51). Carry is built and shipped off because it has not yet been tuned on the data the book trades. It
needs two real contract prices on the same date, which only the per-contract bars give, and the second contract is
missing on many dates: UB has it on 55.4 percent of dates and 6M on 66.0 percent, and 6L's longest gap is 78 dates. When
carry was measured (2026-10-26 and 10-27) the continuous series still had holes (`AlgoGators/algogators#36`) and the gold
and silver micros were stand-ins, and the final retune ran with carry off (`AlgoGators/algogators#186`).

**What is known, and what is not.** Carver's blend rests on the premise that carry is a second return beside trend. On
this book's 36 instruments since 2011 it
was not: unrounded and before costs, carry alone made a 16y Sharpe of 0.02 and the 60/40 blend 0.25 against trend's 0.26,
so the blend moved 40 percent of the forecast to a rule that earned nothing here: it kept trend's Sharpe at lower risk and
added no second return. Carry trades less and tracks its unrounded book a little more closely: on the 2026-10-27 grid's
gold and silver micro book it roughly halved the round trips (76 to 110 a year with carry on against 152 to 216 off, 12
runs per cell), and the grid's median tracking error was 9.5 / 8.7 / 9.6 percent with carry against 9.8 / 9.3 / 11.3
without; on the one stored 16-year run, on the settings of the starting book of the first tuning round, 7.0 against 8.3
percent. On that run the blend's whole-contract book kept a smaller share of its unrounded book (9k of 200k, against 65k
of 260k for trend alone) at a similar dollar shortfall (about 191k against 195k), so the data do not show a larger
rounding or tracking loss with carry. Why the traded blend's after-cost Sharpe falls is unmeasured; the number of rows
that round to zero was never counted.

**The numbers.** On the gold and silver micro book with Carver's overlay covariance, each set on its own limits, the mean
after-cost Sharpe over 17 runs was 0.28 with carry off against -0.08 with carry on. In the 2026-10-27 grid carry lowered
the 12-run mean in two of the three pairs (on the gold and silver micro book 0.34 against 0.04); the third rose from its
one-year starts alone. The dollar cost per run
(a mean 65k / 69k / 110k / 220k of net P&L on the year, 2y, 5y and 10y, 10k added on the 16y) partly reflects the blend's
lower risk (14.7 against 19.4 percent volatility unrounded), so the Sharpe is the measure.

**Decision.** Carry off, 2026-10-27. `AlgoGators/algogators#186` holds the re-measurement of carry on the final data and
a trend-and-carry book beside trend only. Details: [the carry document](../03_tuning_and_results/CARRY.md).

### 3.3 Attenuation, annualisation and volatility details

The volatility estimator keeps Carver's formula and the forecast keeps his annualisation; the sizing annualisation and
the attenuation input differ from his, for mechanical reasons.

| Item | AFTS | The book | Why |
|---|---|---|---|
| Volatility estimator | Strategy three, p.90: 0.7 of a 32-day EWMA plus 0.3 of a ten-year average | The same blend; variance seeded at 0.1 r_1^2 on the first return (`include/trade_ngin/strategy/trend_estimator.hpp:31`) and floored at 1e-6 a bar; long run the mean of the trailing 2,520 values | A series that starts or sits flat must not give zero volatility |
| Sizing annualisation | Strategy three: a fixed 16 | The bars counted over the trailing 256 consumed bars; the forecast keeps 16 | A series with Sunday session rows has more than 256 bars a year |
| Attenuation | Strategy thirteen, p.276 to 279: relative volatility V, the current volatility over its ten-year average, built to be "scale free"; Q its quantile over the instrument's whole history; M = 2 - 1.5 Q, smoothed (p.280) | Q is the quantile of sigma_short itself against its own trailing 2,520 values (from the 253rd bar), with a 10-day smooth (`include/trade_ngin/strategy/trend_estimator.hpp:42-44`) | One estimator serves the quantile; the ranked quantity is not Carver's scale-free ratio |

Known limits: the ranked series is not scale free when bar counts shift, and short histories get M = 1 until 252 values.
Centring is not a difference. Sunday rows count as days because the Sunday evening
session is a real exchange session that prints its own bar, and the runner trades on the bars the feed prints (decided
2026-09-03; kept 2026-10-28); folding them into Monday moves every volatility, forecast and limit, so it waits for a full
engine retune. No variant of the attenuation input has been measured, and the business-day annualisation is not built; both
are among the open design questions.

## 4. Instruments

Mechanics: [OPTIMIZER_AND_RISK_DESIGN.md](../../OPTIMIZER_AND_RISK_DESIGN.md), section "Instrument weights and the IDM",
and [TREND_FOLLOWING_SYSTEM.md](../../TREND_FOLLOWING_SYSTEM.md), section "The instrument universe". Why these 36 markets
and the gold and silver micros, and how markets are added: [the book document](../01_the_book/THE_BOOK.md), section "Why
these 36 markets, and the gold and silver micros".

### 4.1 All 36 under dynamic optimisation

**What it does to the book.** The book trades all 36 instruments at fixed sector weights, with no minimum-capital or
per-instrument cost screen; the one-pass search decides each day which contracts the book can hold.

**Why.** Carver's minimum capital (four contracts; strategy two, p.79; strategy three, p.102) and his static selection
(strategy four, p.126 to 127) are one answer for a small account; dynamic optimisation is his other, written for exactly this case. In his
own test a 500,000 account trading 100 instruments under dynamic optimisation made a Sharpe of 1.22, against 1.18 for 27
instruments chosen by static selection and 1.27 for the full portfolio at 50 million (AFTS, strategy twenty-five, table
124, p.433). Trend following earns from a few large moves, and which market makes the next one cannot be known in
advance, so a small set risks leaving it out; the diversification across 36 instruments is what the IDM of 2.5 assumes.
Keeping every instrument and letting the search choose is Carver's small-account answer, nearly as written.

**Carver's static path, not in the book.** His minimum capital removes 34 of the 36 instruments at 500,000; his cost
limit of 0.01 Sharpe-ratio units a trade removes none (the largest is ZR at 0.00483); his selection algorithm, on this
book's costs and capital, stops at 7 instruments in all 8 ways it was run. Seven instruments give up the diversification
the book is sized on: Carver's IDM table puts seven instruments at 2.10 against 2.50 for 30 or more (table 16, p.126),
and his own 500,000 test kept 27 instruments under static selection (table 124). Data: Carver's selection for the
account size made 0.12 on the 16y against 0.08 for the starting book of the first tuning round, and lost 50.7 percent
over the 10y.

**Decision.** On 2026-10-26: keep and trade every contract, gold and silver included, with trend alone and sizing to carry
it. Adding markets is the study in `AlgoGators/algogators#184` part B.

### 4.2 Micros for the equity indices, gold and silver

**What it does to the book.** Gold and silver trade as MGC (from 2010-10-04, GC before) and SIL (from 2013-06-17, SI
before); the equity indices as MES, MNQ, MYM and M2K from 2019-05-06 (ES, NQ, YM and RTY before); every other contract
full size (`config_template/portfolios/conservative/portfolio.json:26-36`).

**Why.** Carver's minimum-capital advice is to use smaller contracts when capital is limited (AFTS, strategy two), and
his Jumbo portfolio trades gold as a micro (Appendix C). The book uses micros where the full-size contract cannot be held
at 500,000: gold and silver want about 0.05 contracts at an average forecast, so only their micros hold them near
target. Gold and silver were the book's coarsest holdings: one GC
contract is 12.9 of Carver's buffers, and at full size the book held GC and SI rarely and above their target (the
starting book of the first tuning round held GC on 1,137 days at 1.7 times and SI on 957 days at 2.1 times; with the
equity fast-speed cut and the high-volatility switch, one GC contract was held about 450 days at 6 to 8 times), so a
window's result could rest on one contract. Micros let the book hold them near target (GC at
0.9 and SI at 1.1 times over 16 years). For other contracts a micro means the book holds several contracts where it held
none or one, and every one-lot adjustment becomes a trade.

**The numbers.** On the 17-run sizing matrix the gold and silver micro book's mean after-cost Sharpe was 0.31, against
0.18 for the starting book of the first tuning round.

**Micro FX and the copper micro, not in the book.** Micro FX does not solve the problem micros are used for, because FX
is not where the book is coarse, and it turns one-lot adjustments into trades: on the starting book's settings it made
168 to 256 round trips a year against 115 to 192. The copper micro adds trades where copper is not the coarse contract,
and it has only been listed since 2022-05-02: gold, silver and copper micros made 0.13 against 0.30 for gold and silver
micros alone on the seven-run screen. The first wide micro phase on the earlier loop nearly tripled the churn.

**Decision.** Micro metals accepted 2026-10-26, narrowed to gold and silver 2026-10-27.
The design's "no micros" line means no moves of other contracts to micro size; gold and silver are the exception.

### 4.3 Sector-equal weights

**What it does to the book.** Each of seven sectors gets an equal share, split equally among its names; a symbol is capped
at 50 percent of its sector. MBT sits alone in crypto, capped at half of one seventh (a weight of 0.071429), and the half
it gives up goes to the uncapped symbols, so each of the other six sectors gets 13/84 (about 0.1548), not one seventh
(`src/strategy/trend_following.cpp:1236`, `:1278`). Carver's own static selection puts Bitcoin in metals (AFTS,
strategy twenty-five, footnote 241, p.442).

**Why.** Carver allocates top-down: equal shares by asset class, then by group, then by instrument (AFTS, strategy four,
p.122 to 126). The book's seven sectors stand for his asset classes, so it keeps the layer of his method that matters
most, equal risk to each class. The metadata carries one sector field, so the group layer is not built: within the large
sectors (agriculture with 10 names, FX with 9) every name gets the same share where Carver would first split by group.
The 50 percent cap stops a one-contract sector taking a whole share.

**Decision.** Kept in the design of 2026-10-05. The group layer is an open design question.

## 5. Execution

### 5.1 Rolls as two outright legs

**What it does to the book.** On a roll bar the book books two ROLL fills at its held quantity, each priced by the cost
model as an outright trade, on per-contract rolls by the roll rule of 2026-10-23 (`per_contract_rolls` true,
`per_contract_switch_bar` held, both shipped on main). A roll known on its bar is booked at that bar's two closes; only
a vendor switch awaiting the next bar is held (`include/trade_ngin/data/roll_series.hpp:47-59`).

**Why.** Carver rolls with a spread trade, both legs in one order, and notes that passive rolling, done as part of the
normal trading, is cheaper still (AFTS, Tactic one, p.567). The engine has no spread prices, so each leg
is priced as an outright, an upper bound on the cost. That is the same conservative assumption Carver makes when he
costs a roll as "two lots of costs" (AFTS, strategy three, p.94). On the chosen setup's 16y, rolls are 1,948 of the 4,019
a year of costs. The exact roll spread waits for per-contract spread data.

**Known gaps against Carver.** The roll target is the cycle front; Carver's choice of a later month for some roots (a fixed
December for CL, ZC and ZW, against seasonality: in crude it keeps the seasonal component out of the back-adjusted price
the trend rules read (AFTS, strategy one, p.52), and he holds December in corn and wheat too because seasonality
distorts the carry of a book that holds the first or second contract (strategy ten, p.233; tactic one, p.561)) is not
built, and the gold cycle omits October. There is no forced close before a roll inside
the optimiser. The roll rule, how it handles each case, and its reasons are in
[the rolls document](../01_the_book/ROLLS_AND_DATA.md); the mechanics in [FUTURES_ROLLS.md](../../FUTURES_ROLLS.md).

**Decision.** Two legs, 2026-10-01; the roll rule, 2026-10-23. The design's vendor-series change-bar rules are superseded
by the per-contract rule.

### 5.2 The cost model per fill

**What it does to the book.** Every fill pays the per-contract fee, half a tick spread widened with volatility (a quarter
on treasuries and FX), and square-root impact on the fill day's own volume, each part capped. The search reads the same
price of one contract. On the chosen setup's 16y costs came to 4,019 a year (0.80 percent of 500,000), 0.052 Sharpe
units.

**Why.** Carver's cost of a contract is the commission plus half the spread, with impact left to large traders (AFTS,
strategy one, p.24), and he compares costs as a yearly expectation in Sharpe-ratio units (strategy three). This engine
books a dollar cost on every dated fill, so the cost must exist per fill. Carver's two parts are the base; the volatility
widening and the impact term make a thin or stressed day cost more, as it does in the market. The impact on a thin micro
dominates its cost (77 percent of the implicit cost on a worked SIL fill). The Sharpe-unit form is used where Carver uses
it, to remove expensive rules. The extra parts make the book's costs higher than Carver's, not lower.

**The numbers.** Across sets costs are 0.03 to 0.06 Sharpe units, and doubling every fill's cost takes 0.03 to 0.06 off a
run's Sharpe.

**Decision.** An engine convention of the design of 2026-10-05, recorded on 2026-10-10. Mechanics and worked fills:
[COST_MODEL.md](../../COST_MODEL.md), sections "One formula", "The spread model and the impact model: every constant" and
"Where cost enters decisions".

## 6. Not built

| Control | AFTS | In the book |
|---|---|---|
| Margin monitoring and action (concern at 50 percent usage, act at 60) | Tactic four, p.602 to 603 | Not built; filed as `AlgoGators/algogators#172` (daily margin check). Carver expects about 30 percent on average; the final micro setup measured a mean of 12 to 20 percent of the account and a maximum of 65.7 |
| Liquidity screen (100 contracts, about $1.25m of risk a day) | Strategy three, p.104 to 105 | Not built; a per-root liquidity measure is `AlgoGators/algogators#184` part A item 2 |
| Daily and weekly trade limits | Tactic four, p.621 | Not built; `AlgoGators/algogators#184` part A item 3 |
| Volatility in price units for zero or negative prices | Appendix B, p.639 | Not built; one non-positive price stops the day's update |

## 7. Carver's forms not in the book

Each departure above was set against Carver's form, or a near variant, on this book. This table lists the forms that are
not in the book: what each does, the economic reason it does not fit a 500,000 book of whole contracts, and its data.
Figures against the chosen setup are the account Sharpe after costs; figures against the starting book of the first
tuning round are the sizing and strategy-twenty-five rounds' own Sharpe, a different book and measure. Full results:
[the levers document](../03_tuning_and_results/LEVERS.md).

| Carver's form or variant | Section | What it does | Why it does not fit | Data (book, window) |
|---|---|---|---|---|
| Fixed capital; four other sizing forms | Half compounding | Fixed capital never cuts size; the others cut less after a loss, or by a tuned or calendar rule | Carver warns against not cutting size after losses in a live account; the other forms are not his | Reference model 5y 0.72 for fixed capital; engine 5y 0.675 to 0.718 against 0.129 on the sizing capital |
| Edge step, sample covariance | The fraction step | One contract at a time to the buffer's edge | Fills the buffer with a few large contracts (about 12 names against 15 to 17), a concentrated book | Against the chosen setup: mean 0.180 against 0.274 |
| Blanket speed cuts | Removing expensive rules by cost | Drops 2/8 and 4/16 on every instrument | Removes speeds also where their cost is under Carver's limit; each speed catches a different trend length | Unrounded target 16y: fast speeds earned 196k of 270k; reference model 16y: Sharpe 0.30 against 0.15, 2,079 a year of cost saved |
| Fixed 1 percent buffer | 1.1 | Trades whenever tracking error exceeds 1 percent | One contract of most names is wider than the band; 337 to 454 round trips a year at full size, and costs took back the gain before costs | Against the starting book: 16y 0.28 against 0.09, 2y -0.48 against -0.35, 17-run mean 0.16 against 0.18; with micros 0.15 against 0.31 |
| Minimal buffer | 1.1 | No held-only lumpy contract sets the band | The lumpiest searched contract then sets the band (a median of 0.055, still over five times Carver's 1 percent), so the band barely narrows and tracking did not improve (8.2 against 8.3 percent); it changes only which held lumpy positions stay | Chosen setup: mean 0.230 against 0.274; starting book 16y 0.31 against 0.09 |
| Strategy-eight static buffer | 1.1 | A fixed buffer per instrument | Nearly doubles the churn | Reference model: 5y 592, 16y 637 round trips |
| Search from zero, as first designed | 1.2 | Greedy from zero each day | Near-equal answers alternate day to day, and each switch is a trade | First reference model: 588 against 28 reversal pairs a year |
| Wrong-signed rows moved by the buffer (Carver's full method) | 1.3 | No separate close | Under this wide buffer rows sat against the forecast a median 4 cycles against 1, and gold and silver ran at 2.2 and 2.7 times target | Book with the sign close off: 0.38 against -0.02 on the seven screen windows; change 2 on the 16y: 0.41 against 0.09, 140 against 115 round trips |
| Multiplier above 100 | 1.4 | Weighs cost more | Outside Carver's range, where he warns that trading collapses onto the cheapest instruments; at 300 livestock was in a position on 2 percent of the 16y's days against 68 percent at 50 | Engine loop: 300 to 500 needed for the low 100s of executions, at 25 to 60 percent more tracking error |
| Carver's optimiser covariance alone | 1.5 | Follows recent risk | It follows recent risk, so the buffer and the substitutions move with each change in volatility; the 756 window was set on 2026-09-21 to keep the optimiser on the prices the trend sleeve then held, and keeping the sample over Carver's estimator is a measured result | Paired by start: 0.13 below the chosen setup on the 5y and 0.15 below on the 16y, about level elsewhere |
| Carver's optimiser covariance with the edge step | 1.5 | As above, with the edge step | A concentrated book, as the edge step | Against the chosen setup: mean 0.302 against 0.274; 5y 0.082 against 0.273; 16y 0.240 against 0.300 |
| Shorter covariance lookbacks (160, 252, 504) | 1.5 | A shorter sample | No smooth response between neighbouring lookbacks | Means 0.240, 0.124 and 0.199 against 0.274 |
| Carver's overlay limit values | 2.1 | His 99th percentiles on his portfolio | Measured on 100 instruments; they bind at his portfolio's frequency | Reference model 16y maximum drawdown 37.4 against 38.7 percent |
| Carver's position limit | 2.4 | Caps one instrument's share of risk | Not built by design: the cap of 2 already bounds each name, so it would be a second, overlapping per-name cap on a chosen 10 or 20 percent weight; the cap bounds notional where Carver's limit bounds a name's risk, and the overlay's risk limits bound the book's risk; the open-interest part does not bind at 500,000 and there is no open-interest data | Reference model at 10 percent: one held-book change and three clipped rows in 16 years; 16y 0.46 against 0.37, 5y 0.42 against 0.55; at 20 percent identical |
| Equity slow rule off | 3.1 | Equity shorts on the fast speeds alone | On this book's record those shorts lost (113 of 190 equity-index shorts were opened on the fast speeds alone; equity shorts as a whole lost 121,403 over 16 years), which fits Carver's finding that equities are the weakest trend class | Full size: 0.39 against -0.02, equities 16y +28k against +49k; micro book 0.17 against 0.30 |
| Carry 60/40 | 3.2 | A second return beside trend | Not yet tuned on the data the book trades (it needs two real contract prices on one date, and was measured on series with holes and stand-in gold and silver micros); carry earned about nothing on these instruments (16y Sharpe 0.02), so the blend adds no second return; why the traded blend's Sharpe falls is unmeasured | Micro book, 17 runs: -0.08 against 0.28 |
| Minimum-capital selection | 4.1 | Seven instruments | Gives up diversification (IDM 2.10 against 2.50 on Carver's table) | 16y 0.12 against 0.08; -50.7 percent over the 10y |
| Micro FX; the copper micro | 4.2 | Micros beyond gold and silver | FX is not where the book is coarse, so micro FX only turns one-lot adjustments into trades; the copper micro adds trades where copper is not the coarse contract and has only been listed since 2022-05-02 | Micro FX 168 to 256 round trips a year against 115 to 192; gold, silver and copper micros 0.13 against 0.30 |

## 8. Superseded decisions to know

- The first limit values of 2026-10-01 (0.45 / 0.90 / 0.80 / 8.0 / 6.0) are superseded by the Carver-method set
  (section 2.1).
- The design's sample overlay covariance is superseded by Carver's overlay covariance (2026-10-27); the sample covariance
  stands for the optimiser.
- The holding of 2026-09-20 that a from-zero search is Carver's design was reversed on measurement by the search from
  the held book on 2026-09-27.
- The design's "no micros" line was read on 2026-10-10 as turning down moves of other contracts to micro size; gold and
  silver micros were adopted on 2026-10-26 and 2026-10-27.
- The design's vendor-series change-bar rules are superseded by per-contract rolls (section 5.1).
- The design's trade-bar figures (172 / 155 / 158 round trips) were superseded as expectations on 2026-10-07.
- The lookback switch, first kept, was dropped from the release on 2026-10-29.

# How the futures book is set, and why

The futures book is one Carver trend-following sleeve, long and short, on 36 instruments in seven sectors, sized to a
20 percent risk target on Carver's half-compounded capital of 500,000. Each instrument runs up to six trend speeds;
twelve contracts lose their fastest one to three by Carver's cost test, and an equity-index short stands only when both
slow speeds agree. The unrounded target goes through Carver's risk overlay, with limits set by his own method on this
book's history, and then through one whole-contract optimisation pass that starts from the book already held. Gold
and silver trade as micros, the four equity indices as micros from their 2019 listing, and everything else full size.
Carry is built and shipped switched off. Every setting has the same economic logic behind it: at 500,000 most targets
are a fraction of one contract, so the book can hold its risk target only if it trades whole contracts sparingly, on
contracts it can hold near target, and otherwise keeps to Carver's method, with a short list of departures, each for a
stated reason. This document calls the setup in force the chosen setup; it was settled on 2026-10-29, when the net limit was set at 5.50, and the
conservative template at 4c92eb1b8d carries it. For each part of the book this document gives the setting, what it does,
why it fits a 500,000 book of whole contracts, the data, and where the mechanics are explained. The release that put
the setup on main is section 16.

Mechanics live in the tracked documents, and this document points to them rather than repeating them:
[`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md) (the method, stage by stage, with worked examples),
[`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md) (the daily rebalance step by step),
[`RISK_MODULES.md`](../../RISK_MODULES.md) (the overlay as a module), [`COST_MODEL.md`](../../COST_MODEL.md),
[`FUTURES_ROLLS.md`](../../FUTURES_ROLLS.md), [`LIVE_RUN_CYCLE.md`](../../LIVE_RUN_CYCLE.md),
[`CONFIG_GUIDE.md`](../../CONFIG_GUIDE.md) and [`SYSTEM_ARCHITECTURE.md`](../../SYSTEM_ARCHITECTURE.md). Each lever,
with what it does, the economic reason it is or is not in the book and its full results, is in
[../03_tuning_and_results/LEVERS.md](../03_tuning_and_results/LEVERS.md); every departure from Carver's method is in
[../02_decisions/CARVER_DEPARTURES.md](../02_decisions/CARVER_DEPARTURES.md); the roll rule's cases are in
[ROLLS_AND_DATA.md](ROLLS_AND_DATA.md).

Code citations are paths under `services/trade-ngin/` in AlgoGators/algogators at commit 4c92eb1b8d, main's merge of
the import of trade-ngin d6f998da (`AlgoGators/algogators#98`). The three config files of the conservative template
(`config_template/portfolios/conservative/portfolio.json`, `config_template/portfolios/conservative/risk.json`, `config_template/defaults.json`) are
called the portfolio, risk and defaults config. AFTS is Carver's *Advanced Futures Trading Strategies*. Sharpe figures
are the account Sharpe after costs on the whole-contract book unless a sentence says otherwise; a window's figure is the
mean of five start dates, except the 16-year window, which is one run.

---

## 1. The book at a glance

| Part | Setting | Why, in one line | Mechanics |
|---|---|---|---|
| Instruments | 36 in seven sectors; MGC and SIL for gold and silver, the four equity micros from 2019-05-06, everything else full size | which market makes the next large trend cannot be known in advance, and Carver's dynamic optimisation carries a broad universe on a small account; micros where one full contract is far too coarse to hold near target (section 9) | [`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "The instrument universe" |
| Signal | six EWMAC speeds, long and short, Carver's scalars, attenuation, FDM, cap 20; 18 rules removed by cost; the equity slow rule | Carver's trend signal; a speed goes only where its cost eats its return; one declared asymmetry, because equity shorts opened on the fast speeds alone lost on this book | same, "The trend rules: EWMAC, scaling, capping" to "The equity-index slow rule" |
| Sizing | tau 0.20, IDM 2.5, half compounding from 500,000 | Carver's table values and Carver's live-account capital rule | same, "The risk target and the sizing capital" |
| Overlay | five limits 1.75 / 4.75 / 3.00 tau, gross 7.25, net 5.50, on Carver's covariance; one multiplier, applied once | Carver's overlay, with limits from his 99th-percentile method on this book | [`RISK_MODULES.md`](../../RISK_MODULES.md), section "The Carver overlay" |
| The one pass | greedy whole-contract search from the held book, cost multiplier 100, sample covariance of 756 closes, granularity buffer, fraction step, forecast-sign close deferred under 2, per-name cap 2, trim of five | strategy twenty-five, adapted to a book where one contract is often several no-trade bands wide | [`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The one pass" |
| Costs | broker fee per contract, spread widened by volatility, square-root impact on the bar's volume | the cost the account pays, priced the same in the search as in the books | [`COST_MODEL.md`](../../COST_MODEL.md), section "One formula" |
| Rolls | per-contract bars, the roll rule of 2026-10-23, front month of each root's cycle, `held` on an unconfirmed vendor switch | the vendor's series rolls too late for a live book, so the book rolls by its own rule; it is priced on the contract it holds | [`FUTURES_ROLLS.md`](../../FUTURES_ROLLS.md); [ROLLS_AND_DATA.md](ROLLS_AND_DATA.md) |
| Bars | a session classifier per symbol and date | the feed carries Sunday rows, stubs and corrupt prints | [`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "The bars the strategy consumes" |
| Calendars | live runs every calendar day; NYSE calendar for the runners, CME calendar for the roll rule | each symbol answers from its own bars; rolls count the exchange's own trading days | [`LIVE_RUN_CYCLE.md`](../../LIVE_RUN_CYCLE.md), section "The previous day, and closed days" |
| Carry | built, off | not yet tuned on the data the book trades: it needs two real contract prices on one date, from the per-contract bars, and was measured while the series still had holes and the gold and silver micros were stand-ins | [../03_tuning_and_results/CARRY.md](../03_tuning_and_results/CARRY.md) |
| Run modes | a live run needs a date; no date exits 2 | a production run and a replay of the same date must behave the same | [`LIVE_RUN_CYCLE.md`](../../LIVE_RUN_CYCLE.md), section "The two run modes" |
| Storage | the day's book, fills, P&L, the overlay's readings and the final forecast per date | each stored day can be explained and re-run | [`LIVE_RUN_CYCLE.md`](../../LIVE_RUN_CYCLE.md), section "What each table holds" |

---

## 2. What the chosen setup is, and why each of its settings

The chosen setup makes two choices in the optimiser: it reads a sample covariance of each symbol's last 756 daily
closes, and its no-trade buffer moves the book a fraction of the way to the optimised answer, as Carver writes it.
Everything else is the same in the chosen setup and in the setups measured beside it: gold and silver micros on the
per-contract rolls, the granularity buffer, the forecast-sign close, the overlay on Carver's
covariance, carry off, the equity slow rule on, `held` on a vendor switch bar, and 500,000 of capital. Its limits are
its own 99th-percentile limits, net leverage 5.50.

**The configuration of record is the tracked template at 4c92eb1b8d.** The conservative template carries the chosen
setup: limits 1.75 / 4.75 / 3.0 / 7.25 / 5.5 (`config_template/portfolios/conservative/risk.json:8-9`, `:14-16`, mirrored in
`risk_reporting` at `:28-29`), `overlay_covariance` carver (`:19`), the MGC and SIL pairs, `per_contract_rolls` true
and `per_contract_switch_bar` held (`config_template/portfolios/conservative/portfolio.json:33-34`, `:65`, `:67`). It
reached main in commit c42dbbe6f8 of `AlgoGators/trade-ngin#282`. The chosen setup's results were run before the release
on the same settings: the template's portfolio and risk configs have no key that differs from those runs, and the
defaults config differs only in the database block and the window keys. A re-run of the chosen setup's year from
2025-10-05 on the stored settings, changing only the database keys, equals the stored run in every stored cell, and the
anchors reproduce from the template itself.

**Two covariances, two jobs.** The optimiser and the overlay read different covariances, and the difference is
deliberate. The optimiser's covariance decides which whole contracts best track the target, and it sets the width of
the no-trade buffer through each contract's own risk; the chosen setup uses a sample of each symbol's last 756 closes
(`kOptimiserBars`, a literal at `src/strategy/trend_following.cpp:665`). The overlay's covariance decides whether the
whole book is carrying too much risk today; it uses Carver's estimator, the sizing volatility of each symbol with
exponentially weighted weekly correlations over a 26-week span, read over each symbol's last 300 bars
(`include/trade_ngin/risk/overlay.hpp:60-72`; `src/strategy/trend_following.cpp:658-664`). The optimiser choices are
what separate the chosen setup from the setups measured beside it; every setup of the final grid ran Carver's overlay
covariance.

### 2.1 The choices, the options tested, and why each option was not taken

Full results for every option are in [../03_tuning_and_results/LEVERS.md](../03_tuning_and_results/LEVERS.md). Where an
option's code is not on main and is kept, its cell names the archive branch of AlgoGators/trade-ngin that holds it.

| Setting | Value | What it does, and why it fits this book | Options tested: what each does, why it does not fit, and its data |
|---|---|---|---|
| Optimiser covariance | sample covariance of each symbol's last 756 closes | the engine's own estimate, from the price series the engine already reads. A three-year equal-weight sample of daily adjusted returns moves slowly, so the search's substitutions and the one-contract risks that set the buffer change little from day to day. Keeping the sample over Carver's estimator, which follows recent risk, is a measured result, with the data in the next column | Carver's estimator from strategy twenty-five (a 32-day volatility with six months of weekly correlations, AFTS p.417 and Appendix B p.641; the variant tested used the book's blended sizing volatility with the overlay's weekly correlations): it follows recent risk, so the buffer and the substitutions move with each change in volatility. Data, paired by start: alone it was 0.13 below the chosen setup on the 5y and 0.15 below on the 16y, and about level elsewhere; with the edge step, section 3 (`archive/keep-do-not-delete/s25-one-pass`) |
| Covariance window | 756 closes, fixed in the code | about three years of closes, set on 2026-09-21 to keep the optimiser on the prices the trend sleeve then held (its history was capped at 756 at the time), because an equal-weight sample's answer moves with its length | 160 closes (about Carver's six months once dates are shared), 252 and 504, each on three bases: a shorter equal-weight sample follows recent risk more closely. Data: none raised its base's five-window mean, and results moved by up to 0.587 on one window between neighbouring lengths (`archive/keep-do-not-delete/levers`) |
| Buffer step | `fraction`: every row moves the same fraction of the way, then rounds | Carver's own adjustment (strategy twenty-five): the whole book moves toward its target together, keeping its breadth | `edge`, one contract at a time to the buffer's edge: it fills the band with a few large contracts (12 names held a day against 15 to 17), so a book that already holds well under half its 36 names holds fewer still, and its result turns on single episodes. Data, on the sample covariance against the fraction step: 2y -0.31, 5y -0.22, 10y and 16y -0.05, year +0.14 (`archive/keep-do-not-delete/step-edge`). A size-neutral variant was built and dropped before its runs (`archive/keep-do-not-delete/levers`) |
| Buffer width | the larger of 5 percent of tau and the largest one-contract risk in the book | one contract of most futures is wider than Carver's 1 percent band (ZN 1.1 bands, CL 5.2, GC 12.9), so the band is set to the smallest trade the book can make, and the book trades only when a whole contract closes a gap worth that trade | Carver's fixed 1 percent: a band narrower than one contract makes the book trade whenever a target moves by part of a contract, so it halves tracking error and pays for it in costs. Data: at full size it churned 337 to 454 round trips a year, and the four fixed-buffer setups of the October sizing round together 338 to 544; on the 17-run matrix its mean was 0.16 against 0.18 for the full-size starting book of the first tuning round. Account size alone does not explain that churn: Carver's own 500,000 test at 1 percent turned over about as much as his 50 million book (48.8 against 46.5, table 124, p.433); his test searched 100 instruments, not 36, and blended carry; his backtest charges the current cost per contract, adjusted for the volatility of each historical period (p.428; strategy five), and the book adds a square-root impact term on the bar's volume; which difference drives the churn here is unmeasured. A minimal variant that drops held-only rows from the band: when held contracts stop setting the band, the lumpiest searched contract sets it instead (a median of 0.055, still over five times Carver's 1 percent), so the band barely narrows and tracking did not improve (16y: 139 round trips and 8.2 percent tracking error against 115 and 8.3 percent). Data over the five windows: mean 0.230 against 0.274, the 5y 0.107 lower and the year 0.245 lower (`archive/keep-do-not-delete/s25-one-pass`) |
| Wrong-sign rule | a holding against its forecast's sign is closed to flat before the search, deferred while the forecast is under 2 | a position held against its own forecast is a position the system expects to lose on. In Carver's full method the full close at a sign change is the turnover floor that his buffer solves (AFTS, strategy twenty-five, p.430): his buffer moves a wrong-signed row toward zero like any other. Here the buffer is one lumpy contract wide and the search starts from the held book, so without the close a wrong-signed row sat against its forecast a median 4 cycles (longest 217) against 1 (longest 14) with it; the separate close is the book's own departure. Turnover did not rise with the close (140 round trips a year without it on the 16y, 115 with it). The band: the close waits while the opposite forecast is under 2 (on Carver's scale, where the average absolute forecast is 10), a weak reading of a sign change; its measured effect on round trips is near zero | Carver's buffer treatment (the book with the sign close off), where the buffer moves a wrong-signed row like any other: it keeps positions against their forecasts for longer (median 4 cycles against 1) and far from target (32 percent of name-days over twice target, gold at 2.2 times and silver at 2.7), so the book no longer tracks the strategy it sizes. Data: tracking error 20.9 percent on the 7 screen runs, where its mean was 0.38 against -0.02 for the full-size starting book of the first tuning round (`archive/keep-do-not-delete/s25-one-pass`) |
| Search | greedy, one contract up or down, from the held book; cost multiplier 100 | Carver's cost term already prices trades against the held book, so the difference is where the greedy path starts and stops: from zero it lands on a different near-equal answer from day to day and a lumpy book rebuilds itself; from the held book it stops at the held book unless one contract's step helps. The reference model made 187 reversal pairs a year from the held book against 320 from zero, with lower tracking error. The multiplier sets the book's cost budget, how much tracking error the search accepts to save a unit of cost. The cost penalty is the one lever that reliably cuts this book's churn toward its trade bar of about 150 round trips a year, a bar set near what the long-only book already traded (111 to 167), because the excess trading was resizes: the book's own round trips per traded instrument (3 to 11 a year) were already inside the range Carver expects. It stops at 100, the top of Carver's 10 to 100 range (AFTS, strategy twenty-five, p.428), because above it trading collapses onto the cheapest instruments, as Carver warns (p.430). The account size does not enter, because both terms of the objective scale with capital | from zero, as Carver's greedy starts: more reversals with no lower tracking error; multiplier 50: more round trips (16y 316 against 250 on the reference model); 300: livestock in a position on 2 percent of the 16y's days against 68 percent at 50 (2026-10-05); 300 to 500 reached about 150 round trips a year only at 25 to 60 percent more tracking error |
| Fast speeds in high volatility | all speeds run at every volatility | Carver's attenuation already scales forecasts down when volatility is high; the switch is not Carver's form (he attenuates and never switches off), was found on the period it fixes, and lost over 2011 to 2020 | switching the three fastest speeds off above the 0.75 volatility quantile, alone or with the equity indices on their slow speeds only: section 3 |
| Overlay covariance | Carver's (`overlay_covariance` carver) | Carver's own estimator for the overlay (AFTS, tactic four): each contract's sizing volatility, the estimate its position is sized on, with correlations of weekly returns (Saturday to Friday) exponentially weighted over a 26-week span. Weekly returns avoid the low correlations that daily closes taken hours apart give (his reason). The 26-week span weights recent weeks most, so a rise in correlations shows within weeks. The limits are set by his 99th-percentile method on this same estimator, so estimator and limits go together | the sample covariance of the last 252 dates: an equal-weight sample spreads a rise in correlations over a year. Carver notes that the method "isn't too sensitive to how the covariance matrix is estimated" (footnote 289, p.622). Data, each estimator at its own limits: the year 0.06 against 0.40, the 2y 0.27 against 0.33, the 10y 0.26 against 0.32, level on the 5y, and the one 16y run 0.44 against 0.17, with its own looser limits; chosen 2026-10-27 |
| Overlay limits | R 1.75, R_jump 4.75, R_shock 3.00 tau; gross 7.25; net 5.50 | Carver's method: each limit is the 99th percentile of its reading on the book's own history with the overlay off, so each reading alone is over its limit on about 1 percent of cycles; the five together cut the book on 4.1 percent of the 16y's cycles, Carver's own expectation for several multipliers ("around 4%", AFTS, tactic four, p.606) | net 5.75: the method rounds the book's own reading, 5.5926, to 5.50. Data at 5.75: mean 0.275 against 0.274; 16y +0.039, 10y -0.010, 5y -0.028, 2y and year identical |
| Carry | off (no `carry` block) | not yet tuned on the data the book trades: it needs two real contract prices on the same date, which only the per-contract bars give, and it was measured while the continuous series still had holes and the gold and silver micros were stand-ins; the final retune ran with carry off. Known beside that: carry alone earned a 16y Sharpe of 0.02 on the unrounded book before costs, so the blend brings no second return; it roughly halves round trips and tracks its unrounded book a little more closely, and why its traded after-cost Sharpe falls is unmeasured | carry at Carver's 0.4 weight beside trend 0.6. Data: mean 0.28 off against -0.08 on over 17 runs, after costs; section 12 |
| Equity slow rule | on: an equity-index short needs both slow speeds negative | adopted on 2026-10-05 on this book's record: 113 of 190 equity-index shorts were opened on the fast speeds alone, and equity shorts as a whole lost 121,403 over 16 years, which fits Carver's finding that equities are the weakest trend class (median Sharpe 0.03; AFTS, strategy nine, table 37, p.206); a declared departure from his advice against asymmetric rules (strategy twelve, p.268) | off. Data: 0.17 against 0.30 over 7 runs with the gold and silver micros; section 4.3 |
| Rule removals | 18 rules on 12 contracts | Carver's per-instrument cost test (strategy nine) | blanket speed cuts: section 4.2 |
| Instruments | MGC and SIL from listing; every other contract full size but the four equity micros | a micro where the full-size contract cannot be held at 500,000: gold and silver want about 0.05 contracts at an average forecast | more micros or Carver's small-account selection: section 9 |
| Rolls | `per_contract_rolls` true, `per_contract_switch_bar` held | section 7 | the vendor's series: section 7 |

The `held` or `vendor` setting and the `per_contract_rolls` switch are both keys on main. Whether to remove them is an
item in
[../06_open/FUTURE_WORK_ADVICE.md](../06_open/FUTURE_WORK_ADVICE.md), section "Open design questions and their issues".

Everything the tuning tried and did not choose stayed out of the release: the strategy-twenty-five switches (buffer
rule, sign rule, optimiser covariance), the edge step, the covariance window setting, the high-volatility switch and
every tested configuration. The chosen setup ran each of them at its default, which is exactly the code path on main.
Carry stayed in the release, switched off.

---

## 3. The chosen setup's results, and the setups measured beside it

**Results:** 16y 0.300, 10y 0.290, 5y 0.273, 2y 0.102, year 0.407; mean of the five windows 0.274. The target was an
after-cost Sharpe of 0.4 to 0.7 on every window; no setup of the final rounds reached it on every window, and the
chosen setup reached it on the year. What drives these results is in
[../03_tuning_and_results/RESULTS.md](../03_tuning_and_results/RESULTS.md). The changes measured beside the chosen
setup were judged by rules fixed before any result was seen (the five-window mean had to rise and no window could fall
by more than 0.1), because a rule chosen after the results would fit the period; how the setups were compared is in
[../03_tuning_and_results/TUNING_STORY.md](../03_tuning_and_results/TUNING_STORY.md).

**Carver's optimiser covariance with the edge step.** What it does: the covariance follows recent risk, and the edge
step fills the buffer with a few large contracts, so the book holds about 12 names a day against 15 to 17 and its
result turns on whichever large contracts it holds. Why it does not fit: on a book that already holds well under half
its 36 names, concentrating further gives up the diversification a trend book is built on, and the edge step is not
Carver's adjustment (strategy twenty-five moves every row the same fraction). Data: five-window mean 0.302 against 0.274; 2y 0.245 against 0.102, 10y
0.308 against 0.290, year 0.636 against 0.407, 5y 0.082 against 0.273 and 16y 0.240 against 0.300; mean drawdown 30.9
percent on the 5y and 20.7 percent on the 2y against 22.0 and 14.9. The five 2y starts all share 2024 to 2026 and the
year starts lie inside it; its 2y difference and its 5y difference are each one energy episode held by the
concentrated book.

**The equity fast-speed cut and the high-volatility switch together.** What it does: the equity indices run on their
16/64 speed and slower only, and the three fastest speeds switch off on high-volatility bars. Why it does not fit: the
equity cut removes speeds by asset class where Carver removes a speed only on a contract whose cost is over his speed
limit, and no equity speed is (their 2/8 costs 0.018 to 0.049 a year against 0.15), so a cut would be chosen on 2025's
losses, the over-fitting Carver warns of (AFTS, strategy nine, p.207 to 208). The high-volatility switch is not Carver's
form: he attenuates forecasts when volatility is high and never switches a speed off; the switch was found on the
period it fixes, and it lost over 2011 to 2020. Carver's method also sets each book's
limits from its own history: this setup's own 99th-percentile limits are looser (R_shock 3.25 tau, net 5.75), and at
the chosen setup's tighter limits the overlay cut it on 241 of the 16y's cycles against 193 (5.1 percent, about Carver's
rate: he expects "just under 5%" for strategy eleven, p.613). Data: the rules raised the unrounded book on every run,
and in the study of the signal outside the engine where they were found, neither rule's gain was
statistically significant at the 5 percent level; at the chosen setup's limits its mean was 0.332; at its own limits it
was 0.242 against the chosen setup's 0.274, with the year at 0.041 against 0.407. Two simpler forms: the equity indices without their
two fastest speeds, mean 0.339 with the 10y 0.050 lower, and the equity indices on 16/64 and slower only, mean 0.345
with the 5y 0.152 lower. On the full-size book the same speed rules' gain sat in one gold contract held about 450 days
at six to eight times its target; the gold and silver micros remove that holding.

The chosen setup at net 5.75 (mean 0.275) measured level with it; the method rounds the book's own reading, 5.5926, to
5.50 (section 5.2). The chosen setup was chosen on 2026-10-29.

---

## 4. The signal

### 4.1 Volatility, the trend rules and the multipliers

The signal is Carver's: volatility by strategy three's blend of a 32-day EWMA and its long-run mean, six EWMAC speeds
from 2/8 to 64/256 scaled by his fixed scalars, each capped at 20, attenuated when volatility is high (strategy
thirteen), averaged with equal weights, multiplied by the FDM for the number of speeds and capped at 20 again. The
mechanics, with real-day worked rows, are in [`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md),
sections "Volatility", "The trend rules: EWMAC, scaling, capping" and "Forecast weights, the diversification
multiplier, and rules removed by cost", and in [`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md),
section "Worked forecasts". The volatility blend stays for Carver's reason: volatility mean-reverts, and a short estimate
alone builds large positions in the calm before a crisis (AFTS, strategy three, p.89 to 90).

The book trades long and short. Carver's trend strategies are long and short from strategy six on (AFTS, "Strategy
six: Slow trend following, long and short"); a long-only trend book is a different strategy with a standing market bet
in it.

Where the estimator differs from Carver, the reason is the data. The variance is seeded and floored so a flat series
never gives zero volatility, and the long run is the mean of the trailing 2,520 values. The sizing volatility is
annualised by the bars counted over the trailing 256, not by 16, because series with Sunday rows have about 313 bars a
year (`src/strategy/trend_following.cpp:586-588`); the forecast keeps Carver's 16. The attenuation ranks the short
volatility against its own trailing 2,520 values, where Carver ranks relative volatility over the whole history: one
fixed window, no second average. These are listed with the Carver comparison in
[../02_decisions/CARVER_DEPARTURES.md](../02_decisions/CARVER_DEPARTURES.md).

**The scalars, the FDM and the IDM are Carver's table values, and the data agree with them.** The first tuning round
compared each with what the filled data implies: the IDM comes out at 3.13 over 36 instruments; the implied FDM ranges
(1.25 to 1.33 for six speeds, down to 1.09 for three) cover the table's 1.26 to 1.08; and the average absolute scaled
forecast is 9.18 to 9.88 against Carver's 10. Nothing was changed. The IDM stays at Carver's table value, 2.5 for 30
or more instruments (table 16), which is also his cap: he caps the IDM at 2.5 "to avoid excessive leverage on highly
diversified portfolios" and notes that the cap undershoots the risk target (AFTS, Appendix B, p.644). Carver's target
is meant to be realised on average; the book runs about 18 to 19 percent on the sizing capital against 20, the gap left
by whole-contract rounding and the buffer. Raising the IDM would scale every position, including those already at
their target, which raises the volatility target rather than filling the rounding gap (an IDM of 3.13 gave 24.2
percent volatility at the same Sharpe). The multipliers in force are the code's table
(`include/trade_ngin/strategy/trend_following.hpp`, looked up by
the number of speeds in `src/strategy/trend_following.cpp`); the defaults config's `fdm` key is not read
([`CONFIG_GUIDE.md`](../../CONFIG_GUIDE.md), section "Keys that are present and not read").

### 4.2 The speed limit: rules removed by cost

Carver removes a trading rule from an instrument when its yearly cost in Sharpe-ratio units is over his speed limit
(AFTS, strategy nine). The book does the same with his table of rule turnovers (table 35), its own 16-year median costs
and his 0.15 limit, and the list was set on 2026-10-09:

| Speeds removed | Contracts |
|---|---|
| 2/8 | 6M, GF, HE, LE, NG, ZL, ZS |
| 2/8, 4/16 | 6L, KE, ZC, ZW |
| 2/8, 4/16, 8/32 | ZR |

Eighteen rules on twelve contracts (`config_template/portfolios/conservative/portfolio.json:45-58`). Every contract
keeps at least its 16/64, 32/128 and 64/256 speeds. The economics: a fast speed earns its trend return only if that
return survives its trading cost, and on these contracts it does not. Measured on eighteen 5-year start dates the list
had no performance effect either way (9 of 18 ahead, median -0.01 on the sizing capital); it is in the book because it
is Carver's procedure in exactly this setting. The list is the authority: `scripts/trading_rule_costs.py` shows how it
follows from the recipe, `--check` reports whether re-measured costs still give it, and a list that comes out different
goes to a decision, never edited to match (template note `_trading_rule_removals_note`, `:59`). The equity micros keep
all six speeds: Carver's per-instrument cost test keeps them (their 2/8 costs 0.018 to 0.049 a year against the 0.15
limit), and a cut chosen on 2025's losses would be the over-fitting Carver warns of (strategy nine, p.207 to 208).

The list was measured on full-size GC and SI and not re-measured when MGC and SIL replaced them (2026-10-27); MGC and
SIL run all six speeds, and their fee per unit of risk is higher. Re-measuring the list for the micros is an item in
[../06_open/FUTURE_WORK_ADVICE.md](../06_open/FUTURE_WORK_ADVICE.md), section "Open design questions and their issues".

**Blanket speed cuts stay out** (2026-10-03). The six speeds are Carver's design, each catching trends of a different
length; he removes a speed only on a contract where its cost is over his speed limit, which the book applies (the
table above). A blanket cut removes the fast speeds also on the contracts where they pay. Data, each with its book and
window: on the unrounded target book over 16 years the fast speeds earned 196k of 270k; on a reference model run
outside the engine (16 years, long and short, fixed sizing on 500,000), cutting 2/8 and 4/16 everywhere took strategy fills from 607 to
329 a year and saved 2,079 dollars a year of cost (0.4 percent of the account), against a return difference of 3.1
points a year (Sharpe 0.30 against 0.15); and a speed set chosen on one half of the history did not hold on the other
half.

### 4.3 The equity slow rule

For M2K, MES, MNQ and MYM (and their predecessors before listing), a negative combined forecast stands only when the
32/128 and 64/256 scaled forecasts are both negative; otherwise it is 0. A positive forecast and every other symbol are
untouched, and a held short whose forecast becomes 0 leaves through the search and the buffer, not by a forced close.
Key: portfolio config `equity_slow_rule`, required on a futures book (`:21`). Mechanics and a worked run of days:
[`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "The equity-index slow rule".

The rule was adopted on 2026-10-05 on this book's record: 113 of 190 equity-index shorts were opened on the fast speeds
alone; equity shorts as a whole lost 121,403 over 16 years. That fits Carver's finding that equities are the weakest
trend class: across equity instruments his multiple-speed trend strategy has a median Sharpe of 0.03 (AFTS, strategy
nine, table 37, p.206). It is the one declared departure in the signal: Carver declines asymmetric adjustments by asset
class, advising against "any kind of adjustment that is asymmetric", because his results by asset class are not
statistically significant (strategy twelve, p.268). As an economic reading only, his "bouncing cat" (figure 53, p.266)
shows equity indices rallying after strong negative fast trends, though that bounce is in the two fastest speeds and the
rule keys on the slow ones. The re-measure with the gold and silver micros: rule off, 0.17 against 0.30
over 7 runs, with no gain in the equities; an earlier full-size difference with the rule off came from the metals, not
the equities. On the unrounded book the rule is worth about 20 points over 16 years. The same idea for every instrument
is not in the book: Carver's evidence is for equities only. With carry on, the rule applies to the trend part only
(`src/strategy/trend_following.cpp:735-744`).

---

## 5. Sizing, the overlay and the per-name cap

### 5.1 Sizing and half compounding

Each contract's unrounded target is Carver's position formula at tau 0.20 and IDM 2.5, long and short, with no buffer,
minimum position or clamp inside the strategy: the one pass does all rounding. The book is sized on the account it
trades, 500,000. Tau 0.20 is Carver's own risk target, a conservative value under his half-Kelly estimate (AFTS, strategy
two, p.72); at 500,000 a lower tau rounds more names to zero, so drawdown is lowered through the book's construction,
not a smaller target. The capital it sizes on is Carver's half compounding (AFTS, tactic three): 500,000 less the drawdown of
cumulative settled net P&L from its peak, never above 500,000, in the backtest as in live (decided 2026-10-05). The
economics: size falls at once after a loss, as Carver requires of a live account, and profits above the start are set
aside rather than re-risked, so the book keeps the risk it was designed for. Fixed sizing, full compounding, a
proportional drawdown form, a half-rate form, a floor and a yearly reset were measured or argued and not adopted. The
proportional form keeps more risk on in a drawdown from a high peak (it cuts sizing by the drawdown's share of the peak
account, where half compounding cuts it by the full dollar loss), and it is not Carver's rule; data: on eighteen start
dates it was higher in 8 and deeper in drawdown in 13. Mechanics and a worked
table: [`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "The risk target and the sizing capital" and
"The unrounded target"; [`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The sizing capital". The
keys `sizing_mode` and `starting_capital` are required and checked but the sizing reads `initial_capital`
([`CONFIG_GUIDE.md`](../../CONFIG_GUIDE.md), section "Keys that are present and not read"). How to read a result on the sizing
capital and on the account is in [../03_tuning_and_results/RESULTS.md](../03_tuning_and_results/RESULTS.md), section
"Two ways of reading the book: sizing capital and account".

Instrument weights are sector-equal over the seven sectors, a symbol capped at 50 percent of its sector, the rest
renormalised: the metadata carries one sector field, and a one-symbol sector (crypto, MBT alone) must not take a whole
seventh of the risk. The weights are computed once per run and are the same on every date (2026-10-07). Mechanics:
[`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "Instrument weights and the IDM".

### 5.2 The risk overlay

The overlay reads the capped target book and returns one multiplier, the smallest of limit over reading across five
readings (expected risk, risk at each symbol's 99th-percentile volatility, risk with every correlation at 1, gross
leverage, net leverage), and applies it once to every free row; nothing feeds back from the optimiser. Held rows count
at their held quantity and are not scaled. This is Carver's overlay (AFTS, tactic four) with a net leverage reading
added. Mechanics: [`RISK_MODULES.md`](../../RISK_MODULES.md), section "The Carver overlay";
[`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "The per-name cap and the risk overlay"; a real day in
[`RISK_MODULES.md`](../../RISK_MODULES.md), section "A worked day".

| Limit (risk config) | Chosen setup | At tau 0.20 | The chosen setup's unrounded 99th percentile |
|---|---|---|---|
| R_max | 1.75 tau | 0.35 | 1.6847 |
| R_jump_max | 4.75 tau | 0.95 | 4.8233 |
| R_shock_max | 3.0 tau | 0.60 | 3.0581 |
| max_gross_leverage | 7.25 | 7.25 | 7.1847 |
| max_net_leverage | 5.5 | 5.50 | 5.5926 |

**How the limits are set.** Each is the 99th percentile of the overlay's reading of the target book over the complete
cycles of the setup's own 16y run with the overlay off (the per-name cap of 2 stays), rounded to the nearest 0.25:
Carver's method, so each reading alone is over its limit on about 1 percent of cycles and the limits describe this book
rather than Carver's. Because the five readings bind on different cycles, the book is cut on 4.1 percent of the 16y's
cycles (193 of 4,738), in line with Carver's "around 4%" for four multipliers (AFTS, tactic four, p.606) and the "just
under 5%" he expects for strategy eleven (p.613). Carver's own values (1.5 / 3.75 / 3.25 tau,
leverage 20) are the limits of his portfolio, and he notes another set of instruments needs others. Net 5.5926 is 0.032
under the 5.625 midpoint, so it rounds to 5.50; the limit was set there on 2026-10-29, because the method rounds to it,
and 5.75 measured level with it (section 2.1). At 5.50 the net limit binds on one 2y cycle (2026-06-10, multiplier
0.99837) and moves no contract. The method's set was accepted on 2026-10-26 and became the final setup on 2026-10-27;
it replaced the first limits of 2026-10-01, 0.45 / 0.90 / 0.80 / 8.0 / 6.0 (2.25 / 4.5 / 4.0 tau).

**Why a net leverage limit.** Gross leverage bounds the total notional; net leverage bounds how much of it points one
way. Like Carver's gross reading, the net reading needs no volatility estimate (AFTS, tactic four, p.610 to 613); it
applies the same idea to direction. It is a plain signed sum of notional weights, so the low-volatility rates and FX
positions dominate it, and it does not measure the book's one-way market risk; Carver's finer form, leverage limits by
asset class (footnote 292, p.622), is not built. Carver's overlay reads gross leverage only; the net limit is the
book's own, kept in the design of 2026-10-05. It is a limit enforced by a cut on the days it binds, never a target: the
fifth term of the daily multiplier, its level set by his 99th-percentile method. Data: it was the binding term on 54 of
the 193 cut cycles of the chosen setup's 16y.

**Why Carver's covariance for the overlay.** See the table in section 2.1: Carver's estimator measures risk on the
sizing volatility the positions were sized with, with weekly correlations (Saturday to Friday) that avoid the low
correlations of daily closes taken hours apart, exponentially weighted over 26 weeks so that a rise in correlations
shows within weeks; the limits are set on this same estimator. It was chosen for the final setup on 2026-10-27.
`overlay_covariance` is required in the risk config (`src/risk/risk_module_config.cpp:205`); a saved desk row without
it refuses the book, an item listed in [../06_open/FUTURE_WORK_ADVICE.md](../06_open/FUTURE_WORK_ADVICE.md),
section "Open design questions and their issues".

**The per-name cap and the trim.** No symbol's weight may exceed 2 times the sizing capital, on the target, in the
search and on the stored book: Carver's own per-instrument leverage example. Carver keeps a position limit under
dynamic optimisation, at a nominal 10 or 20 percent weight, to bound one instrument's share of risk (AFTS, tactic four,
p.620 to 621). It is not built, by design and not on its numbers (2026-10-05): the cap of 2 already bounds each name,
so Carver's limit at a chosen 10 or 20 percent weight would be a second, overlapping per-name cap on a picked
parameter. The cap bounds notional where Carver's limit bounds a name's risk (a 40 percent volatility contract at
weight 2 carries 0.8 of risk against a target of 0.2), and the overlay's risk limits bound the book's risk as a whole.
The open-interest part of his limit does not bind at 500,000, and the book holds no open-interest data. Data, from a
reference model run outside the engine: at a 10 percent weight it changed the held book once in 16 years (heating oil, 2 to 1
contracts, 2022-03-08, which took that half-compounded run's worst day from -10.9 to -6.7 percent) and clipped three
target rows; at 20 percent the 5- and 16-year results were identical with and
without it. When rounding and held rows leave the stored book over a limit, the trim removes at most five contracts a
day from free rows (risk config `trim_max`). The hold rules keep held rows fixed, which is why the multiplier does not
scale them and the trim exists.

---

## 6. The one pass

After the overlay, one pass turns the scaled unrounded targets into whole contracts. Held rows (a symbol with no
session, a withheld bar, a roll bar awaiting confirmation, or a deferral-band row) are fixed at their held quantity;
the forecast-sign close runs; a greedy search from the held book finds the whole-contract book that best tracks the
target net of costs; the granularity buffer decides whether to trade at all and, if so, the fraction step moves every
row the same fraction of the way and rounds; rows over the cap are clipped and the trim runs. Mechanics:
[`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "The one pass", with a real rebalance in "One
rebalance, end to end"; [`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "Holds, the deferral band and
the forecast-sign close" to "The buffer, the rounding, the clip and the trim";
[`src/optimization/README.md`](../../../src/optimization/README.md), section "The one pass (futures books)".

Why each piece is set as it is, and what was tried instead, is the table in section 2.1. The common economics: in a
500,000 book most targets are a fraction of a contract, so the pass must decide which contracts to hold at all, and
every unnecessary trade is a cost the account pays. Starting from the held book, a cost multiplier of 100 inside
Carver's range and sizing the buffer by one contract's risk all serve that; the fraction step keeps the book Carver's
in how it moves, and the forecast-sign close keeps it on its forecasts' side.

Three config keys look as if they steer the pass and do not reach it: `covariance_history_prices` 756 and
`covariance_stale_dates` 5 feed only the generic optimiser, which a futures book never runs, and
`convergence_threshold` 1e-06 likewise; the pass takes 756, a stale rule of 5 dates and 1e-6 from constants of the code.
The values agree, so nothing moves ([`CONFIG_GUIDE.md`](../../CONFIG_GUIDE.md), section "Keys that are present and not
read"; [`src/portfolio/README.md`](../../../src/portfolio/README.md), section "Configuration"). The covariance keys are covered
by `AlgoGators/algogators#161` and `AlgoGators/algogators#137`.

---

## 7. Rolls and per-contract data

The book runs `per_contract_rolls` true: each futures bar is the held contract's own bar on the vendor's grid, and the
contract rolls by the rule of 2026-10-23, not by the vendor's `.v.0` switch. The rule rolls inside the last five CME
trading days before first notice or last trading day, whichever is first, on the first of the vendor's confirmed
switch, the first bar where the next contract trades more than the held one, or the last bar the book can still act
on; the target is the front month of the root's cycle, and the book never rolls back. Both roll legs are priced on the
roll bar at its two closes; the old contract's move is booked and the price gap between the contracts is stored, never
booked. Every return reads the back-adjusted series and every level the raw contract price. Mechanics:
[`FUTURES_ROLLS.md`](../../FUTURES_ROLLS.md); every case the rule handles, with a worked roll:
[ROLLS_AND_DATA.md](ROLLS_AND_DATA.md).

**Why the book has its own roll rule rather than the vendor's series.** On the vendor's roll date the old contract had
no business days left for most currencies, was past its last trading day for MBT and most 6L rolls, and was on or past
first notice for most gold, silver, copper, platinum and Treasury rolls. A live run trades a roll about two business
days after the vendor's date, so a book following the vendor would hold those contracts into expiry or the delivery
period. The rule rolls before the deadline instead.

**Why the front month of the cycle.** The target is the front contract of a fixed per-root cycle, the months the vendor
held in at least half the years: the front month of the cycle is where the volume and the data sit. Carver finds only
the front contract liquid in equity, bond, FX and metal futures (AFTS, tactic one, p.557), and the busiest later contract
matched the vendor's next contract on 1,962 of 1,990 rolls. The five-day window follows Carver's "few days": 1,713 of
the 1,990 rolls the deadline moved had a volume crossover inside it. Carver's
later-month choice for some roots (a fixed December for crude oil) is not built. His first reason for it is seasonality
in the back-adjusted price: holding one month of the year keeps the seasonal component out of it (AFTS, strategy one,
p.52, footnote 37), which matters most for natural gas and the agricultural contracts, and a trend-only book reads that
price too: on a monthly energy or a grain cycle a seasonal swing can read as a trend. A deferred month rolls earlier,
not later; it needs liquid contracts about a year out (his own condition) and changes every series of those roots, so
it needs its own measurement and a retune. It was left out of the roll rule's scope on 2026-10-21; it is unmeasured
and an open question ([ROLLS_AND_DATA.md](ROLLS_AND_DATA.md), section "The front month of a fixed cycle").

**Why `held`.** On a vendor switch bar that the next bar has not yet confirmed, the book still owns the old contract;
`held` hands out that contract's own bar, so sizing, marks, notional and the overlay are priced on what the book
actually holds, which is the reason the per-contract rule exists. A vendor stray, a one-bar switch the vendor then
reverses, therefore never enters the consumed history. Booked P&L and the roll legs are the same under `vendor`
(template note `_per_contract_switch_bar_note`, `:68`); the measured windows showed no consistent direction between
the two. `held` was settled on 2026-10-26.

Roll legs are two outright fills at the cost model, an upper bound against Carver's spread order: the engine has no
spread prices. Roll legs are their own cost class.

---

## 8. Bars and the session classifier

Each symbol's bar on each date gets one verdict: a usable session; junk (a locked or near-empty bar), withheld from
every consumer with the position held; a closure, held at its last mark; or a feed hole on a day the symbol normally
prints, held and logged as an error. Junk is judged against the median volume of the symbol's own trailing 20 session
weekdays. The holds enter the one pass as fixed rows. The reason is the feed: it carries Sunday rows, stubs and corrupt
prints, and one market's holiday is not another's, so each symbol answers from its own bars rather than from a
calendar. The per-symbol rule dates from 2026-09-17 and 2026-09-19, the instrument-id-change limb from 2026-09-25.
Mechanics and a worked example: [`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "The bars the
strategy consumes".

**A micro restarts its norm at its listing** (2026-10-28; `src/data/session_classifier.cpp:87`). Before, SIL's thin
first bars were judged against SI's full-size volume and withheld from 2013-06-17 to 08-02, so the held position would
have sat unmarked for seven weeks. Now a micro is judged on its own bars from the change of price source, as a new
symbol is. On a 16y run the only verdicts that change are 118 micro bars after listing, withheld before and consumed
now (81 MGC bars, 2010-10-05 to 2011-02-09, and 37 SIL bars); MGC's series then starts on 2010-10-25 rather than
2010-12-10, and that run's total return moved from +47.19 to +68.93 percent (yearly differences -7.2 to +7.3 points).

---

## 9. Why these 36 markets, and the gold and silver micros

### 9.1 The 36

The universe is every futures symbol with bars and a metadata sector, less full-size ES and less a predecessor whose
listed contract is stored (GC with MGC), weighted sector-equal (section 5.1). The weight table, with each contract's
size, is in [`TREND_FOLLOWING_SYSTEM.md`](../../TREND_FOLLOWING_SYSTEM.md), section "The instrument universe".

| Sector | Instruments | Size |
|---|---|---|
| Agriculture (10) | GF, HE, KE, LE, ZC, ZL, ZM, ZR, ZS, ZW | full |
| Crypto (1) | MBT | micro |
| Energy (4) | CL, HO, NG, RB | full |
| Equities (4) | MES, MNQ, MYM, M2K from 2019-05-06; ES, NQ, YM, RTY before | micro from listing |
| FX (9) | 6A, 6B, 6C, 6E, 6J, 6L, 6M, 6N, 6S | full |
| Interest rates (4) | UB, ZF, ZN, ZT | full |
| Metals (4) | HG and PL full; MGC from 2010-10-04 (GC before), SIL from 2013-06-17 (SI before) | gold and silver micro |

**Carver's instrument rules.** Carver screens an instrument on cost and liquidity before anything else: a risk-adjusted
cost of at most 0.01 Sharpe units a trade, so that costs take no more than a third of the likely pre-cost Sharpe, and
at least 100 contracts a day and about 1.25 million dollars a day of risk volume (AFTS, strategy three, p.103 to 105).
Between contract sizes he prefers the micro, for its smaller minimum capital and finer positions, unless it fails
liquidity or cost (p.106). On this book the cost screen removes nothing: the most expensive contract is ZR at 0.00483
Sharpe units a trade, and in FX the most expensive is 6L at 0.00252. The engine has no liquidity screen, and liquidity
against his threshold is unmeasured for the thinnest roots (6L, ZR, MBT, GF).

**Minimum capital, and what the static rule says at 500,000.** Futures are indivisible, so Carver sets an account's
minimum capital for an instrument at the capital that holds at least four contracts at its weight, which leaves room to
keep the position near its risk target (AFTS, strategy two, p.78 to 79; in a portfolio the minimum scales with the
instrument's weight and the IDM, strategy four, p.119). He checks each instrument against it but does not let minimum
capital set the weights (p.121). At 500,000 that rule rejects almost the whole universe:

| Basis | Result at 500,000 |
|---|---|
| The engine's weights, IDM 2.5, tau 20 percent, prices and volatility at the 2026-10-22 cut | 2 of 36 pass (MBT 336k, M2K 488k); every FX pair fails on every date (the cheapest, 6L, needs 1.18m) |
| The engine's weights, prices to 2024-03-28 | 2 of 36 pass (M2K 404k, MYM 432k); MBT misses by 2.8k; the rest fail, most by 4 to 18 times; meeting the rule on every contract needs about 9.2m |
| Smaller contracts, same basis | GC 5.62m, 0.56m with MGC; SI 6.17m, 1.23m at the 1,000-ounce size; CL 2.86m, 1.43m with QM; NG 2.07m, 0.52m with QG |
| Contracts held at an average forecast of 10 | 25 of 36 under one contract |

**Carver's static selection.** For a given capital Carver adds instruments one at a time by the expected Sharpe from
costs and correlations, skipping any that would push a member below its minimum capital (AFTS, strategy four, p.126 to
130); on his data and costs it picks 16 instruments for 100,000, 27 for 500,000 and 36 for 1 million (p.131). Run on
this book's 36 instruments plus ten micro stand-ins, eight variants (pre-cost Sharpe 0.3 or the measured 0.044; equal
or sector weights; measured turnover or his 7) each stop at 7 instruments, on minimum capital rather than on Sharpe; the
first picks M2K, 6J, KE, HE, MCL, MBT and 6M. Carver's 27 used his volatilities of the time and a universe with cheap
short rates and low-volatility bonds; this book's bonds are more volatile. Seven instruments carry little of the
diversification a trend book is built on: Carver finds that going from 1 to 100 instruments almost triples the expected
Sharpe (strategy four, p.134), and that spreading across instruments "makes it less likely that we'll pick an
instrument that doesn't perform once we start trading" (p.110). Data: the seven-instrument set, run as a book, ran
above its volatility target, lost 50.7 percent over the 10y with a 61.9 percent drawdown, and churned 122 to 428 round
trips a year.

**Dynamic optimisation, Carver's answer for a small account.** With little capital most optimal positions round to zero;
the static remedy is a small subset of instruments, with lower expected returns, where dynamic optimisation instead
finds every day the whole-contract portfolio that most closely tracks the unrounded one (AFTS, strategy twenty-five,
p.415), and is very unlikely to hold an instrument with excessive minimum capital, because that would add tracking
error (p.426). His test of strategy eleven (table 124, p.433 to 434): 100 instruments with no optimisation at 50
million, Sharpe 1.27; 27 instruments selected statically at 500,000, 1.18; 100 instruments with dynamic optimisation at
500,000, 1.22. Adjusted for the static 27's selection luck, the static book expects 0.94 against the dynamic 1.22, and
the dynamic book's correlation with the 50 million book is 0.99 against the static book's 0.78 (p.436). The book runs
that design (the one pass, section 6), so it keeps every market the data carries and lets the daily search decide
which to hold: which market will make the next large trend cannot be known in advance, and a market left out is one
where such a trend cannot be caught. Under dynamic optimisation the number of markets is not capped; it shows as how
many names are held on a day and how far each sits from its target. Data: the chosen setup's median names held a day is
14 to 17 in each of its 21 runs (14 on the 16y, 15 to 17 on the 2y starts); on the book of 2026-10-08, before the
micro metals, a mean of 12.9 names a day, and 63.8 percent of the symbol-days with a target held nothing (median over
eighteen 5-year windows).

How markets are added (an explicit universe list, a liquidity measure, a selection study by Carver's criteria rather
than by a candidate's own backtest, and a full retune) is `AlgoGators/algogators#184`.

### 9.2 Micros and listing dates

| Pair | Listed | Predecessor | Ratio |
|---|---|---|---|
| MES, MNQ, MYM, M2K | 2019-05-06 | ES, NQ, YM, RTY | 10 |
| MGC | 2010-10-04 | GC | 10 |
| SIL | 2013-06-17 | SI | 5 |

A contract trades only from its listing date. Before it the predecessor runs on the listed contract's stored history,
and on the first rebalance on or after the date a held predecessor is exited and the micro entered at the day's target
(`open_at_target`, the switch rule with the smallest gap to the signal). A live run trades no predecessor. Mechanics and
a worked switch: [`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "Listing dates and the switch".
The equity dates were set on 2026-10-08 and 2026-10-09, MGC and SIL on 2026-10-27. MGC's listing precedes the 16y
window's first day (2010-10-23), so GC is never traded in any window of the chosen setup; the four equity switches ran
on 2019-05-06. The equity indices trade as micros by Carver's rule of preferring the micro (strategy three, p.106):
M2K meets his minimum capital at 500,000 on both bases of section 9.1 and MYM on one, and the micros' fastest speed
costs 0.018 to 0.049 a year against his 0.15 limit.

**Why micros for gold and silver.** A full gold contract is the coarsest position in the book: one contract is about 13
of Carver's 1 percent no-trade bands at 500,000 (12.9), and its four-contract minimum capital is 5.62m against 0.56m
for MGC (SI 6.17m against 1.23m at SIL's size). The full-size book therefore held gold and silver rarely and away from
target: GC on 1,137 days at 1.7 times its target and SI on 957 days at 2.1 times. With micros the book holds them most
days near target: GC on about 3,500 to 3,800 days at a median 0.9 to 1.1 times and SI on about 2,600 to 2,800 days at
1.1 times. This is Carver's reason for a micro, sizing accuracy (strategy three, p.106). Finer contracts also trade
more: the band is the risk of one contract of the lumpiest name held or searched, so replacing the coarsest contracts
narrows it and raises round trips by design. Data, on the 17-run sizing matrix with the granularity buffer: mean
after-cost Sharpe 0.31 with the gold and silver micros against 0.18 for the full-size starting book of the first tuning
round, at 141 to 216 round trips a year against 115 to 192, tracking error 10.7 against 10.9 percent.

### 9.3 What is not in the book, and why

| Instrument | Status | What it does, why it is not in the book, and its data |
|---|---|---|
| Micro FX | not in the book (2026-10-26) | micros are used where the full-size contract cannot be held at 500,000, and FX is not where the book is coarse, so micro FX does not solve that problem: it makes each FX position finer, so the book holds several contracts where it held none or one and every one-lot adjustment becomes a trade; the band itself is set by the lumpiest contracts (gold, crude, heating oil and the like), which micro FX does not change. Carver's case for a micro is sizing accuracy, not fewer trades. Data: on the starting book's settings over the 16y, micro FX returned +95.1 percent (account Sharpe 0.42) against +4.4 percent (0.08) for the full-size starting book, at 168 to 256 round trips a year against 115 to 192, and the long windows did not order one way. On an older loop, putting all 20 instruments that have a micro or mini on it tripled the 5y round trips (950 a year against 272), because the micro book held 2 to 7 contracts where the full-size book held 0 or 1 |
| MHG (copper micro, with MGC and SIL) | not in the book (2026-10-27) | finer copper positions, at more trades: it adds trades where copper is not the coarse contract, and it has only been listed since 2022-05-02. Data over 7 runs: 0.13 against 0.30 for the setup with gold and silver micros; MHG's own P&L small (2y first start -13.8k, 10y +11.0k, 16y +2.6k), and the widest 2y spread of the setups in that screen (26.5 points) |
| Any contract dropped for cost or size | none (2026-10-26) | every market passes Carver's cost screen, and the daily search decides which to hold (section 9.1) |
| ICE products (KC, SB, CC, CT) and other markets | not in the book | the engine has no exchange calendar or expiry rules for their bars (`AlgoGators/algogators#169`); adding markets is `AlgoGators/algogators#184` |

The full results are in [../03_tuning_and_results/LEVERS.md](../03_tuning_and_results/LEVERS.md).

---

## 10. Costs and fees

A fill costs the broker's fee per contract plus slippage: half a spread in ticks, widened by volatility, plus a
square-root impact term on the signal bar's own volume, the two together capped at 200 basis points. The same model
prices every strategy fill, both roll legs, the listing legs and the search's cost vector, so the optimiser trades
against the cost the account will actually pay. The economics: the fee must be the one paid, because it is an input to
the trade decision (a flat 1.50 would price an equity micro at more than twice its fee and every other contract below
it), and a thin bar costs more to trade than a deep one. MGC and SIL take their full-size roots' spread and impact cap
and pay impact on their own volume. On the final grid costs were 0.03 to 0.06 Sharpe units, and doubling every fill's
cost took 0.03 to 0.06 off a run's Sharpe. Mechanics, the constants, the fee table and fills priced by hand:
[`COST_MODEL.md`](../../COST_MODEL.md), section "One formula", "Futures: the contract comes from the metadata row", "Where
cost enters decisions" and "Worked examples"; [`src/transaction_cost/README.md`](../../../src/transaction_cost/README.md).

SIL's 0.60 exchange fee (0.86 a contract in all) is taken from an older CME notice, where MGC's 0.96 is confirmed; the
search reads the fee, so a different fee moves the book. The MGC and SIL margins are derived from the full-size rows
(divided by 10 or 5); they change the reported margin, not the book. How to confirm the fee, and what to re-measure if
it changes, is in `scripts/futures_release/README.md`, section "Before the seed: the fees".

---

## 11. Weekends, holidays and calendars

The live book runs every calendar day (2026-09-17): the Saturday run books the Friday session; the Sunday run reads
Saturday's bars, which only MBT prints (CME crypto futures trade every day since 2026-05-29), and carries every other
symbol; the Monday run books the Sunday bar, which 26 of the 36 continuous series print. Whether a symbol had a session
is answered per symbol from its own bars, not from a calendar; the whole book is carried only when no symbol printed.
The Sunday bar is the evening open of the Globex session that CME settles as Monday's trade date. It has its own
price move, which the engine consumes as a bar, in the signals and in the annualisation, where the sizing volatility is
annualised on counted rows to correct the yearly figure for the extra rows. It has no settlement, so the roll rule's
CME calendar never counts it as a trading day, and a switch seen on a Sunday bar rolls on the next trading-day bar. Its
volume is a thin opening, so impact is not priced from it alone: the cost model adds the weekend bars' volume to the
neighbouring weekday session's, except for a fill on the weekend day itself
([`COST_MODEL.md`](../../COST_MODEL.md), section "Futures: the signal bar's own volume, with the weekend merge"). Sunday
counts as its own day because the Sunday evening session is a real exchange session that prints its own bar, and the
runner trades on the bars the feed prints (decided 2026-09-03; kept 2026-10-28). Folding the Sunday bar into Monday,
Carver's business-day convention, measured a 16y Sharpe of 0.27 against 0.21 on a rebuild outside the engine with no
costs, but it moves every volatility, forecast and limit, so it waits for a full engine retune. Moving the estimators
to business days is an item in
[../06_open/FUTURE_WORK_ADVICE.md](../06_open/FUTURE_WORK_ADVICE.md), section "Open design questions and their issues".
Stored statistics are annualised with 311.0574 futures sessions a year (`config_template/defaults.json:40`).

Two holiday calendars are in use. The live runners read the NYSE calendar (`include/trade_ngin/core/holidays.json`)
for their checks and their classifier's closure verdicts; the roll rule counts trading days on the CME Group calendar
per product group (`include/trade_ngin/core/futures_holidays.json`), because a futures deadline is set by the exchange
that lists the contract. Backtests make no NYSE closed-day check; the roll rule counts CME trading days in a backtest as
in a live run. The CME calendar was taken for the roll rule on
2026-10-23; moving the runners and the e-mail to CME is `AlgoGators/algogators#168`. Mechanics:
[`LIVE_RUN_CYCLE.md`](../../LIVE_RUN_CYCLE.md), section "The previous day, and closed days", "Where the weekend move
lands: futures" and "Trading-day count and annualisation".

---

## 12. Carry: built, shipped off

Carry (`AlgoGators/trade-ngin#185`; Carver's strategies ten and eleven) is on main, switched off (commit c7cec21205 in
`AlgoGators/trade-ngin#282`). Turned on, it computes carry from the held contract and its cycle neighbour and blends one
forecast at Carver's 0.4 carry and 0.6 trend (table 51); it needs `per_contract_rolls` on and a single trend sleeve. It
is off when the portfolio config has no `carry` block or weight 0, and then the strategy is today's to the last stored
digit (`include/trade_ngin/core/config_loader.hpp:336-353`). No template carries the block.

**Why off.** Carry is built and shipped off because it has not yet been tuned on the data the book trades. It needs two
real contract prices on the same date, which only the per-contract bars give, and the second contract is missing on
many dates: UB has it on 55.4 percent of dates and 6M on 66.0 percent, and 6L's longest gap is 78 dates. When carry was
measured (2026-10-26 and 10-27) the continuous series still had holes (`AlgoGators/algogators#36`) and the gold and
silver micros were stand-ins, and the final retune ran with carry off (`AlgoGators/algogators#186`).

What is known, and what is not. Carver's case for carry is a second return beside trend. On this book's 36 instruments
since 2011 carry alone earned a 16y Sharpe of 0.02 on the unrounded book before costs, so it brought almost no return of its own: the
blend kept trend's Sharpe (0.25 against 0.26) at lower risk, and at 0.4 weight it diluted the trend forecast with one
that earned nothing here. On whole contracts the data do not show a larger rounding or tracking loss with carry: on the
gold and silver micro book of the 2026-10-27 grid (12 runs per cell) carry roughly halves the round trips (76 to 110 a
year against 152 to 216) and the blend tracks its unrounded book a little more closely (median tracking error 9.5 /
8.7 / 9.6 against 9.8 / 9.3 / 11.3 percent; 7.0 against 8.3 on the one stored 16y run). On that run the whole-contract
blend kept a smaller share of its unrounded book (9k of 200k against 65k of 260k) but a similar dollar shortfall (about
191k against 195k). Why the traded blend's after-cost Sharpe falls is unmeasured, and the number of rows that round to
zero was never counted. Data on the final setup, after costs: 0.28 with carry off against -0.08 with it on, mean over
17 runs. The final
setup of 2026-10-27 is carry off. The follow-up is `AlgoGators/algogators#186`; what is known and what is suspected
about why carry earned little is in [../03_tuning_and_results/CARRY.md](../03_tuning_and_results/CARRY.md).

---

## 13. Run modes and the book e-mail

A live futures run takes `YYYY-MM-DD [--send-email]`. With no date it prints the usage and exits 2, and runs, stores
and sends nothing (`include/trade_ngin/live/run_args.hpp:13`). A dated run is either the host's date (the scheduled
run) or a past date (a replay or catch-up); a future date runs with a warning. The reason is that a production run and a replay of the same date must
behave the same: the date fixes the bars read and the instant every stored row carries, so a re-run of a date replaces
its rows instead of adding to them (`AlgoGators/trade-ngin#186`, 2026-10-11). The two modes differ in one rule: a held
symbol with no bar for more than `live.data_staleness_tolerance_days` (4) refuses the run on the host's date, because a
live book must not trade on a stale price, and only warns on a past date, so the missed dates can be run in order after
a refusal. Backtests take no date: the window ends at `backtest.frozen_end_date` when set (2026-10-23 in every tuning
run) and starts `backtest.lookback_years` earlier. Mechanics: [`LIVE_RUN_CYCLE.md`](../../LIVE_RUN_CYCLE.md), section "The
run date" and "The two run modes".

**The book e-mail.** With `--send-email`, after the day is stored, each live runner runs the Python report module
(`services/rpc/algogators_rpc/services/report`; `include/trade_ngin/live/python_mail.hpp`), which reads the stored day
and sends it; a run that stored the day and did not send exits 4. That module has been the only book e-mail path on
main since `AlgoGators/trade-ngin#261`, which removed the C++ e-mail, and it is the design: the earlier plan to keep the
engine's e-mail and revert the other send paths (`AlgoGators/trade-ngin#264`) was superseded by
`AlgoGators/trade-ngin#261`.

---

## 14. What is stored

Each live date stores the day's book (`trading.positions`, with `instrument_id`), its fills (`trading.executions`,
strategy and roll fills apart), its P&L, costs and statistics (`trading.live_results`), the overlay's readings of the
stored book and a nine-key record of the rebalance (`risk_detail`: requested risk, the binding term, the over-limit
marks, BLIND, the sizing capital, the account value), the final forecast after the slow rule (`trading.signals`), and the design
keys of the configuration (`live_run_metadata.portfolio_config`). A backtest stores its book, fills, results and equity curve, with
the same overlay readings and rebalance record, on its own tables; its signals table has no writer. The point of each:
a stored day can be explained (which limit cut the book, on what capital, from which forecast) and re-run to the same
cells. The sizing capital is recomputed every run from settled P&L and stored only as a `risk_detail` key, never as its
own column (2026-10-05), so it cannot drift from the P&L it comes from. The forecast stored is the final one, after the
equity slow rule, because that is the forecast the book traded on. Per-day estimator and one-pass records exist only as
files, when `TRADE_NGIN_SERIES_DUMP_DIR` names a directory. Migration 041 adds what the book e-mail shows that only the
run knew (the marks and settlement prices of each position, and the statistics' sessions a year).

Migration numbers at 4c92eb1b8d are 001 to 006, 012 to 029 and 034 to 041; 040 is the roll split. Mechanics:
[`LIVE_RUN_CYCLE.md`](../../LIVE_RUN_CYCLE.md), section "What each table holds" and the column
dictionary; [`OPTIMIZER_AND_RISK_DESIGN.md`](../../OPTIMIZER_AND_RISK_DESIGN.md), section "What is stored and what is logged";
[`SYSTEM_ARCHITECTURE.md`](../../SYSTEM_ARCHITECTURE.md), section "What the engine reads and writes".

---

## 15. Where an older design said otherwise

The loop design locked on 2026-10-05 was not rewritten for the tuning's outcomes. Where it differs from main, main and
the decisions below are in force.

| Topic | The older design | What the code does |
|---|---|---|
| Overlay limits | 0.45 / 0.90 / 0.80 / 8.0 / 6.0 | 1.75 / 4.75 / 3.00 tau, 7.25, 5.50 (2026-10-26, 10-27, 10-29) |
| Overlay covariance | sample covariance of the gate window, Carver's as a later option | Carver's (2026-10-27) |
| Micros | no contract moved to a micro or mini size | MGC and SIL from listing as well as the four equity micros (2026-10-27) |
| Speeds | no speed cuts | 18 rules removed by cost on 12 contracts (2026-10-09); blanket cuts stay out |
| Rolls | change bars on the vendor's series, a roll rule left for later | `per_contract_rolls` true on the rule of 2026-10-23 (section 7) |
| Invalid price | never 1.0 | `calculate_position` takes the last price in the symbol's history and falls back to 1.0 only when that history is empty (`src/strategy/trend_following.cpp:1307-1316`); the warm-up gate skips a symbol until its history reaches the longest window |

---

## 16. The release

The futures release put the chosen setup on main: `AlgoGators/trade-ngin#282`, then `AlgoGators/trade-ngin#261` (the
Python report as the only book e-mail, section 13), giving trade-ngin d6f998da, which `AlgoGators/algogators#98` imports. The production steps that load the release data
and seed the live history of CONSERVATIVE_PORTFOLIO, with their order, their preconditions, the checks before the seed
(section 10) and the way back from each, are in `scripts/futures_release/README.md` on AlgoGators/algogators main,
ported to the mono paths by `AlgoGators/algogators#124`.

### 16.1 What the release removed and what it kept

| Removed or left out | Detail |
|---|---|
| Unchosen options | the strategy-twenty-five switches, the edge step, the covariance window setting (756 is the behaviour without it), the uncapped build, the stand-in map, the high-volatility switch, every tested configuration |
| Dead code | 36 helpers with their tests (commit cc7e6cf619): the e-mail's retired helpers, helpers nothing calls, the whole `core/query_builder.hpp`, and report pieces kept alive only by their tests |

| Kept | Why |
|---|---|
| Carry | shipped switched off: no template has a carry block, so a carry-off run stores the same settings record |
| `per_contract_rolls`, `per_contract_switch_bar` | the chosen setup runs on them |
| `listing_dates`, `trading_rule_removals`, `equity_slow_rule`, `overlay_covariance` | each is part of the chosen setup |
| Every key the chosen setup's runs carry | the stored settings run unchanged but for the database |
| Dead code outside the release's scope, among it `Position::has_position` and `DynamicOptimizer::weights_to_positions` | left in place; the related covariance keys are covered by `AlgoGators/algogators#137` and `AlgoGators/algogators#161` |

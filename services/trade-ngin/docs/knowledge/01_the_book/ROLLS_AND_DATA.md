# Rolls and futures data

The futures book holds the contract it actually trades and rolls by its own rule, not by the
vendor's continuous series (Databento `.v.0`). That change does three things to the book. It books
the market move the book really earned on a roll day and never the price gap between two
contracts, which the vendor series could not separate. It moves the roll out of the delivery
period: on 17 of the 36 symbols most of the vendor's rolls fell on or after the old contract's last
trading day or first notice day, which a live book cannot do without delivery risk or a forced exit
in a thin market. And it ends the vendor's whipsaws, which charged the book two extra legs each time
the vendor's ranking flipped back. The rule (deadline, window, three triggers, the cycle front
month, never back) and every case it handles are described in the tracked
[FUTURES_ROLLS.md](../../FUTURES_ROLLS.md); this file holds why each choice was made, what the old
series hid and what it cost, how the data had to be repaired before the rule could be trusted (the
holes in the continuous tables moved a one-year result from +6.20% to -9.25%), the micros' story,
what it means for the QT book, and what is open. All of it is on AlgoGators/algogators main with
`per_contract_rolls` on in every futures template.

Code citations are paths under `services/trade-ngin/` in AlgoGators/algogators at commit 4c92eb1b8d,
main's merge of the import of trade-ngin d6f998da (`AlgoGators/algogators#98`). Commits are cited by their ids in that repository or by pull
request. The overview of the book is [THE_BOOK.md](THE_BOOK.md); the departures from Carver are in
[CARVER_DEPARTURES.md](../02_decisions/CARVER_DEPARTURES.md); every decision by area is in
[DECISIONS_BY_AREA.md](../02_decisions/DECISIONS_BY_AREA.md); the effect of the data fill on the
tuning results is in [TUNING_STORY.md](../03_tuning_and_results/TUNING_STORY.md); open design
points are in [FUTURE_WORK_ADVICE.md](../06_open/FUTURE_WORK_ADVICE.md), section "Open design
questions and their issues". The cycle and the deadline of each root, with dated examples, are in
[FRONT_MONTH_EXAMPLES.md](../../FRONT_MONTH_EXAMPLES.md).

## 1. The pull requests that carry it

`AlgoGators/trade-ngin#282` merged the roll work into main on 2026-10-30, and
`AlgoGators/trade-ngin#261` followed it.

| Piece | Pull request | Commit in AlgoGators/algogators |
|---|---|---|
| Fix for `AlgoGators/trade-ngin#188` (a late-confirmed roll charged twice by a live re-run; section 5) | `AlgoGators/trade-ngin#238` | f19734e0bf |
| Rule, per-contract loader, CME calendar, cycle | `AlgoGators/trade-ngin#241` | c640fa3708 |
| Bar source, roll-bar P&L, legs on the roll bar | `AlgoGators/trade-ngin#242` | e170ec4090 |
| Live: data wait, fallbacks, switch-bar setting, roll date in the field | `AlgoGators/trade-ngin#244` | e2dc576983 |
| Roll split (migration 040), e-mail lines, Rollover notice, Front month, per-contract data rule | `AlgoGators/trade-ngin#249` | dde32f6bfa |
| Continuous data row on the same AND rule | `AlgoGators/trade-ngin#250` | e856ec3649 |
| Final check of the whole roll work before it went to main (two tests, plain e-mail words for the micros) | `AlgoGators/trade-ngin#253` | 6a2915f349 |
| Equity index micros onto the rule; 2026-02-23 as a change of price source | `AlgoGators/trade-ngin#282` | 5f5240642a |
| Gold and silver micros (MGC, SIL), listing-leg costs, unpaired-micro refusal, classifier norm restart | `AlgoGators/trade-ngin#282` | eaa40fdfc1 |
| Templates: MGC and SIL pairs, `per_contract_rolls` on, the chosen limits | `AlgoGators/trade-ngin#282` | c42dbbe6f8 |
| Release scripts (`scripts/futures_release/`); a later version merged on 2026-11-04 as `AlgoGators/algogators#124` | `AlgoGators/trade-ngin#282` | 0f39e2d4f8 |
| Book e-mail: the Python report as the book e-mail, carrying the roll lines | `AlgoGators/trade-ngin#261` | 167bfac430 |

The hole fill is `scripts/futures_release/04_fill_holes.sh`. Outside trade-ngin,
`AlgoGators/algogators#87` is a draft that would fill holes after each daily pull.

The procedure that moves a database to the rule, step by step, is in
`scripts/futures_release/README.md` and `AlgoGators/algogators#180`. There is no rule for switching
on in the middle of a chain: the whole history is re-run, because the rule runs from each root's series start and a chain that
changed source midway would mix two price series in its estimators.

## 2. Why the book left the vendor's series

### 2.1 What the vendor's series did to the economics

The vendor's `.v.0` is a raw splice: each day it names one contract by the previous day's volume
and the bar is that contract's own. On the day it switches, the close jumps by the price difference
between two contracts, which no holder earns: a position worth its old-contract price before the
roll is worth its new-contract price after it, and the account changes only by the cost of the two
fills. Robert Carver makes the same point (Advanced Futures Trading Strategies, strategy one,
"Back-adjusting futures price"): a roll at market prices creates no profit or loss, so a series
built for returns has to remove the difference.

The first roll design (2026-10-01) found that the engine before it marked a position across the
switch as if `.v.0` were one price. The roll-day step was booked as P&L: a phantom gain for a long
in contango, a phantom loss for a short. The spread also entered the EMAs and the volatility as a
price move (volatility ratio 1.14 on NG, 1.69 on HE), and no roll trade was booked.

The fix of that era, merged as `AlgoGators/trade-ngin#137`, worked from the splice alone, because
the data held no per-contract rows. It set the roll bar's P&L and return to zero, booked the two
legs with the closing leg at the old contract's previous close, and fed signals a back-adjusted
series. That removed the phantom P&L but left three things approximate: the old contract's real
move on the roll day was dropped, the two legs were priced on two different days, and the roll date
was still the vendor's. A vendor whipsaw lasting past the next bar was booked as two rolls with two
sets of costs (163 found), and a bar with no contract id let the splice gap reach P&L.

### 2.2 What the dropped roll-day move was worth

A first-order measurement of 2026-10-10 on the conservative reference runs of the time (stored
return: the engine's total return on the account after costs; dollars on the 500,000 account; the
last column subtracts the dollars left out as points of 500,000):

| Window | Rolls' market move left out | Total left out | Stored return | Return if booked, first order |
|---|---:|---:|---:|---:|
| 2y | -80,129 | -60,897 | 61.68% | about 49.5% |
| 5y | -36,808 | -47,121 | 22.24% | about 12.8% |
| 10y | -47,352 | -47,067 | 129.11% | about 119.7% |
| 16y | -17,703 | -21,114 | 94.09% | about 89.9% |

The dropped moves have no bias on average (over 16 years 610 rolls hid a gain and 527 a loss, a mean
of -15 dollars a roll with a standard error of 30), but one day can carry a window. GC on 2026-01-30,
long 1 contract: GCG6 closed 5,418.4 on 01-29 and 4,879.6 on 01-30, so the book's real move was
1 x (4,879.6 - 5,418.4) x 100 = -53,880. The old engine stored 0 that day and legs 510.9 apart on
02-02, which is -538.8 of GCG6's own move plus a 27.9 spread. A book that drops its worst roll days
overstates its return, and the two-year window was overstated by about twelve points.

### 2.3 The vendor rolls too late for a live book

A long in a physically delivered contract can be handed delivery from the first notice day; a cash
settled contract stops at its last trading day. Carver's advice is to roll into the next contract
once it is liquid enough, a few days before expiry, and to close a few days before the first notice
day (Advanced Futures Trading Strategies, Tactic one, "When to roll to a new expiry date", page
565). A measurement of the vendor's roll timing, made outside the engine before the rule was built
(called the roll-timing measurement below), found the vendor does not do that. On the
vendor's roll date the old contract had 0 business days left for 6A, 6B, 6E, 6J, 6M, 6N and 6S; was
past its last trading day for MBT (65 of 65 rolls) and 6L (122 of 164); and was on or past first
notice for most GC, SI, HG, PL, ZN, ZF, ZT and UB rolls. Those are the 17 symbols of the opening
paragraph, where most rolls fall on or after the deadline. On three more the vendor rolls just
before it: 6C one business day before its last trading day on 64 of 66 rolls, and CL and NG on or
within two days of the last trading day on most rolls. A live run trades a roll about two business
days after the vendor's date, so a book that follows the vendor holds these contracts into expiry
or the delivery period. This is the case for having a roll rule of the book's own instead of the
vendor's dates; which contract the rule rolls into is a separate choice (section 3.5).

The MBT case shows the cost plainly. MBTQ6's last bar is Friday 2026-08-28, its last trading day;
the vendor names MBTU6 from Saturday 08-29, so a vendor-following book was cash settled each month
rather than rolling (12 MBT and 12 6L cases in the live year). Under the rule the roll is on
Wednesday 08-26 and its trade happens on Thursday, before the contract stops trading.

### 2.4 Why the counts of vendor switches differ

| Count | What it counts | Data |
|---|---:|---|
| 5,502 | every change of contract id in the vendor's series, switches back included | production `ohlcv_1d_raw`, 2010-06-11 to 2026-10-19, read 2026-10-21 |
| 5,500 | the same count by raw id, micros in | copy to 2026-10-02 |
| 5,255 | the same count, micros out (production read 5,255 too) | copy to 2026-10-02; production read of 2026-10-23 |
| 5,271 | the roll-timing measurement's per-symbol table: the 5,255 plus 4 for each equity micro | copy to 2026-10-02 |
| 5,479 and 5,627 | plain raw-id changes after the fill | the production copy and the copy with MGC and SIL (section 6.3) |
| 4,618 and 4,617 | switches the next bar keeps, as the vendor trigger forms them | unfilled and filled |

## 3. Why each part of the rule is what it is

The rule was set on 2026-10-23, after the roll-timing measurement, and the cases of
[FUTURES_ROLLS.md](../../FUTURES_ROLLS.md), section 4 "How the rule handles each case", were settled
when it was built into the engine (`AlgoGators/trade-ngin#241`). Each part below is written with its
reason first.

### 3.1 The deadline: first notice day, else last trading day

What it does: no contract is held into its delivery period or to its expiry. Why: holding a long on
first notice risks delivery, and a broker may close it first at whatever price the thin market
gives; a cash settled contract held to the end is settled, not rolled, so the book loses the
position it wanted to keep. Carver's rule of thumb (close a few days before first notice, roll
before expiry) is the source, and the deadline is the earliest of the two dates that applies to each
root. Numbers: with the first notice reading and N = 5 business days, 1,990 of 3,019 vendor-timed
rolls move, by a median of 5 business days, all 251 rolls that the vendor timed after the deadline
among them.

How each date is read, from `contract_dates` (`src/data/contract_roll_rule.cpp:513-529`): the last
trading day is the stored expiration (the vendor's own definitions, section 3.11), and the
rule-derived last trading day only cross-checks it with a WARN (`:45-49`); the first notice day
comes from `ExchangeRules::first_notice_day` alone, because the per-contract table has no column for
it, so it is not checked against data (`AlgoGators/algogators#183`).

### 3.2 The five-day window, ending at the last actionable bar

What it does: the book may roll on liquidity in the last five CME trading days before the deadline,
and the window closes at the last bar whose trade still happens before the deadline. Why five days:
Carver rolls once the next contract is liquid enough, "a few days" before expiry, and closes "a few
days" before first notice (Tactic one, page 565); five trading days is that few days. The window
catches the shift of volume on most rolls: with a crossover allowed anywhere in it, the next
contract out-traded the held one inside the window on 1,713 of the 1,990 rolls the deadline moved,
and the 277 left to the deadline are the roots where the front month takes over late (largest
counts: 6L 93, MBT 65, ZN 32, UB 32, ZF 25, ZT 21). Why the window ends at the last actionable bar
and not at the deadline: the live run dated R trades the bar of R-1, so a crossover on the Thursday
before a Friday first notice would trade on first notice day; with the window ending earlier, those
rolls go on the deadline trigger instead (246 rolls).

Interpretations where the rule's text is silent, set when the rule was built: the window's first day
is the 5th trading day strictly before D*; on one bar the vendor trigger takes precedence over the
crossover, which takes precedence over the deadline; a crossover needs both contracts to print and
the target strictly busier; the target is the earliest-expiring contract after the held one printing that bar whose month is in the cycle.

### 3.3 Three triggers

What they do: the book rolls on the first of the vendor's kept forward switch, the first window bar
on which the next contract trades more, or the last actionable bar. Why: each is a reading of where
the liquidity is. The vendor's switch is a volume ranking, so following it when it comes early keeps
the book where the market trades; the crossover catches the day volume moves when the vendor is
late; the deadline is the backstop that keeps the book out of delivery whatever the volumes say.
Trading where the volume is also costs less, because the cost model's impact term falls with the
contract's volume ([COST_MODEL.md](../../COST_MODEL.md)).

Over all history to 2026-10-02:

| | Count |
|---|---:|
| The old engine (vendor switch kept by the next bar) | 3,361 (live year 210) |
| "Vendor, never back" (the roll-timing measurement) | 3,019 |
| The rule as built | 3,031: vendor 1,068, crossover 1,449, deadline 514 |
| Earlier than the vendor | 1,907, median 2 trading days, max 10 |

The vendor times nearly every roll of GF (129 of 131), HE (115 of 115), HO, RB, LE and most grains;
the crossover times the currencies, CL, NG and HG; the deadline times MBT (65 of 65), 6L (128) and
the metals and Treasuries where the front month takes over only after the last actionable bar. Three
independent versions of the rule (the C++, a Python reference written from the rule's text, and a third
independent build) agree on every one of 148,676 grid rows and 16 columns.

A close variant measured before the build (vendor, crossover or deadline without the later
refinements) kept the vendor's date on 1,054 of 3,019 rolls, rolled 1,689 on a crossover and 276 on
the deadline; the 1,965 that moved were a median 1 trading day earlier, at most 11.

### 3.4 Never back

What it does: a vendor switch to an earlier contract, or a one-bar stray, is ignored. Why: a switch
back puts the book into the contract it just left, with the same exposure, so its two legs are cost
with nothing bought; and a stray on a thin day would move the book into a contract the market had
not moved to. Numbers: the vendor's series switched contracts 5,502 times, 1,895 of them back to the
contract just left (GC 207, SI 186, 6L 161), and one-bar strays on thin days (GC and 6J in 2024)
would have put the book in a contract it then never left. The rule "roll when the vendor rolls, and
never go back" was set on 2026-10-21; the deadline and the crossover were added on 2026-10-23, after
the roll-timing measurement.

### 3.5 The front month of a fixed cycle

What it does: the book always rolls into the next contract of the root's cycle, the months the
vendor held in at least half the years; GC never holds September or October. Why: the front of the
cycle is where the volume and the data sit. Carver holds the front contract wherever the next one
is not liquid, which he finds true of almost all equity, bond and FX futures and the metals
(Advanced Futures Trading Strategies, Tactic one, "Which expiry date to hold", page 557), and an
expiry that is not liquid costs more to trade, with wider spreads (page 558). Data: the cycle is
the months the vendor's volume ranking held in at least half the years, and no root had a month
between 35% and 65% of its years, so none sits near the cut; on the roll-timing measurement the
busiest later contract on the deadline date was the contract the vendor's series moved to next on
1,962 of the 1,990 rolls the deadline moved. Taking the vendor's own months, fixed in the code,
keeps the book on the contracts the price history was built from, and the fixed table means history
does not move as data grows ([FUTURES_ROLLS.md](../../FUTURES_ROLLS.md), section 3.2 "The cycle").
That the vendor rolls too late for a live book (section 2.3) argues for a rule of the book's own,
not for the front month over a later one.

What was not built: Carver holds one fixed, later month on some commodities (December crude, a year
ahead), first to keep the seasonal component out of the back-adjusted price so the system follows
the "true" movement, and second, rolling it well before expiry, to suit a carry strategy (Advanced
Futures Trading Strategies, strategy one, "Buy and hold with crude oil futures", page 52 and
footnote 37, which names natural gas and agricultural commodities as the markets where seasonality
matters most; Tactic one, "Seasonality", page 560, and "When to roll to a new expiry date", page
565). Carry being off answers only the second reason. The first applies to this book as it is: the
trend rules read the back-adjusted price, so on a monthly energy or a grain cycle a seasonal swing
in that price can read as a trend. Holding a deferred month also moves each roll earlier, not later,
since the book leaves that contract long before its deadline. The choice was left out of the roll
rule's scope on 2026-10-21 and is unmeasured. Building it needs, per root, a fixed month whose
contracts a year out are liquid enough to trade (Carver's own condition, which rules out some
roots; his footnote 38 adds that a smaller account can trade months too thin for a large fund), and
it changes the series every signal of those roots reads, so it would need its own measurement and a
retune. It is a departure from Carver, listed in
[CARVER_DEPARTURES.md](../02_decisions/CARVER_DEPARTURES.md), section 5.1, and open.

### 3.6 The roll bar: the old contract's move booked, the gap stored and never booked

What it does: on the roll bar the book's P&L and return are the old contract's own move, both legs
are priced at that bar's two closes, and the gap between the two contracts is stored beside the roll
(migration 040) but never booked. Why: with both contracts' closes on the same bar the move the book
earned and the gap it did not earn can be told apart for the first time, so the book keeps the first
and shows the second. Roll yield is not stored separately: it is already inside the held contract's
daily P&L, as the contract drifts toward expiry. The mechanics and the stored columns are in
[FUTURES_ROLLS.md](../../FUTURES_ROLLS.md), sections 5 "The roll bar" and 7 "What is stored".

The silver roll of November 2025 ([FUTURES_ROLLS.md](../../FUTURES_ROLLS.md), section 9 "One roll,
day by day: silver, November 2025") shows what the old engine did with the same roll: it held
December through the vendor's switch on 11-27 (P&L and return 0 that day, December's real -25
dropped), sent the legs on the run dated 11-30 at two different days' closes 0.750 apart, and held
December into first notice. Side by side, 11-21 to 11-28: P&L 29,700 under the rule against 29,300,
roll costs 45.29 against 47.04 (the old legs print as 23.49 and 23.56; 47.04 is the sum of the
unrounded legs). Every figure of that table reproduces from its closes, and the stored split
reproduces to the cent.

On the conservative two-year run with the switch on and the `held` setting, the stored totals are
roll-bar moves -43,678.14, roll gap values -38,805.50 and roll costs 1,971.30, each equal to the sum
of its rolls' rows.

### 3.7 Two outright legs

Roll costs stay two outright legs at full cost (decided 2026-10-21), an upper bound on a roll traded
as one calendar-spread order: the engine has no spread prices, and overstating the cost is the
conservative side. The reasons and Carver's own assumption are in
[CARVER_DEPARTURES.md](../02_decisions/CARVER_DEPARTURES.md), section 5.1 "Rolls as two outright
legs"; the spread roll is item 10 of `AlgoGators/algogators#183`.

### 3.8 The switch bar reads the held contract

What it does: on a vendor switch bar the next bar has not yet kept, every price level the book reads
is the contract it holds (`per_contract_switch_bar` `held`). Why: on that bar the book still owns
the old contract, so `held` prices sizing, marks, notional and the overlay on what the book actually
holds, which is the reason the per-contract rule exists. `vendor` would price them on a contract the
book does not hold yet, and on a one-bar stray on a contract it never holds. Booked P&L and the
legs are the same under both settings ([FUTURES_ROLLS.md](../../FUTURES_ROLLS.md), section 10 "The
held/vendor switch-bar setting"); only the levels read on the switch bar differ.

History: the first version of the per-contract bar source handed out the target's bar on that day,
so every price-level consumer (sizing, optimiser, overlay, marks) read a contract the book did not
hold; the version merged (`AlgoGators/trade-ngin#242`) hands out the held contract's own bar. On
2026-10-23 the choice was made a setting, `held` or `vendor`, to be measured, and `held` was settled
on 2026-10-26.

Data: the measured windows show no consistent direction between the two settings.

| Measure | held | vendor |
|---|---:|---:|
| 2y return, the per-contract bar-source code (`AlgoGators/trade-ngin#242`) | 88.42% | 88.10% |
| Year, the starting book of the first tuning round | -7.6% | -7.5% |
| 2y, the same book | -14.6% | -4.9% |
| 5y, the same book | +29.2% | +21.1% |
| 10y, the same book | +69.6% | +51.8% |
| 16y, the same book | +4.4% | +96.7% |

On the 2y comparison the P&L was equal on all 4,391 rows of equal quantity; the close differed on 145
switch bars and the return on 3 RB bars; the positions parted from 2025-10-20. In the starting book
of the first tuning round (17 `held` and 5 `vendor` runs) the books parted on one or two contracts
early; `vendor` returned more on the 2y and 16y and less on the 5y and 10y.

Both `per_contract_switch_bar` and `per_contract_rolls` shipped as keys on main, and the four futures
runners read them. Whether to remove them is open (section 9).

### 3.9 How the rare cases are handled, and why

The rule's text handles each case below; the code is in [FUTURES_ROLLS.md](../../FUTURES_ROLLS.md),
section 4 "How the rule handles each case".

| Case | What the rule does | Why |
|---|---|---|
| The held contract has no bar and the vendor names another contract | carries the held contract's last close | the vendor's bar would be another contract's price |
| The held contract has no bar and the vendor names it | reads the vendor's own print of that contract | it is the held contract's price, from the other table |
| The old contract has no bar on the roll bar but trades later | waits for the next trading-day bar where both print | both closes on one bar are what makes the split exact |
| The vendor's own roll would come after the deadline | the deadline rolls at the last actionable bar | the deadline exists to keep the book out of delivery; this case was why it was built |
| A grid bar with no vendor id | the vendor's contract is read by symbol and instant from `ohlcv_1d_raw` | the raw table has an id on every bar, so this removes the id-less cases the old engine leaked |
| A vendor switch on a Sunday or a holiday session | the switch is seen on that bar and rolls on the next trading-day bar where both print | the Sunday bar is the evening open of the Globex session that CME settles as Monday's trade date, and a holiday session belongs to the next trade date: neither has a settlement for the product, so the rule's CME calendar never counts them as trading days, and the same holds for a Saturday bar (MBT only) (`include/trade_ngin/data/roll_calendar.hpp:36-39`). The deadline and the window are counted in those days, so the roll's two legs go on a settled trading-day bar, the same bar in live and backtest (`src/data/contract_roll_rule.cpp:228-229`, `:283`). The Sunday bar's own price move is still consumed as a bar (section 6.4). The micros' change on Sunday 2026-02-22 rolls on Monday 02-23 |
| The last actionable bar is missing from the grid | rolls on the next grid bar with a WARN (17 rolls) | a live run cannot know in advance that a bar will be missing |
| A carried close | the held contract's latest close, on or off the grid | it is the latest real price, and it adds no grid date |
| Two rolls in a short span | each is booked. Over all history to 2026-10-02, 39 rolls of the rule fall within 20 calendar days of the root's previous roll (6L 14, GF 15, HE 3, seven other roots one each), all on the vendor trigger, where the vendor moves through adjacent cycle months. Of those 39, 38 fall inside the 16-year window of the conservative switch-on backtest; the book held the symbol on 4 of them, and their legs cost 144.92 dollars over the 16 years | each follows a vendor switch to a later contract that the next bar kept, so the volume has moved and the book follows it; following the vendor only near the deadline would remove some, and that choice is open |
| Calendar points inferred from bars | stand | no roll depends on them |

### 3.10 The CME Group calendar, per product group

What it does: the rule counts trading days on CME Group's own calendar for the root's product group,
never on the NYSE equity calendar. Why: the deadline and the window are counted in exchange trading
days, and the exchanges disagree on the days that matter (some CME groups traded when the NYSE closed on
2012-10-29 and 10-30, 2018-12-05 and 2025-01-09; a Good Friday with the employment report settles
only rates and FX). The first version of the rule used the NYSE holiday checker; the version merged
(`AlgoGators/trade-ngin#241`) counts CME Group's days.

What it moved: 2 rolls (RBG5 one trading day earlier, 2025-01-09 being a trading day for energy;
ZSX2 one later, the Sandy days trading for grains) and 6 exchange-rule dates, first order $0 in every
window, because the stored runs held neither contract. Against the bars, the expiry rule on the CME
calendar had 0 last bars after the rule's last trading day (4 on NYSE). So the move is correctness,
not return.

What still reads NYSE for futures (`AlgoGators/algogators#168` covers moving it): the live runners' "yesterday
was a holiday", the session classifier's closure lookup, the e-mail's holiday banner and the QT day
kind. The issue states it moves no stored number, only the banner, log lines and the QT closure
label on days the calendars disagree (2025-01-09, Good Friday 2026-04-03). The ICE calendar and the
seven new futures' expiry rules are `AlgoGators/algogators#169`.

### 3.11 Expirations from the vendor's definitions

What it does: the deadline reads the vendor's own expiration for each contract. Why: the order of
contracts and every deadline come from expirations, and the per-contract table arrived with
`expiration` NULL on every row. A first fill from an id-only map was wrong on 825 of 4,982
contracts (359 dated before the contract's last bar, 466 later), because spreads and other roots'
outrights that reuse an id overwrote the date. The rebuild from the vendor's definition records
gives one expiration for all 4,994 contracts. The dates derived from exchange rules differ from the
vendor's on 54 of 4,733 contracts (Columbus Day on the October currencies, Brazilian holidays on 6L,
GF holiday exceptions, CL and NG holiday weeks), 53 of them 1 to 5 weekdays earlier; they serve only
as the cross-check.

### 3.12 The morning data rule: hold only on a broad, lasting gap

What it does: the morning run is held, with an alert and nothing built, only when more than N
symbols (default 2) have each been without a bar for more than X days (default 4); otherwise it
goes ahead with every missing symbol carried at its last price, and an alert names them. A failed
pull is judged by the same rule: every symbol the table knows counts as missing, so a failed pull
holds the run only when more than N of those symbols have been without a bar for more than X days,
and a failed pull with no symbol in the table holds
(`services/rpc/algogators_rpc/services/desk/catchup.py:1090-1099`).

Why: carrying touches only the missing symbols, which keep their last price, while holding the run
stops every position's rebalance. So a gap of a few days on any number of symbols, or a lasting gap
on one or two symbols, is carried; a gap that is both broad and lasting is a feed failure, where
trading on carried prices would be wrong. X is the engine's existing staleness tolerance
(`live.data_staleness_tolerance_days`), the boundary its own guards use. N = 2 keeps a couple of
carried symbols, small against 36, from stopping the book, while three or more stale at once points
to the feed rather than to a market. Decided on 2026-10-24 as this AND condition, for both
data-ready rows:

- per-contract row: `QT_CONTRACT_DATA_READY_MAX_MISSING` (N, 2) and
  `QT_CONTRACT_DATA_READY_STALE_DAYS` (X; unset means the portfolio's tolerance, 4);
- continuous row (`AlgoGators/trade-ngin#250`): the same code (`MissingRule`,
  `services/rpc/algogators_rpc/services/desk/catchup.py:206`); N is `QT_DATA_READY_MAX_MISSING` (2),
  X the portfolio's tolerance. Replayed on the 14 real `ops.data_ready` rows (bar dates 2026-10-11
  to 10-23), nothing changes, since no symbol was ever missing, so N and X are untested against
  history.

A late bar never produces a wrong roll: the run waits for the data-ready rows, and only a bar the
vendor never publishes uses a fallback, one WARN each. The engine refuses one case the desk lets go
(a held symbol stale past the tolerance on today's run, `FEED_HOLE_REFUSAL`), and the model book's
09:30 cron does not wait for the data-ready rows (part A of `AlgoGators/algogators#176`, which also
lists replaying N and X on a longer history).

### 3.13 The e-mail's roll texts come from the engine

The Python report module is the book e-mail (`AlgoGators/trade-ngin#261`). The roll texts stay the
engine's: the runner hands them to the module, which lays them out and computes none of them, so the
e-mail and the stored split can never disagree. The Rollover notice box and the Front month column
are built on the rule (`AlgoGators/trade-ngin#249`, then the Python template in
`AlgoGators/trade-ngin#261`), so they name the contract the rule rolls into and the date it rolls
by; the earlier box and column, built on a hand-coded expiry calendar, were removed in
`AlgoGators/trade-ngin#205`. The price difference prints without a sign because it is not profit or
loss; it is stored signed. The texts are in [FUTURES_ROLLS.md](../../FUTURES_ROLLS.md), section 12
"What the book e-mail says".

## 4. What switching on moved, and why one window's number is not the test

The conservative backtests of the per-contract bar-source code (`AlgoGators/trade-ngin#242`),
switch off against on, with the `held` switch bar, windows ending 2026-10-02, on a copy of
production's data with the contract bars to 2026-10-02 (called the roll-work copy below); an
independent rebuild gave 0 differences on every roll, and the accounting tied every day. Off and On
are the engine's total return on the account after costs, on the book before the equity micros
moved onto the rule and before MGC and SIL came in; columns (a) to (d) split the dollar change on
the 500,000 account.

| Window | Off | On | Change ($) | (a) roll-bar P&L | (b) same positions, new prices | (c) roll legs | (d) position path |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2y | 99.07% | 88.42% | -53,226.96 | -41,170.30 | +78,785.08 | +119.38 | -90,961.11 |
| 5y | -10.82% | 26.19% | +185,024.22 | +10,819.20 | +22,738.46 | -515.22 | +151,981.78 |
| 10y | 68.75% | 27.92% | -204,144.94 | -22,559.20 | -2,008.95 | +298.33 | -179,875.12 |
| 16y | 46.05% | 127.99% | +409,733.74 | -7,327.30 | +2,137.06 | +915.70 | +414,008.29 |

The price effect of the switch on the same positions is small; nearly the whole change is the
position path. The risk loop's readings differ from the first cycle, positions part where a target
crosses a whole contract, and two switch-off runs that differ only in start date differ by 8.99
return points a year RMS. A fix that changed no booked move (the switch bar reading the held
contract) moved 5y from -14.08% to 26.19% and 10y from 74.54% to 27.92%. So the switch is judged on
its booked arithmetic, checked roll by roll, and on many start dates, not on one window's return.
The tuning rounds that chose the configuration of record all ran with the switch on
([TUNING_STORY.md](../03_tuning_and_results/TUNING_STORY.md)).

The 10y switch-on return appears at four values in the set; each names a version of the code and a
copy of the data, all on the basis above and ending 2026-10-02:

| 10y, switch on | Code | Data copy | 10y, switch off | What moved it |
|---:|---|---|---:|---|
| 27.92% | the per-contract bar-source code (`AlgoGators/trade-ngin#242`) | the roll-work copy | 68.75% | starting point |
| 40.07% | the roll work as checked whole before it went to main (`AlgoGators/trade-ngin#253`) | the roll-work copy | not moved by the code | a 6L return fix (2013-09-18) reaching 6L's long volatility window; the consumed series of the two runs are identical on all 114,269 bars |
| 14.63% | the same code as the row above | the copy the tuning later ran on (production's tables of 2026-10-24, the four full-size equity roots loaded, the vendor's expirations, the 213 corrected bars) | 85.47% | the data alone: the switch-off run moves too |
| 78.70% | the equity micros on the rule | the copy the tuning ran on | 85.47% | the micros' returns change the joint search and the whole-contract rounding; the gap sits in four one-contract spells (GC +167,120, HO +68,153, RB -39,593, UB -33,875) |

None of the four is the result of the configuration of record, which has other limits, MGC and SIL,
and the filled data; its windows are in [RESULTS.md](../03_tuning_and_results/RESULTS.md). The final
check of the roll work (`AlgoGators/trade-ngin#253`) recorded, switch on, 2y 88.42%, 5y 26.19%, 10y
40.07%, 16y 96.82%; with the switch off the roll branch stores exactly what main stored before the
roll work (`AlgoGators/trade-ngin#237`).

## 5. A late-confirmed roll charged twice by a live re-run (`AlgoGators/trade-ngin#188`)

What it did to the book: when the bars that confirm a roll arrived late and the days were then
repaired and re-run, the re-run of the later day read its own stale legs and charged the roll's cost
a second time. A normal day, a first run and a repair with no roll in the window were not affected.
The fix (f19734e0bf, `AlgoGators/trade-ngin#238`) applies behind no switch.

Worked example, the conservative book on a copy of production: every bar of 2026-04-27 is held back
for the runs of 04-28 to 04-30, then put back, and 04-28 to 05-03 is re-run in date order. ZS and MBT
both confirm their rolls on the 04-27 bar.

| Step | Run of | With the old rule | Stored after it |
|---|---|---|---|
| Clean chain, for reference | 04-28 | the 04-27 bars confirm: ZS and MBT roll | four legs on 04-28, `total_roll_fills` 4 |
| First pass | 04-28 | the 04-27 bars are missing: no roll | no legs |
| First pass | 04-29 | the rolls confirm a day late | four legs on 04-29 |
| Repair, bars back | 04-28 | the rolls are booked on their own date | legs on 04-28, cost in that day's equity |
| Repair | 04-29 | reads its own stale legs, takes the old contracts as held, books both rolls again | legs move to 04-29 (same order ids), `total_roll_fills` 0 on 04-28, cost charged on 04-29 as well |
| End of the chain | 05-03 | equity 506,725.97 against 506,766.53 | 40.56 lower from 04-29, the two rolls' cost charged twice; 112 `live_results` cells differ |

The fix is a rule on state: the day's own legs are stale when the rolls stored for earlier dates of
the run's window rolled out of that contract since they last rolled into it, and the run then takes
the contract from its stored T-1 row ([FUTURES_ROLLS.md](../../FUTURES_ROLLS.md), section 6 "The two
ROLL legs", "Which rolls a live run books"). A first version compared the day's legs with the latest
earlier leg only; a symbol that re-books two rolls before the stale date was still charged twice,
because the latest earlier leg rolls out of the middle contract.

| Case | Earlier stored rolls, oldest first | The day's legs roll out of | Verdict |
|---|---|---|---|
| One roll confirmed late | A to B | A | stale |
| Two rolls confirmed late | A to B, B to C | A | stale |
| A contract that comes back | A to B, B to A | A | not stale: the roll back comes after the roll out |
| Re-run of an on-time roll day | only rolls into today's contract | that contract | not stale: the day's own legs stand |

Rejected: deciding by the rows' write time. A re-run of an older date alone would then mark a later
date's fresh late roll stale, and the run would drop that roll.

The proof: each late chain, re-run in order, was compared with its own clean chain cell by cell,
tolerance 0, on every `trading.*` table of the book (one late bar, two late bars, two symbols on one
bar, a flip whose confirming bar is late, two late rolls of one symbol, the BASE book); every one was
identical with the fix. On the shapes the old rule failed it left the equity 29.90 to 59.45 dollars
lower. The unit sweep (`tests/live/test_live_rerun_repair.cpp`) runs 8 bar shapes by 84 late cases:
0 differ with the fix, and the old rule fails 23 to 55 of 84 on every shape. First runs did not move.

One edge remains: a contract re-entered with no stored roll into it (the book flat when the vendor
switched back, or an id reused within the 730-day lookback) reads as rolled out of, so a lone re-run
of a late-roll day out of it drops the roll. On the stored history measured, 0 of 67 roll days are
set aside, and the 16-year bars show no vendor id recurring more than 120 days apart. The remaining
test gaps are item 1 of `AlgoGators/algogators#183`.

## 6. The data: holes in the continuous tables, and the fill

### 6.1 Why the continuous tables still matter

With the switch on the bars are the held contract's own, but `ohlcv_1d` and `ohlcv_1d_raw` still
supply the date grid, the vendor's contract for the vendor trigger, the bar on a day the held
contract has no row and the vendor names it, and the statistics grid. The engine never adds a grid
date. So a missing `.v.0` row removes a real bar from every run, even when the contract table has it.

The per-contract data as delivered (production read of 2026-10-22): 1,178,607 outright bars, 4,690
contracts, 36 roots, 2010-06-06 to 2026-10-02, `expiration` NULL on every row, ES, NQ, YM and RTY
fetched but not loaded, and ids reused across roots (id 2017 is 6BX0 in 2020 and GCQ4 in 2023-24).
Hence the design: the date grid stays the vendor's, a contract's identity is (root, raw symbol,
expiration), and an expiration is required.

### 6.2 How the holes moved a result: +6.20% to -9.25%

When the real gold and silver micros replaced stand-ins built from GC and SI bars, the year from
2025-10-05 fell from +6.20% (stand-in) to -9.25% (real), on the settings of the carry-off set with
Carver's overlay covariance. There was no change from the micros' code on equal inputs: the gap was
the bar calendar. The stand-ins carried production's `.v.0` holes (SI.v.0 lacked 2025-03-24 to
04-04 and three single weekdays; GC.v.0 and SI.v.0 lacked some Sundays) while the real micros did
not. The one-pass optimiser builds one covariance on the dates every free symbol shares, and
annualises by counted rows, so one symbol's holes changed every position of the book. Dropping the
67 micro-only bars gave +7.22%; adding the stand-ins' volumes too reproduced +6.20% in every result
and fill. Over 17 windows the real set averaged 4.49 points below the stand-in, and the aligned set
0.92 above. A one-year result is that sensitive to the data calendar, so the holes are filled
before the history is used.

### 6.3 The fill and its rule

On 2026-10-28 the decision was to fill every hole, every date the contract table has, 6L and Sundays
included, with the session classifier judging the filled bars as any other. A check against
Databento found every checked missing bar on the vendor's side, so the holes came from the ingestion,
not from the vendor.

| | The production copy | With MGC and SIL |
|---|---:|---:|
| Holes filled, into each continuous table | 476 | 484 |
| Sundays / thin under 100 lots / market-wide days | 197 / 79 / 67 | 201 / 79 / 67 |
| SI, ZC, ZS 2025-03-24 to 04-04 | 30 | 30 |
| Rule B / C / V | 335 / 52 / 89 | 335 / 52 / 97 |

The rule: B, both real neighbours' contract when they agree; else C, carry the earlier neighbour's
contract if it prints; else V, the previous weekday's busiest contract, written with a NULL vendor
id. Rule V alone reproduces the vendor's contract on 97.59% of existing rows, and the filled contract
equals Databento's on all 42 hole dates checked. The NULL id on V rows is there because a V pick on
two thin 6L days in 2012 had made the rule roll 6LV2 to 6LZ2 on 08-23 instead of 09-06; with no id the
vendor trigger cannot fire on a guessed contract. Rolls: 3,418 of 3,428 identical; nine move one
trading day earlier onto a filled date, and LE 2012-07-18 moves through an accepted exception.

What it moved (on the limits of the time, not a tuning result): the year from 2025-10-05 went from
-9.25% unfilled to +0.35% filled. The tuning that chose the configuration of record ran on the
filled copy with MGC and SIL; the fill's effect on the tuning results is in
[TUNING_STORY.md](../03_tuning_and_results/TUNING_STORY.md). The two-year window shows the same SI,
ZC, ZS hole and put its effect at -0.15% of 500k. The same window was a hole in GC.v.0 too, and it
had held GCJ5 a week into its notice period.

Cause: the daily pull started at the whole table's latest date plus 1, so a symbol-day missed once
was never fetched again; since 2026-10-12 both pulls re-fetch five days. 33 V rows are probably the
vendor's own holes (unchecked). The fill runs with `scripts/futures_release/04_fill_holes.sh`;
`AlgoGators/algogators#87` is a draft that would fill holes after each daily pull.

### 6.4 Weekends, Sundays and duplicates

- The Sunday bar is the evening open of the Globex session that CME settles as Monday's trade date.
  It has its own price move, which the engine consumes as a bar like any other (signals, the counted
  annualisation) unless the classifier withholds it; it has no settlement, so the roll rule never
  counts it as a trading day (section 3.9). Its volume is a thin opening, so the cost model prices
  impact on it together with the next session's volume, not on it alone
  ([COST_MODEL.md](../../COST_MODEL.md), "Futures: the signal bar's own volume, with the weekend
  merge").
- A Sunday book reads Saturday's bars, and only MBT has a Saturday bar (CME crypto futures trade
  every day since 2026-05-29), so on a Sunday book only MBT trades; the other symbols trade on
  Monday's book, which reads their Sunday bars.
- Before the first Saturday bar (2026-05-30 in the per-contract table, 2026-06-13 on the continuous
  grid, the fill leaving Saturdays unfilled) a backtest stamps a Friday bar's trades on Sunday and a
  live chain on Saturday (HO, ZW 2026-02-20) (section 9).
- The weekend counts come from different tables on different dates and do not agree:
  Saturday bars are MBT only (39 from 2026-05-30 in the per-contract table on 2026-10-23; 46 after the
  expiration fix); Sunday bars were 70,809 in the per-contract table and 20,736 in the continuous
  series on 2026-10-22, 77,215 in the per-contract table after the expiration fix, on 33 roots.
- Sunday as a day: the engine counts a Sunday row as a day of its own because the Sunday evening
  session is a real exchange session that prints its own bar, and the runner trades on the bars the
  feed prints (decided 2026-09-03; kept 2026-10-28). Carver measures returns and volatility over
  business days (Advanced Futures Trading Strategies, strategy one, pages 40 to 42). Folding Sunday
  rows into business-day spans moved the 16-year Sharpe from 0.21 to 0.27 on a rebuild of the
  engine's combined trend forecast outside the engine (the 36 adjusted series, scaled to 17.8
  percent volatility, no costs), but it moves every volatility, forecast and limit the configuration
  of record was tuned on, so it waits for a full engine retune (section 9).
- Duplicates: `ohlcv_1d` once held 970 duplicate rows in 306 symbol-dates. `AlgoGators/algogators#24`
  removed them on production and keyed the table on `(time, symbol)`; the per-contract table is keyed on
  `(instrument_id, date)`. A copy of the database without the key can still repeat rows, which is why the roll
  rule's grid read takes distinct dates ([DATA_SOURCES_OF_TRUTH.md](../../DATA_SOURCES_OF_TRUTH.md), section 4.3).
- 16 orphan `.v.0` rows of 2014-05-25 and 2014-08-31 remain (six grain Sunday prints consumed; ZL's
  Sunday high 39.66 against closes near 32.9), and the futures data check (i) no longer sees the
  real switches that follow a NULL row (50 on the production copy, 56 with MGC and SIL).

### 6.5 The session classifier with the switch on

The classifier decides which bars each history consumes. With the switch on it judges the held
contract's own bar, not the `.v.0` bar, so a few bars are withheld on one side and consumed on the
other between switch-off and switch-on runs, and the held/vendor setting changes which bars are
consumed. A change of price source restarts the symbol's volume norm (decided 2026-10-28;
`src/data/session_classifier.cpp:87-89`, eaa40fdfc1): at a listing the norm carried the predecessor's
volume, so SIL's 20-lot first bar was judged against SI's norm of 35,570, and 81 MGC bars (2010-10-05
to 2011-02-09) and 37 SIL bars were withheld. With the restart they are consumed, and the MGC series
starts on 2010-10-25.

## 7. Listing dates and the micros

### 7.1 Equity index micros (MES, MNQ, MYM, M2K)

What it does: before CME listed the micros on 2019-05-06 the book trades the full-size contract (ES,
NQ, YM, RTY), each on its own metadata row, as one instrument with the micro. Why: before the listing
the bars under the micro symbols are the E-mini's, and the engine had sized them with micro terms, so
the backtest held tenths of an E-mini, a position no account could hold. Decided 2026-10-08 (a
contract trades only from its listing date) and 2026-10-09 (the switch is made inside the one pass at
the scaled target, `open_at_target`); merged as `AlgoGators/trade-ngin#173`.

The vendor relabelled the four micros' ids on 2026-02-22. On the vendor's series that read as a roll
and booked false legs and lost the move (over the February window -1,092.25 of bar moves, -730.75 not
booked), so it was declared not a roll (`instrument_id_relabels`). With the switch on (decided before
the tuning, 2026-10-25), the micros read the full-size root's contracts before 2026-02-22 and their
own from Monday 02-23 by the same rule; since 2026-10-26 the change is a change of price source, not
a trade, because the account trades nothing that day: the gaps are MES 0, MNQ +0.25, MYM 0, M2K -0.10
points. The rule rolls the micros on the crossover about two bars before the vendor, never after.
This reached main in `AlgoGators/trade-ngin#282` (5f5240642a).

### 7.2 Gold and silver micros (MGC, SIL)

MGC and SIL trade on their own contracts by the rule, and before listing each reads its full-size
root's contracts and grid (MGC reads GC to 2010-10-04, SIL reads SI to 2013-06-17); the listing table
is in code (`src/data/contract_roll_rule.cpp:424-432`). Each switch leg is priced on its own
contract's volume: a held SI exit at SIL's listing costs $20.75 on SIN3's 24,211 lots instead of
$102.99 on SILU3's 30, because the exit trades in SI's own market, and pricing it on the new micro's
thin first day would charge a cost no trader would pay. The SIL switch lands on 2013-06-18.

### 7.3 The runners refuse an unpaired micro

Since 2026-10-29 a micro stored beside its full-size root with no listing pair is refused before any
write, for every pair of the alias table (`src/data/listing_dates.cpp:246-263`, eaa40fdfc1). Why: a
micro beside its full-size root with no pair is traded as a second instrument on the same market,
so a runner holding GC, SI, MGC and SIL side by side doubles the book's gold and silver exposure;
without the refusal only the order of the production steps prevents that. The runners also refuse a
full-size-grid pair (MGC, SIL) with `per_contract_rolls` off (`src/data/listing_dates.cpp:235-240`).

## 8. The QT book and part B of the load

Part A of the load is the MGC and SIL contract rows plus the two metadata rows that carry their fees
(`scripts/futures_release/load/load_micro_metals.sql:92`, `:100`). The rest is in
`scripts/futures_release/README.md`: the pull for the days after part A, part A2, the hole fill,
part B (MGC.v.0 and SIL.v.0 in both continuous tables), the micros' 8 holes, the listing pairs in
every production futures config, the merged build, and the switch on.

Part B reaches the QT book. The live runner takes its universe from the stored `.v.0` symbols
(`apps/strategies/live_portfolio_conservative.cpp:1085`), dropping only `.c.0` variants and ES.v.0
(`:1174-1181`), so once MGC.v.0 and SIL.v.0 are stored they enter every QT book that reads that
database. No config works on both sides of the load:

| QT config | Before part B | After part B |
|---|---|---|
| Without the MGC and SIL pairs | runs | refuses on the merged build (`src/data/listing_dates.cpp:246-263`); code without the refusal takes MGC and SIL in beside GC and SI |
| With the pairs | refuses: GC.v.0 and SI.v.0 are stored and the micros are not, so GC and SI would read no bars (`src/data/listing_dates.cpp:227-233`) | runs, on the merged build with `per_contract_rolls` on; with the switch off it refuses (`:235-240`) |

So the QT runs must stop before part B and restart only after their configs and the merged build are
in. It is a written warning, not a check (decided 2026-10-30): no script reads a server's config or
schedule. The stop-and-restart wording is in the release README and at the top of
`scripts/futures_release/07_micros_part_B.sh` (`AlgoGators/algogators#124`).

The QT platform decisions of 2026-10-16 (`AlgoGators/algogators#105`, commit 02158cdee1, for the QT master decisions
document in `docs/design/`) include three that bear on rolls: Sunday waits for approval and the 10:00
fallback like any day, a price that arrives after the run raises a message and the day is re-run, and
an override cannot trade a symbol the engine is holding (no price yet, or a roll pending).

## 9. What is open

| Item | Where |
|---|---|
| The QT runs must stop before part B and restart after their configs and the merged build are in; a warning only | section 8, and `scripts/futures_release/README.md` |
| Whether to remove `per_contract_switch_bar` and the switch now that both shipped (section 3.8) | [FUTURE_WORK_ADVICE.md](../06_open/FUTURE_WORK_ADVICE.md), "Open design questions and their issues" |
| Whether to follow the vendor only near the deadline (section 3.9) | [FUTURE_WORK_ADVICE.md](../06_open/FUTURE_WORK_ADVICE.md), "Open design questions and their issues" |
| Sunday as a day (section 6.4) | [FUTURE_WORK_ADVICE.md](../06_open/FUTURE_WORK_ADVICE.md), "Open design questions and their issues" |
| The re-entered-contract edge and the test gaps of the late-roll fix (section 5) | `AlgoGators/algogators#183` item 1 |
| Rolls as a spread trade; two rolls on one bar; first notice days from the exchange rule, unchecked against data; display | `AlgoGators/algogators#183` |
| Broker close-out rules before first notice against the five-day window | `AlgoGators/algogators#183` item 9 |
| Rest of the futures side on CME's calendar; ICE and the new futures | `AlgoGators/algogators#168`, `AlgoGators/algogators#169` |
| Cron data wait; N and X on a longer history | `AlgoGators/algogators#176` |
| Engine `FEED_HOLE_REFUSAL` against the desk rule | section 3.12 |
| 2014 orphan rows; 33 probable vendor holes | section 6 |
| Friday trades stamped Sunday or Saturday before the first Saturday bar | section 6.4 |
| Carver's later-month choice for some energies and grains, not built | section 3.5; [CARVER_DEPARTURES.md](../02_decisions/CARVER_DEPARTURES.md), section 5.1 |

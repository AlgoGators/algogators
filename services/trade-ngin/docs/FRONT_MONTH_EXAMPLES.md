# Front month examples

The front month is the contract the futures book holds. With `per_contract_rolls` on (every
futures template) it is set by the roll rule of [FUTURES_ROLLS.md](FUTURES_ROLLS.md): the book holds
one contract of each root's fixed cycle and rolls into the next contract of that cycle before the
contract's deadline. There is no separate front-month calculation by date: the contract held is the
one the roll rule holds, its deadline worked from the contract's stored expiration and the exchange
rules named below, and the e-mail's Front month column and Rollover notice read the rule. This document lists the cycle of every root, the deadline rule of every root, and dated
examples worked from those rules.

The code: the cycle is `roll_cycle_table` (`include/trade_ngin/data/roll_cycle.hpp`); the deadline,
the window and the last actionable bar are `contract_dates` (`src/data/contract_roll_rule.cpp`); the
exchange rules are `ExchangeRules` (`src/data/roll_calendar.cpp`); trading days are counted on the
CME Group calendar of the root's product group (`include/trade_ngin/core/futures_holidays.json`).

---

## 1. The cycle of each root

The delivery months the book holds. A contract outside its root's cycle is never held, even when the
vendor's continuous series holds it.

| Roots | Cycle | Months |
|---|---|---|
| 6A, 6B, 6C, 6E, 6J, 6M, 6N, 6S | HMUZ | Mar Jun Sep Dec |
| 6L | FGHJKMNQUVXZ | every month |
| CL, HO, NG, RB | FGHJKMNQUVXZ | every month |
| GC, MGC | GJMQZ | Feb Apr Jun Aug Dec (never Oct) |
| SI, SIL, HG | HKNUZ | Mar May Jul Sep Dec |
| PL | FJNV | Jan Apr Jul Oct |
| ZC | HKNZ | Mar May Jul Dec |
| ZS | FHKNX | Jan Mar May Jul Nov |
| ZW, KE | HKNUZ | Mar May Jul Sep Dec |
| ZL, ZM | FHKNZ | Jan Mar May Jul Dec |
| ZR | FHKNUX | Jan Mar May Jul Sep Nov |
| LE | GJMQVZ | Feb Apr Jun Aug Oct Dec |
| HE | GJMNQVZ | Feb Apr Jun Jul Aug Oct Dec |
| GF | FHJKQUVX | Jan Mar Apr May Aug Sep Oct Nov |
| ZT, ZF, ZN, UB | HMUZ | Mar Jun Sep Dec |
| MES, MNQ, MYM, M2K (and ES, NQ, YM, RTY before 2026-02-22) | HMUZ | Mar Jun Sep Dec |
| MBT | FGHJKMNQUVXZ | every month |

Month codes: F Jan, G Feb, H Mar, J Apr, K May, M Jun, N Jul, Q Aug, U Sep, V Oct, X Nov, Z Dec.

The target of a roll is the earliest-expiring contract of the cycle after the held one that prints
on the roll bar; if that month prints nothing that day, the next cycle month that does, with a WARN.

## 2. The deadline of each root

D* is the contract's first notice day, or its last trading day where that comes first or where the
contract has no first notice day. The last trading day the rule uses is the stored `expiration` of
the contract's rows in `futures_data.contract_ohlcv_1d`; the exchange rule below only cross-checks it
and logs a WARN when they differ. The first notice day has no column and comes from the rule.
"Trading day" is a weekday on which CME publishes a settlement for the root's product group.

| Roots | Last trading day (the exchange rule) | First notice day | D* |
|---|---|---|---|
| 6A, 6B, 6E, 6J, 6M, 6N, 6S | two trading days before the third Wednesday of the contract month | none | last trading day |
| 6C | one trading day before the third Wednesday of the contract month | none | last trading day |
| 6L, HO, RB | the last trading day of the month before the contract month | none | last trading day |
| CL | three trading days before the 25th of the month before the contract month (four when the 25th is not a trading day) | none | last trading day |
| NG | three trading days before the first day of the contract month | none | last trading day |
| GC, SI, HG, PL, MGC, SIL | two trading days before the last trading day of the contract month | the last trading day of the month before the contract month | first notice day |
| ZC, ZS, ZW, KE, ZM, ZL, ZR | the trading day before the 15th of the contract month | the last trading day of the month before the contract month | first notice day |
| ZN, UB | seven trading days before the last trading day of the contract month | the last trading day of the month before the contract month | first notice day |
| ZF, ZT | the last trading day of the contract month | the last trading day of the month before the contract month | first notice day |
| LE | the last trading day of the contract month | the first trading day of the contract month | first notice day |
| HE | the tenth trading day of the contract month | none | last trading day |
| GF | the last Thursday of the contract month (in November the Thursday before Thanksgiving), a week earlier while that day is not a trading day | none | last trading day |
| MES, MNQ, MYM, M2K, ES, NQ, YM, RTY | the third Friday of the contract month, or the trading day before it | none | last trading day |
| MBT | the last Friday of the contract month, or the trading day before it | none | last trading day |

From D*:

- **the window** opens on the 5th trading day strictly before D*;
- **the last actionable bar (LAB)** is the latest trading day t with t + 1 calendar day still before
  D*. The live run dated R trades the bar of R-1, and runs go seven days a week, so the LAB is the
  last bar whose trade happens before D*. Its run date (the day after it) is the "by" date of the
  e-mail's Rollover notice.

The book rolls on the first of: the vendor's kept forward switch, the first window bar on which the
target trades more than the held contract, or the LAB.

## 3. Dated examples

### 3.1 Gold, August 2026: a first notice deadline

| Step | Value |
|---|---|
| Held | GCQ6 (August 2026) |
| First notice day | the last trading day of July: Fri 2026-07-31 |
| Last trading day | two trading days before Mon 08-31 (the last trading day of August): Thu 08-27 |
| D* | Fri 07-31, the earlier of the two |
| Window opens | the 5th trading day before 07-31 (07-30, 07-29, 07-28, 07-27, 07-24): Fri 07-24 |
| LAB | Wed 07-29: Thu 07-30 plus one day is not before D* |
| Rollover notice "by" | 07-30 |
| Target | GCZ6: after August the next GC cycle month is December |

The roll fell on Wed 07-29 by the crossover. The day-by-day table is section 3.4 of
[FUTURES_ROLLS.md](FUTURES_ROLLS.md). On the e-mails of the settled days 07-24 to 07-28 the
Rollover notice lists GC: holding the August contract, switching to December "by 07-30, first notice
07-31". From the settled day 07-29 the Front month column shows the December contract and GC leaves
the box.

### 3.2 Silver, December 2025: a holiday inside the window

| Step | Value |
|---|---|
| Held | SIZ5 (December 2025) |
| First notice day | the last trading day of November: Fri 2025-11-28 (Thanksgiving, Thu 11-27, is a CME closure) |
| Window opens | the 5th trading day before 11-28 (11-26, 11-25, 11-24, 11-21, 11-20): Thu 11-20 |
| LAB | Wed 11-26 |
| Target | SIH6 (March) |
| Rolled | Tue 11-25, the first window bar on which March traded more than December |

The day-by-day table, with the P&L, the legs and the split, is section 9 of
[FUTURES_ROLLS.md](FUTURES_ROLLS.md). It is a book that trades SI at full size; the template books
trade SIL from 2013-06-17, on the same cycle and deadline rule, and SIL's crossover reads SIL's own
volumes, so its roll bar can differ.

### 3.3 The run-date deadline in live

Rolls of held contracts in a sequence of dated live runs of the conservative book, one run a day,
with the switch on. The last
column is the run that books the legs: the run dated the day after the roll bar.

| Contract held | D* | Window to the LAB | Rolled on (bar, trigger) | Legged by the run of |
|---|---|---|---|---|
| ZNM6, ZFM6, ZTM6, UBM6 | first notice Fri 2026-05-29 (Memorial Day, Mon 05-25, inside the window) | 05-21 to Wed 05-27 | Wed 05-27, crossover on the LAB | Thu 05-28 |
| MBTK6, 6LM6 | last trading day Fri 2026-05-29 | 05-21 to Wed 05-27 | Wed 05-27, deadline | Thu 05-28 |
| 6LU6 | last trading day Mon 2026-08-31 | 08-24 to Fri 08-28 | Fri 08-28, deadline | Sat 08-29 |
| ZNU6, ZFU6, ZTU6 | first notice Mon 2026-08-31 | 08-24 to Fri 08-28 | Thu 08-27, crossover a bar before the LAB | Fri 08-28 |
| MBTQ6 | last trading day Fri 2026-08-28 | 08-21 to Wed 08-26 | Wed 08-26, deadline | Thu 08-27 |
| 6LV6 | last trading day Wed 2026-09-30 | 09-23 to Mon 09-28 | Mon 09-28, deadline | Tue 09-29 |

How the dates follow from section 2:

- **6LU6**: 6L's last trading day is the last trading day of the month before the contract month,
  Mon 08-31. The five trading days before it are 08-28, 08-27, 08-26, 08-25 and 08-24. The LAB is the
  latest trading day t with t + 1 day before 08-31: Fri 08-28 (Sat 08-29 is before 08-31, Sun 08-30
  is not a trading day). The Saturday run of 08-29 trades Friday's bar at Friday's settle.
- **MBTQ6**: MBT's last trading day is the last Friday of August, Fri 08-28 (08-31 is a Monday). The
  LAB is Wed 08-26: Thu 08-27 plus one day is not before 08-28. The roll is two bars before MBTQ6's
  last bar, and its trade happens on Thursday, before the contract stops trading.
- **ZNM6**: the first notice day is the last trading day of May, Fri 05-29. Memorial Day closes every
  group, so the window counts back over it: 05-28, 05-27, 05-26, 05-22 and 05-21.

## 4. What the e-mail shows

| Item | Reads |
|---|---|
| Front month (Symbols reference) | the contract the book holds after the settled day's bar, as the IB symbol, the month code and the year's last digit (JPYZ5); the old contract until its roll legs are booked |
| Contract months (Symbols reference) | the root's cycle in month names, from section 1 |
| Rollover notice | each held contract whose window is open on the settled day, with its target, the LAB's run date and its deadline |

[FUTURES_ROLLS.md](FUTURES_ROLLS.md), section 12, has the exact texts.

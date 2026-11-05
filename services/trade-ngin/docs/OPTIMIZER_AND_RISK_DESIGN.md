# How the futures loop works

This document describes one rebalance of a futures book, from the forecast to the stored whole-contract
book, as the engine runs it. It is written for an engineer who has to read, debug or change the loop. It
covers the two futures books: CONSERVATIVE (one trend sleeve) and BASE (two trend sleeves; BASE is the
repository's base template, `config_template/portfolios/base/`). The equity book does not go through this loop.

What is elsewhere:

| Topic | Document |
|---|---|
| The strategy end to end, with the source in Carver's book for every stage | [TREND_FOLLOWING_SYSTEM.md](TREND_FOLLOWING_SYSTEM.md) |
| The risk module interface, the refusal contract, the overlay as a module | [RISK_MODULES.md](RISK_MODULES.md) |
| Change bars, the adjusted series, roll legs | [FUTURES_ROLLS.md](FUTURES_ROLLS.md) |
| What a fill costs, fees per contract, netting between sleeves | [COST_MODEL.md](COST_MODEL.md) |
| The order of a live run, the tables, the column dictionary | [LIVE_RUN_CYCLE.md](LIVE_RUN_CYCLE.md) |
| Every config key | [CONFIG_GUIDE.md](CONFIG_GUIDE.md) |

AFTS is Robert Carver, *Advanced Futures Trading Strategies* (Harriman House, 2023), cited by strategy, section and table. "The book" in this document is the futures book the engine runs.

Code citations name the file and, where one is meant, the function or key. A file is given with its full path the first time and by
its file name after that. The two live futures runners (`apps/strategies/live_portfolio_conservative.cpp`
and `apps/strategies/live_portfolio.cpp`) carry the same loop code; the conservative one is cited.

The settings tables give the values in the conservative template (`config_template/portfolios/conservative/`
and `config_template/defaults.json`). Most worked examples (sections 3.7, 4.2, 5.2, 8.4 and the first part of
section 11) come from runs made before the template took its present form: those runs read the vendor's
continuous series (`per_contract_rolls` off), traded GC and SI full size rather than the gold and silver micros,
and read the overlay on the sample covariance with the limits of that time (R_max, R_jump_max and R_shock_max
2.25, 4.5 and 4.0 tau; gross leverage 8.0, net 6.0). The arithmetic of each stage is the same today. The last
part of section 11 works a day on the template's present settings.

---

## 1. The loop on one page

One rebalance per book per signal date. A live run given the date T signals on the bars up to T-1 and books
its fills at the T-1 close with the date T. A backtest cycle for day T signals on the previous bar group in
the same way.

| # | Stage | What it produces | Where |
|---|---|---|---|
| 1 | Sizing capital | E, the capital every capital-terms quantity divides by | `half_compounded_capital`, `include/trade_ngin/portfolio/sizing_capital.hpp`; set through `PortfolioManager::set_sizing_capital`, `src/portfolio/portfolio_manager.cpp` |
| 2 | Forecast | F in [-20, 20] per symbol per sleeve | `trend_estimator::estimate`, `src/strategy/trend_estimator.cpp`; called from `TrendFollowingStrategy::on_data`, `src/strategy/trend_following.cpp` |
| 3 | Unrounded target | N*, fractional contracts, long or short | `TrendFollowingStrategy::calculate_position`, `src/strategy/trend_following.cpp` |
| 4 | Classification | each symbol is free, held (fixed) or a close-out | `one_pass::rebalance`, `src/optimization/one_pass.cpp` |
| 5 | Cap and overlay | the capped target, five readings, one scalar m | `one_pass.cpp`; arithmetic in `src/risk/overlay.cpp` |
| 6 | Forecast-sign close, search | a whole-contract answer y from the held book | `one_pass.cpp`; `search`, `one_pass.cpp` |
| 7 | Buffer, rounding, clip | the book before the trim | `b_sigma`, `buffer` and `clip_to_cap`, `one_pass.cpp` |
| 8 | Trim | the stored book | `trim`, `one_pass.cpp` |
| 9 | Split, fills, record | per-sleeve books, fills, `risk_detail`, log lines | `PortfolioManager::rebalance_one_pass`, `src/portfolio/portfolio_manager.cpp` |

Stages 4 to 8 are one pure function of plain vectors, `one_pass::rebalance(const DayInputs&)`. It reads no
database, no clock and no state from the previous call. `rebalance_one_pass` builds its inputs, calls it
once, and stores what comes back. Nothing feeds back from a later stage into an earlier one: the scalar
is computed from the target alone, never from the optimiser's answer, and is applied once. The rounded book
is read again by the overlay only for the trim (section 7.5).

Read the design comments before the code. They state the rules this document summarises:

| Comment | File |
|---|---|
| The estimators: window rule, volatility, forecast | `include/trade_ngin/strategy/trend_estimator.hpp` |
| The annualisation count | `include/trade_ngin/strategy/vol_annualisation.hpp` |
| Half compounding as a closed form | `include/trade_ngin/portfolio/sizing_capital.hpp` |
| The live sizing reads and their failure outcomes | `include/trade_ngin/live/live_sizing_read.hpp` |
| The pass as pure functions; `DayInputs` and `DayResult` field by field | `include/trade_ngin/optimization/one_pass.hpp` |
| The gate window, the readings, the multiplier | `include/trade_ngin/risk/overlay.hpp` |
| The log lines | `include/trade_ngin/optimization/one_pass_log.hpp` |
| The stored record and its nine keys | `include/trade_ngin/risk/risk_detail.hpp` |
| Listing dates, the switch rules, the relabel list | `include/trade_ngin/data/listing_dates.hpp` |
| The live runners' per-symbol session rule and the hold | `include/trade_ngin/live/session_book_gate.hpp` |
| The split of a symbol over sleeves | `include/trade_ngin/portfolio/allocation_split.hpp` |

Where a header comment cites a section number, the rule it refers to is stated in this document or in one of
the documents it links.

A book is rebalanced by this loop when its `PortfolioConfig` names an overlay sleeve
(`one_pass_book`, `portfolio_manager.cpp`). The runners name the book's first sleeve
(`apps/backtest/bt_portfolio_conservative.cpp`). A book that holds a trend sleeve and names none is
refused, never sent through the generic optimiser step (`portfolio_manager.cpp`). The class in
`dynamic_optimizer.hpp` serves books with no overlay sleeve and takes no part in a futures rebalance.

---

## 2. Notation and settings

| Symbol | Meaning | Value in force | Where it is set |
|---|---|---|---|
| S_0 | starting capital | 500,000 | `portfolio.json` `initial_capital`, which the sizing reads (`include/trade_ngin/live/live_sizing_read.hpp`). `starting_capital` is required on a futures book and checked equal to it (`src/core/config_loader.cpp`); nothing computes from it |
| E | sizing capital of the rebalance (section 4) | recomputed every run | not a config key, not a column |
| V | account value, S_0 plus cumulative settled net P&L | | `risk_detail.account_value` |
| tau | annual risk target | 0.20 (BASE's fast sleeve 0.25) | `portfolio.json` `strategies.<sleeve>.config.risk_target`, required (`include/trade_ngin/strategy/sleeve_config.hpp`) |
| IDM | instrument diversification multiplier | 2.5 | `strategies.<sleeve>.config.idm`, required |
| w_i | instrument weight (section 5) | from the metadata `Sector` column | `TrendFollowingStrategy::get_weights`, `trend_following.cpp` |
| M_i | contract multiplier | metadata `Contract Size` | instrument registry |
| P_i | the raw close of the signal bar | | |
| sigma_i | annualised volatility of the adjusted returns | | `trend_estimator.cpp` |
| F_i | combined forecast after the equity slow rule | [-20, 20] | section 3 |
| N*_i | unrounded target in contracts | | section 5 |
| u_i | weight of one contract on the sizing capital, M_i P_i / E | | `one_pass.cpp` |
| L | per-name cap on a position's weight, abs(n_i) u_i <= L | 2 (BASE: 2) | `risk.json` carver module `per_name_cap` |
| m | the overlay's scalar, in (0, 1] | | section 6 |
| h_i, n_i | held and stored position, whole contracts | | |

The other constants of the pass:

| Constant | Value | Key |
|---|---|---|
| Cost multiplier of the search | 100 | `defaults.json` `optimization.cost_penalty_scalar` |
| Deferral band | 2 | `defaults.json` `optimization.sign_close_band` |
| Floor of the no-trade band, as a ratio to tau | 0.05 | `defaults.json` `optimization.b_sigma_floor` |
| Floor of the search's pass cap | 100 | `defaults.json` `optimization.max_iterations` |
| R_max, R_jump_max, R_shock_max, as ratios to tau | 1.75, 4.75, 3.0 (BASE: 2.25, 4.5, 4.0) | `risk.json` carver module `R_max`, `R_jump_max`, `R_shock_max` |
| Gross and net leverage limits | 7.25, 5.5 (BASE: 8.0, 6.0) | `risk.json` carver module `max_gross_leverage`, `max_net_leverage` |
| How the overlay's covariance is built | `carver` (BASE: `sample`) | `risk.json` carver module `overlay_covariance` (`sample` or `carver`, required) |
| Most contracts the trim removes in a day | 5 (BASE: 5) | `risk.json` carver module `trim_max` |
| Closes behind the optimiser's covariance | 756 | none: `kOptimiserBars`, a literal in `trend_following.cpp` |
| Sizing mode | `half_compounding` | `portfolio.json` `sizing_mode`. It is required, it is the one value accepted, and it selects nothing: the code has one sizing rule (`src/core/config_loader.cpp`) |

`ConfigLoader::require_loop_keys` (`src/core/config_loader.cpp`) refuses a futures book that lacks the
equity slow rule, the three risk ratios, `sizing_mode`, `starting_capital`, `cost_penalty_scalar`,
`sign_close_band` or `b_sigma_floor`; the risk schema requires `per_name_cap`, `trim_max` and
`overlay_covariance` with the ratios (`src/risk/risk_module_config.cpp`). `apply_loop_config`
(`include/trade_ngin/portfolio/loop_config.hpp`) then carries the band, the floor, the sizing mode, the
slow rule, the cap and the trim cap into `PortfolioConfig`; the cost multiplier and the pass-cap floor are
read by the loader into `opt_config`, and the limits stay on the carver module. One constant has an in-code
default: `optimization.max_iterations` is not required and reads 100 when absent
(`include/trade_ngin/optimization/dynamic_optimizer.hpp`).

Three keys in the files look as if they steer the pass and do not reach it. `portfolio.json` carries
`covariance_history_prices` 756 and `covariance_stale_dates` 5, and `defaults.json` carries
`optimization.convergence_threshold` 1e-06. The pass takes its 756 closes from `kOptimiserBars`
(`src/strategy/trend_following.cpp`), its stale threshold of five dates and its step threshold of 1e-6 from
literals in `one_pass.cpp`. `covariance_stale_dates` and `convergence_threshold` are read only on the generic
optimiser path (`portfolio_manager.cpp`; `src/optimization/dynamic_optimizer.cpp`), which a futures book never
runs, and `covariance_history_prices` caps a stored price history of which the futures book reads only the latest
close. The values agree today, so nothing moves; a change to any of the three has no effect on a futures book.

---

## 3. The forecast

The method and its citations are in [TREND_FOLLOWING_SYSTEM.md](TREND_FOLLOWING_SYSTEM.md). This section
is the mechanics.

### 3.1 The window

Every estimator is a function of the last 3,200 consumed bars of the symbol ending at the signal bar
(`kWindowBars`, `trend_estimator.hpp`) and of nothing else. Every recursion starts at the window's first
bar. Nothing is carried from one call to the next, so a live run and a backtest that hold the same bars
compute the same numbers. A symbol with fewer bars uses what it has; the run counts such rows and prints one
`ESTIMATOR_SHORT_WINDOW` line.

Returns and EMAs read the back-adjusted series; every level (the price the forecast divides by, the sizing
price) reads the raw close. See [FUTURES_ROLLS.md](FUTURES_ROLLS.md).

### 3.2 Volatility

A mean-centred EWMA standard deviation of the adjusted returns, span 32 (the fast sleeve: 16).
`sigma_short` is that daily figure times the annualisation factor, clamped to [0.005, 5.0]. `sigma_long` is
the mean of `sigma_short` over the trailing 2,520 values. The sizing volatility is
`sigma = 0.7 sigma_short + 0.3 sigma_long`.

The annualisation factor is the square root of the bars a year, counted over the trailing 256 bars:
`(256 - 1) / (calendar days they span / 365.25)` (`trend_estimator.cpp`;
the rule is stated at `vol_annualisation.hpp`). A series
that prints a Sunday session has more bars a year than 256, so its factor is above 16. The forecast's own
volatility uses the same variance with the fixed factor 16.

### 3.3 The six speeds, scaling and capping

For each EWMAC pair (fast, slow), with both EMAs on the adjusted level:

```
raw     = (EMA_fast - EMA_slow) / (P_raw x forecast_sigma / 16)
scaled  = clamp(raw x attenuation x scalar, -20, +20)
```

| Pair | 2/8 | 4/16 | 8/32 | 16/64 | 32/128 | 64/256 |
|---|---|---|---|---|---|---|
| Scalar (`forecast_scalar`, `trend_estimator.cpp`) | 12.1 | 8.53 | 5.95 | 4.10 | 2.79 | 1.91 |

The scalars are fixed (AFTS, strategy nine, table 29). A pair with no scalar is refused when the sleeve
is initialised (`trend_following.cpp`). The trend sleeve runs all six pairs; BASE's fast sleeve runs the first
four.

The attenuation is `2 - 1.5 q`, where q is the share of the trailing 2,520 `sigma_short` values at or below
today's, smoothed by a 10-bar EWMA. It is 1 until 252 values exist (`trend_estimator.cpp`).

### 3.4 Weights and the forecast diversification multiplier

The combined forecast is the equal-weight mean of the scaled forecasts of the pairs the contract runs,
times the multiplier for that number of pairs, capped at +/-20 (`trend_estimator.cpp`).

| Pairs run | 6 | 5 | 4 | 3 | 2 | 1 |
|---|---|---|---|---|---|---|
| Weight of each | 1/6 | 1/5 | 1/4 | 1/3 | 1/2 | 1 |
| Multiplier | 1.26 | 1.19 | 1.13 | 1.08 | 1.03 | 1.00 |

These are the rows of AFTS table 36 (strategy nine). The engine reads the row by the number of pairs
run. Carver states each row for the slowest pairs of that number, which is what a contract with removed
rules runs. BASE's fast sleeve runs the four fastest pairs and reads the four-pair row all the same. The
table in force is
`TrendFollowingConfig::fdm` (`include/trade_ngin/strategy/trend_following.hpp`). `defaults.json`
`strategy_defaults.fdm` carries the same six rows and changes nothing: the runners copy it into a sleeve
only when the sleeve's own table is empty, and the table in the code is never empty
(`apps/strategies/live_portfolio_conservative.cpp`).

### 3.5 Rules removed from a contract by cost

`portfolio.json` `trading_rule_removals` names, per contract, the pairs that contract does not run. Eighteen
rules are removed on twelve contracts:

| Contracts | Pairs not run | Pairs left | Multiplier |
|---|---|---|---|
| 6M, GF, HE, LE, NG, ZL, ZS | 2/8 | 5 | 1.19 |
| 6L, KE, ZC, ZW | 2/8, 4/16 | 4 | 1.13 |
| ZR | 2/8, 4/16, 8/32 | 3 | 1.08 |

Every other contract runs all six.

How the forecast is rebuilt for a listed contract (`trend_following.cpp`): the sleeve looks the
symbol's root up in the list, `trend_estimator::pairs_after_removal` (`trend_estimator.cpp`) returns the
pairs left, the multiplier is read from the table for their number, and `estimate` is called with those
pairs and that multiplier. Each pair left is scaled, attenuated and capped exactly as on any contract. The
mean is over the pairs left, so each carries an equal weight of one over their number. A removed pair is
never computed: it has no value, not a value of zero.

What is refused when the sleeve is initialised (`validate_config`, `trend_following.cpp`):

- a name that is not a base symbol, or that the instrument registry does not hold;
- a contract listed with no pair;
- a set of removed pairs that is not the contract's fastest ones in order, that leaves none, or that names
  one twice (only the fastest can be removed, because table 36 is stated for exactly those sets);
- a number of pairs left that the multiplier table has no row for;
- the removal of a pair the equity slow rule reads, on a symbol the rule names;
- the two contracts of a listing-date pair (section 8.4) losing different rules.

The config loader accepts the block only on a book of exactly one enabled trend sleeve
(`src/core/config_loader.cpp`). BASE, with two sleeves, carries no list. The list crosses from the
loader to the sleeve in one function, `hand_over_trading_rule_removals`
(`include/trade_ngin/strategy/sleeve_config.hpp`), which all four futures runners call. One
`TRADING_RULE_REMOVALS` line per listed contract is logged when the sleeve initialises
(`trend_following.cpp`).

Where the list comes from. It is Carver's procedure (AFTS, strategy nine, "Removing expensive trading rules"): a
rule is removed from an instrument when the rule's yearly cost on it is over 0.15 Sharpe-ratio units, where

```
yearly cost     = (turnover of the rule + 2 x rolls a year) x cost per trade
cost per trade  = cost of one contract / (price x multiplier) / sigma
```

The turnovers are Carver's (AFTS table 35: 98.5, 50.2, 25.4, 13.2, 7.6, 5.2 for the six pairs, the same for
every contract). The cost inputs are ours: the engine's own cost of one contract at the signal close (the
fee per contract plus spread and impact, see [COST_MODEL.md](COST_MODEL.md)), and its sizing
volatility, whose ratio is taken as the median over the signal days of the sixteen-year run the list is computed from, and the
rolls the engine confirmed on the contract's series in that run, over the series' span in years.
`scripts/trading_rule_costs.py` shows the arithmetic and, with `--check`, reports whether the costs of a
new run of that kind still give the committed list. The committed list is the authority; the script never writes it.

### 3.6 The equity slow rule

On the book's first sleeve only, for the symbols `portfolio.json` `equity_slow_rule.symbols` names (M2K,
MES, MNQ, MYM): a negative combined forecast stands only when the scaled forecasts of both slow pairs,
32/128 and 64/256, are negative. Otherwise it is set to 0. A forecast that is not negative is never touched
(`equity_slow_ruled`, `trend_estimator.cpp`; applied at `trend_following.cpp`).

The forecast after the slow rule is the forecast from there on: it sizes the target, its sign drives the sign
close and the deferral band, and it is the value stored in `trading.signals.signal_value`. With carry on (it is
off in every template; see [TREND_FOLLOWING_SYSTEM.md](TREND_FOLLOWING_SYSTEM.md), section 1.6a), the rule acts on
the trend part of the blend only.

A held equity-index short whose forecast the rule sets to 0 is not closed by rule. A zero forecast has no
sign, so the symbol is a free row with a target of 0 that may only step toward zero; the short leaves the
book as the search and the buffer take it.

On BASE the fast sleeve carries neither slow pair and the rule does not apply to it
(`apps/backtest/bt_portfolio.cpp`). A predecessor contract of a listing-date pair (ES for MES) comes under the
rule through its listed contract's root
(`ListingDates::pair_root`, `include/trade_ngin/data/listing_dates.hpp`).

### 3.7 Worked forecasts

An example of the arithmetic, from a replay of dated live runs of the conservative book, one run a day in
date order, on the conservative configuration of that time (see the note at the head), which carried the
listing-date, relabel and rule-removal blocks: run date 2026-04-30, signal bar 2026-04-29. The per-pair figures are from the run's estimator record, a diagnostic file the
sleeve writes when `TRADE_NGIN_SERIES_DUMP_DIR` is set (`trend_estimator_record.hpp`). The combined forecast
in the last column equals the stored `trading.signals.signal_value` of that run date.

| Symbol | 2/8 | 4/16 | 8/32 | 16/64 | 32/128 | 64/256 | Pairs | Mean | Multiplier | Combined F |
|---|---|---|---|---|---|---|---|---|---|---|
| MES | 7.512 | 13.665 | 12.832 | 6.412 | 2.420 | 3.045 | 6 | 7.648 | 1.26 | 9.636 |
| MBT | -8.605 | 3.367 | 11.016 | 5.381 | -9.629 | -17.790 | 6 | -2.710 | 1.26 | -3.415 |
| ZW | not run | not run | 3.505 | 4.663 | 3.458 | -0.696 | 4 | 2.733 | 1.13 | 3.088 |
| ZR | not run | not run | not run | -6.124 | -9.420 | -18.844 | 3 | -11.462 | 1.08 | -12.379 |

Check for ZW: (3.505 + 4.663 + 3.458 - 0.696) / 4 = 2.733; 2.733 x 1.13 = 3.088. MES is one of the slow rule's symbols; its
forecast is positive, so the equity slow rule leaves it alone.

---

## 4. The sizing capital

### 4.1 The rule

The book is sized on Carver's half compounding (AFTS, Tactic three, "Half compounding"): the starting
capital less the current drawdown of the settled P&L, never above the starting capital. Losses come off the
sizing capital at once. A profit rebuilds it up to the start. Profit above the start is set aside and never
sized on.

With C the cumulative settled net P&L in date order (0 at the start) and P its running peak floored at 0:

```
E = S_0 - (P - C)          account V = S_0 + C
```

Row by row this is `E' = min(S_0, E + net)`. It is a function of the settled history alone
(`half_compounded_capital`, `sizing_capital.hpp`). A day's net is its P&L less the day's
costs after netting: each fill's own cost minus its `netting_adjustment` (see
[COST_MODEL.md](COST_MODEL.md)). On a one-sleeve book every adjustment is 0.

E is the capital of every capital-terms quantity: each sleeve's capital, the weight per contract u, the
overlay's weights and leverage, the per-name cap, and the cost term of the search.
`set_sizing_capital` hands it to every strategy (times its allocation) and every risk module in one call; a
refusal from any of them stops the rebalance.

No column persists E. Every run recomputes it:

| Engine | Settled history | Code |
|---|---|---|
| Backtest | the run's own equity curve up to its last row, which is the previous cycle's close. Warm-up rows are flat, so warm-up sizes on S_0 | `backtest_half_compounding`, `sizing_capital.hpp`; `src/backtest/backtest_coordinator.cpp` |
| Live | the book's stored `trading.live_results.daily_pnl` rows before Day T-1, then Day T-1's own net rebuilt before the rebalance | `read_live_sizing_equity`, `live_sizing_read.hpp`; `apps/strategies/live_portfolio_conservative.cpp` |

The live run sizes before it finalises Day T-1, so it rebuilds Day T-1's net from the parts the
finalize will use: the T-1 settlement move of the stored T-1 book on the consumed bars
(`live_sizing_equity`, `sizing_capital.hpp`), less the costs stored on Day T-1's row. After the
finalize, a `SIZING_CAPITAL_CHECK` line compares the finalized `daily_pnl` with the rebuilt net.

### 4.2 Worked table: a loss, the recovery, a gain above the start, a loss again

An example of the arithmetic, from the same run as section 3.7 (a replay of dated live runs of the
conservative book on the configuration of that time, with the listing-date, relabel and rule-removal blocks),
run dates 2026-04-24 to 2026-05-02. `E` and `V` are the stored `risk_detail.sizing_capital` and
`risk_detail.account_value` of each run date's `trading.live_results` row. "Net added" is the stored
`daily_pnl` of the day or days that settled since the previous sized run. C is V minus 500,000. The first
row's peak is not stored; it is derived from the two stored keys as P = (S_0 - E) + C.

| Run date | Net added (day settled) | C | Peak P | E = 500,000 - (P - C) | V = 500,000 + C | What happened |
|---|---|---|---|---|---|---|
| 2026-04-24 | | -2,559.13 | 2,254.73 | 495,186.14 | 497,440.87 | in drawdown: the account once stood 2,254.73 above the start |
| 2026-04-25 | -201.48 (04-24) | -2,760.60 | 2,254.73 | 494,984.67 | 497,239.40 | a loss comes off at once |
| 2026-04-27 | 0.00 (04-25), +234.38 (04-26) | -2,526.23 | 2,254.73 | 495,219.04 | 497,473.77 | a gain rebuilds it |
| 2026-04-28 | +6,139.19 (04-27) | 3,612.96 | 3,612.96 | 500,000.00 | 503,612.96 | recovered: 495,219.04 + 6,139.19 = 501,358.23, capped at the start; a new peak |
| 2026-04-29 | +4,224.78 (04-28) | 7,837.74 | 7,837.74 | 500,000.00 | 507,837.74 | gain above the start: set aside, E does not move |
| 2026-04-30 | +3,168.22 (04-29) | 11,005.96 | 11,005.96 | 500,000.00 | 511,005.96 | the same |
| 2026-05-01 | -5,134.39 (04-30) | 5,871.57 | 11,005.96 | 494,865.61 | 505,871.57 | a loss from the peak comes off E, although the account is still above the start |
| 2026-05-02 | -572.06 (05-01) | 5,299.51 | 11,005.96 | 494,293.55 | 505,299.51 | a further loss |

The run of 2026-04-26 stores no `risk_detail`: its signal date, a Saturday, has no session bar, so no
rebalance is sized. The row of 2026-05-01 shows the point of the rule: the account is 5,871.57 above the
start and the book is sized on less than the start, because the drawdown is taken from the peak.

V minus E is the profit set aside (11,005.96 on 2026-05-01). It is derived from the two keys and is not
stored.

**What a lower E does to the rest of the rebalance.** An illustration with invented inputs, none of them a
stored row: a book that starts at 500,000 settles +6,000 on day 1 and -24,500 on day 2.

| Sized day | Net settled before it | C | Peak P | E = 500,000 - (P - C) | Row by row, min(500,000, E + net) | V = 500,000 + C | Set aside V - E |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | (none) | 0 | 0 | 500,000.00 | start | 500,000.00 | 0.00 |
| 2 | +6,000 | +6,000 | 6,000 | 500,000.00 | min(500,000, 506,000) = 500,000.00 | 506,000.00 | 6,000.00 |
| 3 | -24,500 | -18,500 | 6,000 | 475,500.00 | min(500,000, 475,500) = 475,500.00 | 481,500.00 | 6,000.00 |

On day 3 every capital-terms quantity moves with E, because each divides by it:

| Quantity | At E = 500,000 | At E = 475,500 | Why |
|---|---:|---:|---|
| Weight of one contract u = M P / E | x 1 | x 1.051525 (500,000 / 475,500) | the same contract is a larger share of a smaller capital |
| N* of a ZT row at the same forecast (+16) | 16.005634 | 15.221358 | N* is proportional to E |
| Per-name cap in contracts, L / u, for that ZT row | 4.801690 | 4.566407 | the cap is a weight, so fewer contracts fit |
| Cost term of one contract at a cost of 1.50, 100 x 1.50 / E | 0.000300 | 0.000315 | a trade costs more against a smaller capital |

The profit of day 1 is set aside and never sized on; the loss of day 2 comes off E at once, measured from the
peak of 6,000, so E falls by 24,500 while the account falls to 481,500.00.

### 4.3 The failure-path rule

When the live sizing read finds Day T-1 on one of the finalize's failure paths, the run keeps the sizing
capital of the last settled day, adds nothing for the unsettled day, and logs `SIZING_CAPITAL_UNSETTLED`
(WARN) with the unsettled date, the reason and the capital used (`live_sizing_read.hpp`). The paths, tested with the finalize's own predicates:

| Reason logged | Condition |
|---|---|
| `no Day T-1 row` | the book has stored history and no `live_results` row for Day T-1 |
| `no T-1 closes` | the price manager's T-1 close map is empty and the book holds a position |
| `no T-2 closes` | the price manager's T-2 close map is empty |

The next run that finds the day settled recomputes E over the whole settled history in date order. Because
E is a closed form of that history, the late day lands in its own date's place and the result equals the
row-by-row recursion. Rows sized while a day was unsettled keep the capital they were sized on
(`risk_detail.sizing_capital`).

A stored day on which no symbol printed (a held Saturday) is never finalised; it counts at its stored
`daily_pnl` as soon as any later bar is loaded (`sizing_history_day_unsettled`, `live_sizing_read.hpp`).

A database error is a different case from "nothing stored" (`live_sizing_read.hpp`):

| Outcome | When | What the run does |
|---|---|---|
| `kHoldBook` | every sleeve's T-1 book loaded, but the Day T-1 row, the previous row or the P&L history could not be read | no rebalance and no order: every sleeve is held at its T-1 book, the day's `live_run_metadata.risk_refusal` is marked with scope `sizing`, exit code 3 |
| `kRefuseRun` | a sleeve's T-1 book could not be read | the run refuses to start, exit code 1, no row |

---

## 5. The unrounded target

```
N* = (F / 10) x (C_s x IDM x w x tau) / (M x P x FX x sigma)
```

`C_s` is the sleeve's capital, E times the sleeve's allocation. FX is 1 (every traded contract is in
dollars). A negative forecast gives a short target. The strategy publishes N* as it is: unbuffered,
unrounded and unclamped (`trend_following.cpp`). The buffer, the rounding and the per-name cap all
belong to the pass.

Guards in `calculate_position` (`trend_following.cpp`): a forecast that is not finite or is
beyond 20 in size sizes at 0; sigma is clamped to [0.01, 1.0]; tau to [0.01, 0.5]; the denominator is at
least 1. Also floored: the capital at 1,000, the IDM and FX at 0.1. A sigma or a price that is not positive
is replaced (the caller puts 0.2 for a sigma that is not a positive finite number, `trend_following.cpp`, and the function itself 0.01; a bad price takes the last price of the symbol's history, or 1 when
there is none), and a result that is not finite is set to 0. Of these, only the sigma clamp at 1.0 binds on this book, on NG
and MBT in high-volatility months.

### 5.1 Instrument weights and the IDM

`get_weights` (`trend_following.cpp`) builds the weights from the metadata `Sector` column. The
universe is every metadata symbol that has a row in `futures_data.ohlcv_1d` at any date, less ES and less a
predecessor whose listed contract is stored (GC while MGC is, SI while SIL is; `ListingDates::run_universe`,
called in `trend_following.cpp`; the symbol list is read with no date filter,
`src/data/postgres_database.cpp`). If that symbol list comes back empty, every metadata symbol
is kept. The weights are computed once per sleeve and cached, so they are the
same on every date of a run. In a backtest that starts before a contract's first bar, the contract carries
its weight from the first day and the other weights are not rescaled for the dates it has no bar: MBT,
whose first bar is dated 2021-05-03, holds 0.071429 on every date of a window that starts in 2010.

The rule:

1. each sector gets an equal share, and each symbol an equal share of its sector;
2. a symbol is capped at half of its sector's share;
3. the weights are brought back to a sum of 1 by scaling only the symbols that were not capped.

With the 36 traded symbols in seven sectors:

| Sector | Symbols | Weight of each |
|---|---|---|
| Agriculture | GF, HE, KE, LE, ZC, ZL, ZM, ZR, ZS, ZW | 0.015476 |
| FX | 6A, 6B, 6C, 6E, 6J, 6L, 6M, 6N, 6S | 0.017196 |
| Energy | CL, HO, NG, RB | 0.038690 |
| Equities | M2K, MES, MNQ, MYM | 0.038690 |
| Interest Rates | UB, ZF, ZN, ZT | 0.038690 |
| Metals | HG, MGC, PL, SIL | 0.038690 |
| Crypto | MBT | 0.071429 |

Check: MBT is alone in its sector, so it is capped at half of 1/7, 0.071429. The other 35 are scaled by
(1 - 0.071429) / (6/7) = 1.083333: an agriculture symbol is (1/7) / 10 x 1.083333 = 0.015476. The sum is 1.

The two contracts of a listing-date pair read one weight: a predecessor takes its listed contract's
(`trend_following.cpp`). So GC trades on MGC's weight before MGC's listing date, and SI on SIL's.

The IDM is 2.5 on both books (AFTS, strategy four, table 16: the value for 30 or more instruments). It
is a required sleeve key with no default.

### 5.2 Worked targets

Same run as section 3.7 (signal bar 2026-04-29, E = 500,000, one sleeve at allocation 1, IDM 2.5,
tau 0.20). `sigma`, weight, multiplier and price are from the estimator record.

| Symbol | F | w | E x IDM x w x tau | M | P | sigma | M x P x sigma | N* |
|---|---|---|---|---|---|---|---|---|
| MES | 9.636 | 0.038690 | 9,672.62 | 5 | 7,192.50 | 0.20078 | 7,220.41 | 1.291 |
| MBT | -3.415 | 0.071429 | 17,857.14 | 0.1 | 76,035 | 0.43330 | 3,294.60 | -1.851 |
| ZW | 3.088 | 0.015476 | 3,869.05 | 50 | 653.00 | 0.29830 | 9,739.59 | 0.123 |
| ZR | -12.379 | 0.015476 | 3,869.05 | 2,000 | 10.92 | 0.17968 | 3,924.21 | -1.221 |

Check for MES: 0.9636 x 9,672.62 / 7,220.41 = 1.291. For ZW, sigma is 0.7 x 0.31336 + 0.3 x 0.26317 =
0.29830.

---

## 6. The overlay in brief

The module, its config and its refusal contract are in [RISK_MODULES.md](RISK_MODULES.md). What the loop
needs:

**What it reads.** The capped target book: every free symbol at `sign(N*) x min(abs(N*), L / u)`
(`cap_target`, `one_pass.cpp`), plus every held row at its held quantity. Capping first means the
overlay never reads a position the book cannot hold. The scalar is never computed from the optimiser's
answer.

An illustration of why the cap comes first, with invented inputs (four contracts, none a stored row): ZT's
forecast of +16 gives an uncapped N* of 16.005634 contracts, a weight of 6.67 on the sizing capital, where the
cap L / u is 4.801690 contracts, a weight of 2.

| Book the overlay reads | R | R_jump | R_shock | L_g | L_n | m at the limits in force | Binding |
|---|---:|---:|---:|---:|---:|---:|---|
| Capped target (what the code reads) | 0.0808 | 0.2019 | 0.210 | 2.7652 | 2.3806 | 1 | none |
| The same book uncapped (for comparison) | 0.2003 | 0.5008 | 0.350 | 7.4319 | 7.0473 | 0.780443 | L_n (L_g 0.975526 beside) |

Read uncapped, a position the book can hold only at a weight of 2 would cut every other name by 22 percent.

**In capital terms.** Each position is a signed weight on the sizing capital, `x_i = N_i M_i P_i / E`. The
five readings (`overlay.hpp`):

| Reading | Formula | Limit |
|---|---|---|
| R, portfolio risk | sqrt(x' Sigma x), Sigma the overlay's annualised covariance (below) | R_max = 1.75 tau = 0.35 |
| R_jump | sqrt(x' Sigma_jump x): the overlay's correlations on each symbol's jump volatility, the 99th percentile of its trailing 2,520 `sigma_short` values each taken back to a daily figure by its own bar's factor (`trend_estimator.cpp`), annualised by the gate window's factor (`one_pass.cpp`) | R_jump_max = 4.75 tau = 0.95 |
| R_shock | sum of abs(x_i) s_i (every correlation taken as 1). With `overlay_covariance` `carver`, s_i is the symbol's sizing volatility of section 3.2; with `sample`, its own volatility in the gate window, sqrt(Sigma_ii) | R_shock_max = 3.0 tau = 0.60 |
| L_g, gross leverage | sum of abs(x_i) | 7.25 |
| L_n, net leverage | sum of x_i, signed; the limit applies to its size | 5.5 |

The gate window is the last 252 dates on which any participant has a return, ending at the signal date.
Below 21 complete dates the three covariance readings are blind (their multipliers are 1, `OVERLAY_BLIND`
is warned) and the two leverage readings still apply. Which participants are in each reading and how the
readings are annualised are the window's in both covariance settings. The full window rules are in the comment
at `overlay.hpp`.

**The overlay's covariance.** `risk.json`'s `overlay_covariance` chooses how Sigma is built
(`carver_covariance`, `src/risk/overlay.cpp`; the comment at `overlay.hpp`):

| Setting | Sigma over the participants in R | In force |
|---|---|---|
| `carver` | sigma_i sigma_j rho_ij. sigma_i is the symbol's sizing volatility as its sleeve sizes it (section 3.2). rho_ij is the exponentially weighted correlation of the two symbols' weekly returns, span 26 weeks, newest week weighted most, over the weeks both have a return; a weekly return is the sum of the daily adjusted returns of a Saturday-to-Friday week; a pair with fewer than two common weeks has rho 0. The weeks come from every row the sleeve hands the overlay, which is each symbol's last 300 consumed bars (`kOverlayBars`, `trend_following.cpp`), not only the window's 252 | yes, in the conservative template |
| `sample` | the sample covariance (n - 1) of the window's complete dates, or of every window date with missing returns as zeros below 120 complete dates | no |

So the overlay and the optimiser read different covariances: the overlay Carver's form above, the optimiser the
sample covariance of 756 closes (section 7.3). Whatever the setting, the one-pass record also stores R and the
stored book's R on the carver covariance (`carver_R`, `carver_stored_R`), so that books compare.

**Where the limits are.** The five limits are fixed values of the carver module in `risk.json`. The pass
multiplies the three risk limits by the first sleeve's tau and takes the two leverage limits as they are
(`portfolio_manager.cpp`), so a change of tau moves the three risk limits with it and leaves
the leverage limits and the per-name cap where the file puts them. The loader bounds R_max at 3 tau, R_jump_max
and R_shock_max at 6 tau, the per-name cap at 4, the trim at 20 contracts, and both leverage limits at 10 with
the net limit no larger than the gross (`src/risk/risk_module_config.cpp`).

**How the limit values are set.** By Carver's method (AFTS, Tactic four, "An exogenous risk overlay"): each limit
is the 99th percentile of its reading's own history, rounded. Here the history is the conservative book's own
sixteen-year backtest with the limits taken out of play (the three risk limits at 100 tau, both leverage limits
at 10; the per-name cap stays at 2, so the readings are of the capped target), over the cycles with a complete
window after the warm-up. The loader above refuses risk limits that large, so measuring the readings this way needs a
build with the loader's three risk bounds lifted (`kMaxRRatio` and `kMaxTailRatio`). The percentile is taken by linear interpolation (`overlay::percentile`) and rounded to
the nearest 0.25, in tau for the three risks and as a plain ratio for leverage:

| Limit | 99th percentile | Nearest 0.25 | Value in force |
|---|---:|---:|---:|
| R_max | 1.6847 tau | 1.75 | 1.75 tau = 0.35 |
| R_jump_max | 4.8233 tau | 4.75 | 4.75 tau = 0.95 |
| R_shock_max | 3.0581 tau | 3.00 | 3.0 tau = 0.60 |
| Gross leverage | 7.1847 | 7.25 | 7.25 |
| Net leverage | 5.5926 | 5.50 | 5.5 |

The net percentile, 5.5926, lies 0.032 under the midpoint between 5.50 and 5.75, so it rounds to 5.50. A change of
universe, sizing or covariance changes the readings, so the limits are set again from a new run of the same
kind. Section 13 gives Carver's own values beside these.

**One scalar, applied once.** `m` is the smallest of `min(1, limit / reading)` over the five terms
(`overlay::multiplier`, `src/risk/overlay.cpp`). The scaled target of every free symbol is
`m x capped target` (`one_pass.cpp`). Held rows are counted in every reading and are not scaled. The
binding term is the first term at m, or `none`.

**When it cannot answer.** If an input or a reading is not a finite number, the pass is refused
(`one_pass.cpp`): every sleeve keeps its held book, there is no search, no trim and no fill,
and a REFUSE decision is recorded. A blind window is not a refusal.

**How often it cuts.** On the conservative book's sixteen-year backtest at the limits in force, the overlay cut
193 of 4,738 cycles counted from the first fill (4.1 percent): gross leverage 32, net leverage 54, R 11, R_jump
51, R_shock 45. On the two-year backtest of section 11's last part, 18 cycles were cut: 16 on R_shock, one on
R_jump and one on net leverage (2026-06-10, worked there).

---

## 7. The one pass

### 7.1 The three kinds of row

Each symbol in the pass is classified once (`one_pass.cpp`):

| Kind | Condition | Treatment |
|---|---|---|
| Free | some sleeve signals it and it is not in the hold set | capped, scaled, searched, buffered, rounded, clipped, a trim candidate |
| Held (fixed) | it is not free and not a close-out, and it has a non-zero held position, or it is a signalling hold-set symbol at zero | fixed at its held quantity; counted in every reading; never scaled, traded or trimmed |
| Close-out | it has a bar, is not held, no sleeve signals it any more, some sleeve signalled it before, and it holds a position | closed to flat with one fill |

The hold set is section 8. The deferral band (7.2) adds to it.

### 7.2 The deferral band and the forecast-sign close

Before the search, a free symbol whose held position is on the other side of its target's sign is closed
to flat with one fill at the signal close (`one_pass.cpp`). The sign is the sign of the uncapped N*,
summed over the sleeves that publish. A target of exactly zero has no sign and closes nothing. The search,
its cost term and the buffer all start from the closed book.

The deferral band postpones that close. A symbol the first sleeve signals, whose held position is opposite
to that sleeve's forecast while `abs(F) < 2`, is held that day (`one_pass.cpp`): it joins the hold set,
is fixed at its held quantity and gets no fill. The test is strict: at `abs(F) = 2` the symbol is free and
the close applies. A zero forecast is never in the band. On BASE only the first sleeve's forecast is read;
the fast sleeve's never enters the band.

A pending roll wins over both (section 8): with `per_contract_rolls` on, as in the templates, a symbol on a
vendor switch bar that awaits its confirming bar is held (a crossover or deadline roll, known on its bar, is not);
with it off, a symbol on a change bar is held.

### 7.3 The search from the held book

The optimiser's covariance is its own (`optimiser_covariance`, `one_pass.cpp`): the sample covariance of
the free symbols' adjusted returns over their last 756 consumed closes, on the dates they share, annualised
by the count of those dates. A symbol whose last close trails the newest by more than five dates, or that
leaves fewer than 20 shared returns, gets a 0.01 variance on its own and is named in a
`COVARIANCE_STALE_PARTICIPANT` or `COVARIANCE_FLOOR_DROP` warning.

The 756 is `kOptimiserBars`, a literal in `trend_following.cpp`: the sleeve hands the pass each symbol's last 756
consumed closes and adjusted levels. Each symbol keeps its own 756 before the dates are intersected, and the roots
keep different calendars, so the covariance rests on fewer than 756 shared returns. The stale threshold of five
dates is a literal in `one_pass.cpp` too; neither reads a config key (section 2).

The search minimises, over whole-contract books n:

```
TE(n) = sqrt((n u - x)' Sigma (n u - x)) + 100 x sum_i abs(n_i - h_i) x c_i / E
```

`x` is the scaled target in weights, `h` the held book after the sign closes, and `c_i` the cost of trading
one contract at the signal close as the cost model prices it (`portfolio_manager.cpp`). A symbol the
cost model has no usable volume for has no cost; it stays in the pass as a held row and is logged
`BOOK_UNPRICED`, never traded on a default.

How it runs (`search`, `one_pass.cpp`):

- It starts from the held book, not from zero.
- Each pass tries one contract up and one contract down in every free symbol, in symbol order.
- It keeps the single step with the lowest TE (ties go to the earlier candidate) and takes it only if it
  lowers TE by more than 1e-6.
- **The sign guard** (`admissible`, `one_pass.cpp`): a step is allowed when the result is on the
  target's side or at zero, or when it moves toward zero without crossing it. A symbol whose target is
  exactly zero may only step toward zero.
- **The cap guard**: a step away from zero that lands above the per-name cap is refused. A step toward zero
  never is.
- The pass cap is the larger of 100 and twice the contracts between the held book and the target, plus one
  (`pass_cap`, `one_pass.cpp`). Reaching it logs `OPTIMISER_PASS_CAP`.

From the held book the search moves a row only when the tracking error it removes is worth more than 100 times
the trade's cost, so a holding stays unless moving it pays. Carver's greedy also prices trades from the held
contracts (AFTS, strategy twenty-five, "Dealing with costs: a cost penalty"); what differs is that it builds its
path from zero each day (section 13).

### 7.4 The granularity buffer, the rounding and the clip

The no-trade band is sized by the book's own contracts (`b_sigma`, `one_pass.cpp`):

```
B = max(0.05 x tau, largest u_i x sqrt(Sigma_ii) over the free symbols with a non-zero answer or holding)
```

The second term is the annual risk of one contract of the lumpiest symbol in the book, as a share of the
sizing capital. The first is the floor, 0.01 at tau 0.20.

Then (`buffer`, `one_pass.cpp`), with y the search's answer:

```
TE_h = sqrt(d' Sigma d),  d_i = (h_i - y_i) u_i        no cost term
if TE_h <= B: no trade, the book is h
else:         a = (TE_h - B) / TE_h,  book = round(h + a x (y - h))
```

The book trades toward the answer only as far as the edge of the band. The rounding is to the nearest whole
contract, a half away from zero (`round_half_away`, `one_pass.cpp`). It is the only rounding in the
loop. When the rounding gives the held book back, the OPTIMISER line says so.

After the rounding, every free row beyond the per-name cap is clipped toward zero to
`floor(L / u)` contracts (`clip_to_cap`, `one_pass.cpp`). A held row beyond the cap is not touched; it
is marked instead (7.5).

So the per-name cap acts in three places: on the target the overlay reads, as a guard in the search, and
as a clip on the rounded book.

### 7.5 The trim

The rounded, clipped book is read once more by the overlay (`one_pass.cpp`). If a reading is
over its limit, `trim` removes one contract at a time (`one_pass.cpp`):

- the first breached term in the order R, R_jump, R_shock, L_g, L_n is served first;
- the contract removed is the one, among the free non-zero rows, whose removal lowers that reading most,
  toward zero on a short as on a long;
- the book is re-read after each removal; at most five contracts in a day; a removal that lowers nothing
  stops it.

What is still over a limit afterwards is stored as it is and marked: `over_limit_after_rounding_terms` and
the excess in units of the largest stored weight per contract, with one `RISK_TRIM` warning. When held rows
alone keep a reading over, or a held row is beyond the cap (term `CAP`), the mark is
`over_limit_by_hold_terms` with the held rows named, and a `RISK_OVER_LIMIT_BY_HOLD` warning. Held rows are
never cut. There is never a second scalar.

The trim is rare. On the conservative book's sixteen-year backtest at the limits in force it removed 15 contracts
on 14 days (CL 5, UB 3, HO 2, and one each of NQ, RB, 6B, HG and PL), and one contract (HG) on its two-year
backtest; no stored day was left over a limit.

---

## 8. Holds and the listing-date switch

### 8.1 The hold set

A held symbol is fixed at its held quantity for the rebalance: no fill, counted in every reading, never a
trim candidate. With a held quantity of zero it is not opened. It re-enters the search on its next usable
bar. The hold applies on sized days only; in warm-up the book follows the search
(`portfolio_manager.cpp`).

| Hold | Rule | Where it is decided |
|---|---|---|
| Session classifier | a symbol whose T-1 verdict is not `SESSION` (`JUNK`, a closure, a feed hole) is held. The key is the verdict, never whether a price exists: a junk print has a price | `SessionVerdict`, `include/trade_ngin/data/session_classifier.hpp`; live: `live_portfolio_conservative.cpp`; backtest: `backtest_coordinator.cpp` |
| Change bar | a symbol whose last consumed bar is pending is held until its next consumed bar, on every rebalance. With `per_contract_rolls` on, as in the conservative template, that is a vendor switch bar awaiting its confirming bar; a crossover or deadline roll is known on its own bar and is not held (`RollFromKind`, `include/trade_ngin/core/types.hpp`). On the vendor's series it is a change bar, either bar of a flip, or a bar with no instrument id inside a pending roll | live: `CHANGE_BAR_HOLD`, `live_portfolio_conservative.cpp`; backtest: `backtest_coordinator.cpp`; see [FUTURES_ROLLS.md](FUTURES_ROLLS.md) |
| Deferral band | section 7.2 | `one_pass.cpp` |
| Cannot be weighed or priced | a held symbol with no series today is valued at its last usable close; a symbol with no cost is fixed | `BOOK_UNPRICED`, `portfolio_manager.cpp` |

The live runner hands its set to the pass through `PortfolioManager::set_hold_set`
(`live_portfolio_conservative.cpp`). The backtest hands the set of session symbols, and every symbol
outside it is held.

### 8.2 Never-signalled symbols

A symbol publishes no target until its sleeve holds as many bars as its longest EMA (256 for the trend
sleeve, 64 for the fast one; `is_signalling`, `trend_following.cpp`). Such a symbol is not a free row.
At zero it is not a participant of the pass. With a held position it is a held row. It is not a close-out, because a
close-out needs an earlier signal (`one_pass.cpp`). A live process keeps no memory of earlier signals,
so there a held position stands for one (`portfolio_manager.cpp`).

### 8.3 Close-outs

A symbol that signalled before and no longer does, or whose sleeve is stopped, is closed with one fill to
flat at the signal close on a session bar. It does not go through the buffer. On a non-session bar it is a
hold.

### 8.4 Listing dates and the switch

`portfolio.json` `listing_dates` declares contracts that trade only from a date, and the contract the book
trades before it. On the conservative template (the base template carries only the MGC and SIL rows):

| Listed contract | Listed | Predecessor | Ratio |
|---|---|---|---|
| MES, MNQ, MYM, M2K | 2019-05-06 | ES, NQ, YM, RTY | 10 |
| MGC (micro gold) | 2010-10-04 | GC | 10 |
| SIL (micro silver) | 2013-06-17 | SI | 5 |

The listed contract's symbol carries the price history from before its listing (for the equity micros the
vendor stores the E-mini's history under the micro's symbol; for MGC and SIL the per-contract source takes the
bars before the listing from GC's and SI's contracts), so a backtest whose window starts before the date runs
the predecessor as a symbol of its own on those same bars (`ListingDates`, `listing_dates.hpp`). The predecessor
may hold a position only on signal bars dated before the listing date, the listed contract only on or after it
(`tradeable`, `listing_dates.hpp`; read at `trend_following.cpp`). Each is costed on its own contract's size and
fee, and the predecessor's exit on the switch is priced on the full-size contract's own volume
(`listing_dates.cpp`). The pair counts as one instrument for its weight, its removed rules and the equity slow
rule. MGC's listing comes before the first traded day of a sixteen-year window, so such a window never trades
GC; it trades SI until 2013-06-17.

The switch is made inside the pass of a backtest, once per pair, on the first sized rebalance whose signal bar is dated on
or after the listing date with both contracts free to trade and the listed contract signalling
(`portfolio_manager.cpp`). Under the rule in force, `open_at_target`
(`plan_listing_switch`, `src/data/listing_dates.cpp`):

1. the held predecessor is exited in full;
2. the listed contract is entered at that day's target after the overlay's scalar: the capped target times
   m, rounded to the nearest whole contract, and clipped to the cap. These are the pass's own `cap_target`,
   `round_half_away` and `clip_to_cap`, called, not copied;
3. the pass then runs from that held book.

The scalar is read from the pass itself, run once on the book with the due predecessors exited
(`portfolio_manager.cpp`), so on a day the overlay cuts, the entry lands on the cut target.

The two moves are fills of type STRATEGY at the signal close, told apart by their ids: `LC-<sleeve>-<n>`
for the predecessor's exit and `LO-<sleeve>-<n>` for the listed contract's entry
(`make_listing_switch_fills`, `listing_dates.cpp`). On a book of several sleeves the switch is the
book's: the net holding is exited, the summed target entered, and the whole number is split and netted by
the pass's own rules (section 9).

Exceptions, all in the code cited above:

- a predecessor held inside the deferral band is carried instead: q predecessor contracts become exactly
  ratio x q listed contracts, and the pass holds them;
- if either contract is in the hold set that day, the switch waits, the predecessor stays held, and the
  listed contract is not opened beside it;
- if the pass is refused, the switch is undone and made on a later pass;
- a pair whose predecessor never traded in the run has no switch.

A live run never trades a predecessor. With contracts declared it refuses, before anything of the day is
stored, a predecessor in its universe or its stored book on or after the listing date, and a signal date
before the listing date of a listed contract in its universe (`live_listing_refusals`, `include/trade_ngin/live/live_listing_guard.hpp`).

Worked switch, an example of the arithmetic: a sixteen-year backtest of the conservative book (2010-10-07
to 2026-10-07) on the conservative configuration of that time (see the note at the head), which carried the
listing-date, relabel and rule-removal blocks, the cycle of 2019-05-07 on the signal bar of 2019-05-06. The overlay's scalar is 1 that day.
Targets are from the run's `LISTING_LEG` log lines; fills are that run's `backtest.executions` rows.

| Pair | Predecessor held | Listed contract's scaled target | Rounded entry | `LC-` fill | `LO-` fill | Fill price |
|---|---|---|---|---|---|---|
| ES to MES | 0 | 4.484 | 4 | none | BUY 4 MES | 2,918.00 |
| NQ to MNQ | 1 | 4.788 | 5 | SELL 1 NQ | BUY 5 MNQ | 7,762.75 |
| YM to MYM | 1 | 1.899 | 2 | SELL 1 YM | BUY 2 MYM | 26,281.00 |
| RTY to M2K | 0 | 3.526 | 4 | none | BUY 4 M2K | 1,609.60 |

The commission on each fill is the contract's own fee: 2.247 on the one NQ, 5 x 0.614 = 3.07 on the five
MNQ.

### 8.5 The relabel list

`portfolio.json` `instrument_id_relabels` declares vendor instrument-id changes that are not rolls (four
entries dated 2026-02-22, one per equity index micro). With `per_contract_rolls` off, from the date a bar
carrying the new id is read as carrying the old one, at the loader (`InstrumentIdRelabel`, `listing_dates.hpp`),
and no consumer sees a change: no change bar, no hold, no roll legs. With `per_contract_rolls` on, as in the
conservative template, the list is declared and not read (`relabels_read`, `listing_dates.cpp`; one
`INSTRUMENT_ID_RELABELS not read` line is logged): the per-contract roll rule reads each of these id changes as
a change of price source between two contracts of the same expiry (its alias trigger, `contract_roll_rule.hpp`),
which books no legs and no cost. See [FUTURES_ROLLS.md](FUTURES_ROLLS.md).

---

## 9. Sleeves on BASE

BASE has two sleeves: the trend sleeve at allocation 0.7 and tau 0.20, and the
fast sleeve at 0.3 and tau 0.25. How the loop treats several sleeves:

- **Sized on capital times allocation.** Each sleeve's N* is sized on E times its allocation
  (`portfolio_manager.cpp`), so its contracts are already contracts of the account's book.
- **Aggregation.** The book's target for a symbol is the sum of the sleeves' N*, with no allocation applied
  a second time (`portfolio_manager.cpp`). The held book is the sum of the sleeves' held
  quantities.
- **One pass on the book.** The overlay, the search, the buffer and the trim run once on the summed book.
  The book-level tau, the three risk ratios, the deferral band and the equity slow rule are the first
  sleeve's.
- **The split.** Each moved row is split back in proportion to the sleeves' unrounded contributions by
  largest remainder, so the sleeves' whole numbers sum to the book's exactly
  (`distribute_optimizer_contracts`, `allocation_split.hpp`). When the sleeves' targets have opposite
  signs, only the optimiser's deviation from the net target is split, so each sleeve keeps its own side.
- **Netting.** Each sleeve's fill is priced as if it traded alone; the account sends one order per symbol,
  and each row's `netting_adjustment` is its share of what the account did not pay. Every cost total (a
  day's costs, a run's costs, the costs the sizing capital reads) is the fills' own costs minus their
  adjustments. In the backtest the
  sign closes are netted among themselves and the other fills among themselves
  (`portfolio_manager.cpp`). Roll legs are never netted. See [COST_MODEL.md](COST_MODEL.md).
- **A stopped sleeve** signals nothing, so its held rows close on the next session bar. A stopped first
  sleeve leaves nothing to weigh the book on and refuses the pass.

---

## 10. What is stored and what is logged

### 10.1 Stored each day

| Where | What |
|---|---|
| `trading.live_results.risk_scale` | the delivered scale: the stored book's gross weight over the gross weight of the capped target the overlay read, held rows counted in both, at the raw signal closes. It can exceed 1. A day with no sized rebalance, and a flat target, store 1. It is not the request (`delivered_scale_for_book`, `portfolio_manager.cpp`; written at `live_portfolio_conservative.cpp`) |
| `trading.live_results.risk_detail` and `backtest.equity_curve.risk_detail` (jsonb, migration 020) | the loop's record of the rebalance, nine flat keys. Written on the row of a sized rebalance the overlay answered; NULL on a refused rebalance, a day with no session, a warm-up row and every equity row (`risk_detail_json`, `risk_detail.hpp`) |
| `trading.signals.signal_value` | the forecast F of each symbol after the equity slow rule |
| `trading.positions`, `trading.executions` | each sleeve's stored book and fills. A live forecast-sign close is its own fill with the id suffix `_SC` (`session_book_gate.hpp`), stored ahead of the symbol's other fill of the day |
| `backtest.executions` | the same fills with ids `EX-<sleeve>-<n>`, and the switch fills `LC-` and `LO-` |

The nine keys of `risk_detail` (`kRiskDetailKeys`, `risk_detail.hpp`):

| Key | Type | Meaning |
|---|---|---|
| `risk_requested` | number | m, 1.0 with no cut |
| `binding_term` | text | `R`, `R_jump`, `R_shock`, `L_g`, `L_n` or `none` |
| `over_limit_after_rounding_terms` | text or null | the terms still over their limits after the trim, separated by `;` |
| `over_limit_after_rounding_excess` | number or null | the largest of those excesses, in units of the largest non-zero stored weight per contract |
| `over_limit_by_hold_terms` | text or null | the terms held rows keep over; `CAP` for a held row beyond the cap |
| `over_limit_by_hold_symbols` | text or null | those held rows, sorted, separated by spaces |
| `overlay_blind` | boolean | the gate window had fewer than 21 complete dates |
| `sizing_capital` | number | E, the capital the book was sized on |
| `account_value` | number | V at the same instant: S_0 plus the cumulative settled net P&L that E was built from |

The overlay's five readings of the stored book and its one-day 95 percent value at risk are
stored as columns on futures rows: `overlay_risk`, `overlay_risk_jump`, `overlay_risk_shock`,
`overlay_gross_leverage`, `overlay_net_leverage` and `var_95_1d` (migration 036 on
`trading.live_results`, 037 on `backtest.equity_curve`; `overlay_columns_of`, `overlay_columns.hpp`).
They are written on the rows that carry `risk_detail`; on a blind window the three risk readings and
`var_95_1d` are NULL. The limits and the multipliers are not stored: they are on the OVERLAY line.
The sizing capital has no column of its own.

### 10.2 The design keys in `portfolio_config`

Every run records the constants it ran with. `PortfolioConfig::to_json`
(`include/trade_ngin/portfolio/portfolio_manager.hpp`) is written to
`backtest.run_metadata.portfolio_config` and to `trading.live_run_metadata.portfolio_config`:

| Key | Value in force |
|---|---|
| `total_capital` | 500,000, the starting capital (never the day's sizing capital) |
| `opt_config.cost_penalty_scalar` | 100 |
| `opt_config.sign_close_band` | 2 |
| `opt_config.b_sigma_floor` | 0.05 |
| `opt_config.max_iterations` | 100 |
| `risk_config.R_max`, `R_jump_max`, `R_shock_max` | 1.75, 4.75, 3.0 (ratios to tau) |
| `risk_config.max_gross_leverage`, `max_net_leverage` | 7.25, 5.5 |
| `risk_config.per_name_cap` | 2 |
| `risk_config.overlay_covariance` | `carver`; written only when it is `carver`, so a `sample` book stores the object it stored before the key existed |
| `risk_config.trim_max` | 5 |
| `sizing_mode` | `half_compounding` |
| `equity_slow_rule` | the symbols and the pairs |
| `oracle_hash_list` | a fixed string that the code writes on the record of every book that names an overlay sleeve (`portfolio_manager.hpp`). No code reads it and no config key sets it |

The backtest adds `strategy_names`, `strategy_allocations` and, when the block is present,
`trading_rule_removals` (`bt_portfolio_conservative.cpp`), and the `carry` block only when carry is on
(`carry_config_json`, `include/trade_ngin/strategy/sleeve_config.hpp`). Each sleeve's tau and IDM are recorded with
the sleeve's own config, not here.

### 10.3 Logged

One of each per rebalance, in this order. A refused rebalance prints the OVERLAY line only.

| Line | Carries | Built by |
|---|---|---|
| `SIZING_CAPITAL` | live: capital, account, peak, the last settled date, the count of settled rows, the count of earlier unsettled days, then the parts Day T-1 was rebuilt from (the row before, the settlement, the costs, positions priced and unpriced). Backtest: capital, account, peak, `settled_through` | `sizing_capital_log_line`, `live_sizing_read.hpp`; `backtest_coordinator.cpp` |
| `OVERLAY` | m, the binding term, the five readings (the three risk readings print `blind` on a blind window), the five limits, the five multipliers, capital, tau, the window (mode, dates, first, last, complete and dropped dates), the counts of participants, free and held rows, and the symbols left out of a reading | `overlay_line`, `one_pass_log.hpp` |
| `OPTIMISER` | the search's objective, its passes, TE_h, the band B and the symbol that sets it (`floor` when the floor governs), a, whether it traded, whether the rounding returned the held book, and the lists of sign closes, cap clips, close-outs and stale rows | `optimiser_line`, `one_pass_log.hpp` |
| `BOOK` | gross weights of the raw, capped and scaled targets and of the held and stored books, the stored net, the count of held rows, the deferral-band holds, the symbols the equity slow rule zeroed, and every participant as `symbol:N*:scaled:held:stored` | `book_line`, `one_pass_log.hpp` |

Beside them: `T4_RISK_WINDOW`, `COVARIANCE_DATE_ALIGNED`, `COVARIANCE_MAX_RHO` and
`OPTIMIZER_NOT_SIGNALLING` (info), and the warnings `SIZING_CAPITAL_UNSETTLED`, `SIZING_CAPITAL_HISTORY`,
`OVERLAY_BLIND`, `OPTIMISER_PASS_CAP`, `RISK_TRIM`, `RISK_OVER_LIMIT_BY_HOLD`, `BOOK_UNPRICED`,
`COVARIANCE_STALE_PARTICIPANT` and `COVARIANCE_FLOOR_DROP`. Listing switches log `LISTING_LEG` and
`LISTING_SWITCH`.

With `TRADE_NGIN_SERIES_DUMP_DIR` set, a run also writes one row a day and one row a symbol with every
value of the pass at seventeen digits (`one_pass_record.hpp`). A production run never sets it.

---

## 11. One rebalance, end to end

An example of the arithmetic, from the same run as section 3.7 (a replay of dated live runs of the
conservative book, one run a day in date order, on the configuration of that time, which carried the
listing-date, relabel and rule-removal blocks): run date 2026-04-30, signal bar 2026-04-29. Stored rows are
`trading.live_results`, `trading.positions`, `trading.executions` and `trading.signals` of that run. Figures that no stored row carries are from the
run's log lines and its one-pass record, and are marked so.

### Step 1: sizing capital

| Quantity | Value | Source |
|---|---|---|
| Account value V | 511,005.96 | `risk_detail.account_value` |
| Peak of cumulative settled P&L | 11,005.96 | `SIZING_CAPITAL` line |
| Sizing capital E | 500,000.00 | `risk_detail.sizing_capital` |
| Day T-1 net rebuilt | 3,202.06 settlement - 33.84 costs = 3,168.22 | `SIZING_CAPITAL` line; equals the stored `daily_pnl` of 2026-04-29 |

The account is at its peak, so the drawdown is 0 and E is the start: 500,000 - (11,005.96 - 11,005.96).

### Step 2: holds

All 36 symbols have a `SESSION` verdict for 2026-04-29. One symbol is in the hold set: HG, whose last
consumed bar is a pending contract change (`CHANGE_BAR_HOLD HG.v.0 ... kind=pending_change`). It holds 0, so
it stays at 0 and is not opened, although its forecast is +3.69. No symbol is in the deferral band.

### Step 3: the book, row by row

F is the stored signal. N* and u are from the one-pass record (N* is checked for four symbols in section
5.2; u = M x P / 500,000). This table carries every row through the later steps. The overlay's scalar is 1 (step 4), and no target is
beyond the cap, so the scaled target equals N*. The column `h + a (y - h)` uses a = 0.6145 from step 6, and h after the sign close.

| Symbol | F | N* | u | Held | Row | Scaled target | Search answer y | h + a (y - h) | Stored | Fill |
|---|---|---|---|---|---|---|---|---|---|---|
| 6A | 7.00 | 0.422 | 0.1424 | 0 | free | 0.422 | 0 | 0.000 | 0 | |
| 6B | 2.05 | 0.147 | 0.1686 | 0 | free | 0.147 | 0 | 0.000 | 0 | |
| 6C | 4.67 | 0.554 | 0.1466 | 0 | free | 0.554 | 0 | 0.000 | 0 | |
| 6E | -2.91 | -0.133 | 0.2927 | 0 | free | -0.133 | 0 | 0.000 | 0 | |
| 6J | -20.00 | -1.773 | 0.1566 | -1 | free | -1.773 | -2 | -1.615 | -2 | -1 |
| 6L | 20.00 | 4.010 | 0.0401 | 2 | free | 4.010 | 2 | 2.000 | 2 | |
| 6M | 8.78 | 1.245 | 0.0568 | 1 | free | 1.245 | 1 | 1.000 | 1 | |
| 6N | -0.94 | -0.066 | 0.1170 | 0 | free | -0.066 | 0 | 0.000 | 0 | |
| 6S | -5.40 | -0.207 | 0.3177 | 0 | free | -0.207 | 0 | 0.000 | 0 | |
| CL | 10.77 | 0.130 | 0.2159 | 0 | free | 0.130 | 0 | 0.000 | 0 | |
| GC | -0.38 | -0.003 | 0.9148 | 0 | free | -0.003 | 0 | 0.000 | 0 | |
| GF | 16.52 | 0.227 | 0.3724 | 0 | free | 0.227 | 0 | 0.000 | 0 | |
| HE | -6.44 | -0.309 | 0.0830 | 0 | free | -0.309 | 0 | 0.000 | 0 | |
| HG | 3.69 | 0.090 | 0.2972 | 0 | held | held at 0 | | | 0 | |
| HO | 4.60 | 0.039 | 0.3458 | 0 | free | 0.039 | 0 | 0.000 | 0 | |
| KE | 6.95 | 0.264 | 0.0704 | 1 | free | 0.264 | 1 | 1.000 | 1 | |
| LE | 17.58 | 0.485 | 0.2041 | 1 | free | 0.485 | 1 | 1.000 | 1 | |
| M2K | 8.21 | 2.492 | 0.0275 | 2 | free | 2.492 | 2 | 2.000 | 2 | |
| MBT | -3.41 | -1.851 | 0.0152 | 1 | free, sign closed | -1.851 | -2 | -1.229 | -1 | -1 (sign close), -1 |
| MES | 9.64 | 1.291 | 0.0719 | 1 | free | 1.291 | 2 | 1.615 | 2 | +1 |
| MNQ | 10.50 | 0.683 | 0.1102 | 1 | free | 0.683 | 1 | 1.000 | 1 | |
| MYM | 7.14 | 1.562 | 0.0489 | 1 | free | 1.562 | 1 | 1.000 | 1 | |
| NG | -9.34 | -0.781 | 0.0528 | 0 | free | -0.781 | -1 | -0.615 | -1 | -1 |
| PL | -0.62 | -0.014 | 0.1900 | 0 | free | -0.014 | 0 | 0.000 | 0 | |
| RB | 8.71 | 0.091 | 0.3024 | 0 | free | 0.091 | 0 | 0.000 | 0 | |
| SI | -2.12 | -0.010 | 0.7171 | 0 | free | -0.010 | 0 | 0.000 | 0 | |
| UB | -13.76 | -1.052 | 0.2297 | 0 | free | -1.052 | -2 | -1.229 | -1 | -1 |
| ZC | -0.65 | -0.060 | 0.0478 | 0 | free | -0.060 | 0 | 0.000 | 0 | |
| ZF | -13.27 | -3.546 | 0.2153 | -3 | free | -3.546 | -3 | -3.000 | -3 | |
| ZL | 16.13 | 0.540 | 0.0891 | 1 | free | 0.540 | 1 | 1.000 | 1 | |
| ZM | 10.20 | 0.527 | 0.0648 | 1 | free | 0.527 | 1 | 1.000 | 1 | |
| ZN | -10.18 | -1.681 | 0.2206 | 0 | free | -1.681 | 0 | 0.000 | 0 | |
| ZR | -12.38 | -1.221 | 0.0437 | 0 | free | -1.221 | 0 | 0.000 | 0 | |
| ZS | 5.28 | 0.223 | 0.1198 | 1 | free | 0.223 | 0 | 0.385 | 0 | -1 |
| ZT | -11.42 | -2.807 | 0.4139 | -1 | free | -2.807 | -3 | -2.229 | -2 | -1 |
| ZW | 3.09 | 0.123 | 0.0653 | 1 | free | 0.123 | 0 | 0.385 | 0 | -1 |

The "Held" column equals the stored `trading.positions` of 2026-04-29 and the "Stored" column those of
2026-04-30.

### Step 4: the overlay

Readings of the capped target, from the `OVERLAY` line, against the limits this run used (on the sample
covariance; the values in force are in section 6). L_g and L_n are recomputed from the table above
(the sum of abs(N* x u) and of N* x u over the 35 free rows; HG adds 0) and agree to three decimals (the
table's columns are rounded).

| Term | Reading | Limit | min(1, limit / reading) |
|---|---|---|---|
| R | 0.1059 | 0.45 | 1 |
| R_jump | 0.3007 | 0.90 | 1 |
| R_shock | 0.3199 | 0.80 | 1 |
| L_g | 4.1905 | 8.0 | 1 |
| L_n | -1.9934 (size 1.9934) | 6.0 | 1 |

m = 1, binding term `none`. The window is complete: 252 dates from 2025-07-10 to 2026-04-29, of which 185
are complete; 36 participants, 35 free and 1 held.

### Step 5: the forecast-sign close

MBT holds +1 with a forecast of -3.41. That is beyond the band of 2, so it is not deferred: it is closed to
flat with one fill before the search. The search starts from MBT at 0.

### Step 6: the search and the buffer

From the `OPTIMISER` line: 11 passes, no pass cap; the search's answer is the column y.

| Quantity | Value |
|---|---|
| TE_h, the held book against the answer | 0.09137 |
| B, the band | 0.03522, set by NG (one NG contract: u 0.0528 times an annual volatility of about 0.668) |
| Floor of the band, 0.05 x 0.20 | 0.01 |
| a = (TE_h - B) / TE_h | (0.09137 - 0.03522) / 0.09137 = 0.6145 |

TE_h is above the band, so the book trades 0.6145 of the way from the held book to the answer and rounds.
Three rows show the effect:

- MES: 1 + 0.6145 x (2 - 1) = 1.615, stored 2.
- UB: 0 + 0.6145 x (-2 - 0) = -1.229, stored -1. The answer was -2; the band delivers one.
- ZS: 1 + 0.6145 x (0 - 1) = 0.385, stored 0.

The search answers for the book, not row by row: ZN has a target of -1.681 and stays at 0, while ZT and UB
each go beyond their own targets.

### Step 7: clip and trim

No row is beyond the cap (the largest stored weight is ZT, 2 x 0.4139 = 0.83, against 2). The re-read of
the stored book is inside every limit, so the trim removes nothing: stored L_g 3.0076, stored L_n -1.1612.

### Step 8: what is stored

| Stored | Value | Check |
|---|---|---|
| `risk_scale` | 0.7177 | stored gross 3.0076 / capped target gross 4.1905 = 0.7177 |
| `gross_notional` | 1,503,789.19 | 3.007578375 x 500,000 |
| `net_notional` | -580,612.19 | -1.161224375 x 500,000 |
| `active_positions` | 16 | the non-zero rows of the Stored column |
| `daily_transaction_costs` | 75.14 | the sum of the nine fills below |
| `risk_detail` | `risk_requested` 1.0, `binding_term` `none`, `overlay_blind` false, `sizing_capital` 500000.0, `account_value` 511005.961068, the four over-limit keys null | |

The nine fills, all at the 2026-04-29 close and dated 2026-04-30 (`trading.executions`):

| `exec_id` | Side | Quantity | Price | Cost |
|---|---|---|---|---|
| `EXEC_6J.v.0_20260430` | SELL | 1 | 0.006264 | 4.87 |
| `EXEC_MBT.v.0_20260430_SC` | SELL | 1 | 76,035 | 2.46 |
| `EXEC_MBT.v.0_20260430` | SELL | 1 | 76,035 | 2.46 |
| `EXEC_MES.v.0_20260430` | BUY | 1 | 7,192.50 | 1.34 |
| `EXEC_NG.v.0_20260430` | SELL | 1 | 2.638 | 15.51 |
| `EXEC_UB.v.0_20260430` | SELL | 1 | 114.84375 | 10.00 |
| `EXEC_ZS.v.0_20260430` | SELL | 1 | 1,197.75 | 15.20 |
| `EXEC_ZT.v.0_20260430` | SELL | 1 | 103.46875 | 3.65 |
| `EXEC_ZW.v.0_20260430` | SELL | 1 | 653.00 | 19.65 |

MBT has two fills: the sign close to flat, then the move to -1. Every `netting_adjustment` is 0: the book
has one sleeve.

### A day the net leverage limit binds, on the template's present settings

A two-year backtest of the conservative book on the template's present settings (per-contract rolls, the gold
and silver micros, the overlay on the carver covariance at the limits of section 6): signal date 2026-06-10, cycle 2026-06-11, the only cycle of that run cut by net leverage.
Every figure is from the run's one-pass record (`one_pass_record.hpp`). E = 447,895.51; the window is complete,
252 dates of which 201 complete.

**The overlay.**

| Term | Reading of the capped target | Limit | min(1, limit / reading) |
|---|---:|---:|---:|
| R | 0.241677 | 0.35 | 1 |
| R_jump | 0.561330 | 0.95 | 1 |
| R_shock | 0.481710 | 0.60 | 1 |
| L_g | 6.339650 | 7.25 | 1 |
| L_n | -5.508971 (size 5.508971) | 5.50 | 0.99837164 |
| m, binding | | | 0.99837164, `L_n` |

Every free row's capped target is multiplied by 0.99837164 (6A: -0.360195 to -0.359608). The stored book reads
L_n -3.867659 and L_g 5.081839, inside the limits, so the trim removes nothing. The cut moves no contract: the
buffer and the rounding absorb a scaling this small.

**The search and the buffer.** The search reached TE 0.058058 in 13 passes. TE_h = 0.122357 and B = 0.066929,
set by SIL, so a = (0.12235664 - 0.06692875) / 0.12235664 = 0.453003 to six places (the stored a is 0.45300277, from the unrounded inputs). The rows with a holding, a
search answer or a stored position:

| Symbol | u = M P / E | Held h | N* | Capped N*c | Scaled m N*c | Search y | h + a (y - h) | Stored | Cost of one contract | Cost term 100 x cost / E |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 6A | 0.156152 | 0 | -0.360195 | -0.360195 | -0.359608 | -2 | -0.906006 | -1 | 4.5327 | 0.00101200 |
| 6E | 0.321950 | -2 | -0.935367 | -0.935367 | -0.933844 | -2 | -2.000000 | -2 | 5.4839 | 0.00122437 |
| M2K | 0.031482 | +2 | +1.094619 | +1.094619 | +1.092837 | +1 | +1.546997 | +2 | 1.0149 | 0.00022659 |
| MBT | 0.013754 | -10 | -11.839523 | -11.839523 | -11.820244 | -11 | -10.453003 | -10 | 2.4335 | 0.00054332 |
| MGC | 0.090858 | 0 | -1.314319 | -1.314319 | -1.312179 | -1 | -0.453003 | 0 | 1.6457 | 0.00036744 |
| MNQ | 0.126656 | +2 | +0.165157 | +0.165157 | +0.164888 | 0 | +1.093994 | +1 | 0.9278 | 0.00020716 |
| MYM | 0.055632 | +4 | +0.528218 | +0.528218 | +0.527358 | +2 | +3.093994 | +3 | 0.9705 | 0.00021668 |
| SIL | 0.139151 | -1 | -0.327063 | -0.327063 | -0.326531 | -1 | -1.000000 | -1 | 4.9143 | 0.00109719 |
| UB | 0.253477 | -1 | -0.941289 | -0.941289 | -0.939756 | -2 | -1.453003 | -1 | 10.0083 | 0.00223451 |
| ZF | 0.237953 | -3 | -5.587177 | -5.587177 | -5.578079 | -4 | -3.453003 | -3 | 3.5581 | 0.00079441 |
| ZM | 0.067493 | 0 | -0.678568 | -0.678568 | -0.677463 | -1 | -0.453003 | 0 | 9.2250 | 0.00205964 |
| ZN | 0.243256 | -1 | -3.522592 | -3.522592 | -3.516856 | -2 | -1.453003 | -1 | 5.6443 | 0.00126018 |
| ZT | 0.459911 | -4 | -2.989688 | -2.989688 | -2.984820 | -4 | -4.000000 | -4 | 3.9052 | 0.00087190 |
| 6J, 6L, 6M, ZL | | | | | | at the held book | | unchanged | | |

What the table shows:

- The buffer moves every row 45.3 percent of the way. A row whose move is under half a contract rounds back to
  the held book (M2K, MBT, MGC, UB, ZF, ZM, ZN). A row two contracts from its search answer moves 0.906 of a
  contract and rounds to one (6A, MNQ, MYM).
- The search answers for the book, not row by row: MNQ's target is +0.165 contract and the search takes it to 0;
  MBT's is -11.82 and the search goes one contract further than the held -10, to -11.
- No row reaches the cap (L / u is 4.35 contracts for ZT and 145.4 for MBT).
- The cost term runs from 0.00021 (MNQ) to 0.00827 (GF) over the 36 rows, against a band of 0.0669: a one-contract
  trade is worth making only when it removes more tracking error than its cost term.
- The fills are 6A SELL 1, MNQ SELL 1 and MYM SELL 1, the stored book less the held.

---

## 12. Account size

The design is built for a 500,000 book. No other account size is proposed.

A book of this size is path-dependent because it holds whole contracts. One contract of many of the 36
instruments is a large share of the book's risk, so a target can be well below one contract (section 11
has such rows) and the book holds the contracts that fit. The no-trade band of section 7.4 is sized by the
risk of one contract of the lumpiest symbol in the book, and the search of section 7.3 starts from the held
book. So when a position opens, changes or closes depends on what the book already holds, on the sizing
capital of the day and on the prices of the day, and two runs that start on different dates can hold
different books on the same day.

---

## 13. Where the loop departs from Carver

Each row carries one of three labels, the same as in
[TREND_FOLLOWING_SYSTEM.md](TREND_FOLLOWING_SYSTEM.md). CARVER AS WRITTEN: the engine does what AFTS
describes, with Carver's own numbers. CARVER, ADAPTED: Carver's method with a stated difference. OURS:
AFTS has no such rule. "Not adopted" marks a rule of AFTS the engine does not use. Departures that concern rolls and the adjusted
series are in [FUTURES_ROLLS.md](FUTURES_ROLLS.md), and those of the cost model in
[COST_MODEL.md](COST_MODEL.md).

| Topic | Carver (AFTS) | The engine | Why | Kind |
|---|---|---|---|---|
| Forecast scalars | fixed per rule (strategy nine, table 29) | the same six values | | CARVER AS WRITTEN |
| Forecast weights and multiplier | equal weights and a multiplier by the set of rules (strategy nine, table 36) | the same table, read by the number of pairs (BASE's fast sleeve reads the four-pair row for its four fastest pairs) | | CARVER AS WRITTEN |
| Removing rules by cost | a rule is dropped from an instrument when its cost is over 0.15 Sharpe-ratio units (strategy nine, "Removing expensive trading rules"; turnovers of table 35) | the same procedure, limit and turnover table, with our cost inputs: the engine's cost of one contract, its sizing volatility, its confirmed rolls | the costs must be the ones this engine charges | CARVER, ADAPTED |
| Asymmetric rules | advises against asymmetric adjustments to a forecast (strategy twelve) | the equity slow rule: an equity index short needs both slow pairs negative | adopted on 2026-10-05 on this book's record: on the sixteen-year backtest 113 of 190 equity-index shorts were opened on the fast speeds alone, and equity shorts as a whole lost 121,403. That fits Carver's finding that equities are the weakest trend class (a median Sharpe of 0.03 for multiple-speed trend, strategy nine, table 37, p.206). The rule lets an equity index short open only when the slow trend agrees. It is a declared departure from Carver, who advises against asymmetric adjustments by asset class because those results are not statistically significant (strategy twelve, p.268). As an economic reading only: Carver finds that in equity indices a strong negative fast forecast is followed by a rally, a "bouncing cat" in the two fastest speeds (strategy twelve, figure 53), while this rule keys on the slow speeds agreeing | OURS |
| Volatility estimate | EWMA standard deviation of span 32, centred on its EWMA mean, blended 0.7 with 0.3 of a ten-year average (strategy three, "Forecasting future volatility") | the same estimator and blend, with a seeded and floored variance and a long run of the trailing 2,520 values, on the adjusted change over the raw previous close | a series that starts or sits flat must not give a zero | CARVER, ADAPTED |
| Annualisation | 16, the square root of 256 business days | the square root of the bars a year counted over the trailing 256 bars | a series with a Sunday session row has more than 256 bars a year | CARVER, ADAPTED |
| Volatility attenuation | the quantile of volatility relative to its long-run average, a multiplier of 2 - 1.5 Q, a ten-day smooth (strategy thirteen) | the same multiplier and smooth, with Q the quantile of `sigma_short` in its own trailing 2,520 values | one estimator serves the quantile | CARVER, ADAPTED |
| Instrument weights | a top-down method: equal shares by asset class, then by group, then by instrument (strategy four, "An algorithm for allocating instrument weights") | equal by sector in one layer, a symbol capped at half its sector | the metadata carries one sector field, and a one-contract sector must not take a whole share | CARVER, ADAPTED |
| IDM | by the number of instruments (strategy four, table 16) | 2.5, the table's value for 30 or more | | CARVER AS WRITTEN |
| Sizing capital | Carver reports his backtests on fixed capital and offers two forms for a live account, full compounding and half compounding (Tactic three, "Half compounding") | half compounding in live and in the backtest alike, recomputed each run from the settled P&L | one sizing rule in both engines, so a backtest sizes as the live book will | CARVER, ADAPTED |
| Minimum position, strategy buffer | buffering under dynamic optimisation belongs to the optimiser (strategy twenty-five, "Dealing with costs: buffering") | the strategy publishes N* unbuffered and unrounded | | CARVER AS WRITTEN |
| Overlay: what is scaled | all positions are multiplied by the scalar | free rows are scaled; held rows are counted and not scaled; the trim bounds the stored book | a held row cannot trade that day | CARVER, ADAPTED |
| Overlay: net leverage reading | none: Carver's leverage reading is gross | a fifth reading, net leverage, with its own limit of 5.5, beside gross | like Carver's gross reading it needs no volatility estimate (Tactic four, "An exogenous risk overlay"), here applied to direction. It is a plain signed sum of notional weights, so the low-volatility rates and FX positions dominate it, and it does not measure the book's one-way market risk; Carver's finer form, leverage limits by asset class (footnote 292), is not built. It is a limit enforced by a cut on the days it binds, never a target: the fifth term of the daily multiplier, its level set by his 99th-percentile method (section 6). On the conservative book's sixteen-year backtest at the limits in force it was the binding term on 54 of the 193 cut cycles (section 6) | OURS |
| Overlay: limits | the 99th percentile of each measure's own history on Carver's portfolio, rounded: 1.5, 3.75 and 3.25 times the target, and 20 for leverage (Tactic four, "An exogenous risk overlay") | the same method on this book's own sixteen-year backtest with the limits out of play, each 99th percentile rounded to the nearest 0.25: 1.75, 4.75 and 3.0 times the target, 7.25 for gross leverage (section 6) | Carver sets each limit from the history of his own portfolio and notes that another set of instruments calls for other limits; the rounding is fixed so that the values follow from the run | CARVER, ADAPTED |
| Overlay: jump reading | a jump risk multiplier from the 99th percentile of each instrument's volatility | on each symbol's jump volatility (the 99th percentile of its trailing 2,520 `sigma_short` values, each taken back to a daily figure, annualised by the gate window's factor), with the overlay's correlations | uses the engine's own estimator | CARVER, ADAPTED |
| Overlay: thin windows | | below 21 complete dates the covariance readings are blind and leverage still applies; a participant with no return in the window is out of the three covariance readings and stays in leverage; one with fewer than 120 returns is left out of R and R_jump and stays in R_shock once it has two returns (on its sizing volatility with `carver`, its own window volatility with `sample`) and in leverage | a new listing must not blind the book | OURS |
| Overlay covariance | EWMA volatility with correlations from six months of weekly returns (strategy twenty-five) | `carver`: each symbol's sizing volatility with exponentially weighted correlations of weekly returns, span 26 weeks, over each symbol's last 300 bars; membership and annualisation are the gate window's | Carver's own estimator for the overlay (Tactic four, footnote 289). The volatility is each symbol's sizing volatility (section 3.2), so the overlay reads risk on the estimate the position was sized on. The correlations come from weekly returns because daily closes taken hours apart give correlations that are too low (his reason), and the 26-week exponential weighting puts most weight on recent weeks, so a rise in correlations shows within weeks, where an equal-weight sample of the last 252 dates spreads it over a year. Carver notes that the method "isn't too sensitive to how the covariance matrix is estimated" (footnote 289, p.622); the limits of section 6 were set by his 99th-percentile method on this same estimator, so the estimator and the limits go together | CARVER, ADAPTED |
| Optimiser covariance | the same covariance as above (strategy twenty-five) | the sample covariance of daily adjusted returns over each symbol's last 756 closes, dates intersected | the window length was set on 2026-09-21: an equal-weight sample's answer moves with its length, and 756 closes (`kOptimiserBars`) kept the optimiser on the same prices the trend sleeve then held for itself (its own history was capped at 756 prices). A three-year equal-weight sample of daily adjusted returns moves slowly, so the search's substitutions and the one-contract risks that set the buffer change little from day to day; Carver's estimator follows recent risk, so the substitutions and the buffer move with each change in volatility. Keeping the sample over Carver's estimator is a measured result, not a design reason. Data, paired by start over five windows (2, 5, 10 and 16 years and the last year): with Carver's optimiser covariance alone the Sharpe was about level on the 2-year, the 10-year and the last year, 0.13 lower on the 5-year and 0.15 lower on the 16-year | CARVER, ADAPTED |
| Per-name cap | a position limit from a leverage ratio, with two as the worked example (Tactic four, "Setting position limits") | a cap of 2 on a position's weight on the sizing capital, applied to the target, in the search and on the rounded book | the overlay must never read a position the book cannot hold | CARVER, ADAPTED |
| Position limit at the maximum forecast, open-interest limit | a per-instrument limit from the position at the maximum forecast, and one as a share of open interest | not applied | by design, not on its numbers: the per-name cap already bounds each name (section 14) | Not adopted |
| Starting point of the search | the greedy algorithm starts from a zero weight in every instrument (strategy twenty-five, "The greedy algorithm") | from the held book, one contract up or down per step, with the sign guard | Carver's cost penalty is already measured from the held contracts, so the difference is where the path starts: from zero the greedy rebuilds a lumpy book each day and two nearly equal answers can flip from one day to the next; from the held book a holding is kept unless moving it lowers the objective | CARVER, ADAPTED |
| Cost multiplier | 50, with the remark that anything from 10 to 100 behaves much the same (strategy twenty-five, "Dealing with costs: a cost penalty") | 100, the top of that range | the cost penalty is the one lever that reliably cuts this book's churn toward its bar of about 150 round trips a year, and it stops at 100, the top of Carver's range of 10 to 100 (p.428), because above it trading collapses onto the cheapest instruments, as Carver warns (p.430): on a reference model run outside the engine (2026-10-05), livestock was in a position on 2 percent of the sixteen years' days at 300, against 68 percent at 50. The bar is set near what the long-only book already traded (111 to 167 round trips a year), because the excess trading was resizes: the book's round trips per traded instrument (3 to 11 a year) were already in the range Carver expects. The weight is the price the search puts on cost against tracking error, the book's cost budget: at 100 the search trades only where a whole-contract step removes tracking error worth more than 100 times the trade's cost over the sizing capital, the most weight on cost Carver's range allows. The size of the book does not set it: a contract's tracking-error term and its cost term both scale as one over the capital. The data: fewer round trips at 100 than at 50 on the 2-, 5- and 16-year windows | CARVER, ADAPTED |
| Forecast changes sign | the greedy search from zero closes the whole position; Carver names this a floor on turnover, and his tracking-error buffer then moves a wrong-signed position like any other (strategy twenty-five, "Dealing with costs: buffering") | closed once the opposite forecast reaches 2; held while it is weaker | a position held against its own forecast is a position the system expects to lose on. In Carver's full method the close at a sign change is the turnover floor his buffer solves, and his buffer moves a wrong-signed row toward zero like any other; here the buffer is one lumpy contract wide and the search starts from the held book, so without the close a wrong-signed row sat against its forecast a median of 4 cycles (longest 217) against 1 (longest 14) with it, on the earlier full-size book. Turnover did not rise with the close: 140 round trips a year without it on the sixteen-year backtest, 115 with it. The band waits while the opposite forecast is under 2, on a scale whose average absolute forecast is 10, a weak reading of a sign change; its measured effect on round trips is near zero | OURS |
| Tracking-error buffer | 5 percent of the risk target, 1 percent at a 20 percent target | the larger of that figure and the risk of one contract of the lumpiest symbol in the book | on a 500,000 book one contract of many instruments is larger than a 1 percent band | CARVER, ADAPTED |
| After the rounding | | the trim: up to five contracts removed when the rounded book is over a limit | whole contracts can land a book above a limit the target was inside | OURS |
| Session logic | none | the classifier's holds, applied inside the pass | a bar that is not a usable print must not move the book | OURS |
| A held symbol's target | | set to its holding, so no correlated symbol stands in for it | avoids hedging a held row with its neighbours | OURS |
| Several sleeves in one book | one strategy per account | BASE's two sleeves: summed targets, one pass, a split by largest remainder | the account sends one order per symbol, so the sleeves' targets are summed and the book is searched once | OURS |
| Netting between sleeves | | opposite sleeve fills in one symbol are credited the cost the account did not pay ([COST_MODEL.md](COST_MODEL.md)) | the account sends one order per symbol | OURS |
| Listing dates and the switch | | a contract trades only from its listing date, the predecessor before it; on the switch the predecessor is exited and the listed contract entered at the day's scaled target | a backtest must not trade a contract before it existed | OURS |
| The relabel list | | declared vendor id changes that are not rolls are read as no change | a relabel must not be booked as a roll | OURS |
| Minimum volatility | an instrument whose volatility is below a minimum is left out of the universe (Tactic four, "Instruments that are too safe to trade") | no screen; the per-name cap bounds a low-volatility contract's position every day | the cap is the daily limit, and no contract is removed from the 36 | Not adopted |
| A stopped sleeve | | its held rows are closed with one fill on the next session bar | a sleeve that no longer signals must not leave positions behind | OURS |
| Order of the overlay and the optimiser | the multiplier is applied to the unrounded positions, then the buffering (Tactic four, "An exogenous risk overlay") | the same order, once: the overlay reads the capped target, the scalar is applied once and nothing feeds back into it; the trim follows the rounding | | CARVER AS WRITTEN |
| Fast sleeve's volatility span | one span for every rule | BASE's fast sleeve uses 16 | by design (2025-12-21): BASE's fast sleeve is a sleeve meant to react and trade faster than the main one, so its volatility span is 16 days where the main sleeve's is 32; the conservative book's one sleeve uses one span | OURS |

---

## 14. Tried and not adopted

Each of these is an option the design does not use, with the reason.

| Option | What it is | Why it is not adopted |
|---|---|---|
| Carver's per-instrument position limit | a limit from the position at the maximum forecast, and a limit as a share of open interest (Tactic four, "Setting position limits") | Not built, by design and not on its numbers (decided 2026-10-05). The per-name cap of 2, Carver's own example value for a leverage limit (Tactic four, "Setting position limits"), already bounds each name, so his limit at the maximum forecast, which he keeps under dynamic optimisation at a chosen nominal 10 or 20 percent weight (Tactic four, "Position limits and dynamic optimisation"), would be a second, overlapping per-name cap on a picked parameter. The two bound different things: the cap bounds a name's notional weight (at the cap of 2 a contract with 40 percent volatility carries 0.8 of risk against a target of 0.2), where Carver's limit bounds a name's share of the risk; the overlay's risk limits (section 6) bound the risk of the book as a whole. The open-interest limit does not bind at 500,000, and the book holds no open-interest data. As support, from a reference model run outside the engine: at a 10 percent weight the limit changed the held book once in sixteen years (heating oil, 2 to 1 contracts, 2022-03-08, which took that half-compounded run's worst day from -10.9 to -6.7 percent) and clipped three target rows; at 20 percent the 5- and 16-year results were identical with and without it |
| Switching other contracts to micro or mini sizes | trading the micro or mini of the contracts that have one, beyond the four equity index micros and the gold and silver micros the book already trades | the book then holds several contracts where it holds none or one, so every one-lot adjustment becomes a trade. Gold and silver are the exception: one GC contract weighs about 0.9 of the sizing capital and one SI about 0.7 (section 11), so at full size the per-name cap let the book hold at most two contracts of either, each step a large share of the capital |
| Sector caps | at most K names of a sector may hold a position | the cap changes the risk the book runs instead of how it trades |
| Search hysteresis | the search pays an extra term, a multiple of a contract's own risk, to trade it | the term decides which groups of instruments the book can hold at all |
| Blanket speed changes | removing the fastest rules from every contract | the six speeds are Carver's design, each catching trends of a different length. He removes a speed only on a contract where its cost is above his speed limit of 0.15 Sharpe a year, which is what section 3.5 applies; a blanket cut also removes speeds where they pay. On the unrounded target book over sixteen years the fast speeds earned 196k of the 270k; on a reference model's sixteen years outside the engine (long and short, fixed 500,000 sizing), cutting 2/8 and 4/16 on every contract saved 2,079 dollars a year of cost (0.4 percent of the account); and a speed set chosen on one half of the history did not hold on the other half |
| A roll back-refusal on the vendor's series (`per_contract_rolls` off) | refusing a vendor switch back into the contract the series has just left | on the vendor's series it cannot tell a whipsaw from the vendor's early look at the next contract, and in the second case it holds the symbol on the wrong contract. With `per_contract_rolls` on, as in the templates, the roll rule itself never rolls back and ignores one-bar strays, so this case does not arise. See [FUTURES_ROLLS.md](FUTURES_ROLLS.md) |

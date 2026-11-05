# Optimization Module

## Overview

`src/optimization/` holds the two position optimisers the `PortfolioManager` can run. Which one a
book gets is decided once per rebalance in `PortfolioManager::process_market_data`
(`src/portfolio/portfolio_manager.cpp`):

| Book | Step | Entry point |
|---|---|---|
| A futures book (one that names an overlay sleeve) | the one pass | `one_pass::rebalance` (`src/optimization/one_pass.cpp`), called from `PortfolioManager::rebalance_one_pass` (`src/portfolio/portfolio_manager.cpp`) |
| Every other book | the generic optimiser step, once, if `use_optimization` is true | `DynamicOptimizer::optimize`, called from `PortfolioManager::optimize_positions` (`src/portfolio/portfolio_manager.cpp`). No shipped book runs it: the equity book sets `use_optimization` false and its runners refuse true |

A book that holds a trend sleeve and names no overlay sleeve is refused: a trend sleeve is never
rebalanced by the generic step (`PortfolioManager::process_market_data`, `src/portfolio/portfolio_manager.cpp`).

The design of the futures rebalance is described in
[docs/OPTIMIZER_AND_RISK_DESIGN.md](../../docs/OPTIMIZER_AND_RISK_DESIGN.md). The risk modules and
the overlay are described in [docs/RISK_MODULES.md](../../docs/RISK_MODULES.md); their code is in
`src/risk/`, not here.

---

## File layout

```
src/optimization/
├── one_pass.cpp             # the futures rebalance, as pure functions of plain vectors
└── dynamic_optimizer.cpp    # the generic optimiser step

include/trade_ngin/optimization/
├── one_pass.hpp             # the design comment of every step; DayInputs, DayResult, rebalance()
├── one_pass_log.hpp         # the OVERLAY, OPTIMISER and BOOK log lines and the trim warnings
├── one_pass_record.hpp      # optional per-day record files, written only when
│                            #   TRADE_NGIN_SERIES_DUMP_DIR names a directory
└── dynamic_optimizer.hpp    # DynamicOptConfig, OptimizationResult, DynamicOptimizer
```

---

## The one pass (futures books)

One call to `one_pass::rebalance` takes the sleeves' unrounded targets and the held book and returns
the stored whole-contract book and its fills. It reads no database and no clock, and nothing is
carried from one call to the next. Positions are in contracts; `u[i]` is the weight of one contract
of symbol `i` on the sizing capital (multiplier x raw close / capital).

The steps, in the order the function applies them. Each has its design comment in
`include/trade_ngin/optimization/one_pass.hpp`, above the function named.

| Step | What it does | Function |
|---|---|---|
| Cap the target | each symbol's target is limited to the per-name cap, `sign(N) x min(abs(N), L / u)`, still fractional | `cap_target` |
| Risk overlay | the five readings (R, R_jump, R_shock, L_g, L_n) of the capped target give one multiplier `m`, applied once to the free rows | `overlay::readings`, `overlay::multiplier` (`include/trade_ngin/risk/overlay.hpp`); the covariance they read is set by `overlay_covariance` (`carver_covariance`) |
| Forecast-sign close | a held position on the opposite side of the forecast's sign is closed to flat, once the forecast is outside the deferral band | inline in `rebalance` (`src/optimization/one_pass.cpp`); `forecast_close` is the same rule as a standalone function, called only by the tests |
| Covariance | the optimiser's own covariance: the sample covariance of each symbol's last 756 consumed closes (`kOptimiserBars`, a literal in `src/strategy/trend_following.cpp`), dates intersected | `optimiser_covariance` |
| Search | from the held book, one contract at a time, minimising `sqrt((n u - x)' Sigma (n u - x)) + cost_mult x sum(abs(n_i - h_i) x c_i / E)`, with `c_i` the cost in currency of one contract and `E` the sizing capital (the division is made in `rebalance` before the search) | `tracking_error`, `search` |
| Buffer and rounding | no trade while the held book's tracking error is at or below `B_sigma`; otherwise the book moves part of the way and is rounded to whole contracts, a half away from zero | `b_sigma`, `buffer`, `round_half_away` |
| Clip | a free row beyond the cap after rounding is clipped toward zero | `clip_to_cap` |
| Trim | while a reading of the stored book is over its limit, one contract is removed at a time, up to `trim_max` | `trim` |

There is no correlation cap inside the optimiser, the pass runs once (there are no laps), and the
strategy publishes an unbuffered target: the only buffer is the one above. If the overlay cannot
produce `m`, the day is refused and the held book is kept on every row (`DayResult::refusal`,
`one_pass.hpp`).

### Inputs and configuration

`DayInputs` (`one_pass.hpp`) lists every input. The constants come from the book's files:

| Constant | Key | File |
|---|---|---|
| Cost multiplier of the search | `optimization.cost_penalty_scalar` | `config_template/defaults.json` |
| Deferral band | `optimization.sign_close_band` | the same defaults file |
| Floor of `B_sigma` as a ratio to the risk target | `optimization.b_sigma_floor` | the same defaults file |
| Floor on the search's pass cap | `optimization.max_iterations` | the same defaults file |
| Per-name cap, trim cap, the five overlay limits, the overlay's covariance | `per_name_cap`, `trim_max`, `R_max`, `R_jump_max`, `R_shock_max`, `max_gross_leverage`, `max_net_leverage`, `overlay_covariance` on the carver module | `config_template/portfolios/<name>/risk.json` |

`apply_loop_config` (`include/trade_ngin/portfolio/loop_config.hpp`) copies the deferral band,
the floor of `B_sigma`, the sizing mode, the equity slow rule, the per-name cap and the trim cap
into the `PortfolioConfig`. The cost
multiplier and the floor on the pass cap are read by the loader into the optimiser's configuration,
and the five overlay limits stay on the carver module. Three keys do not reach the pass:
`covariance_history_prices` and `covariance_stale_dates` in `portfolio.json` and
`optimization.convergence_threshold` in the defaults file; the pass takes 756 closes, a stale rule of five dates
and a step threshold of 1e-6 from constants of the code (section 2 of
[docs/OPTIMIZER_AND_RISK_DESIGN.md](../../docs/OPTIMIZER_AND_RISK_DESIGN.md)). The values in force and the retired keys are in
[docs/CONFIG_GUIDE.md](../../docs/CONFIG_GUIDE.md).

The cost of one contract comes from the `PortfolioManager`'s transaction cost manager; see
[docs/COST_MODEL.md](../../docs/COST_MODEL.md).

---

## The generic optimiser step (books with no overlay sleeve)

`DynamicOptimizer` (`include/trade_ngin/optimization/dynamic_optimizer.hpp`) is configured by
`DynamicOptConfig` and exposes `optimize`, `optimize_single_period` and
`apply_buffering`. `OptimizationResult` carries `positions`,
`tracking_error`, `cost_penalty`, `iterations` and `converged`. On this path the cost vector is zero
for every symbol, and the portfolio risk step runs once after the optimiser.

---

## Testing

Every test is built into one binary, `trade_ngin_tests`, and `ctest` lists each case by its suite
name, so filter on suite names:

```bash
cd build
ctest -R "OnePass|DynamicOptimizer" --output-on-failure
```

---

## References

- [docs/OPTIMIZER_AND_RISK_DESIGN.md](../../docs/OPTIMIZER_AND_RISK_DESIGN.md): the futures rebalance end to end
- [docs/RISK_MODULES.md](../../docs/RISK_MODULES.md): the risk modules and the overlay
- [docs/COST_MODEL.md](../../docs/COST_MODEL.md): what a fill costs
- [docs/CONFIG_GUIDE.md](../../docs/CONFIG_GUIDE.md): the configuration keys
- [Portfolio Module](../portfolio/README.md)
- [Strategy Module](../strategy/README.md)

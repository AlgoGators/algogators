# Strategy Module

## Overview

`src/strategy/` holds the strategy interface, the common base class and the strategies the runners
build: the trend-following sleeve (in two configurations, TREND and FAST) and the equity
mean-reversion strategy. It also holds a two-leg spread strategy that no runner builds (see
[The spread strategy](#the-spread-strategy)). A strategy receives bars, computes a forecast per symbol and publishes a
target position per symbol. It does not round, buffer or cap its target on a futures book: that is
the portfolio's rebalance (see [Portfolio Module](../portfolio/README.md)).

The trend-following method, stage by stage, with every setting in force, is in
[docs/TREND_FOLLOWING_SYSTEM.md](../../docs/TREND_FOLLOWING_SYSTEM.md). The equity strategy is in
[docs/EQUITY_STRATEGY_GUIDE.md](../../docs/EQUITY_STRATEGY_GUIDE.md).

---

## File layout

```
src/strategy/
├── base_strategy.cpp        # state, positions, persistence of signals; shared by every strategy
├── trend_following.cpp      # the trend sleeve: history, weights, the unrounded target
├── trend_estimator.cpp      # volatility, EWMAC forecasts, scaling and capping, as pure functions
├── carry_estimator.cpp      # the carry rules and their blend with the trend rules (off unless configured)
├── mean_reversion.cpp       # the equity mean-reversion strategy
├── spread.cpp               # a two-leg spread (pairs) strategy; no runner builds it
└── regime_detector.cpp      # a regime classifier; no runner or strategy calls it

include/trade_ngin/strategy/
├── strategy_interface.hpp       # StrategyInterface
├── base_strategy.hpp            # BaseStrategy
├── types.hpp                    # StrategyConfig, StrategyState, StrategyMetadata, StrategyMetrics
├── trend_following.hpp          # TrendFollowingConfig, fast_trend_following_config(), TrendFollowingStrategy
├── trend_estimator.hpp          # the estimators' design comment, Window, Estimate, estimate()
├── trend_estimator_record.hpp   # optional per-day record of the estimators
├── carry_estimator.hpp          # the carry rules' design comment, Estimate, Blend
├── carry_estimator_record.hpp   # optional per-day record of the carry rules and the blend
├── carry_log.hpp                # the one line a carry-on run prints for equity shorts driven by carry
├── trend_readings.hpp           # a trend sleeve's forecast, sizing volatility and averages for the positions files
├── vol_annualisation.hpp        # bars a year counted from the bars' own dates
├── sleeve_config.hpp            # a futures sleeve's required keys; hands over the rule removals and the carry block
├── short_window_log.hpp         # the one line a run prints for symbols with a short history
├── mean_reversion.hpp           # MeanReversionConfig, MeanReversionStrategy
├── equity_strategy_builder.hpp  # builds the equity strategies from portfolio.json
├── spread.hpp                   # SpreadConfig, SpreadState, SpreadStrategy
└── regime_detector.hpp          # RegimeDetector
```

---

## The interface

`StrategyInterface` (`include/trade_ngin/strategy/strategy_interface.hpp`) is what the
`PortfolioManager` drives. `BaseStrategy` (`include/trade_ngin/strategy/base_strategy.hpp`)
implements all of it; a new strategy derives from `BaseStrategy` and overrides what it needs.

| Group | Methods |
|---|---|
| Lifecycle | `initialize`, `start`, `stop`, `pause`, `resume` |
| Data | `on_data(const std::vector<Bar>&)`, `on_execution`, `on_signal(symbol, signal)` |
| State | `get_state`, `get_metrics`, `get_config`, `get_metadata`, `get_price_history` |
| Positions | `get_positions`, `get_target_positions`, `update_position`, `seed_positions` |
| Capital and history | `set_capital_allocation`, `seed_history`, `set_backtest_mode` |
| Risk limits | `update_risk_limits`, `check_risk_limits` |
| For the rebalance | `overlay_series`, `is_signalling` |

`StrategyConfig` (`include/trade_ngin/strategy/types.hpp`) carries `capital_allocation`,
`max_leverage`, `position_limits`, `trading_params`, `costs`, the asset classes and the data
frequencies. `BaseStrategy`'s constructor is
`BaseStrategy(std::string id, StrategyConfig config, std::shared_ptr<PostgresDatabase> db)`
(`base_strategy.hpp`), and `validate_config()` is the protected hook for parameter checks.

**The risk-limit check logs and never cuts.** After each `on_data`, and on `update_risk_limits`,
`BaseStrategy::check_risk_limits` (`src/strategy/base_strategy.cpp`) computes the sleeve's leverage,
the sum of `abs(quantity x average price x contract size)` over its positions divided by its
capital allocation, and logs a WARN when it is above the sleeve's `max_leverage`. The futures
runners set that limit to the `carver` module's `max_gross_leverage`
(`loop_gross_leverage_limit`, `include/trade_ngin/portfolio/loop_config.hpp`), and the equity
runner to `risk.json`'s top-level `max_leverage`. When that limit is 0 or below, the check falls back
to the strategy config's `max_leverage`, and to 2.0 only when that is also 0 or below (which
`validate_config` refuses), and logs a WARN naming the value used. A drawdown past `max_drawdown` also only warns. Neither line changes a
position or fails the bar: the risk overlay and the risk modules own every cut (see
[docs/RISK_MODULES.md](../../docs/RISK_MODULES.md)). The leverage line's mention of a
`RiskManager` scaling the positions is older wording: on a futures book the overlay cuts on its own
readings.

---

## The trend sleeve in brief

`TrendFollowingStrategy` (`include/trade_ngin/strategy/trend_following.hpp`) is one class with
two configurations. `TrendFollowingConfig` is the TREND sleeve;
`fast_trend_following_config()` is the FAST sleeve (the four fast EMA
pairs, a 16-bar short volatility span, a 0.25 risk target). A futures sleeve's `risk_target`, `idm`
and `vol_lookback_short` are required in `portfolio.json`: a runner refuses a missing key and never
falls back to the struct's own values (`read_required_sleeve_keys`, `include/trade_ngin/strategy/sleeve_config.hpp`). The
sleeve's `vol_lookback_long` key is copied into the config and read by nothing that computes: the
long-run mean is a constant of the code, 2,520 values (`TrendFollowingConfig::vol_lookback_long`'s comment).

What the sleeve computes at each signal bar:

- **Series.** Returns and the EMAs read the back-adjusted series; the price a forecast divides by
  and the price a position is sized on are the raw close. See
  [docs/FUTURES_ROLLS.md](../../docs/FUTURES_ROLLS.md).
- **Volatility.** A mean-centred EWMA standard deviation of returns, annualised by the square root
  of the bars a year counted from the bars' own dates (not Carver's fixed 16, the square root of
  256, which only the forecast's volatility keeps), then blended:
  `sigma = 0.7 x sigma_short + 0.3 x sigma_long`, where `sigma_long` is the mean of `sigma_short`
  over the trailing 2,520 values. The design comment is in
  `include/trade_ngin/strategy/trend_estimator.hpp`; the annualisation rule is in
  `include/trade_ngin/strategy/vol_annualisation.hpp`.
- **Forecast.** For each EMA pair, `raw = (EMA_fast - EMA_slow) / (close x forecast_sigma / 16)`,
  multiplied by the attenuation and by the pair's fixed scalar and capped at plus or minus 20. The
  attenuation is `2 - 1.5 q`, `q` the smoothed share of the trailing 2,520 `sigma_short` values at
  or below the bar's own, and 1 until 252 values exist. The combined forecast is the
  equal-weight mean of the pairs the contract runs, times the forecast diversification multiplier
  for that number of pairs, capped at plus or minus 20. Pairs removed from a contract by cost come
  from `portfolio.json`'s `trading_rule_removals`; the equity slow rule comes from
  `equity_slow_rule` (the fields of `TrendFollowingConfig`).
- **Carry.** The sleeve also holds Carver's carry rules (strategies ten and eleven, `carry_estimator.hpp`),
  blended with the trend rules into one forecast when `portfolio.json` carries a `carry` block with a
  weight above 0. With the block absent, or a weight of 0, carry is off and the forecast is the trend
  forecast alone; no template carries the block. Carry needs `per_contract_rolls` on and a single trend
  sleeve (`CarryConfig`, `include/trade_ngin/core/config_loader.hpp`). The sleeve refuses carry when
  a carry span would trade faster than the trend rules each contract keeps: every span must have a
  turnover in Carver's table 40 (spans 5, 20, 60 and 120), and the fastest span's turnover must not be
  above that of the fastest EMA pair a contract keeps after its `trading_rule_removals` (table 35),
  because that pair is the one shown to pass the 0.15 speed limit on the contract
  (`TrendFollowingStrategy::validate_config`, `src/strategy/trend_following.cpp`;
  `carry_span_turnover`, `trend_speed_turnover`, `carry_estimator.hpp`). With the shipped removals
  the most trimmed contract, ZR, keeps EWMAC16 as its fastest pair, 13.2 a year against span 5's
  5.75, so the default spans pass.
- **Instrument weights.** Sector-budgeted weights that sum to 1.0, with one symbol capped at half of
  its sector's allocation (`TrendFollowingStrategy::get_weights`,
  `src/strategy/trend_following.cpp`). The universe is every metadata symbol with a bar in
  `futures_data.ohlcv_1d` at any date, less ES and less a predecessor whose listed contract is stored
  (GC while MGC is, SI while SIL is; `ListingDates::run_universe`). The weights are computed once per
  sleeve and are the same on every date of a run, so in a long backtest a contract carries its
  weight before its first bar.
- **Target.** The unrounded target

  ```
  N* = (forecast x capital x weight x IDM x risk_target) / (10 x multiplier x price x FX x sigma)
  ```

  (`TrendFollowingStrategy::calculate_position`, `src/strategy/trend_following.cpp`), where
  `capital` is the sleeve's slice of the sizing capital. The target is published as it is: the
  strategy applies no buffer, no rounding and no position cap. The per-name cap, the buffer and the
  rounding belong to the portfolio's one pass; see
  [docs/OPTIMIZER_AND_RISK_DESIGN.md](../../docs/OPTIMIZER_AND_RISK_DESIGN.md).

The warm-up a backtest excludes is the sleeve's longest EMA window
(`TrendFollowingStrategy::get_max_required_lookback`, `src/strategy/trend_following.cpp`).

---

## The spread strategy

`SpreadStrategy` (`include/trade_ngin/strategy/spread.hpp`, `src/strategy/spread.cpp`) trades
exactly two symbols as a mean-reverting pair. On each bar where both legs have a close it
re-estimates the hedge ratio `beta` by rolling least squares over `beta_period` bars (60), forms the
spread (`log(Pa) - beta x log(Pb)`, or `Pa - beta x Pb`), and turns it into a z-score over
`zscore_period` bars (20). It opens at `abs(z)` of `entry_z` (2.0), long A and short B when the
spread is cheap, closes at `exit_z` (0.5) after `min_holding_period` bars, stops out at `stop_z`
(4.0) and, after a stop, does not re-enter until the spread is back inside `exit_z`. Leg A carries
`capital_per_leg_pct` (0.10) of the sleeve's capital in notional and leg B `beta` times that. A
`beta` outside its bounds forms no spread and, by default, flattens an open position.

No runner, `portfolio.json` type string or template builds it: it is reached only from its tests
(`tests/strategy/test_spread.cpp`). The relative-value
trade definitions are a separate layer (`src/relative_value/README.md`).

---

## Adding a strategy

1. Add `include/trade_ngin/strategy/<name>.hpp` and `src/strategy/<name>.cpp`, with a class derived
   from `BaseStrategy`. Override `initialize()` and `on_data()`; override `validate_config()` for
   parameter checks. Return `Result<void>` and check every result.
2. Add the `.cpp` to `TRADE_NGIN_SOURCES` in the root `CMakeLists.txt`.
3. Add a branch for the strategy's `type` string where the runners build their sleeves, for example
   the `TrendFollowingStrategy` branch of `apps/strategies/live_portfolio.cpp` and of
   `apps/backtest/bt_portfolio.cpp` for a futures sleeve. The equity runners build theirs through
   `include/trade_ngin/strategy/equity_strategy_builder.hpp`.
4. Declare it under `strategies` in `config/portfolios/<name>/portfolio.json`, with
   `enabled_backtest`, `enabled_live`, `default_allocation`, `type` and its `config` object. See
   [docs/CONFIG_GUIDE.md](../../docs/CONFIG_GUIDE.md).
5. Add tests under `tests/strategy/`.

A book that holds a trend sleeve must name an overlay sleeve (the runners name the first sleeve);
any other book runs the generic optimiser step only when `use_optimization` is true, which no
shipped book sets (the equity book runs only the portfolio risk step). See
[Portfolio Module](../portfolio/README.md).

---

## Testing

Every test is built into one binary, `trade_ngin_tests`, and `ctest` lists each case by its suite
name, so filter on suite names:

```bash
cd build
ctest -R "TrendFollowing|TrendEstimator|BaseStrategy|MeanReversion" --output-on-failure
```

---

## References

- [docs/TREND_FOLLOWING_SYSTEM.md](../../docs/TREND_FOLLOWING_SYSTEM.md): the trend-following method end to end
- [docs/OPTIMIZER_AND_RISK_DESIGN.md](../../docs/OPTIMIZER_AND_RISK_DESIGN.md): from the target to the stored book
- [docs/FUTURES_ROLLS.md](../../docs/FUTURES_ROLLS.md): the adjusted series and rolls
- [docs/CONFIG_GUIDE.md](../../docs/CONFIG_GUIDE.md): the configuration keys
- [Backtest Module](../backtest/README.md)
- [Portfolio Module](../portfolio/README.md)
- [Transaction Cost Module](../transaction_cost/README.md)
- [Data Module](../data/README.md)

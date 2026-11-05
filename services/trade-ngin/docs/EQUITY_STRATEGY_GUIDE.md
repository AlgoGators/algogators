# Equity Strategy Guide

How to create, integrate, configure, and backtest an equities strategy in trade-ngin.

---

## Architecture Overview

Trade-ngin uses a layered strategy architecture. Every strategy, futures or equities, plugs into the same framework:

```
StrategyInterface          (pure virtual contract)
    └── BaseStrategy       (state machine, positions, PnL, risk)
            └── YourStrategy   (signal logic, custom config)
```

The backtest pipeline flows:

```
Config ─> DataLoader ─> Strategy.on_data() ─> Positions ─> ExecutionManager ─> PnLManager ─> MetricsCalculator ─> DB Storage
```

All orchestration is handled by `BacktestCoordinator`. Your strategy implements `on_data()`, which sets each symbol's target, and overrides `get_target_positions()`, which hands those targets to the portfolio; it may override `initialize()`. It never writes a target or a mark into `positions_`: that map is the book's actual holdings. Its quantity and cost basis move with fills (`BaseStrategy::on_execution()`), corporate actions (which restate `average_price`) and the live runner's seeding of the held book; the strategy itself only creates each symbol's empty entry in `initialize()` and refreshes `unrealized_pnl` from the close in `on_data()` (see Step 2 and `docs/AVERAGE_PRICE_LIFECYCLE.md`).

---

## Step 1: Define Your Strategy Config Struct

Create a config struct that holds all tunable parameters. This keeps strategy logic decoupled from hardcoded values.

**File:** `include/trade_ngin/strategy/your_strategy.hpp`

```cpp
struct YourStrategyConfig {
    int lookback_period{20};
    double entry_threshold{2.0};
    double exit_threshold{0.5};
    double risk_target{0.15};
    double position_size{0.1};        // Fraction of capital per position
    int vol_lookback{20};
    bool use_stop_loss{true};
    double stop_loss_pct{0.05};
    bool allow_fractional_shares{true};
};
```

Reference: `include/trade_ngin/strategy/mean_reversion.hpp`, `MeanReversionConfig` (it also carries `fractional_min_price` 1.00 and `fractional_min_adv` 50,000, the price and average daily volume a symbol needs before fractional shares are allowed)

---

## Step 2: Create Your Strategy Class

Extend `BaseStrategy` and implement your signal logic.

**Header:** `include/trade_ngin/strategy/your_strategy.hpp`

```cpp
#pragma once
#include "trade_ngin/strategy/base_strategy.hpp"
#include "trade_ngin/instruments/instrument_registry.hpp"

namespace trade_ngin {

struct YourInstrumentData {
    std::vector<double> price_history;
    double current_price = 0.0;
    double target_position = 0.0;
    // No entry_price: the cost basis is positions_[symbol].average_price
    double current_volatility = 0.01;
    Timestamp last_update;
    // ... add your indicator fields
};

class YourStrategy : public BaseStrategy {
public:
    YourStrategy(std::string id, StrategyConfig config,
                 YourStrategyConfig custom_config,
                 std::shared_ptr<PostgresDatabase> db,
                 std::shared_ptr<InstrumentRegistry> registry = nullptr);

    Result<void> initialize() override;
    Result<void> on_data(const std::vector<Bar>& data) override;

    // Targets come from instrument_data_, never from positions_ (Step 2, on_data)
    std::unordered_map<std::string, Position> get_target_positions() const override;

    std::unordered_map<std::string, std::vector<double>>
        get_price_history() const override;

protected:
    Result<void> validate_config() const override;

private:
    YourStrategyConfig custom_config_;
    std::shared_ptr<InstrumentRegistry> registry_;
    std::unordered_map<std::string, YourInstrumentData> instrument_data_;

    // Your indicator/signal methods
    double generate_signal(const std::string& symbol,
                           const YourInstrumentData& data) const;
    double calculate_position_size(const std::string& symbol,
                                   double price, double volatility) const;
};

}  // namespace trade_ngin
```

**Implementation:** `src/strategy/your_strategy.cpp`

Key methods to implement:

### initialize()

```cpp
Result<void> YourStrategy::initialize() {
    // 1. Call base class init
    auto base_result = BaseStrategy::initialize();
    if (base_result.is_error()) return base_result;

    // 2. Set PnL accounting (the mean-reversion strategy uses MIXED; never REALIZED_ONLY)
    set_pnl_accounting_method(PnLAccountingMethod::MIXED);

    // 3. Initialize per-symbol data from config_.trading_params
    for (const auto& [symbol, _] : config_.trading_params) {
        YourInstrumentData data;
        data.price_history.reserve(custom_config_.lookback_period * 2);
        instrument_data_[symbol] = data;

        Position pos;
        pos.symbol = symbol;
        pos.quantity = 0.0;
        pos.average_price = 0.0;
        positions_[symbol] = pos;
    }

    return Result<void>();
}
```

### on_data()

This is called once per bar batch (all symbols for one timestamp). The framework passes a `std::vector<Bar>` where each `Bar` has:

| Field       | Type       | Description              |
|-------------|------------|--------------------------|
| `symbol`    | `string`   | Ticker (e.g. "AAPL")    |
| `open`      | `Decimal`  | Open price               |
| `high`      | `Decimal`  | High price               |
| `low`       | `Decimal`  | Low price                |
| `close`     | `Decimal`  | Close price              |
| `volume`    | `Quantity` | Volume                   |
| `timestamp` | `Timestamp`| Bar timestamp            |

```cpp
Result<void> YourStrategy::on_data(const std::vector<Bar>& data) {
    if (state_ != StrategyState::RUNNING)
        return make_error<void>(ErrorCode::STRATEGY_ERROR, "Not running", "YourStrategy");

    for (const auto& bar : data) {
        auto& inst = instrument_data_[bar.symbol];

        // 1. Update price history
        inst.price_history.push_back(bar.close.as_double());
        inst.current_price = bar.close.as_double();

        // 2. Trim history to prevent memory growth
        //    Keep at most lookback * 2 entries
        while (inst.price_history.size() > lookback * 2)
            inst.price_history.erase(inst.price_history.begin());

        // 3. Skip until enough data
        if (inst.price_history.size() < lookback) continue;

        // 4. Calculate your indicators
        // ... your logic here ...

        // 5. Generate signal (-1.0 short, 0.0 flat, +1.0 long)
        double signal = generate_signal(bar.symbol, inst);

        // 6. Size the position
        double size = calculate_position_size(bar.symbol,
            bar.close.as_double(), inst.current_volatility);
        inst.target_position = signal * size;

        // 7. A short on a cash-account equity is not allowed: clamp it to zero
        if (inst.target_position < 0.0 && registry_) {
            auto equity = registry_->get_equity_instrument(bar.symbol);
            if (equity && !equity->is_short_allowed()) inst.target_position = 0.0;
        }

        // 8. Do NOT write the target or the close into positions_ here. It holds
        //    the actual holdings, moved by fills (on_execution()) and corporate
        //    actions: writing the target into it makes
        //    on_execution() treat the moved quantity as the pre-trade holding and
        //    apply each fill twice, and writing the close into average_price
        //    overwrites the cost basis the stop loss and the P&L read.
    }
    return Result<void>();
}
```

### get_target_positions()

The portfolio reads targets through this method. Build them from `instrument_data_`; the
base version returns `positions_`, which after seeding would hand the book's own holdings
(valued at cost) to risk as if they were targets.

```cpp
std::unordered_map<std::string, Position> YourStrategy::get_target_positions() const {
    std::unordered_map<std::string, Position> targets;
    for (const auto& [symbol, inst] : instrument_data_) {
        Position pos;
        pos.symbol = symbol;
        pos.quantity = Quantity(inst.target_position);
        pos.average_price = Decimal(inst.current_price);  // a mark, on the target map only
        auto it = positions_.find(symbol);                // carry fill-derived P&L
        if (it != positions_.end()) {
            pos.realized_pnl = it->second.realized_pnl;
            pos.unrealized_pnl = it->second.unrealized_pnl;
        }
        pos.last_update = inst.last_update;
        targets[symbol] = pos;
    }
    return targets;
}
```

A stop loss reads the cost basis from `positions_` (its `average_price`), never the price at
which the target was set. Reference: `src/strategy/mean_reversion.cpp`.

### PnL Accounting Methods

| Method            | Use Case | Behavior |
|-------------------|----------|----------|
| `REALIZED_ONLY`   | Futures  | The day's mark move is realized (daily settlement) |
| `UNREALIZED_ONLY` | Equities | A fill realizes P&L on the closed quantity; an open position carries unrealized P&L against its cost basis |
| `MIXED`           | Equities | Behaves as `UNREALIZED_ONLY`: the engine tests only for `REALIZED_ONLY` |

The engine distinguishes `REALIZED_ONLY` from the other two and nothing else
(`src/backtest/backtest_coordinator.cpp`). The mean-reversion strategy sets `MIXED`. For an
equity strategy use `MIXED` or `UNREALIZED_ONLY`, never `REALIZED_ONLY`; set it in
`initialize()`.

---

## Step 3: Register in CMake

### Library Sources

Add your `.cpp` to the library's source list, `TRADE_NGIN_SOURCES` in the root `CMakeLists.txt`:

```cmake
set(TRADE_NGIN_SOURCES
    ...
    src/strategy/base_strategy.cpp
    src/strategy/mean_reversion.cpp
    src/strategy/your_strategy.cpp       # <-- add
    ...
)
```

### The strategy type

The equity runners build only the strategy types they know. Add your type string to
`known_equity_strategy_types()` in `include/trade_ngin/strategy/equity_strategy_builder.hpp`
and a branch for it in your runner's construction dispatch; an enabled strategy of an unknown
type stops the run with an error rather than being skipped.

### Backtest Executable

Add a backtest app in `apps/backtest/CMakeLists.txt`:

```cmake
set(BT_YOUR_STRATEGY_SOURCES
    bt_your_strategy.cpp
)

add_executable(bt_your_strategy ${BT_YOUR_STRATEGY_SOURCES})

target_link_libraries(bt_your_strategy PRIVATE trade_ngin)

set_target_properties(bt_your_strategy
    PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${CMAKE_BINARY_DIR}/bin/Debug"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/bin/Release"
)
```

---

## Step 4: Create the Configuration

### Portfolio Config

Create a directory under `config/portfolios/<your_strategy>/` with all three portfolio files.
`ConfigLoader::load` requires all four files below and stops on a missing one
(`src/core/config_loader.cpp`). Start from the equity template,
`config_template/portfolios/equity_mr/`, and see `docs/CONFIG_GUIDE.md` for every key. No
equity runner takes a portfolio argument: each names `equity_mr` in code, so a new directory is
run only by a runner that names it (Step 5). (The live futures runners and `qt_settings_check`
do take `--portfolio-config <dir>`.)

```
config/
  defaults.json                         # Shared: database, execution, backtest params
  portfolios/
    your_strategy/
      portfolio.json                    # Strategy-specific config
      risk.json                         # Risk modules and the risk reporting block
      email.json                        # Notification settings
```

**`config/portfolios/your_strategy/portfolio.json`:**

```json
{
  "_description": "YOUR_PORTFOLIO - Description of your strategy",
  "portfolio_id": "YOUR_PORTFOLIO",
  "initial_capital": 100000,
  "strategies": {
    "YOUR_STRATEGY": {
      "enabled_backtest": true,
      "enabled_live": true,
      "default_allocation": 1.0,
      "type": "YourStrategy",
      "symbols": ["AAPL", "MSFT", "AMZN", "GOOGL", "META"],
      "config": {
        "lookback_period": 20,
        "entry_threshold": 2.0,
        "exit_threshold": 0.5,
        "risk_target": 0.15,
        "position_size": 0.1,
        "vol_lookback": 20,
        "use_stop_loss": true,
        "stop_loss_pct": 0.05,
        "allow_fractional_shares": true
      }
    }
  },
  "use_optimization": false
}
```

**Key fields:**

| Field                  | Description |
|------------------------|-------------|
| `portfolio_id`         | Unique ID stored in DB with results |
| `initial_capital`      | Starting capital in USD |
| `symbols`              | Tickers to trade, loaded from config (avoids a slow DB scan) |
| `enabled_backtest`     | Whether this strategy runs in backtest mode |
| `enabled_live`         | Whether this strategy runs in live mode |
| `default_allocation`   | Weight in portfolio (1.0 = 100%) |
| `config`               | Passed directly to your strategy config struct |
| `use_optimization`     | Required. `false`: the equity runners do not run the optimizer and refuse to start on `true` |

**`risk.json`.** The equity template runs no portfolio-risk module (one module of type
`none`, with its reason and date) and keeps a `risk_reporting` block so the book's risk is
still measured. Keep `risk_reporting.max_gross_leverage` at 1 or below: every equity is held
in a cash account, and the equity runners refuse to start when a cash-account equity is in a
book whose `max_gross_leverage` is above 1.0, because a cash account cannot borrow. The refusal's
message offers two ways out, setting the `REG_T` account mode or lowering `max_gross_leverage` to
1.0, but no config key reaches the account mode (`EquitySpec::account_mode`, which defaults to cash),
so the only way out from config is the leverage value.

### Shared Defaults (`config/defaults.json`)

These apply to all portfolios unless overridden:

| Section              | Key Fields |
|----------------------|------------|
| `database`           | `host`, `port`, `username`, `password`, `name` |
| `execution`          | `position_limit_backtest`; `commission_rate` and `slippage_bps` are present and not read by any cost model (costs come from the cost model, Step 6) |
| `backtest`           | `lookback_years`, `store_trade_details` |
| `live`               | `execution_price_max_staleness_days`, `spinoff_child_policy`, `data_staleness_tolerance_days` |
| `statistics`         | `sessions_per_year`: 252 for equities |
| `strategy_defaults`  | `fdm` table, `max_strategy_allocation`, `min_strategy_allocation`. `use_optimization` lives in `portfolio.json`; `use_risk_management` was removed and is a load error |

---

## Step 5: Write the Backtest App

Create `apps/backtest/bt_your_strategy.cpp`. This is the `main()` entry point that wires everything together.

The standard flow (see `bt_equity_mean_reversion.cpp` as reference):

```cpp
int main() {
    // 1. Initialize logger
    auto& logger = Logger::instance();
    // ... configure logger ...

    // 2. Load configuration (all four files; the directory is named here, not by argument)
    auto app_config = ConfigLoader::load("./config", "your_strategy").value();

    // 3. Wire the holiday calendar into the equity instruments
    EquityInstrument::set_holiday_checker(
        std::make_shared<HolidayChecker>(HolidayChecker::resolve_holidays_path()));

    // 4. Setup database connection pool
    DatabasePool::instance().initialize(app_config.database.get_connection_string(),
                                        app_config.database.num_connections);
    auto db_guard = DatabasePool::instance().acquire_connection();
    auto db = db_guard.get();

    // 5. Initialize instrument registry
    auto& registry = InstrumentRegistry::instance();
    registry.initialize(db);
    registry.load_instruments();  // loads futures metadata (optional)

    // 6. Collect the enabled strategies (an unknown type is an error) and their symbols
    auto entries = apps::collect_enabled_equity_strategies(
        app_config.strategies_config, "enabled_backtest").value();
    std::vector<std::string> symbols = /* union of each entry.def["symbols"] */;

    // 7. Register equity instruments, with the exchange of each symbol
    registry.load_equity_instruments(symbols, "data/equity_exchanges.json");

    // 8. Refuse what the book cannot do: the optimizer, and leverage on a cash account
    if (apps::refuse_if_optimizer_requested(app_config.use_optimization).is_error()) return 1;
    if (app_config.risk_config.max_gross_leverage > 1.0) { /* refuse if any symbol is CASH */ }

    // 9. Configure backtest dates
    auto [start_date, end_date] =
        ConfigLoader::resolve_backtest_window(app_config.backtest, std::chrono::system_clock::now());

    // 10. Create BacktestCoordinator
    BacktestCoordinatorConfig coord_config;
    coord_config.initial_capital = app_config.initial_capital;
    coord_config.portfolio_id = app_config.portfolio_id;
    coord_config.store_trade_details = app_config.backtest.store_trade_details;
    coord_config.statistics_sessions_per_year = app_config.statistics.equity_sessions_per_year;
    auto coordinator = std::make_unique<BacktestCoordinator>(db, &registry, coord_config);
    coordinator->initialize();

    // 11. Create your strategy for each entry
    StrategyConfig strategy_config;
    strategy_config.asset_classes = {AssetClass::EQUITIES};
    strategy_config.frequencies = {DataFrequency::DAILY};
    strategy_config.capital_allocation = app_config.initial_capital * /* entry weight */;
    for (const auto& sym : symbols) {
        strategy_config.trading_params[sym] = {};
        strategy_config.position_limits[sym] = app_config.execution.position_limit_backtest;
    }
    YourStrategyConfig custom_config = /* parsed from entry.def["config"] */;
    auto strategy = std::make_shared<YourStrategy>(
        entry.id, strategy_config, custom_config, db, registry_ptr);
    strategy->initialize();
    strategy->start();

    // 12. Create the portfolio (optimizer off; risk modules from risk.json)
    PortfolioConfig portfolio_config;
    portfolio_config.total_capital = Decimal(app_config.initial_capital);
    portfolio_config.use_optimization = false;
    portfolio_config.risk_modules = app_config.risk_schema.portfolio;
    portfolio_config.risk_config = app_config.risk_config;
    portfolio_config.allow_fractional_positions = custom_config.allow_fractional_shares;
    auto portfolio = std::make_shared<PortfolioManager>(portfolio_config);
    portfolio->add_strategy(strategy, /* weight */ 1.0, false);

    // 13. Set each symbol's cost tier from the 30 calendar days before the start
    backtest::register_equity_cost_warmup(symbols, start_date, /* bar loader */,
        coordinator->get_execution_manager()->get_transaction_cost_manager(), *portfolio);

    // 14. Run
    auto result = coordinator->run_portfolio(
        portfolio, symbols, start_date, end_date,
        AssetClass::EQUITIES, DataFrequency::DAILY);

    // 15. Display + save results
    const auto& r = result.value();
    std::cout << "Total Return: " << r.total_return * 100 << "%" << std::endl;
    coordinator->save_portfolio_results_to_db(r, ...);
    return 0;
}
```

---

## Step 6: Transaction Costs for Equities

Equity transaction costs differ from futures. An equity symbol with no registered config is priced on the FUTURES default (point value 100), not the equity defaults below: `AssetCostConfigRegistry::get_config` returns `get_equity_default_config` only when the caller passes `AssetType::EQUITY`, and the fill paths (`src/backtest/backtest_execution_manager.cpp`, `src/live/execution_manager.cpp`) pass no asset type, so its costs come out about 100 times too high. Registration is therefore required, not optional. The equity runners register a tier per symbol from its average daily volume (`get_tiered_equity_config`): live from its last 20 loaded bars (`register_equity_costs_from_bars`), the backtest from the 30 calendar days before its start (`register_equity_cost_warmup`, Step 5). A new runner must do the same before it prices a fill. The full model is in `docs/COST_MODEL.md`.

### Equity Cost Model

| Parameter                | Equity Default        | Futures Default       |
|--------------------------|-----------------------|-----------------------|
| `commission_per_unit`    | $0.005/share (IBKR)  | -1.0 (uses global)   |
| `min_commission_per_order` | $1.00               | N/A                   |
| `point_value`            | 1.0 (per share)       | Contract multiplier   |
| `tick_size`              | $0.01 (penny)         | Varies by contract    |
| `max_commission_pct`     | 1% of trade value     | N/A                   |
| `baseline_spread_ticks`  | 2.0 (the volume tier sets 1 to 10) | Varies by contract    |

### Cost Calculation Formula

```
Commissions = min(max_commission_pct * abs(quantity) * price,
                  max(min_commission, abs(quantity) * commission_per_unit))
Spread Cost = spread_model(baseline_spread_ticks, tick_size, spread_cost_multiplier)
Market Impact = impact_model(order_size, max_impact_bps)
Total Cost = Commissions + (Spread Cost + Market Impact) * point_value * quantity
```

### Registering Custom Costs

In `src/transaction_cost/asset_cost_config.cpp`, the registry pre-registers ten equities (AAPL, MSFT, AMZN, GOOGL, META, TMUS, NSC, ABT, ABEV, ABM); any other symbol needs a config before its first fill. To override for a specific symbol:

```cpp
AssetCostConfig config;
config.symbol = "AAPL";
config.commission_per_unit = 0.005;
config.min_commission_per_order = 1.0;
config.tick_size = 0.01;
config.point_value = 1.0;
config.baseline_spread_ticks = 1.0;
registry.register_config(config);
```

---

## Step 7: Build and Run

### Build

```bash
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make bt_your_strategy -j$(nproc)
```

### Run

```bash
cd /path/to/trade-ngin    # must be project root (config/ is relative)
./build/bin/Release/bt_your_strategy
```

A Release build puts the binary in `build/bin/Release/`. The executable expects `./config/defaults.json` and `./config/portfolios/<dir>/portfolio.json`, `risk.json` and `email.json` to exist relative to the working directory.

### Output

Results are printed to stdout and saved to the database:

```
======= Your Strategy Backtest Results =======
Total Return: X.XX%
Sharpe Ratio: X.XXX
Sortino Ratio: X.XXX
Max Drawdown: X.XX%
Calmar Ratio: X.XXX
Volatility (annualised): X.XX%
Trade win rate (closing fills): X.XX%
Closing fills: N
===============================================
```

---

## Step 8: Database Results

Backtest results are stored in the `backtest` schema:

| Table                       | Contents |
|-----------------------------|----------|
| `backtest.results`          | One row per run: summary statistics and cost totals, keyed on `run_id` |
| `backtest.equity_curve`     | The account value (`equity`) after each bar date (`timestamp`) |
| `backtest.executions`       | Every fill: symbol, side, quantity, price, costs |
| `backtest.final_positions`  | Each strategy's holding of each symbol on each bar date (a daily snapshot, despite the name) |
| `backtest.run_metadata`     | Each strategy's configuration within the run |

Each table's key and writer are in `docs/LIVE_RUN_CYCLE.md` section 5. A run whose `run_id` already has rows in `backtest.results` or `backtest.final_positions` is refused before its first cycle.

### Querying Results

```sql
-- Latest backtest summary
SELECT * FROM backtest.results
WHERE portfolio_id = 'YOUR_PORTFOLIO'
ORDER BY end_date DESC LIMIT 1;

-- Equity curve
SELECT timestamp, equity
FROM backtest.equity_curve
WHERE run_id = '<run_id>'
ORDER BY timestamp;

-- Execution details
SELECT timestamp, symbol, side, quantity, price,
       commissions_fees, slippage_market_impact
FROM backtest.executions
WHERE run_id = '<run_id>'
ORDER BY timestamp;
```

---

## Step 9: Live Trading App (Optional)

To run your strategy live, create `apps/strategies/live_your_strategy.cpp`. The pattern mirrors the backtest app but uses `LiveTradingCoordinator` instead of `BacktestCoordinator`. See `live_equity_mean_reversion.cpp` as reference.

Register it in `apps/strategies/CMakeLists.txt` the same way.

What the reference runner fixes that a new one must decide:

- It loads `equity_mr` by name, runs exactly one enabled strategy (it refuses more), and stores every row under the fixed ids `LIVE_EQUITY_MEAN_REVERSION` / `EQUITY_MEAN_REVERSION`. A second strategy in the same book is the multi-strategy equity work (AlgoGators/algogators#141), not a config change.
- A run names its date (`live_your_strategy YYYY-MM-DD [--send-email]`); with no date it exits 2 and does nothing.
- A new live strategy needs its `trading.strategy_trading_days_metadata` row (`live_start_date`, the book's first day) written by hand before its first run; annualised returns count from it.
- Corporate actions, the cost basis and the cash account rules come with the runner: `docs/AVERAGE_PRICE_LIFECYCLE.md`, `docs/CORP_ACTIONS_DATA_BOUNDARY.md`, `docs/LIVE_RUN_CYCLE.md`.
- Every equity is held in a cash account. A cash account cannot borrow, so it cannot short and it posts the full price paid as margin (`EquityInstrument::get_margin_requirement`, `src/instruments/equity.cpp`). The code also carries a Reg T mode (a long posts 50 percent of its notional, a short 150 percent: the proceeds plus 50 percent), reached only through `EquitySpec::account_mode`, which defaults to cash and which no config key sets (`include/trade_ngin/instruments/equity.hpp`). Opening it is the margin and shorting work (AlgoGators/algogators#139, AlgoGators/algogators#140), not a config change.
- A fill the strategy cannot take is fatal in live. When `on_execution` fails on a fill, the live runner exits 1, because the fill's cost would stay in the day's costs while its realized P&L reached neither the stored row nor the statistics, and the day could no longer sum to its aggregate (`apps/strategies/live_equity_mean_reversion.cpp`). The backtest coordinator logs a WARN and goes on (`src/backtest/backtest_coordinator.cpp`), so a backtest can carry such a fill; making it fatal there too is AlgoGators/algogators#138.

---

## Checklist for a New Equity Strategy

- [ ] Strategy config struct in header (`include/trade_ngin/strategy/`)
- [ ] Strategy class extending `BaseStrategy` (header + cpp)
- [ ] Set `PnLAccountingMethod::MIXED` (or `UNREALIZED_ONLY`, never `REALIZED_ONLY`) in `initialize()`
- [ ] Implement `on_data()` with your signal logic, setting targets only (never a target or a mark in `positions_`)
- [ ] Override `get_target_positions()` to build targets from your instrument data
- [ ] Implement `validate_config()` for parameter bounds checking
- [ ] Implement `get_price_history()` override
- [ ] Add `.cpp` to `TRADE_NGIN_SOURCES` in the root CMakeLists.txt
- [ ] Add the type to `known_equity_strategy_types()` and the runner's dispatch
- [ ] Create portfolio config directory under `config/portfolios/`
- [ ] Write `portfolio.json` (symbols, strategy params, `use_optimization: false`), `risk.json` (`risk_reporting.max_gross_leverage` at most 1) and `email.json`
- [ ] Create backtest app (`apps/backtest/bt_*.cpp`)
- [ ] Register backtest executable in `apps/backtest/CMakeLists.txt`
- [ ] Build and run: `make bt_your_strategy && ./build/bin/Release/bt_your_strategy`
- [ ] Verify results in `backtest.results` and `backtest.equity_curve`
- [ ] (Optional) Create live app in `apps/strategies/`

---

## Reference: Existing Mean Reversion Implementation

| Component | File |
|-----------|------|
| Strategy header | `include/trade_ngin/strategy/mean_reversion.hpp` |
| Strategy impl | `src/strategy/mean_reversion.cpp` |
| Backtest app | `apps/backtest/bt_equity_mean_reversion.cpp` |
| Live app | `apps/strategies/live_equity_mean_reversion.cpp` |
| Portfolio config | `config_template/portfolios/equity_mr/` (`portfolio.json`, `risk.json`, `email.json`) |
| Strategy types and config parsing | `include/trade_ngin/strategy/equity_strategy_builder.hpp` |
| Base strategy | `include/trade_ngin/strategy/base_strategy.hpp` |
| Strategy interface | `include/trade_ngin/strategy/strategy_interface.hpp` |
| Transaction costs | `include/trade_ngin/transaction_cost/asset_cost_config.hpp` |
| Cost manager | `src/transaction_cost/transaction_cost_manager.cpp` |
| Config loader | `include/trade_ngin/core/config_loader.hpp` |
| Backtest coordinator | `include/trade_ngin/backtest/backtest_coordinator.hpp` |
| Portfolio manager | `src/portfolio/portfolio_manager.cpp` |

### How the mean-reversion strategy sizes a position

When a symbol's signal is not zero, the strategy's target is the signal (+1 or -1) times a share count from
`MeanReversionStrategy::calculate_position_size` (`src/strategy/mean_reversion.cpp`):

1. **Value.** `capital x position_size`, `capital` the sleeve's capital allocation and `position_size` 0.1 by default.
2. **Volatility scaling.** Times `risk_target / volatility`, kept between 0.25 and 2 (`risk_target` 0.15 by default).
   `volatility` is the sample standard deviation of the daily log returns between the last `vol_lookback` (20) closes
   (so 19 returns), annualised by the square root of 252, and 0.01 until two returns exist; a volatility below 0.01 is read as 0.01. A calm symbol gets up
   to twice the base value, a wild one no less than a quarter.
3. **Shares.** The value over the close. The count keeps six decimals (a fractional share) only when
   `allow_fractional_shares` is on, the close is at least `fractional_min_price` (1.00), and the symbol's average daily
   volume is at least `fractional_min_adv` (50,000 shares) after at least `vol_lookback` bars of volume; otherwise it is
   rounded down to whole shares. The average daily volume is an exponential average whose window grows to
   `vol_lookback` bars.
4. **Limit.** The count is capped at the symbol's entry in the sleeve's position limits, when it has one.
5. **Shorts.** A negative target on an equity whose account cannot short (a cash account) is set to 0, with a WARN.

A price or a volatility below 1e-8 gives 0 shares. The portfolio then reconciles the targets to whole quantities and
the cost model prices the final trade; the strategy itself is not cost-aware.

Worked shapes, on a sleeve capital of 100,000 (base value 10,000):

| Symbol | Close | Volatility | Scalar `0.15 / vol`, kept in [0.25, 2] | Value | Shares before rounding | Fractional allowed | Target shares |
|---|---:|---:|---:|---:|---:|---|---:|
| A | 50.00 | 0.30 | 0.50 | 5,000 | 100.0 | yes (ADV 2,000,000) | 100.000000 |
| B | 0.80 | 0.05 | 3.00, capped at 2.00 | 20,000 | 25,000.0 | no (close below 1.00) | 25,000 |
| C | 33.00 | 0.90 | 0.1667, floored at 0.25 | 2,500 | 75.7576 | no (ADV 40,000) | 75 |

The z-score that sets the signal (entry at 2.0, exit at 0.5, a 5% stop on the cost basis) is described in the
strategy's header, `include/trade_ngin/strategy/mean_reversion.hpp`.

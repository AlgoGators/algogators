# Data Module

## Overview

The data module (`src/data/`) holds the database layer, the connection pool, the credential store,
the market data bus and the Arrow conversions, and the pieces that decide what a futures bar is:
the session classification of a symbol's bar on a date, the futures roll series, the listing
dates and, with `per_contract_rolls` on, the per-contract roll rule and the bar source built on it.
Headers are in `include/trade_ngin/data/`.

---

## Architecture

```
data/
├── postgres_database.cpp            # Main database layer
├── postgres_database_extensions.cpp # Additional DB operations
├── database_pooling.cpp             # Connection pool management
├── credential_store.cpp             # Reads credentials from a JSON file
├── market_data_bus.cpp              # Pub-sub for market data events
├── conversion_utils.cpp             # Arrow table conversions
├── market_data_utils.cpp            # The bar queries (one futures row per symbol and day)
├── session_classifier.cpp           # What a symbol's bar, or its absence, is on a date
├── roll_series.cpp                  # Contract switches, the back-adjusted series, roll legs
├── listing_dates.cpp                # Listing dates and vendor id relabels
├── contract_bars.cpp                # Reads futures_data.contract_ohlcv_1d (one bar per listed contract)
├── contract_roll_rule.cpp           # The per-contract roll rule: which contract is held on each date
├── roll_cycle.cpp                   # The delivery months the rule holds, per root
├── roll_calendar.cpp                # The futures exchanges' trading calendars (futures_holidays.json)
├── contract_rolls.cpp               # The roll series of one root, the one call callers make
├── per_contract_source.cpp          # The bar source with per_contract_rolls on: the held contract's bars
├── carry_quote.cpp                  # Carry's second contract on each date
├── report_roll_view.cpp             # What the daily report says about rolls
├── market_data_source.cpp           # The data-source seam the Python module's backtest uses
└── postgres_strategy_config.cpp     # The QT desk's settings row (trading.strategy_config)
```

The session classifier, the roll series, the listing dates and the per-contract rolls are described
in [docs/FUTURES_ROLLS.md](../../docs/FUTURES_ROLLS.md) and
[docs/DATA_SOURCES_OF_TRUTH.md](../../docs/DATA_SOURCES_OF_TRUTH.md); the design comments at the
top of their headers are the reference.

---

## Components

### 1. PostgresDatabase

**File**: `postgres_database.cpp` (header `include/trade_ngin/data/postgres_database.hpp`)

The central database access layer with 50+ methods.

#### Key Methods

```cpp
// Connection management
Result<void> connect();
void disconnect();
bool is_connected() const;

// Market data
Result<std::shared_ptr<arrow::Table>> get_market_data(
    const std::vector<std::string>& symbols,
    const Timestamp& start_date,
    const Timestamp& end_date,
    AssetClass asset_class,
    DataFrequency freq = DataFrequency::DAILY,
    const std::string& data_type = "ohlcv");

// Storage operations (book is the portfolio_type of the rows: "system", "qt_proposal" or "qt")
Result<void> store_executions(
    const std::vector<ExecutionReport>& executions,
    const std::string& strategy_id,
    const std::string& strategy_name,
    const std::string& portfolio_id,
    const std::string& table_name = "trading.executions",
    const std::string& book = kDefaultBook);   // "system"

Result<void> store_positions(
    const std::vector<Position>& positions,
    const std::string& strategy_id,
    const std::string& strategy_name,
    const std::string& portfolio_id,
    const std::string& table_name = "trading.positions",
    const std::string& book = kDefaultBook);

Result<void> store_signals(
    const std::unordered_map<std::string, double>& signals,
    const std::string& strategy_id,
    const std::string& strategy_name,
    const std::string& portfolio_id,
    const Timestamp& timestamp,
    const std::string& table_name);

// Query operations
Result<std::vector<std::string>> get_symbols(
    AssetClass asset_class,
    DataFrequency freq = DataFrequency::DAILY,
    const std::string& data_type = "ohlcv");

Result<std::unordered_map<std::string, double>> get_latest_prices(
    const std::vector<std::string>& symbols,
    AssetClass asset_class,
    DataFrequency freq = DataFrequency::DAILY,
    const std::string& data_type = "ohlcv");

Result<std::unordered_map<std::string, Position>> load_positions_by_date(
    const std::string& strategy_id,
    const std::string& strategy_name,
    const std::string& portfolio_id,
    const Timestamp& date,
    const std::string& table_name = "trading.positions",
    const std::string& book = kDefaultBook);

// Metadata
Result<std::shared_ptr<arrow::Table>> get_contract_metadata() const;
```

`data_type` is the base name of the table. The table read is `<schema>.<data_type>_<frequency>`
(`build_table_name`, `include/trade_ngin/core/types.hpp`), so `AssetClass::FUTURES`,
`DataFrequency::DAILY` and `"ohlcv"` read `futures_data.ohlcv_1d`. Futures symbols are stored with
the continuous suffix, for example `MES.v.0`. With `per_contract_rolls` on, the futures query hands
out the held contract's own bar on each of those dates instead, from
`futures_data.contract_ohlcv_1d` (`include/trade_ngin/data/per_contract_source.hpp`).

`get_market_data` also publishes one `BAR` event per returned row on the `MarketDataBus` unless
publishing is switched off.

---

### 2. DatabasePool

**File**: `database_pooling.cpp`

Connection pool for database access.

```cpp
// Initialize pool
Result<void> initialize(const std::string& connection_string, size_t pool_size = 5);

// Acquire connection (RAII guard)
auto guard = DatabasePool::instance().acquire_connection();
auto db = guard.get();   // std::shared_ptr<PostgresDatabase>

// Connection automatically returned when guard goes out of scope
```

The runners pass `database.num_connections` from `defaults.json` as the pool size (5 when the key
is absent).

---

### 3. CredentialStore

**File**: `credential_store.cpp`

Reads values from a JSON file by section and key. The file is the path given to the constructor
(default `config.json`), or the path in the environment variable `TRADING_CONFIG_PATH` when that is
set to a `.json` path. The live runners do not use it for e-mail: the book e-mail is sent by the
Python report module (`services/rpc/algogators_rpc/services/report`) with the SMTP settings of the
book's `email.json`.

```cpp
auto credentials = std::make_shared<CredentialStore>("path/to/file.json");

// get<T>(section, key) returns Result<T>
auto username = credentials->get<std::string>("database", "username");
if (username.is_ok()) {
    const std::string& value = username.value();
}
```

The runners do not build their database connection through `CredentialStore`. They load the
`database` block of `config/defaults.json` with `ConfigLoader` and take the connection string from
`DatabaseConfig::get_connection_string()` (`include/trade_ngin/core/config_loader.hpp`).

---

### 4. MarketDataBus

**File**: `market_data_bus.cpp`

Pub-sub for market data events. A subscriber registers one `SubscriberInfo` (an id, the event types
and symbols it wants, and a callback that receives a `MarketDataEvent`); `publish` takes one
`MarketDataEvent`.

```cpp
SubscriberInfo info{
    "my_subscriber",
    {MarketDataEventType::BAR},
    {"MES.v.0"},
    [](const MarketDataEvent& event) {
        // event.symbol, event.timestamp, event.numeric_fields.at("close"), ...
    }};
auto subscribed = MarketDataBus::instance().subscribe(info);   // Result<void>

MarketDataEvent event;
event.type = MarketDataEventType::BAR;
event.symbol = "MES.v.0";
MarketDataBus::instance().publish(event);

auto unsubscribed = MarketDataBus::instance().unsubscribe("my_subscriber");   // Result<void>
```

`set_publish_enabled(false)` switches publishing off; the backtest does this while it loads data, and so do the two
futures live runners while they load bars (`apps/strategies/live_portfolio_conservative.cpp`,
`apps/strategies/live_portfolio.cpp`), because a bar published during the load would reach the strategies a second time
when the runner feeds the day itself. The equity runner does not call it.

---

### 5. DataConversionUtils

**File**: `conversion_utils.cpp`

Apache Arrow table conversions.

```cpp
// Convert an Arrow table of OHLCV rows to bars
auto bars = DataConversionUtils::arrow_table_to_bars(arrow_table);   // Result<std::vector<Bar>>
```

There is no conversion from bars back to a table. The class also carries the type-checked cell
readers `safe_get_double`, `safe_get_int64` and `safe_get_string`, each returning a `Result`.

---

## Writes: what lands together and what is refused

### Several writes in one transaction

Every write method opens and commits its own transaction. Where two writes must land together or
not at all, a caller opens a unit of work, passes it to the overloads that take a `DbTransaction`,
and commits it (`include/trade_ngin/data/postgres_database.hpp`):

```cpp
auto unit = db->begin_unit_of_work();                       // Result<std::unique_ptr<DbTransaction>>
if (unit.is_error()) return 1;
DbTransaction& txn = *unit.value();
db->store_positions(txn, positions, strategy_id, strategy_name, portfolio_id, "trading.positions");
audit_log.save_in(txn);                                      // the corporate-action dedup rows
txn.commit();                                                // destroyed without commit: rolled back
```

The equity runner uses it twice: the positions a corporate action restated go in with the rows
that stop the action being applied again (`CorporateActionsAuditLog::save_in`,
`include/trade_ngin/live/corporate_actions_audit_log.hpp`, which writes them through
`store_applied_corp_actions`), and the positions a lifecycle event changed with theirs
(`apps/strategies/live_equity_mean_reversion.cpp`). Split across two transactions, a failure
between them would leave the positions adjusted with no record, and the next run would apply the
split or dividend a second time. While a unit is open, a call to a method that opens its own
transaction on the same connection is refused with an error that says so, instead of failing
inside the database library. No futures caller opens a unit; the futures runners replace a day's
executions in one transaction of their own (`replace_roll_day_executions`).

### Names the writers refuse

The methods check the names they are given before any SQL is built
(`src/data/postgres_database.cpp`, the `validate_*` functions):

| Argument | Refused when |
|---|---|
| table name | empty or over 100 characters; any character other than a letter, a digit, `_` or `.`; or it contains, anywhere and in any case, one of `drop`, `delete`, `insert`, `update`, `alter`, `create`, `union`, `select`, `script`, `--`, `/*`, `*/` |
| symbol | empty or over 20 characters; any character other than a letter, a digit, `_`, `.` or `-`; more than 1,000 symbols in one call |
| strategy id | empty or over 100 characters; any character other than a letter, a digit, `_` or `-`; two dashes in a row |
| book (`portfolio_type`) | anything but `system`, `qt_proposal` or `qt` (migration 021) |

The table-name check is a substring match, so an ordinary name that contains one of the words is
refused: `trading.position_updates` or `backtest.created_runs` cannot be written through these
methods. Name a new table without them.

### Numbers that are not finite, and typed cells

A number is written as the shortest text that reads back as the same double (15 to 17 significant
digits), so a stored cell holds the value the engine held (`round_trip_number`,
`include/trade_ngin/data/finite_statement.hpp`). A NaN or an infinity is never written into a
statement:

- on a column that may be NULL it is stored NULL, and the writer logs which cells;
- on a NOT NULL column the whole row is refused, no statement is sent, and the log names the
  column and the value. The NOT NULL number columns are `daily_roll_costs` and `total_roll_costs`
  on `trading.live_results`, and `transaction_costs`, `roll_costs` and `total_roll_fills` on
  `backtest.results`.

The statistics cells of `trading.live_results` (migration 036) travel as text cast to their type,
which must be one of `numeric`, `integer`, `date`, `text` or `jsonb`
(`include/trade_ngin/data/live_results_cell.hpp`). The overlay cells of `backtest.equity_curve`
(migration 037) are written in one multi-row insert, so every point must carry the same columns
and types in the same order, or none; any other shape refuses the curve
(`include/trade_ngin/data/backtest_curve_cells.hpp`).

`backtest.final_positions` stores a row with a quantity always; a row closed to zero only on a
book that keeps closed rows (a cash book) and only when it carries a realised P&L
(`backtest_position_row_is_stored`, `include/trade_ngin/data/backtest_position_row.hpp`). A
futures row of a symbol that printed no bar that date carries `row_mark` `NO_BAR` (migration 038).

---

## Connection String Format

```
postgresql://username:password@host:port/database
```

`DatabaseConfig::get_connection_string()` builds it from the `database` block.

---

## Configuration

The `database` block of `config/defaults.json` (copy `config_template/defaults.json` and replace
the placeholders):

```json
{
  "database": {
    "host": "YOUR_DB_HOST",
    "port": "YOUR_DB_PORT",
    "username": "YOUR_DB_USERNAME",
    "password": "YOUR_DB_PASSWORD",
    "name": "YOUR_DB_NAME",
    "num_connections": 5
  }
}
```

`host`, `username`, `password` and `name` are required. See
[docs/CONFIG_GUIDE.md](../../docs/CONFIG_GUIDE.md).

---

## Usage Example

This is how the runners open the database and read bars (run from the engine's folder,
`services/trade-ngin/`, with `./config` in place):

```cpp
#include "trade_ngin/core/config_loader.hpp"
#include "trade_ngin/core/time_utils.hpp"
#include "trade_ngin/data/database_pooling.hpp"
#include "trade_ngin/data/postgres_database.hpp"

using namespace trade_ngin;

// Load the configuration of one book
auto app_config_result = ConfigLoader::load("./config", "conservative");
if (app_config_result.is_error()) {
    std::cerr << app_config_result.error()->what() << std::endl;
    return 1;
}
auto app_config = app_config_result.value();

// Initialize pool
auto pool_result = DatabasePool::instance().initialize(
    app_config.database.get_connection_string(), app_config.database.num_connections);
if (pool_result.is_error()) {
    std::cerr << pool_result.error()->what() << std::endl;
    return 1;
}

// Acquire connection
auto guard = DatabasePool::instance().acquire_connection();
auto db = guard.get();
if (!db || !db->is_connected()) {
    return 1;
}

// Query data: daily futures bars from futures_data.ohlcv_1d
std::vector<std::string> symbols = {"MES.v.0", "MNQ.v.0"};
Timestamp start, end;
if (!core::parse_utc_date("2024-01-01", start) || !core::parse_utc_date("2024-12-31", end)) {
    return 1;
}

auto result = db->get_market_data(symbols, start, end, AssetClass::FUTURES);

if (result.is_ok()) {
    auto table = result.value();
    // Process data...
}
```

---

## Error Handling

Database operations return `Result<T>`:

```cpp
auto result = db->connect();
if (result.is_error()) {
    std::cerr << "Connection failed: " << result.error()->what() << std::endl;
}
```

Common errors:
- `CONNECTION_ERROR`: the connection could not be opened
- `DATABASE_ERROR`: a query failed
- `DATA_NOT_FOUND`: no matching data
- `INVALID_ARGUMENT`: bad query parameters

---

## Dependencies

- **libpqxx**: PostgreSQL C++ interface
- **Apache Arrow**: columnar data processing
- **nlohmann_json**: configuration parsing

---

## Testing

The tests are registered under their GoogleTest names (`Suite.Test`), and `ctest -R` matches a
regular expression against that name. To run the main suites of `tests/data` by name (the
expression matches any test whose suite or test name contains one of these words, so it also
selects the roll-leg cases of `tests/backtest`, `tests/live`, `tests/core` and
`tests/transaction_cost`, and it leaves out the suites of `tests/data`
it does not name):

```bash
cd build
ctest -R "PostgresDatabase|DatabasePool|CredentialStore|ConversionUtils|MarketDataBus|MarketDataUtils|SessionClassifier|RollSeries|RollLegs" --output-on-failure
```

---

## References

- [Backtest Module](../backtest/README.md): reads stored bars for a backtest
- [Live Trading Module](../live/README.md): reads and writes the live tables
- [Strategy Module](../strategy/README.md): consumes market data

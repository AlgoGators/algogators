# Test Organization

Tests are organized by functional category to improve maintainability and discoverability.

## Directory Structure

```
tests/
├── conftest.py              # Shared pytest fixtures and utilities
├── README.md                # This file
│
├── backtest/                # Backtest functionality tests
│   ├── test_backtest.py
│   ├── test_backtest_catalog.py
│   ├── test_backtest_comparison.py
│   └── test_backtest_storage.py
│
├── trading/                 # Trading functionality tests
│   ├── test_attribution.py
│   ├── test_engine.py
│   ├── test_momentum_backtest.py
│   ├── test_parameter_tuning.py
│   ├── test_polymarket_backtest.py
│   ├── test_portfolio.py
│   ├── test_reporting.py
│   ├── test_risk_modules.py
│   ├── test_risk_profiles.py
│   ├── test_signal_engine.py
│   ├── test_signals_helpers.py
│   └── test_strategies.py
│
├── data/                    # Data collection and processing tests
│   ├── test_collection.py
│   ├── test_data.py
│   ├── test_data_db.py
│   ├── test_data_modules.py
│   └── test_resolution.py
│
├── config/                  # Configuration and CLI tests
│   ├── test_cli_utils.py
│   ├── test_config.py
│   └── test_src_config.py
│
├── live/                    # Live trading tests
│   └── test_live_trading.py
│
└── unit/                    # Unit tests for specific modules
    ├── test_dashboard.py
    ├── test_distributions.py
    ├── test_experiment_tracker.py
    ├── test_liquidity_collector.py
    ├── test_quality_monitor.py
    ├── test_taxonomy.py
    └── test_walk_forward.py
```

## Test Categories

### Backtest (`backtest/`)

Tests for backtesting functionality:

- Backtest engine execution
- Backtest result storage and cataloging
- Backtest comparison utilities
- Backtest metadata management

### Trading (`trading/`)

Tests for trading functionality:

- Trading strategies (whale, momentum, mean reversion, etc.)
- Signal generation and processing
- Risk management modules
- Portfolio management
- Performance reporting and attribution
- Parameter tuning

### Data (`data/`)

Tests for data functionality:

- Data collection (prices, trades, liquidity)
- Data storage (database, parquet)
- Data processing utilities
- Market resolution handling
- Data categorization

### Config (`config/`)

Tests for configuration:

- Configuration loading and merging
- CLI utilities
- Settings management

### Live (`live/`)

Tests for live trading:

- Paper trading simulation
- Order execution
- Real-time signal processing

### Unit (`unit/`)

Unit tests for specific modules:

- Dashboard functionality
- Distribution analysis
- Experiment tracking
- Liquidity analysis
- Quality monitoring
- Taxonomy handling
- Walk-forward validation

## Running Tests

Run all tests:

```bash
pytest
```

Run tests by category:

```bash
pytest tests/backtest/      # Backtest functionality
pytest tests/trading/       # Trading strategies and signals
pytest tests/data/          # Data collection and processing
pytest tests/config/        # Configuration and CLI
pytest tests/live/          # Live trading
pytest tests/unit/          # Unit tests for specific modules
```

Run specific test file:

```bash
pytest tests/trading/test_strategies.py
```

Run specific test:

```bash
pytest tests/data/test_data_modules.py::TestCategories::test_categorize_market_crypto
```

Run with coverage:

```bash
pytest --cov=src --cov-report=html
```

## Test Organization Summary

| Category     | Directory   | Test Files | Purpose                                         |
| ------------ | ----------- | ---------- | ----------------------------------------------- |
| **Backtest** | `backtest/` | 4 files    | Backtest execution, storage, comparison         |
| **Trading**  | `trading/`  | 12 files   | Strategies, signals, risk, portfolio, reporting |
| **Data**     | `data/`     | 5 files    | Collection, storage, processing, resolution     |
| **Config**   | `config/`   | 3 files    | Configuration loading, CLI utilities            |
| **Live**     | `live/`     | 1 file     | Paper trading, order execution                  |
| **Unit**     | `unit/`     | 8 files    | Module-specific unit tests                      |

## Adding New Tests

When adding new tests:

1. Place them in the appropriate category directory
2. Follow the naming convention: `test_<module_name>.py`
3. Use descriptive test function names: `test_<functionality>_<scenario>()`
4. Add fixtures to `conftest.py` if they're shared across multiple test files

## Test Fixtures

Shared fixtures are defined in `conftest.py`:

- `sample_trades_df` - Sample trades DataFrame
- `sample_prices_df` - Sample price history DataFrame
- `sample_markets_df` - Sample markets DataFrame
- `mock_db` - Temporary SQLite database
- `populated_db` - Database with sample data
- Various strategy-specific fixtures

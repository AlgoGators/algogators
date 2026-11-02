# New Test Suite

This document describes the test files added for the new monitoring, UI, and tooling features.

## Test Files

### `test_attribution.py`

Tests for performance attribution analysis module (`src/trading/attribution.py`).

**Coverage:**

- Sharpe ratio calculation
- Max drawdown calculation
- Profit factor calculation
- Strategy attribution breakdown
- Category attribution breakdown
- Time period attribution breakdown
- Volume tier attribution breakdown
- Expiry window attribution breakdown
- Trade size attribution breakdown
- Full attribution report generation
- DataFrame conversion
- Edge cases (empty data, missing columns)

**Run tests:**

```bash
pytest tests/test_attribution.py -v
```

### `test_parameter_tuning.py`

Tests for automatic parameter tuning module (`src/trading/parameter_tuning.py`).

**Coverage:**

- ParameterSpace creation
- ParameterTuner initialization
- Grid search optimization
- Random search optimization
- Bayesian optimization
- Minimization vs maximization
- Save/load tuning results
- DataFrame conversion
- Error handling
- Edge cases (empty spaces, failing objectives)

**Run tests:**

```bash
pytest tests/test_parameter_tuning.py -v
```

### `test_cli_utils.py`

Tests for CLI utilities (`src/tools/cli_utils.py`).

**Coverage:**

- Console retrieval
- Table printing (with and without rich)
- Panel printing
- Progress context creation
- Currency formatting
- Percentage formatting
- Number formatting
- User prompts (with choices, defaults)
- User confirmations (with defaults)

**Run tests:**

```bash
pytest tests/test_cli_utils.py -v
```

### `unit/test_dashboard.py`

Tests for dashboard script (`scripts/monitoring/dashboard.py`).

**Coverage:**

- Loading paper trading logs
- Loading execution logs
- Portfolio summary calculation
- Dashboard table creation (with/without rich)
- Static dashboard display
- Handling missing log files
- Invalid JSON handling
- Recent entries filtering

**Run tests:**

```bash
pytest tests/unit/test_dashboard.py -v
```

## Running All New Tests

```bash
# Run all new tests
pytest tests/test_attribution.py tests/test_parameter_tuning.py tests/test_cli_utils.py tests/unit/test_dashboard.py -v

# Run with coverage
pytest tests/test_attribution.py tests/test_parameter_tuning.py tests/test_cli_utils.py tests/unit/test_dashboard.py --cov=src.trading.attribution --cov=src.trading.parameter_tuning --cov=src.tools.cli_utils --cov-report=html
```

## Test Fixtures

The tests use pytest fixtures for:

- Sample trade DataFrames with various configurations
- Sample databases with liquidity data
- Sample log files
- Temporary directories for file operations

## Notes

- Some tests use direct file imports to avoid issues with the main package's `__init__.py`
- Tests are designed to work with or without the `rich` library installed
- Mock objects are used where external dependencies aren't needed
- All tests follow the existing test patterns in the repository

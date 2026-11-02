# Backtests Directory

This directory contains organized backtest results with full reproducibility tracking.

## Structure

```
backtests/
├── {strategy_name}/
│   ├── {run_id}/                    # e.g., momentum_20240207_143022_a1b2c3
│   │   ├── metadata.json            # Full run metadata
│   │   ├── config.yaml              # Config snapshot
│   │   ├── results/
│   │   │   ├── trades.csv
│   │   │   ├── summary.json
│   │   │   ├── diagnostics.json
│   │   │   ├── quantstats.html
│   │   │   └── equity_curve.csv
│   │   ├── signals/                  # Input signals (if applicable)
│   │   └── logs/                     # Run logs
│   └── latest -> {run_id}            # Symlink to most recent
├── catalog.db                        # Searchable index
└── README.md                         # This file
```

## Usage

### Running a Backtest

Backtests are automatically tracked when using the updated scripts:

```bash
python scripts/backtest/run_momentum_backtest.py --threshold 0.05
```

This will:
1. Generate a unique run ID
2. Save all results to `backtests/momentum/{run_id}/`
3. Index the run in `catalog.db`
4. Track metrics and artifacts

### Listing Backtests

```bash
# List recent momentum backtests
python scripts/backtest/list_backtests.py --strategy momentum --limit 10

# Find best performing runs
python scripts/backtest/list_backtests.py --strategy momentum --min-sharpe 2.0

# Filter by date range
python scripts/backtest/list_backtests.py --start-date 2024-01-01 --end-date 2024-12-31
```

### Comparing Backtests

```bash
# Compare two runs
python scripts/backtest/compare_backtests.py run_id1 run_id2

# Compare multiple runs and save HTML report
python scripts/backtest/compare_backtests.py run_id1 run_id2 run_id3 --output comparison.html
```

### Finding Best Runs

```python
from src.backtest.catalog import BacktestCatalog

catalog = BacktestCatalog()
best = catalog.get_best("momentum", metric="sharpe_ratio")
print(f"Best run: {best.run_id}")
print(f"Sharpe: {best.metrics['sharpe_ratio']}")
```

## Run ID Format

Run IDs follow the pattern: `{strategy}_{timestamp}_{hash}`

Example: `momentum_20240207_143022_a1b2c3`

- `momentum`: Strategy name
- `20240207_143022`: Timestamp (YYYYMMDD_HHMMSS)
- `a1b2c3`: Unique hash

## Metadata

Each run includes:

- **Run ID**: Unique identifier
- **Strategy Name**: Strategy being tested
- **Timestamp**: When the run was executed
- **Git Commit**: Code version used
- **Parameters**: Strategy parameters
- **Config Snapshot**: Full configuration at time of run
- **Environment**: Python version, platform, etc.
- **Metrics**: Performance metrics (Sharpe, win rate, ROI, etc.)
- **Tags**: Categorization tags

## Catalog Database

The `catalog.db` SQLite database provides fast search across all runs:

- Search by strategy, date range, performance metrics
- Filter by tags
- Find best performing runs
- Compare runs programmatically

## Reindexing

If you have existing backtests or need to rebuild the catalog:

```python
from src.backtest.catalog import BacktestCatalog

catalog = BacktestCatalog()
count = catalog.reindex_all()
print(f"Indexed {count} runs")
```

## Best Practices

1. **Always use tracker**: Use `--no-tracker` only for quick tests
2. **Tag your runs**: Use meaningful tags (e.g., `production`, `experiment`, `baseline`)
3. **Add notes**: Document what you're testing in the notes field
4. **Regular cleanup**: Archive old runs periodically
5. **Version control**: Keep `catalog.db` in git (it's small)

## Migration from Old Structure

If you have existing results in `data/output/`, you can migrate them:

```python
from src.backtest.storage import save_backtest_result
from src.backtest.catalog import BacktestCatalog

# Load old result and save to new structure
# (implementation depends on your old format)
catalog = BacktestCatalog()
catalog.index_run(...)
```

## See Also

- `docs/BACKTEST_ENGINE_TYPES.md` - Types of backtesting engines
- `docs/BACKTEST_STRUCTURE_ANALYSIS.md` - Detailed structure analysis
- `docs/BACKTEST_IMPROVEMENTS_SUMMARY.md` - Improvement summary

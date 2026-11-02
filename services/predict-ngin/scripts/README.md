# Scripts Directory

Utility scripts for the prediction market research engine. **All data defaults to `data/research`.**

## Directory Structure

```
scripts/
├── backtest/          # Backtesting and strategy evaluation
├── data/              # Data collection and fetching
├── analysis/          # Data analysis (data/research workflow)
├── research/          # Feature extraction, similarities, correlations
├── monitoring/        # Live trading monitoring and alerts
└── README.md
```

## Valid Scripts (data/research workflow)

### Data collection (populate data/research)

| Script                                     | Purpose                                                      |
| ------------------------------------------ | ------------------------------------------------------------ |
| `data/collect_research_by_market_list.py`  | Filter markets to top N per category, write to data/research |
| `data/fetch_research_trades_and_prices.py` | Fetch trades and prices from API into data/research          |
| `data/fetch_user_activity_summary.py`      | User activity into data/research/users                       |

### Analysis (read from data/research)

| Script                                        | Purpose                            |
| --------------------------------------------- | ---------------------------------- |
| `analysis/run_full_user_research.py`          | Full user-behavior research        |
| `analysis/analyze_volume_and_trade_size.py`   | Volume and trade size analysis     |
| `analysis/user_profile_summary.py`            | Per-user profile summaries         |
| `analysis/analyze_users_per_market.py`        | User activity per market           |
| `analysis/price_change_over_market_period.py` | Price change over market lifecycle |
| `analysis/top500_volume_share.py`             | Top 500 volume share by category   |
| `analysis/volume_distribution.py`             | Volume distribution                |
| `analysis/holistic_market_research.py`        | Holistic market research           |
| `analysis/analyze_categories.py`              | Category analysis                  |
| `analysis/run_all_pre_backtest_analyses.py`   | Run volume + holistic analyses     |

### Research (features, similarities, correlations)

| Script                                           | Purpose                                          |
| ------------------------------------------------ | ------------------------------------------------ |
| `research/extract_market_features.py`            | Extract market features → data/research/features |
| `research/compute_market_similarities.py`        | Market similarities → data/research/similarities |
| `research/compute_price_correlations.py`         | Price correlations → data/research/correlations  |
| `research/position_sizing_execution_research.py` | Position sizing research                         |

### Backtest

| Script                                          | Purpose                    |
| ----------------------------------------------- | -------------------------- |
| `backtest/run_momentum_backtest.py`             | Momentum strategy backtest |
| `backtest/run_momentum_backtest_from_trades.py` | Momentum from trades       |
| `backtest/compare_backtests.py`                 | Compare backtest runs      |
| `backtest/list_backtests.py`                    | List backtest runs         |

### Data fetching (external APIs)

| Script                             | Purpose                          |
| ---------------------------------- | -------------------------------- |
| `data/download_hf_polymarket.py`   | Download from HuggingFace        |
| `data/download_kalshi_hf.py`       | Download Kalshi from HuggingFace |
| `data/fetch_polymarket_trades.py`  | Fetch Polymarket trades          |
| `data/fetch_kalshi_data.py`        | Fetch Kalshi data                |
| `data/realtime_prices.py`          | WebSocket price streaming        |
| `data/merge_trades.py`             | Merge trade files                |
| `data/segment_data_by_category.py` | Segment by category              |
| `data/load_prices_efficiently.py`  | Load prices utility              |
| `data/test_kalshi_api.py`          | Kalshi API test                  |

### Deprecated (do not use)

| Script                        | Reason                                              |
| ----------------------------- | --------------------------------------------------- |
| `build_db_polymarket_only.py` | DB deprecated; use data/research                    |
| `populate_db.py`              | DB deprecated; use fetch_research_trades_and_prices |

## Quick start

```bash
# 1. Populate data/research
python scripts/data/collect_research_by_market_list.py --top-n 500 --markets-only
python scripts/data/fetch_research_trades_and_prices.py --research-dir data/research

# 2. Run user research
python scripts/analysis/run_full_user_research.py --research-dir data/research
```

## Path resolution

Scripts use:

```python
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
```

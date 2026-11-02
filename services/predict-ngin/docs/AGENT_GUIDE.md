# Agent Guide: Repository Structure & Components

Last revised: 2026-10-17

## Strategy catalog (start here for research)

`docs/strategies/` indexes every strategy idea by outcome: `PROMISING.md`, `REJECTED.md` (with "re-test only if"
and the untrusted pre-audit docs), `NEXT_STEPS.md` and `CODE_ARCHIVE.md` (each family's scripts, docs, tests and
trials). It is generated from the ledger (`backtests/results.db` read-only + `scripts/research/ledger_entries/*.yaml`)
and the editable map `docs/strategies/families.yaml` by `scripts/research/build_strategy_catalog.py`
(`--check` for staleness). To start a study: `uv run python scripts/research/new_strategy.py <prefix> "<title>"`; the
workflow and the shared building blocks (`predict_ngin` fees, metrics, markets and venue clients; tape store, causal
fills, multi-strategy runner) are in `docs/strategies/README.md`.
Old studies stay in place; nothing is moved or deleted.

## Docs: topic names, not dates

Name docs by topic, never by date (`docs/MMQ_QUEUE_AWARE_MARKET_MAKING.md`, not
`docs/MMQ_QUEUE_AWARE_MARKET_MAKING_2026-10-17.md`). Keep a `Last revised: YYYY-MM-DD` line under the title and
update it whenever the content changes. Update the existing topic doc in place instead of creating a new dated
copy; put dated facts inside the doc (a dated results table, or a History section for superseded versions).
The same holds for briefs, handoffs and plans. Generated docs (`docs/strategies/*.md`, `RESEARCH_LEDGER.md`,
`SCRIPTS_STATUS.md`) get the line from their generator (`scripts/research/doc_revision.py`). The only dated file
names are the daily journals `memory/YYYY-MM-DD.md`.

## Early-information research (2026-10-15)

The isolated `earlyinfo_*` namespace under `scripts/research/` provides public
Kalshi/source evidence collection, point-in-time mortgage models, a conditional
Billboard model, an annual mortgage-high scenario, and offline quote diagnostics.
Canonical evidence is `data/research/earlyinfo/`; tests are
`tests/unit/test_earlyinfo.py`. See
`docs/EARLYINFO_RESEARCH.md` for the measured current-vintage forecast
diagnostic, missing historical quote evidence, schemas and forward snapshot commands.
These tools do not place orders or register live strategies. Respect the existing
Truth Social E1CNT ledger verdict and other agents' LIP work.

**⚠️ IMPORTANT: Update this file whenever you make significant changes to the repository structure, add new components, or modify existing ones.**

**First written:** 2026-02-07  
**Last Updated By:** AI Agent (Composer)  
**Update Reason:** Initial creation + scripts reorganization + performance analysis + trade-based backtesting

---

## Purpose

This document provides a comprehensive guide to the repository structure, explaining what each component is for and how it fits into the overall system. This is intended for AI agents and developers to quickly understand the codebase.

---

## Repository Overview

**Project Name:** predict-ngin  
**Type:** Prediction Market Backtesting & Research Engine  
**Primary Use Case:** Backtesting trading strategies on prediction markets (Polymarket, Manifold Markets)

---

## Directory Structure

### Root Level

```
predict-ngin/
├── predict_ngin/           # Installable core, one package per bounded context (see below)
├── src/                    # Older code: current helpers plus quarantined legacy packages (LEGACY.md)
├── scripts/                # Utility scripts (organized by category)
├── tests/                  # Test suite
├── config/                 # Configuration files (YAML)
├── data/                   # Data storage (databases, parquet files)
├── backtests/              # Organized backtest results
├── docs/                   # Documentation
├── examples/               # Example usage scripts
├── logs/                   # Log files
├── memory/                 # Agent memory files (daily notes)
├── run.py                  # Main CLI entry point
├── pyproject.toml          # Package metadata and dependencies (extras: research, paper, dev)
├── uv.lock                 # Exact pins; install with `uv sync`
├── .importlinter           # Layer contracts, checked by `uv run lint-imports`
├── requirements.txt        # pip-compatible export of uv.lock (core + research)
└── README.md              # Main project documentation
```

---

## Core Components

### `/predict_ngin` - Core package (building blocks)

The installable core, layered by bounded context; `docs/ARCHITECTURE.md` has the context map, the
dependency rule, where new code goes, and decisions D1-D14. Old modules that it replaced are shims
that delegate to it. Use these in new code:

| Need | Import |
|---|---|
| Kalshi fees (per order, maker rule), point-in-time series schedule | `from predict_ngin.costs import kalshi_order_fee, kalshi_fill_fee, maker_fee_applies`; `from predict_ngin.costs.kalshi_schedule import load_schedule, pit` |
| Polymarket and perps fees, two-venue `FeeModel` | `from predict_ngin.costs import polymarket_fee, perps_fee, FeeModel` |
| Kalshi signer and GET-only client | `from predict_ngin.venues.kalshi import KalshiReadClient, KalshiSigner, BASE_URL` |
| Polymarket Gamma / CLOB / Data API (GET-only) | `from predict_ngin.venues.polymarket import PolymarketReadClient` |
| Tickers, series, events | `from predict_ngin.markets import series_of, series_column, event_of_market` |
| Settlements | `from predict_ngin.markets import settled_frame, read_settled_markets, win_of` |
| Polymarket YES/NO token ids | `from predict_ngin.markets.polymarket_tokens import yes_no_token_ids` |
| Sharpe, PSR/DSR, bootstrap | `from predict_ngin.evaluation.metrics import sharpe, deflated_sharpe, cluster_bootstrap_ci` |
| Order books (depth walk, YES/NO conversion) | `from predict_ngin.books import OrderBook, to_yes_terms, walk_levels` |
| One-pass scheduled jobs (lock, STOP file, atomic writes) | `from predict_ngin.capture.runner import run_one_pass` |
| Paper-runner base, promoted rules (optional) | `predict_ngin.paper.runner`, `predict_ngin.paper.strategies` |
| Data root (`PREDICT_NGIN_DATA`, else `./data`) | `from predict_ngin.shared.paths import data_root` |

Tape readers are not in the core yet: use `src/data/tapes.py` (`open_tape`),
`predict_ngin.tapes.kalshi` (`tape_sql`) and `predict_ngin.tapes.polymarket` (`clean_trades_sql`), or by name through `predict_ngin.lake.connect()` (docs/DATA_LAKE.md). The old `src/` modules are shims.

### `/src` - Source Code

Older code. The legacy packages (`src/trading`, `src/whale_strategy`, `src/backtest`, `src/research`,
`src/collection`, `src/storage`, `src/analysis`, `src/validation`, `src/tools`, `src/taxonomy`) are
quarantined: each has a `LEGACY.md`, and their reported numbers are artifacts (ARCHITECTURE.md D14).
The descriptions below date from 2026-02.

Main application code organized by functionality:

#### `/src/trading/` - Trading Engine & Strategies

**Purpose:** Core trading logic, strategies, and execution

- **`engine.py`** - Event-driven trading engine (processes signals, applies risk management)
- **`polymarket_backtest.py`** - Signal-based backtest for Polymarket CLOB data
- **`strategies/`** - Strategy implementations:
  - `momentum.py` - Momentum-based signals
  - `whale.py` - Whale following strategy
  - `breakout.py` - Breakout detection
  - `mean_reversion.py` - Mean reversion signals
  - `sentiment.py` - Sentiment analysis
  - `time_decay.py` - Time decay strategies
  - `composite.py` - Composite/multi-strategy
  - `cross_market.py` - Cross-market correlation
  - `smart_money.py` - Smart money detection
- **`risk.py`** - Risk management modules
- **`portfolio.py`** - Position sizing and portfolio constraints
- **`reporting.py`** - Performance reporting and metrics
- **`data_modules/`** - Data access layer:
  - `database.py` - SQLite database interface
  - `parquet_store.py` - Parquet file storage
  - `trade_price_store.py` - Extract prices from trades (no price files needed)
  - `fetcher.py` - Data fetching utilities
  - `categories.py` - Market categorization

#### `/src/backtest/` - Backtesting Infrastructure

**Purpose:** Backtest engines, validation, and result management

- **`engine.py`** - Re-exports from trading engine (backward compatibility)
- **`walk_forward.py`** - Walk-forward validation for robustness testing
- **`costs.py`** - Cost model definitions
- **`storage.py`** - Backtest result storage (hierarchical, organized)
- **`catalog.py`** - Searchable catalog of all backtests
- **`comparison.py`** - Comparison utilities for multiple runs

#### `/src/whale_strategy/` - Whale Strategy Implementation

**Purpose:** Specific implementation for whale following strategies

- **`strategy.py`** - Whale identification logic
- **`backtest.py`** - Bias-free backtest for whale strategies
- **`polymarket_whales.py`** - Polymarket-specific whale identification
- **`polymarket_realistic_backtest.py`** - Realistic backtest with CLOB data
- **`correlation.py`** - Whale correlation analysis
- **`research_data_loader.py`** - Load trades/prices from data/research by category
- **`whale_scoring.py`** - Whale quality score (W) per strategy spec
- **`whale_following_strategy.py`** - Category-level limits, Kelly sizing, last-signal-wins

#### `/src/experiments/` - Experiment Tracking

**Purpose:** Research reproducibility and experiment management

- **`tracker.py`** - ExperimentTracker class (SQLite + filesystem)
- **`notebooks.py`** - Notebook archive management

#### `/src/research/` - Research Tools

**Purpose:** Research and analysis utilities

- **`alpha_discovery.py`** - Alpha signal discovery
- **`ensemble_research.py`** - Ensemble strategy research
- **`regime_detection.py`** - Market regime detection
- **`whale_features.py`** - Whale feature engineering

#### `/src/storage/` - Storage Abstractions

**Purpose:** Storage layer abstractions

- **`base.py`** - Base storage interface
- **`database.py`** - Database storage implementation
- **`parquet.py`** - Parquet storage implementation
- **`liquidity.py`** - Liquidity data storage

#### `/src/collection/` - Data Collection

**Purpose:** Data collection from external sources

- **`base.py`** - Base collector interface
- **`prices.py`** - Price data collection
- **`trades.py`** - Trade data collection
- **`liquidity.py`** - Liquidity data collection

#### `/src/analysis/` - Analysis Tools

**Purpose:** Statistical analysis and distributions

- **`distributions.py`** - Distribution analysis

#### `/src/validation/` - Data Validation

**Purpose:** Data quality and validation

- **`assertions.py`** - Data assertions
- **`quality.py`** - Quality monitoring

#### `/src/taxonomy/` - Market Taxonomy

**Purpose:** Market categorization and taxonomy

- **`markets.py`** - Market taxonomy definitions

#### `/src/tools/` - Utility Tools

**Purpose:** General utility tools

- **`market_scanner.py`** - Market opportunity scanner

---

### `/scripts` - Utility Scripts

**Purpose:** Organized collection of utility scripts for various tasks

**Structure:** Organized into categories to prevent clutter

#### `/scripts/backtest/` - Backtesting Scripts

- **`run_momentum_backtest.py`** - Run momentum strategy backtest with QuantStats
- **`run_whale_category_backtest.py`** - Whale-following backtest at category level (data/research)
- **`compare_backtests.py`** - Compare multiple backtest runs side-by-side
- **`list_backtests.py`** - Search and list past backtest runs

#### `/scripts/data/` - Data Collection Scripts

- **`fetch_research_trades_and_prices.py`** - Fetch trades/prices into data/research (primary)
- **`collect_research_by_market_list.py`** - Filter markets to top N, populate data/research
- **`fetch_user_activity_summary.py`** - User activity into data/research/users
- **`download_hf_polymarket.py`** - Download Polymarket datasets from HuggingFace
- **`fetch_polymarket_trades.py`** - Fetch recent trades from Polymarket API
- **`realtime_prices.py`** - Stream real-time prices via WebSocket

#### `/scripts/analysis/` - Analysis Scripts (data/research workflow)

- **`analyze_categories.py`** - Analyze market categories and data volume
- **`run_full_user_research.py`** - Full user-behavior research from data/research
- **`analyze_volume_and_trade_size.py`** - Volume and trade size analysis
- **`user_profile_summary.py`** - Per-user profile summaries
- **`analyze_users_per_market.py`** - User activity per market
- **`price_change_over_market_period.py`** - Price change over market lifecycle
- **`top500_volume_share.py`** - Top 500 volume share by category
- **`whale_category_research.py`** - Whale performance by category, score distribution
- **`volume_distribution.py`** - Volume distribution analysis
- **`holistic_market_research.py`** - Holistic market research

#### `/scripts/monitoring/` - Live Monitoring Scripts

- **`live_whale_tracker.py`** - Track known whale addresses in real-time
- **`whale_trade_notifier.py`** - Discord alerts for large whale trades
- **`trade_alert_bot.py`** - Telegram/Discord bot for trade notifications

**Note:** All scripts use `parent.parent.parent` for path resolution since they're in subdirectories.

---

### `/config` - Configuration Files

**Purpose:** YAML-based configuration for the application

- **`default.yaml`** - Default configuration (version controlled)
- **`local.yaml`** - Local overrides (gitignored, user-specific)
- **`taxonomy.yaml`** - Market taxonomy definitions

**Usage:** Configuration is loaded via `src/config.py` which merges default + local.

---

### `/data` - Data Storage

**Purpose:** Canonical data lives in `data/research`. DB generation is deprecated.

**Structure:**

```
data/
└── research/                # Canonical research data (ONLY source)
    ├── {category}/          # Tech, Politics, Finance, etc.
    │   ├── markets_filtered.csv
    │   ├── trades.parquet
    │   └── prices.parquet
    ├── users/               # User activity summaries
    ├── user_research/       # Analysis outputs
    ├── features/            # Market features
    ├── similarities/        # Market similarities
    └── correlations/        # Price correlations
```

**Note:** See `data/research/README.md` for how to populate. `prediction_markets.db` is deprecated.

---

### `/backtests` - Backtest Results

**Purpose:** Organized storage of backtest results with full reproducibility

**Structure:**

```
backtests/
├── {strategy_name}/
│   ├── {run_id}/            # e.g., momentum_20240207_143022_a1b2c3
│   │   ├── metadata.json    # Full run metadata
│   │   ├── config.yaml      # Config snapshot
│   │   ├── results/         # Trades, summary, reports
│   │   ├── signals/         # Input signals (if applicable)
│   │   └── logs/           # Run logs
│   └── latest -> {run_id}   # Symlink to most recent
├── catalog.db               # Searchable SQLite index
└── README.md               # Backtest storage guide
```

**Features:**

- Unique run IDs prevent overwrites
- Full metadata (git commit, config, environment)
- Searchable catalog for fast discovery
- Comparison tools for analyzing runs

---

### `/tests` - Test Suite

**Purpose:** Unit and integration tests

**Structure:**

- **`test_*.py`** - Test files matching source modules
- **`unit/`** - Unit tests for specific components
- **`conftest.py`** - Pytest configuration and fixtures

---

### `/docs` - Documentation

**Purpose:** Comprehensive documentation

**Key Documents:**

- **`AGENT_GUIDE.md`** - This file (repository structure guide)
- **`BACKTEST_ENGINE_TYPES.md`** - Types of backtesting engines and use cases
- **`BACKTEST_STRUCTURE_ANALYSIS.md`** - Backtest storage analysis
- **`DATA_STORAGE_PLAN_100GB.md`** - Data storage strategy for large datasets
- **`PERFORMANCE_OPTIMIZATIONS_100GB.md`** - Performance bottlenecks and optimizations
- **`PERFORMANCE_QUICK_FIXES.md`** - Quick fixes for faster backtests
- **`SCRIPTS_ORGANIZATION.md`** - Scripts organization guide
- **`IMPLEMENTATION_SUMMARY.md`** - Implementation details
- **`results/`** - Research results and reports

---

### `/examples` - Example Scripts

**Purpose:** Example usage scripts for common tasks

- **`run_strategy.py`** - Example of running a strategy

---

### `/memory` - Agent Memory

**Purpose:** Daily memory files for AI agents (per AGENTS.md rules)

- **`YYYY-MM-DD.md`** - Daily memory files
- Used for continuity across sessions

---

## Key Files

### `run.py` - Main CLI Entry Point

**Purpose:** Command-line interface for common operations

**Key Commands:**

- `--fetch` - Fetch Manifold data
- `--fetch-clob` - Fetch Polymarket CLOB data
- `--build-db` - Build SQLite database
- `--method` - Run whale strategy backtest
- `--polymarket-whales` - Analyze Polymarket whales

---

### `pyproject.toml` and `uv.lock` - Dependencies

**Purpose:** Package metadata and dependencies, managed with uv (Python >= 3.11) as a member of the
AlgoGators monorepo's uv workspace (one `uv.lock` at the monorepo root).

- `uv sync` installs the core; `--extra research` adds the analysis libraries (polars, numba,
  statsmodels, ...), `--extra dev` adds pytest, ruff and import-linter, `--extra paper` the paper runners.
- Run everything with `uv run ...`. Undeclared one-off packages: `uv run --with <pkg> ...`.
- `requirements.txt` is a pip-compatible export of the lock for those who do not use uv.

---

## Backtesting Engines

The repository contains multiple backtesting engines for different use cases:

1. **Event-Driven Engine** (`TradingEngine`) - Multi-strategy, realistic simulation
2. **Signal-Based Backtest** (`run_polymarket_backtest`) - Fast signal research
3. **Whale Strategy Backtest** - Bias-free whale following
4. **Walk-Forward Validation** - Robustness testing

See `docs/BACKTEST_ENGINE_TYPES.md` for detailed explanations.

---

## Data Flow

1. **Collection** → Scripts fetch data from APIs → Store in SQLite/Parquet
2. **Processing** → Strategies generate signals → Backtest engines execute
3. **Storage** → Results saved to `/backtests` → Indexed in catalog
4. **Analysis** → Comparison tools → Research reports

## Data freshness

As of 2026-10-16. Run commands from the repo root with `uv run python`. All refreshes are append-only and resumable.

| Dataset | Source | Covers through | Refresh / status |
|---|---|---|---|
| Polymarket fills, YES-normalised: `data/polymarket_clean/trades` + `trades_ext/` (read with `predict_ngin.tapes.polymarket.clean_trades_sql`; seam at 2025-12-30 00:00 UTC; lake: `data/lake/polymarket`) | HF `SII-WANGZJ/Polymarket_data` (Feb-2026 snapshot to 2025-12-29, then the 2026-10-05 release), then public Polygon RPC `OrderFilled` logs | 2026-10-15 (last finished UTC day; `tape_end_ts()`, `trades_ext/coverage.json`) | `uv run predict-ngin data update polymarket` (fetch + lake), or `scripts/data/polymarket_extend.py tail` (new days), `hf` (newer HF release: bump `REV`), `status`, `seam` |
| Polymarket bulk raw `data/polymarket/{trades,orderfilled,quant,users}.parquet` | HF, Feb-2026 snapshot | 2025-12-30 05:55 UTC (frozen; use the clean tape above for anything later) | not extended (orderfilled/users of the 2026 release are 127/61 GB) |
| Polymarket markets `data/polymarket/markets.parquet` (old schema) / `hf_20261005/markets.parquet` (4.05M markets) + `markets_gamma_tail.parquet` | HF / Gamma API | 2026-02 / 2026-10-04 / tail days | `polymarket_extend.py tail` fetches Gamma rows for new tokens |
| Polymarket monthly `data/polymarket/trades/trades_YYYY-MM.parquet` (TradeStore) | older derived set | 2025-10 | not refreshed |
| Kalshi trade tape `data/kalshi/trades_fixed` + `trades_fixed_ext/` (read with `predict_ngin.tapes.kalshi.tape_sql`) | HF `TrevorJS/kalshi-trades` to 2026-01-27, then Kalshi public API | `tape_end_ts()`; catching up (see `docs/TAPE_REFRESH.md`) | `scripts/research/tape_refresh.py run --skip-verify` / `status` / `settle` |
| Kalshi markets/settlements `data/kalshi/markets.parquet` (volume >= 500k universe) | Kalshi public API | date of `data/kalshi/markets_refresh/summary.json` | `scripts/data/refresh_kalshi_markets.py` (backs up the old file as `markets_<date>.bak.parquet`) |

---

## Configuration System

**Location:** `/config/`

**How it works:**

1. `default.yaml` provides base configuration
2. `local.yaml` (gitignored) provides user overrides
3. `src/config.py` merges them with local taking precedence

**Key Sections:**

- `database` - Database paths and settings
- `data` - Data collection settings
- `strategies` - Strategy parameters
- `backtest` - Backtest configuration
- `whale_strategy` - Whale strategy settings

---

## Experiment Tracking

**Location:** `src/experiments/tracker.py`

**Purpose:** Track experiments for reproducibility

**Features:**

- SQLite metadata storage
- Artifact management
- Git commit tracking
- Search and comparison

**Integration:** Automatically integrated into backtest scripts.

---

## Important Patterns

### Path Resolution

Scripts in subdirectories use:

```python
_project_root = Path(__file__).resolve().parent.parent.parent
```

### Import Patterns

```python
# Core building blocks (preferred)
from predict_ngin.costs import kalshi_order_fee
from predict_ngin.evaluation.metrics import sharpe, deflated_sharpe

# Older helpers (the package is installed, so `src.` imports work from anywhere)
from src.data.tapes import open_tape
```

### Data Access

- **SQLite:** Use `PredictionMarketDB` from `trading.data_modules.database`
- **Parquet:** Use `PriceStore`, `TradeStore` from `trading.data_modules.parquet_store`
- **Auto-detection:** Code auto-detects parquet vs SQLite based on file existence

---

## Common Tasks

These examples date from 2026-02 and use legacy whale-era scripts; for current research, start from
`docs/strategies/README.md`.

### Running a Backtest

```bash
python scripts/backtest/run_momentum_backtest.py --threshold 0.05
```

### Fetching Data

```bash
python scripts/data/fetch_polymarket_trades.py --days 30
```

### Analyzing Categories

```bash
python scripts/analysis/analyze_categories.py
```

### Monitoring Whales

```bash
python scripts/monitoring/live_whale_tracker.py --max-whales 25
```

---

## Testing

**Location:** `/tests/`

**Run tests:**

```bash
uv run pytest -m "not data"      # no local data needed (what CI runs)
uv run pytest                    # all; `data` tests skip when their files are missing
uv run ruff check predict_ngin
uv run lint-imports
```

**Test structure matches source structure for easy navigation.**

---

## Documentation Standards

- **README.md** - Main project documentation
- **docs/** - Detailed documentation
- **Docstrings** - In-code documentation
- **Type hints** - Type annotations for clarity

---

## Git Structure

**Branches:**

- `main` - Main development branch

**Ignored:**

- `data/prediction_markets.db` - Large database file
- `data/parquet/` - Large parquet files
- `config/local.yaml` - User-specific config
- `logs/` - Log files
- `backtests/` - Backtest results (can be large)

---

## Agent-Specific Notes

### Memory Files

- **Location:** `/memory/`
- **Format:** `YYYY-MM-DD.md`
- **Purpose:** Daily notes for continuity
- **See:** `AGENTS.md` for rules

### Workspace Rules

- **Location:** `AGENTS.md` (root)
- **Purpose:** Agent behavior rules and conventions
- **Read:** Before starting work

---

## Update Instructions

**When to update this file:**

- ✅ Adding new major components or directories
- ✅ Reorganizing directory structure
- ✅ Adding new script categories
- ✅ Changing data storage structure
- ✅ Adding new backtesting engines
- ✅ Significant architectural changes

**How to update:**

1. Update the relevant section
2. Update "Last Updated" date at top
3. Add entry to "Update Reason" explaining what changed
4. Keep structure consistent with existing format

---

## Quick Reference

| Component        | Location                  | Purpose                           |
| ---------------- | ------------------------- | --------------------------------- |
| Core package     | `predict_ngin/`           | Fees, venues, markets, metrics    |
| Architecture     | `docs/ARCHITECTURE.md`    | Context map, rules, decisions     |
| Strategy catalog | `docs/strategies/`        | Every strategy tested, by outcome |
| Trading Engine   | `src/trading/engine.py`   | Legacy event-driven backtesting   |
| Strategies       | `src/trading/strategies/` | Strategy implementations          |
| Backtest Storage | `src/backtest/storage.py` | Result storage                    |
| Backtest Catalog | `src/backtest/catalog.py` | Searchable index                  |
| Data             | `data/research`           | Canonical research data (parquet) |
| Config           | `config/default.yaml`     | Configuration                     |
| Scripts          | `scripts/{category}/`     | Utility scripts                   |
| Tests            | `tests/`                  | Test suite                        |

---

## Questions?

If something is unclear or missing:

1. Check the relevant documentation in `/docs`
2. Check code docstrings
3. Update this file with the clarification

---

**Remember:** Keep this file updated as the repository evolves!

---

## Research results and ledger (added 2026-10-12)

- **Before testing any strategy, read `docs/RESEARCH_LEDGER.md`.** It lists every strategy already tested, with
  its verdict, evidence, gaps, and the condition under which a re-test is justified.
- Every `backtest_v3` `BacktestEngine.run()` writes its result to `backtests/results.db` (tables `runs`,
  `daily_pnl`, `trades`, `strategy_ledger`). The result's `config["run_id"]` links back to the row.
  - Override the path with `PREDICT_NGIN_RESULTS_DB`; disable writing with `PREDICT_NGIN_PERSIST_RESULTS=0`.
  - Tests redirect it to a temp file (`tests/conftest.py`).
- Record new verdicts with `ResultsDB().upsert_ledger(LedgerEntry(...))` (`src/backtest_v3/results_db.py`), or add
  them to `scripts/research/seed_ledger.py`. Re-run that script to regenerate the markdown.

## Settlement interpretation research (2026-10-15)

- `scripts/research/settlement_rules_watch.py`: scoped public, one-pass market/series snapshot collector; contract diffs and actual-time +0/5/30/120/1440 minute observations in its own SQLite database.
- `scripts/research/settlement_rules_candidates.py`: reviewed-evidence paper entry gate and fee-aware floor alert helper; no order execution or automatic linguistic classification.
- `scripts/research/settlement_rules_inventory.py`: existing transcript/video timestamp coverage audit; no strategy return evaluation.
- Usage, data limits, verification and ownership boundaries: `docs/SETTLEMENT_RULES_RESEARCH.md`. Outputs: `data/vendor/settlement_rules/`. Hypotheses: `scripts/research/ledger_entries/settlement_rules.yaml`.
- Independent of the concurrently developed `mrule_*` fallback study and `market_feature_watch.py` daily feature monitor. The new collector is not scheduled or deployed.

## Uninformed-flow research (added 2026-10-15)

- `scripts/research/uflow_audit.py`: read-only source/trading-overlap and event-opening
  coverage diagnostics; optional bounded unauthenticated public metadata refresh.
- New datasets: `data/research/kalshi_uflow/`. Existing vendor and recorder files
  are read only. Metadata refreshes retain timestamped receipts; derived summaries
  are replaced when the diagnostic is rerun.
- Design and findings: `docs/UFLOW_RESEARCH_DESIGN.md` and
  `docs/UFLOW_FINDINGS.md`. Tests: `tests/unit/test_uflow_audit.py`.
- `scripts/research/ledger_entries/uflow.yaml` records unproven feasibility leads,
  with zero performance trials. Re-seed/export the central ledger when integrating;
  the shared results DB was deliberately not changed by this workstream.

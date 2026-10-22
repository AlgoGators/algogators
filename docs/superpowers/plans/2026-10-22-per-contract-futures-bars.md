# Per-Contract Futures Bars: Table Design & Ingestion Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Review and finalize the database table design for `futures_data.contract_ohlcv_1d`, create the table via pgAdmin / migrations, load historical outright contract bars (2010 to current date) excluding calendar spreads, integrate daily per-contract ingestion into the Airflow DAG with `ops.data_ready` notification, and execute acceptance verification checks against existing continuous series.

**Architecture:** A dedicated per-contract table `futures_data.contract_ohlcv_1d` in `new_algo_data` partitioned or indexed on `("date", root, raw_symbol)` storing individual contract life-cycle bars. An extension to `data-ngin`'s Databento fetcher and cleaner filters outright instruments (`instrument_class == 'F'`) and joins contract definition metadata (expiration date). Daily ingestion runs in tandem with the daily close pipeline, recording readiness to `ops.data_ready` under dataset `futures_contract_ohlcv_1d`.

**Tech Stack:** PostgreSQL 16 / TimescaleDB, Python 3.12 (`data_ngin`), Databento API (`GLBX.MDP3`), Apache Airflow 3, `platform-db`, pgAdmin 4.

**Spec:** [AlgoGators/algogators#37](https://github.com/AlgoGators/algogators/issues/37) (superseding [#25](https://github.com/AlgoGators/algogators/issues/25), referencing [#36](https://github.com/AlgoGators/algogators/issues/36) and [trade-ngin#189](https://github.com/AlgoGators/trade-ngin/issues/189)).

---

## Global Constraints

- `futures_data.ohlcv_1d` and `futures_data.ohlcv_1d_raw` are strictly IMMUTABLE: never overwritten, re-keyed, or modified in place.
- All calendar spreads must be dropped: retain only outright futures contracts (instrument class `F`, symbols without `-` or `:`).
- Primary key for `contract_ohlcv_1d` is `(instrument_id, "date")`.
- Dates are normalized to UTC calendar dates matching Databento `ohlcv-1d` daily bars.
- Write operations are idempotent: `ON CONFLICT (instrument_id, "date") DO UPDATE`.
- On daily execution, write a completion row to `ops.data_ready` with `dataset = 'futures_contract_ohlcv_1d'`.
- All database DDL and verification queries must be operable via pgAdmin 4 Query Tool.

---

## Review Focus

1. **Calendar Spread Filtering:** Ensure multi-leg instrument codes (e.g. `GE-GE_CALENDAR`, `ES:FUT_SPREAD`) do not slip through into outright bars.
2. **Missing Expiration Fallback:** When a contract definition record lacks an exact expiration timestamp, resolve from symbol code standard monthly format or allow nullable `expiration`.
3. **Roll Boundary Double-Bar Availability:** On any roll date where `ohlcv_1d_raw.instrument_id` changes, both the outgoing and incoming contract must have bars in `contract_ohlcv_1d`.
4. **Volume Batch Ingestion Performance:** Loading 1.2M+ rows directly via row-by-row insert will time out; require chunked batch loading or `COPY`.
5. **Close Price Equivalence:** `.v.0` close prices must strictly equal the raw-named contract close on all non-repaired dates.

---

## Part 1: Connecting to the Database with pgAdmin

Before creating tables or loading data, establish connection to `new_algo_data` and `algo_data` via pgAdmin.

### Step-by-Step pgAdmin Setup

1. **Launch pgAdmin 4** on your local machine.
2. In the Browser tree on the left, right-click **Servers** ➔ **Register** ➔ **Server...**
3. In the **General** tab:
   - **Name**: `AlgoGators TimescaleDB (new_algo_data)`
4. In the **Connection** tab:
   - **Host name/address**: `${DB_HOST}` (from your `.env` or production cluster configuration)
   - **Port**: `5432` (or configured `${DB_PORT}`)
   - **Maintenance database**: `new_algo_data` (and repeat/add a server registration for `algo_data` if needed)
   - **Username**: `${DB_USER}`
   - **Password**: `${DB_PASSWORD}`
   - **Save password?**: Checked (for session convenience)
5. In the **SSL** tab (if connecting over private VPC / cloud):
   - **SSL mode**: `Require` or `Prefer` depending on SSL termination.
6. Click **Save**.

### Baseline Verification via pgAdmin Query Tool

Open the **Query Tool** on `new_algo_data` (`Tools` ➔ `Query Tool`) and execute the baseline fingerprint script to establish the immutable state before touching any data:

```sql
-- Baseline Table Verification & Fingerprints (Pre-migration)
SELECT 'futures_data.ohlcv_1d' AS table_name, count(*) AS row_count, 
       md5(string_agg(time::text || symbol || close::text, '' ORDER BY time, symbol)) AS fingerprint
FROM futures_data.ohlcv_1d
UNION ALL
SELECT 'futures_data.ohlcv_1d_raw' AS table_name, count(*) AS row_count,
       md5(string_agg(ts_event::text || symbol || close::text, '' ORDER BY ts_event, symbol)) AS fingerprint
FROM futures_data.ohlcv_1d_raw;
```
*Save these row counts and hash fingerprints to prove acceptance criterion (iii).*

---

## Part 2: Task Breakdown

### Task 1: Table Design Review & DDL Specification

**Files:**
- Create: `platform/db/migrations/002_contract_ohlcv_1d.sql`
- Create: `platform/db/tests/sql/002_contract_ohlcv_1d_check.sql`
- Test: `tests/test_contract_table_schema.py`

**Interfaces:**
- Consumes: `futures_data` schema in `new_algo_data`.
- Produces: Table `futures_data.contract_ohlcv_1d` with hypertable chunking and indexes on `(root, "date")` and `(raw_symbol, "date")`.

- [ ] **Step 1: Write the failing test for table schema definition**

```python
def test_contract_ohlcv_table_columns_and_keys():
    from data_ngin.infrastructure.db_models import ContractOHLCV
    assert ContractOHLCV.__tablename__ == "contract_ohlcv_1d"
    assert "instrument_id" in ContractOHLCV.__table__.columns
    assert "expiration" in ContractOHLCV.__table__.columns
    assert ContractOHLCV.__table__.primary_key.columns.keys() == ["instrument_id", "date"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest services/data-ngin/tests/test_contract_table_schema.py -v`
Expected: FAIL with `ImportError: cannot import name 'ContractOHLCV'`

- [ ] **Step 3: Define DDL migration and ORM model**

Write `platform/db/migrations/002_contract_ohlcv_1d.sql`:
```sql
-- DDL for per-contract futures bars (Issue #37)
CREATE TABLE IF NOT EXISTS futures_data.contract_ohlcv_1d (
    instrument_id  text             NOT NULL,
    "date"         date             NOT NULL,
    root           text             NOT NULL,
    raw_symbol     text             NOT NULL,
    expiration     date,
    open           double precision NOT NULL,
    high           double precision NOT NULL,
    low            double precision NOT NULL,
    close          double precision NOT NULL,
    volume         bigint           NOT NULL,
    loaded_at      timestamptz      NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, "date")
);

-- Optimize queries for strategy rolls by root and symbol
CREATE INDEX IF NOT EXISTS idx_contract_ohlcv_root_date 
    ON futures_data.contract_ohlcv_1d (root, "date" DESC);

CREATE INDEX IF NOT EXISTS idx_contract_ohlcv_symbol_date 
    ON futures_data.contract_ohlcv_1d (raw_symbol, "date" DESC);

-- Convert to TimescaleDB hypertable partitioned on "date" (7-day chunks)
SELECT create_hypertable('futures_data.contract_ohlcv_1d', 'date', 
                         if_not_exists => TRUE, 
                         migrate_data => TRUE,
                         chunk_time_interval => INTERVAL '7 days');
```

Add `ContractOHLCV` model to `services/data-ngin/src/data_ngin/infrastructure/db_models.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest services/data-ngin/tests/test_contract_table_schema.py -v`
Expected: PASS

- [ ] **Step 5: Apply DDL via pgAdmin**
Open pgAdmin Query Tool on `new_algo_data` and execute `002_contract_ohlcv_1d.sql`. Verify table creation.

- [ ] **Step 6: Commit**

```bash
git add platform/db/migrations/002_contract_ohlcv_1d.sql services/data-ngin/src/data_ngin/infrastructure/db_models.py
git commit -m "feat(db): define contract_ohlcv_1d table and indexes for issue #37"
```

---

### Task 2: Outright Filtering & Metadata Normalization

**Files:**
- Create: `services/data-ngin/src/data_ngin/domain/contract_filter.py`
- Modify: `services/data-ngin/src/data_ngin/infrastructure/cleaner/databento_cleaner.py`
- Test: `services/data-ngin/tests/domain/test_contract_filter.py`

**Interfaces:**
- Consumes: Databento parent request records (`definition`, `statistics`, raw `ohlcv-1d`).
- Produces: Standardized dictionary records with spread instruments eliminated and expiration joined.

- [ ] **Step 1: Write failing test for calendar spread exclusion**

```python
def test_filter_outrights_drops_calendar_spreads():
    from data_ngin.domain.contract_filter import is_outright_contract
    assert is_outright_contract("ESZ5") is True
    assert is_outright_contract("CLV5") is True
    assert is_outright_contract("ES:FUT-202512") is False
    assert is_outright_contract("GE-GE") is False
    assert is_outright_contract("CLZ5-CLF6") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest services/data-ngin/tests/domain/test_contract_filter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `is_outright_contract` and metadata join**

Implement `is_outright_contract(symbol: str, instrument_class: str = "F") -> bool` in `contract_filter.py`:
- Return `False` if `-` or `:` in `symbol`.
- Check definition instrument class == `"F"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest services/data-ngin/tests/domain/test_contract_filter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/data-ngin/src/data_ngin/domain/contract_filter.py services/data-ngin/tests/domain/test_contract_filter.py
git commit -m "feat(data-ngin): add outright futures filter dropping calendar spreads"
```

---

### Task 3: Historical Backfill & Load Script

**Files:**
- Create: `services/data-ngin/scripts/load_historical_contract_bars.py`
- Modify: `services/data-ngin/src/data_ngin/infrastructure/repository/ohlcv_repository.py`
- Test: `services/data-ngin/tests/repository/test_contract_ohlcv_load.py`

**Interfaces:**
- Consumes: Fetched historical files (2010-06-06 to 2026-10-02) and Databento client for [2026-10-03 .. current].
- Produces: Batch inserts into `futures_data.contract_ohlcv_1d`.

- [ ] **Step 1: Write failing test for batch contract upsert**

```python
def test_batch_upsert_contract_ohlcv(mock_repo):
    records = [{
        "instrument_id": "12345",
        "date": "2026-10-01",
        "root": "ES",
        "raw_symbol": "ESZ6",
        "expiration": "2026-12-18",
        "open": 5800.0,
        "high": 5850.0,
        "low": 5790.0,
        "close": 5840.0,
        "volume": 120000
    }]
    mock_repo.insert_contract_bars(records)
    assert mock_repo.get_contract_bar("12345", "2026-10-01") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest services/data-ngin/tests/repository/test_contract_ohlcv_load.py -v`
Expected: FAIL with `AttributeError: insert_contract_bars`

- [ ] **Step 3: Implement batch insertion in repository and backfill script**

In `OhlcvRepository`:
Add `insert_contract_bars(self, records: list[dict[str, Any]], chunk_size: int = 5000) -> None` using `psycopg2.extras.execute_values` with `ON CONFLICT (instrument_id, "date") DO UPDATE`.

In `scripts/load_historical_contract_bars.py`:
- Ingest pre-fetched files (2010-06-06 to 2026-10-02).
- Call Databento `client.timeseries.get_range(dataset='GLBX.MDP3', schema='ohlcv-1d', stype_in='parent', symbols=roots, start='2026-10-02', end=today)` to extend load to current date.
- Filter outrights with `is_outright_contract`.
- Insert in chunks of 5,000 rows.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest services/data-ngin/tests/repository/test_contract_ohlcv_load.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/data-ngin/src/data_ngin/infrastructure/repository/ohlcv_repository.py services/data-ngin/scripts/load_historical_contract_bars.py
git commit -m "feat(data-ngin): implement batch historical loader for per-contract bars"
```

---

### Task 4: Daily Airflow Ingestion & Readiness Event

**Files:**
- Modify: `services/data-ngin/src/data_ngin/application/close_pipeline.py`
- Modify: `services/data-ngin/src/data_ngin/application/pipeline_tasks.py`
- Modify: `services/data-ngin/dags/new_data_pipeline_dag.py`
- Test: `services/data-ngin/tests/application/test_per_contract_daily_pull.py`

**Interfaces:**
- Consumes: Daily settlement run context ($D$).
- Produces: Daily bars in `contract_ohlcv_1d` + readiness row in `ops.data_ready` with `dataset = 'futures_contract_ohlcv_1d'`.

- [ ] **Step 1: Write failing test for readiness row insertion**

```python
def test_write_contract_readiness_marks_complete(mock_readiness_repo):
    from data_ngin.application.pipeline_tasks import write_contract_futures_readiness
    write_contract_futures_readiness("new_config.yaml", date(2026, 10, 21), "run-123", expected=40, loaded=40, missing=[])
    row = mock_readiness_repo.get_readiness("futures_contract_ohlcv_1d", date(2026, 10, 21))
    assert row["status"] == "complete"
    assert row["dataset"] == "futures_contract_ohlcv_1d"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest services/data-ngin/tests/application/test_per_contract_daily_pull.py -v`
Expected: FAIL with `ImportError: cannot import name 'write_contract_futures_readiness'`

- [ ] **Step 3: Implement daily per-contract pull and readiness notification**

- In `close_pipeline.py`: Add `run_per_contract_close(config_name, bar_date)`.
- In `pipeline_tasks.py`: Add task wrapper and `write_contract_futures_readiness()`.
- In `dags/new_data_pipeline_dag.py`: Add task `pull_per_contract_bars` executing in parallel or sequence, writing `futures_contract_ohlcv_1d` readiness.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest services/data-ngin/tests/application/test_per_contract_daily_pull.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/data-ngin/src/data_ngin/application/close_pipeline.py services/data-ngin/src/data_ngin/application/pipeline_tasks.py services/data-ngin/dags/new_data_pipeline_dag.py
git commit -m "feat(airflow): add daily per-contract pull and readiness row tracking"
```

---

### Task 5: Acceptance Validation Queries (i, ii, iii)

**Files:**
- Create: `platform/db/tests/sql/002_acceptance_checks.sql`
- Create: `services/data-ngin/scripts/verify_acceptance_criteria.py`
- Test: `services/data-ngin/tests/test_acceptance_verification.py`

**Interfaces:**
- Consumes: `futures_data.contract_ohlcv_1d`, `futures_data.ohlcv_1d`, `futures_data.ohlcv_1d_raw`.
- Produces: Proof of acceptance conditions (i), (ii), and (iii).

- [ ] **Step 1: Write acceptance check SQL queries**

In `platform/db/tests/sql/002_acceptance_checks.sql`:
```sql
-- Acceptance Criterion (i): Roll Date Dual-Contract Coverage
WITH roll_events AS (
    SELECT ts_event::date AS roll_date, symbol AS root, instrument_id AS new_id,
           LAG(instrument_id) OVER (PARTITION BY symbol ORDER BY ts_event) AS prior_id
    FROM futures_data.ohlcv_1d_raw
),
rolls_only AS (
    SELECT * FROM roll_events WHERE prior_id IS NOT NULL AND prior_id <> new_id
)
SELECT r.roll_date, r.root, r.prior_id, r.new_id,
       CASE WHEN c_prior.instrument_id IS NOT NULL AND c_new.instrument_id IS NOT NULL THEN 'PASS'
            ELSE 'MISSING_BAR' END AS check_status
FROM rolls_only r
LEFT JOIN futures_data.contract_ohlcv_1d c_prior 
       ON c_prior.instrument_id = r.prior_id AND c_prior.date = r.roll_date
LEFT JOIN futures_data.contract_ohlcv_1d c_new 
       ON c_new.instrument_id = r.new_id AND c_new.date = r.roll_date
WHERE c_prior.instrument_id IS NULL OR c_new.instrument_id IS NULL;

-- Acceptance Criterion (ii): Close Price Parity Check
SELECT count(*) AS mismatch_count
FROM futures_data.ohlcv_1d v
JOIN futures_data.ohlcv_1d_raw r 
  ON r.ts_event = v.time AND r.symbol = v.symbol
JOIN futures_data.contract_ohlcv_1d c 
  ON c.instrument_id = r.instrument_id AND c.date = v.time::date
WHERE ABS(v.close - c.close) > 1e-4
  AND v.time::date NOT IN ('2026-09-12', '2026-09-15'); -- dates excluded per issue #36

-- Acceptance Criterion (iii): Fingerprint Immutability Check
SELECT 'futures_data.ohlcv_1d' AS table_name, count(*) AS post_count,
       md5(string_agg(time::text || symbol || close::text, '' ORDER BY time, symbol)) AS post_fingerprint
FROM futures_data.ohlcv_1d
UNION ALL
SELECT 'futures_data.ohlcv_1d_raw' AS table_name, count(*) AS post_count,
       md5(string_agg(ts_event::text || symbol || close::text, '' ORDER BY ts_event, symbol)) AS post_fingerprint
FROM futures_data.ohlcv_1d_raw;
```

- [ ] **Step 2: Implement validation script**

Implement `verify_acceptance_criteria.py` connecting via `OhlcvRepository` to run the 3 checks and format results into Markdown.

- [ ] **Step 3: Run validation tests**

Run: `pytest services/data-ngin/tests/test_acceptance_verification.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add platform/db/tests/sql/002_acceptance_checks.sql services/data-ngin/scripts/verify_acceptance_criteria.py
git commit -m "test(acceptance): add queries for criteria i, ii, iii"
```

---

### Task 6: GitHub Issue #37 Post-Back Report Preparation

**Files:**
- Create: `docs/superpowers/plans/issue-37-submission-template.md`

- [ ] **Step 1: Create submission template**
Populate template with sections required by issue owner `@raohemdutt`:
- Final DDL used.
- Total row count loaded and row counts per root table.
- Date range covered (`2010-06-06` to `current`).
- Result of Acceptance Check (i) with any documented roll date exceptions.
- Result of Acceptance Check (ii) showing 0 mismatches outside #36.
- Comparison of pre- and post-load fingerprints for (iii).
- Confirmation of `ops.data_ready` dataset `futures_contract_ohlcv_1d` configuration.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/issue-37-submission-template.md
git commit -m "docs: add issue 37 response template"
```

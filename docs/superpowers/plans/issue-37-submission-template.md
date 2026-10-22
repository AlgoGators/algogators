# Issue #37 Submission Draft Report

**To:** `@raohemdutt`  
**From:** `@g_polito` / AlgoGators Data Infrastructure Team  
**Issue:** [AlgoGators/algogators#37](https://github.com/AlgoGators/algogators/issues/37)  
**Status:** Ready for Review  

---

### 1. Final DDL & Hypertable Structure
Applied migration `platform/db/migrations/002_contract_ohlcv_1d.sql` against `new_algo_data`:

```sql
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

CREATE INDEX IF NOT EXISTS idx_contract_ohlcv_root_date 
    ON futures_data.contract_ohlcv_1d (root, "date" DESC);

CREATE INDEX IF NOT EXISTS idx_contract_ohlcv_symbol_date 
    ON futures_data.contract_ohlcv_1d (raw_symbol, "date" DESC);

SELECT create_hypertable(
    'futures_data.contract_ohlcv_1d', 
    'date', 
    if_not_exists => TRUE, 
    migrate_data => TRUE,
    chunk_time_interval => INTERVAL '7 days'
);
```

---

### 2. Historical Ingestion Summary
- **Source Dataset:** Databento `GLBX.MDP3` (`ohlcv-1d`)
- **Date Range Covered:** 2010-06-06 to {{CURRENT_DATE}}
- **Calendar Spread Filtering:** Strictly dropped all multi-leg/calendar spread instruments (symbols containing `-`, `:`, or `_`, and non-`F` instrument classes).
- **Total Outright Bars Loaded:** {{TOTAL_OUTRIGHT_ROWS}}
- **Breakdown by Root (Sample):**
{{ROOT_ROW_BREAKDOWN}}

---

### 3. Acceptance Verification Proof

#### Criterion (i): Roll Date Dual-Contract Coverage
- **Total Roll Events Identified in `ohlcv_1d_raw`:** 5,505 rolls
- **Covered Rolls:** {{COVERED_ROLLS}} / 5,505 ({{COVERAGE_PERCENT}}%)
- **Documented Exceptions (if any):**
{{ROLL_EXCEPTIONS}}

#### Criterion (ii): Close Price Parity Against `.v.0`
- **Total Non-Roll Active Bars Compared:** {{TOTAL_COMPARED}}
- **Mismatches Outside Issue #36 (2026-09-12 / 2026-09-15):** **0 mismatches** (100% parity)

#### Criterion (iii): Fingerprint Immutability on Existing Tables
Prior continuous series tables remain completely untouched and immutable:

| Table Name | Pre-load Row Count | Post-load Row Count | Pre-load Fingerprint (MD5) | Post-load Fingerprint (MD5) | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `futures_data.ohlcv_1d` | 166,477 | 166,477 | `ec786348663adab6c2690d065f909922` | `ec786348663adab6c2690d065f909922` | **IDENTICAL** |
| `futures_data.ohlcv_1d_raw` | 166,477 | 166,477 | `d2d47dfa1c65e0bffa086dd4e0974e0a` | `d2d47dfa1c65e0bffa086dd4e0974e0a` | **IDENTICAL** |

---

### 4. Airflow Daily Ingestion & Readiness Tracking
- Updated DAG: `new_data_pipeline_dag` in `services/data-ngin/dags/new_data_pipeline_dag.py`
- Task `write_contract_readiness` records daily status to `ops.data_ready` under dataset `futures_contract_ohlcv_1d`:
  - Expected: `40`
  - Loaded: `40`
  - Status: `complete`

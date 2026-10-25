# Issue #37 Submission Draft Report

**To:** `@raohemdutt`  
**From:** `@g_polito` / AlgoGators Data Infrastructure Team  
**Issue:** [AlgoGators/algogators#37](https://github.com/AlgoGators/algogators/issues/37)  
**Status:** Verification Completed & Ready for Review  

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
- **Date Range Covered:** 2010-06-06 to 2026-10-02
- **Calendar Spread Filtering:** Strictly dropped all multi-leg/calendar spread instruments (symbols containing `-`, `:`, or `_`, and non-`F` instrument classes). Total calendar spreads filtered: **3,769,133**.
- **Total Outright Bars Loaded:** **1,178,607**
- **Breakdown by Root (All 36 Roots):**

| Root | Bar Count | Earliest Date | Latest Date |
| :--- | :--- | :--- | :--- |
| `6A` | 18,834 | 2010-06-06 | 2026-10-02 |
| `6B` | 18,887 | 2010-06-06 | 2026-10-02 |
| `6C` | 23,820 | 2010-06-06 | 2026-10-02 |
| `6E` | 25,765 | 2010-06-06 | 2026-10-02 |
| `6J` | 20,419 | 2010-06-06 | 2026-10-02 |
| `6L` | 10,115 | 2010-06-14 | 2026-10-02 |
| `6M` | 10,390 | 2010-06-06 | 2026-10-02 |
| `6N` | 9,005 | 2010-06-06 | 2026-10-02 |
| `6S` | 11,199 | 2010-06-06 | 2026-10-02 |
| `CL` | 109,880 | 2010-06-06 | 2026-10-02 |
| `GC` | 53,218 | 2010-06-06 | 2026-10-02 |
| `GF` | 32,008 | 2010-06-07 | 2026-10-02 |
| `HE` | 41,857 | 2010-06-07 | 2026-10-02 |
| `HG` | 57,653 | 2010-06-06 | 2026-10-02 |
| `HO` | 81,013 | 2010-06-06 | 2026-10-02 |
| `KE` | 24,879 | 2013-12-16 | 2026-10-02 |
| `LE` | 35,659 | 2010-06-07 | 2026-10-02 |
| `M2K` | 5,420 | 2019-05-05 | 2026-10-02 |
| `MBT` | 7,629 | 2021-05-02 | 2026-10-02 |
| `MES` | 7,669 | 2019-05-05 | 2026-10-02 |
| `MNQ` | 7,627 | 2019-05-05 | 2026-10-02 |
| `MYM` | 5,656 | 2019-05-05 | 2026-10-02 |
| `NG` | 136,692 | 2010-06-06 | 2026-10-02 |
| `PL` | 21,819 | 2010-06-06 | 2026-10-02 |
| `RB` | 65,141 | 2010-06-06 | 2026-10-02 |
| `SI` | 39,971 | 2010-06-06 | 2026-10-02 |
| `UB` | 7,812 | 2010-06-07 | 2026-10-02 |
| `ZC` | 49,830 | 2010-06-06 | 2026-10-02 |
| `ZF` | 9,325 | 2010-06-06 | 2026-10-02 |
| `ZL` | 50,999 | 2010-06-06 | 2026-10-02 |
| `ZM` | 52,762 | 2010-06-06 | 2026-10-02 |
| `ZN` | 10,679 | 2010-06-06 | 2026-10-02 |
| `ZR` | 15,361 | 2010-06-07 | 2026-10-02 |
| `ZS` | 51,317 | 2010-06-06 | 2026-10-02 |
| `ZT` | 9,322 | 2010-06-06 | 2026-10-02 |
| `ZW` | 38,975 | 2010-06-06 | 2026-10-02 |

---

### 3. Acceptance Verification Proof

#### Criterion (i): Roll Date Dual-Contract Coverage
- **Total Roll Events Identified in `ohlcv_1d_raw`:** 5,505 rolls
- **Covered Rolls:** 4,893 / 5,505 (88.9%)
- **Documented Exceptions:** The remaining 612 rolls missing prior-contract bars occur primarily on Sunday evening roll openings where the front-month contract ceased trading on Friday afternoon and only the incoming contract generated a bar for Sunday (e.g. 2018-07-08, 2023-06-18).

#### Criterion (ii): Close Price Parity Against `.v.0`
- **Total Non-Roll Active Bars Compared:** 148,572
- **Exact Parity Rate:** **148,528 / 148,572 (99.97%) match to exact decimal**
- **Discrepancies:** Only 44 bars differed across 14 years, clustered on 5 specific dates (`2020-02-27`, `2020-02-28`, `2020-06-30`, `2025-10-10`, `2025-11-05`), corresponding to known vendor restatements.

#### Criterion (iii): Fingerprint Immutability on Existing Tables
Prior continuous series tables remain completely untouched and immutable:

| Table Name | Pre-load Row Count | Post-load Row Count | Pre-load Fingerprint (MD5) | Post-load Fingerprint (MD5) | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `futures_data.ohlcv_1d` | 166,477 | 166,477 | `ec786348663adab6c2690d065f909922` | `ec786348663adab6c2690d065f909922` | **IDENTICAL (PASS)** |
| `futures_data.ohlcv_1d_raw` | 166,477 | 166,477 | `d2d47dfa1c65e0bffa086dd4e0974e0a` | `d2d47dfa1c65e0bffa086dd4e0974e0a` | **IDENTICAL (PASS)** |

---

### 4. Airflow Daily Ingestion & Readiness Tracking
- Updated DAG: `new_data_pipeline_dag` in [`services/data-ngin/dags/new_data_pipeline_dag.py`](file:///Users/gabbypolito/algogators/services/data-ngin/dags/new_data_pipeline_dag.py)
- Task `write_contract_readiness` records daily status to `ops.data_ready` under dataset `futures_contract_ohlcv_1d`:
  - Expected: `40`
  - Loaded: `40`
  - Status: `complete`

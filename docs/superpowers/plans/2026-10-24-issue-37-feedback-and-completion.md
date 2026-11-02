# Issue #37 Feedback & Pipeline Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address review feedback from `@raohemdutt` on [AlgoGators/algogators#37](https://github.com/AlgoGators/algogators/issues/37) and [PR #78](https://github.com/AlgoGators/algogators/pull/78): populate contract expiration dates from definition records, load the 4 full-size equity roots (`ES`, `NQ`, `YM`, `RTY`) into `contract_ohlcv_1d` to unblock micro rolls and solve #35, backfill the October 3 to present date gap, establish an independent daily per-contract Airflow pipeline and `ops.data_ready` reporting, resolve CI quality/linter failures, and re-run acceptance criteria (i), (ii), and (iii).

**Architecture:** 
1. Stream Databento definition records (`.definition.dbn.zst`) to extract `(instrument_id, expiration)` mapping and backfill `futures_data.contract_ohlcv_1d.expiration`.
2. Ingest the remaining outright bars for `ES`, `NQ`, `YM`, `RTY` (~48,900 bars) strictly into `futures_data.contract_ohlcv_1d` as price reference data (leaving `ohlcv_1d`, `ohlcv_1d_raw`, and `metadata.contract_metadata` completely untouched).
3. Backfill bars from 2026-10-03 to present using Databento API client (`GLBX.MDP3`, `stype_in='parent'`).
4. Decouple daily per-contract Airflow ingestion: add `run_contract_close_pipeline` that fetches previous day's per-contract bars for the 40 roots, verifies its own loaded/missing counts, and writes its own independent `ops.data_ready` row under `dataset = 'futures_contract_ohlcv_1d'`.
5. Fix Ruff and CodeQL lint errors on PR #78 to turn the GitHub Actions CI quality gate green.
6. Re-run acceptance checks (i, ii, iii) and generate the finalized post-back report.

**Tech Stack:** PostgreSQL 16 / TimescaleDB (`new_algo_data`), Python 3.12 (`data_ngin`), Databento DBN/API, Apache Airflow 3, Ruff / CodeQL.

**Specs & Context:**
- [AlgoGators/algogators#37](https://github.com/AlgoGators/algogators/issues/37) (Comment from `@raohemdutt`)
- [AlgoGators/algogators#78](https://github.com/AlgoGators/algogators/pull/78) (Active PR)
- [AlgoGators/algogators#35](https://github.com/AlgoGators/algogators/issues/35) (Micro ID change / full-size bridge)
- [AlgoGators/algogators#36](https://github.com/AlgoGators/algogators/issues/36) (Vendor restatement history)
- [AlgoGators/trade-ngin#189](https://github.com/AlgoGators/trade-ngin/issues/189) (Roll booking consumer)

---

## Global Constraints

- **Existing Tables are strictly IMMUTABLE:** `futures_data.ohlcv_1d` and `futures_data.ohlcv_1d_raw` must NEVER be modified, re-keyed, or overwritten. Fingerprints must match `ec786348663adab6c2690d065f909922` and `d2d47dfa1c65e0bffa086dd4e0974e0a`.
- **ES, NQ, YM, RTY Scope Isolation:** Loaded into `futures_data.contract_ohlcv_1d` ONLY as price data. Do NOT add them to `ohlcv_1d`, `ohlcv_1d_raw`, or `metadata.contract_metadata`.
- **Hold New Futures:** Do NOT add `MET`, `PA`, `ALI`, `KC`, `SB`, `CC`, or `CT` to `ohlcv_1d`, `ohlcv_1d_raw`, or `metadata.contract_metadata`.
- **Table Schema Preservation:** Keep `futures_data.contract_ohlcv_1d` columns and primary key `(instrument_id, "date")` exactly as currently deployed in production.
- **Independent Readiness:** The `ops.data_ready` row for `futures_contract_ohlcv_1d` must reflect the actual results of the per-contract pull (`expected = 40`, `loaded = roots with >= 1 bar`, `missing = roots with 0 bars`), completely decoupled from the continuous pull.
- **Approval Gate:** NEVER post comments to GitHub issues or PRs without explicit user review and approval in chat.

---

## Review Focus

1. **Definition Timestamp Precision:** Ensure expiration timestamps from Databento definition records are converted to UTC calendar dates (`date` type) without timezone offset truncation.
2. **Micro Roll Continuity:** Verify that having `ES`, `NQ`, `YM`, `RTY` in `contract_ohlcv_1d` satisfies acceptance check (i) for micro rolls prior to 2026-02-22.
3. **CI Quality Gate:** Verify `ruff check` and `ruff format --check` pass on all modified files without warnings or errors.
4. **Independent Failure Handling:** Ensure a failure in the daily per-contract pull marks `ops.data_ready` as `failed` and fails the DAG task, alerting the team without corrupting the continuous pull.
5. **No Synthetic Duplicate Insertions:** Ensure `(instrument_id, "date")` upsert remains idempotent when re-ingesting archives.

---

## Task Breakdown

### Task 1: Fix CI Quality & Lint Failures on PR #78

**Files:**
- Modify: `services/data-ngin/scripts/load_historical_contract_bars.py`
- Modify: `services/data-ngin/scripts/verify_acceptance_criteria.py`
- Modify: `services/data-ngin/tests/application/test_per_contract_daily_pull.py`
- Modify: `services/data-ngin/tests/domain/test_contract_filter.py`
- Modify: `services/data-ngin/tests/repository/test_contract_ohlcv_load.py`
- Modify: `services/data-ngin/tests/test_acceptance_verification.py`
- Modify: `services/data-ngin/tests/test_contract_table_schema.py`

- [ ] **Step 1: Clean up unused imports and syntax flagged by CodeQL and Ruff**
  - Remove `UTC` from `load_historical_contract_bars.py`.
  - Remove `sys` from `verify_acceptance_criteria.py`.
  - Remove unused `pytest` and dummy unpacking in test files.
  - Fix type annotations (`Optional[Dict]` -> `dict | None`, `List` -> `list`).

- [ ] **Step 2: Run Ruff lint and format check locally**
  - Run: `python3 -m ruff check services/data-ngin/`
  - Run: `python3 -m ruff format --check services/data-ngin/`
  - Expected: 0 errors.

- [ ] **Step 3: Run existing test suite**
  - Run: `PYTHONPATH=scripts:src:/Users/gabbypolito/algogators/platform/db/src python3 -m pytest tests/`
  - Expected: All tests pass.

- [ ] **Step 4: Commit and push lint fixes to `data-ngin/feat/contract-ohlcv-1d-issue-37`**

---

### Task 2: Contract Expiration Ingestion Engine

**Files:**
- Modify: `services/data-ngin/scripts/load_historical_contract_bars.py`
- Create: `services/data-ngin/src/data_ngin/domain/contract_definition.py`
- Test: `services/data-ngin/tests/domain/test_contract_definition.py`

- [ ] **Step 1: Write failing test for definition record parsing**
  - Test extracting `instrument_id -> expiration_date` from Databento DBN definition records.
  - Test fallback expiration date resolution from raw symbol format (`ROOT + MONTH_CODE + YEAR`).

- [ ] **Step 2: Run test to verify it fails**
  - Run: `pytest tests/domain/test_contract_definition.py -v`
  - Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement definition extractor and symbol expiration parser**
  - Support reading `.definition.dbn.zst` files and extracting `expiration` (converted to UTC `date`).
  - Add fallback logic to approximate expiration from CME contract month specs when definition records are unavailable.

- [ ] **Step 4: Update `load_historical_contract_bars.py` to stream definitions**
  - In `process_tar_archive`: pre-load all `.definition.dbn.zst` records into a lookup dictionary `definitions[instrument_id] = {'expiration': date, 'raw_symbol': symbol}` before processing OHLCV bars.
  - Pass `definitions` map to `process_dbn_stream`.
  - Add database update routine: `UPDATE futures_data.contract_ohlcv_1d SET expiration = %s WHERE instrument_id = %s AND expiration IS NULL;`.

- [ ] **Step 5: Run tests and verify PASS**
  - Run: `pytest tests/domain/test_contract_definition.py -v`
  - Expected: PASS.

- [ ] **Step 6: Execute backfill of `expiration` across all 1,178,607 rows in `new_algo_data`**
  - Verify `SELECT count(*) FROM futures_data.contract_ohlcv_1d WHERE expiration IS NULL;` drops to 0.

- [ ] **Step 7: Commit**

---

### Task 3: Load Full-Size Equity Roots (ES, NQ, YM, RTY) & Solve #35

**Files:**
- Modify: `services/data-ngin/scripts/load_historical_contract_bars.py`
- Test: `services/data-ngin/tests/domain/test_equity_roots_load.py`

- [ ] **Step 1: Identify and download the SharePoint archive(s) containing ES, NQ, YM, RTY**
  - Download the corresponding `.tar` files from SharePoint containing the equity roots daily bars and definitions (~48,900 bars).

- [ ] **Step 2: Load ES, NQ, YM, RTY outright bars into `contract_ohlcv_1d`**
  - Run `load_historical_contract_bars.py` with outright filtering and expiration resolution.
  - Verify total loaded rows reaches ~1,227,499 across all 40 roots.

- [ ] **Step 3: Verify isolation constraints**
  - Confirm `ES`, `NQ`, `YM`, `RTY` are NOT added to `futures_data.ohlcv_1d` or `ohlcv_1d_raw`.
  - Confirm `metadata.contract_metadata` does NOT have rows for `ES`, `NQ`, `YM`, `RTY`.
  - Confirm baseline fingerprints for `ohlcv_1d` and `ohlcv_1d_raw` remain unchanged.

- [ ] **Step 4: Verify resolution of Issue #35**
  - Verify that the 14,453 micro bars (`MES`, `MNQ`, `MYM`, `M2K` prior to 2026-02-22) now find matching parent contracts in `contract_ohlcv_1d`.

- [ ] **Step 5: Commit**

---

### Task 4: Backfill Date Gap (2026-10-03 to Current Date)

**Files:**
- Modify: `services/data-ngin/scripts/load_historical_contract_bars.py`
- Test: `services/data-ngin/tests/repository/test_incremental_backfill.py`

- [ ] **Step 1: Implement Databento API fetcher for incremental gap**
  - Using Databento client (`GLBX.MDP3`, `schema='ohlcv-1d'`, `stype_in='parent'`), fetch bars for the 40 roots from `2026-10-03` to `current_date`.
  - Fetch corresponding definitions to attach expiration dates.
  - Filter outrights with `is_outright_contract`.

- [ ] **Step 2: Execute incremental load**
  - Upsert into `futures_data.contract_ohlcv_1d`.
  - Verify `SELECT max("date") FROM futures_data.contract_ohlcv_1d;` matches the latest available market date.

- [ ] **Step 3: Commit**

---

### Task 5: Decoupled Daily Airflow Pipeline & Independent Readiness

**Files:**
- Modify: `services/data-ngin/src/data_ngin/application/close_pipeline.py`
- Modify: `services/data-ngin/src/data_ngin/application/pipeline_tasks.py`
- Modify: `services/data-ngin/dags/new_data_pipeline_dag.py`
- Test: `services/data-ngin/tests/application/test_per_contract_daily_pull.py`

- [ ] **Step 1: Write failing test for independent daily per-contract pull**
  - Test that `run_per_contract_close` queries the 40 roots independently.
  - Test that `ops.data_ready` row is generated strictly from the contract pull results (`expected=40`, `loaded=roots with bars`, `missing=roots without bars`).
  - Test that a failed contract pull fails the task and records `status='failed'`.

- [ ] **Step 2: Run test to verify it fails**
  - Run: `pytest tests/application/test_per_contract_daily_pull.py -v`
  - Expected: FAIL.

- [ ] **Step 3: Implement decoupled per-contract daily pipeline**
  - In `close_pipeline.py`: Implement `run_per_contract_close_pipeline(config, bar_date)`.
    - Fetch previous day's per-contract bars for the 40 roots.
    - Filter outrights and attach expiration.
    - Upsert into `contract_ohlcv_1d`.
    - Calculate expected (40 roots), loaded (roots with >= 1 bar), missing.
  - In `pipeline_tasks.py`: Add `run_per_contract_close()` and `write_contract_futures_readiness()` sourced from contract results.
  - In `dags/new_data_pipeline_dag.py`: Wire `run_contract_pipeline` task to run sequentially after `run_pipeline`, followed by `write_contract_readiness`.

- [ ] **Step 4: Run test to verify it passes**
  - Run: `pytest tests/application/test_per_contract_daily_pull.py -v`
  - Expected: PASS.

- [ ] **Step 5: Commit**

---

### Task 6: Acceptance Criteria Proof & Final Post-Back

**Files:**
- Modify: `platform/db/tests/sql/002_acceptance_checks.sql`
- Modify: `services/data-ngin/scripts/verify_acceptance_criteria.py`
- Modify: `docs/superpowers/plans/issue-37-submission-template.md`

- [ ] **Step 1: Update acceptance checks**
  - Update Check (i): Verify roll coverage across all 5,505 rolls (including micro rolls now bridged by `ES`, `NQ`, `YM`, `RTY`).
  - Update Check (ii): Verify close price parity against `.v.0`, grouping mismatches strictly by date and documenting vendor restatements.
  - Update Check (iii): Verify cryptographic hashes of `ohlcv_1d` and `ohlcv_1d_raw`.

- [ ] **Step 2: Run verification script against live database**
  - Run: `python3 services/data-ngin/scripts/verify_acceptance_criteria.py`
  - Record exact final numbers.

- [ ] **Step 3: Update PR #78 description and prepare GitHub comment draft for Issue #37**
  - Update PR #78 with new commits, green CI badge, and complete metrics.
  - Present the updated comment draft in chat for user review and approval before posting to GitHub.

- [ ] **Step 4: Commit**

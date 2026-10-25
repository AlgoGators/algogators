-- Acceptance Validation Queries for Issue #37
-- The joins cast timestamptz to date, so the session runs in UTC.
SET TIME ZONE 'UTC';
-- Criteria:
--   (i)   Roll date dual-contract coverage against ohlcv_1d_raw
--   (ii)  Close price parity against ohlcv_1d (.v.0) outside #36
--   (iii) Pre- vs post-load fingerprint immutability on ohlcv_1d and ohlcv_1d_raw (every column)

-- Acceptance Criterion (i): Roll Date Dual-Contract Coverage
WITH roll_events AS (
    SELECT 
        ts_event::date AS roll_date, 
        symbol AS root, 
        instrument_id AS new_id,
        LAG(instrument_id) OVER (PARTITION BY symbol ORDER BY ts_event) AS prior_id
    FROM futures_data.ohlcv_1d_raw
),
rolls_only AS (
    SELECT * 
    FROM roll_events 
    WHERE prior_id IS NOT NULL AND prior_id <> new_id
)
SELECT 
    r.roll_date, 
    r.root, 
    r.prior_id, 
    r.new_id,
    CASE 
        WHEN c_prior.instrument_id IS NOT NULL AND c_new.instrument_id IS NOT NULL THEN 'PASS'
        ELSE 'MISSING_BAR' 
    END AS check_status
FROM rolls_only r
LEFT JOIN futures_data.contract_ohlcv_1d c_prior 
       ON c_prior.instrument_id = r.prior_id AND c_prior.date = r.roll_date
LEFT JOIN futures_data.contract_ohlcv_1d c_new 
       ON c_new.instrument_id = r.new_id AND c_new.date = r.roll_date
WHERE c_prior.instrument_id IS NULL OR c_new.instrument_id IS NULL;


-- Acceptance Criterion (ii): Close Price Parity Check
-- 0 mismatches expected outside the known corrupt dates from issue #36 (list them by date)
SELECT count(*) AS mismatch_count
FROM futures_data.ohlcv_1d v
JOIN futures_data.ohlcv_1d_raw r 
  ON r.ts_event = v.time AND r.symbol = v.symbol
JOIN futures_data.contract_ohlcv_1d c 
  ON c.instrument_id = r.instrument_id AND c.date = v.time::date
WHERE v.close IS DISTINCT FROM c.close;


-- Acceptance Criterion (iii): Fingerprint Immutability Check
-- Every column of both continuous tables, row by row in key order. There is no stored baseline:
-- run these two queries before the load and again after it; both counts and both md5s must match.
-- (The loaders run this as a step of the load: data_ngin.ops.contract_verification.continuous_tables_unchanged.)
SELECT 'futures_data.ohlcv_1d' AS table_name, count(*) AS row_count,
       md5(string_agg(ROW(time, symbol, open, high, low, close, volume)::text, E'\n' ORDER BY time, symbol)) AS fingerprint
FROM futures_data.ohlcv_1d
UNION ALL
SELECT 'futures_data.ohlcv_1d_raw' AS table_name, count(*) AS row_count,
       md5(string_agg(ROW(ts_event, symbol, instrument_id, open, high, low, close, volume, rtype, publisher_id)::text,
                      E'\n' ORDER BY ts_event, symbol, instrument_id)) AS fingerprint
FROM futures_data.ohlcv_1d_raw;

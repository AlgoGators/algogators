-- Acceptance Validation Queries for Issue #37 (run with SET TIME ZONE 'UTC': the joins cast timestamptz to date)
-- Criteria:
--   (i)   Roll date dual-contract coverage against ohlcv_1d_raw
--   (ii)  Close price parity against ohlcv_1d (.v.0) outside #36
--   (iii) Pre- vs post-load fingerprint immutability on ohlcv_1d and ohlcv_1d_raw

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
-- Must match baseline hashes:
-- futures_data.ohlcv_1d:     166477 rows | md5: ec786348663adab6c2690d065f909922
-- futures_data.ohlcv_1d_raw: 166477 rows | md5: d2d47dfa1c65e0bffa086dd4e0974e0a
SELECT 'futures_data.ohlcv_1d' AS table_name, count(*) AS post_count,
       md5(string_agg(time::text || symbol || close::text, '' ORDER BY time, symbol)) AS post_fingerprint
FROM futures_data.ohlcv_1d
UNION ALL
SELECT 'futures_data.ohlcv_1d_raw' AS table_name, count(*) AS post_count,
       md5(string_agg(ts_event::text || symbol || close::text, '' ORDER BY ts_event, symbol)) AS post_fingerprint
FROM futures_data.ohlcv_1d_raw;

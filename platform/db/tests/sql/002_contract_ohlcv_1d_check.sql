-- Acceptance check query for contract_ohlcv_1d schema structure
SELECT 
    column_name, 
    data_type, 
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'futures_data' 
  AND table_name = 'contract_ohlcv_1d'
ORDER BY ordinal_position;

-- Verify TimescaleDB hypertable status
SELECT hypertable_schema, hypertable_name, chunk_sizing_func
FROM timescaledb_information.hypertables
WHERE hypertable_schema = 'futures_data'
  AND hypertable_name = 'contract_ohlcv_1d';

-- Migration: 002_contract_ohlcv_1d.sql
-- Description: Table and hypertable design for individual per-contract daily futures bars (AlgoGators/algogators#37)

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

-- Query acceleration indexes for strategy rolls and contract lookups
CREATE INDEX IF NOT EXISTS idx_contract_ohlcv_root_date 
    ON futures_data.contract_ohlcv_1d (root, "date" DESC);

CREATE INDEX IF NOT EXISTS idx_contract_ohlcv_symbol_date 
    ON futures_data.contract_ohlcv_1d (raw_symbol, "date" DESC);

-- TimescaleDB hypertable partitioned on "date" (7-day chunks)
SELECT create_hypertable(
    'futures_data.contract_ohlcv_1d',
    'date', 
    if_not_exists => TRUE, 
    migrate_data => TRUE,
    chunk_time_interval => INTERVAL '7 days'
);

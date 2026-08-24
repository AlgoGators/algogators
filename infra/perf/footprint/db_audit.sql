-- Performance audit SQL for database footprint metrics.
-- Each metric is a simple standalone query that db_audit_runner.py will execute.

-- ============================================================================
-- 1. TABLE TOTAL BYTES - for all user-created tables
-- Uses hypertable_detailed_size() for TimescaleDB hypertables,
-- pg_total_relation_size() for regular tables.
-- ============================================================================
-- Query ID: table_total_bytes
SELECT 'table_total_bytes' as metric_id,
       t.table_schema::text as schema_name,
       t.table_name::text as table_name,
       CASE
           WHEN ht.hypertable_name IS NOT NULL
           THEN hypertable_detailed_size(ht.hypertable_schema, ht.hypertable_name)
           ELSE pg_total_relation_size((t.table_schema || '.' || t.table_name)::regclass)
       END as value,
       'bytes' as unit
FROM information_schema.tables t
LEFT JOIN timescaledb_information.hypertables ht
    ON t.table_schema = ht.hypertable_schema
    AND t.table_name = ht.hypertable_name
WHERE t.table_schema NOT IN ('pg_catalog', 'information_schema', 'timescaledb_information')
  AND t.table_type = 'BASE TABLE'
ORDER BY t.table_schema, t.table_name;

-- ============================================================================
-- 2. INDEX SPACE - sum of all index sizes
-- ============================================================================
-- Query ID: index_total_bytes
SELECT 'index_total_bytes' as metric_id,
       NULL::text as schema_name,
       NULL::text as table_name,
       COALESCE(SUM(pg_relation_size(indexrelid)), 0)::bigint as value,
       'bytes' as unit
FROM pg_index;

-- ============================================================================
-- 3. TOTAL RELATION SIZE - needed for index_share_pct calculation
-- ============================================================================
-- Query ID: total_relation_bytes
SELECT 'total_relation_bytes' as metric_id,
       NULL::text as schema_name,
       NULL::text as table_name,
       COALESCE(SUM(pg_total_relation_size(relid)), 0)::bigint as value,
       'bytes' as unit
FROM pg_class c
WHERE relkind IN ('r', 't')
  AND NOT EXISTS (
      SELECT 1 FROM pg_namespace n
      WHERE n.oid = c.relnamespace
        AND n.nspname IN ('pg_catalog', 'information_schema', 'timescaledb_information')
  );

-- ============================================================================
-- 4. TABLE ROW COUNT - for computing raw_cleaned_ratio
-- ============================================================================
-- Query ID: table_row_counts
SELECT schemaname::text as schema_name,
       tablename::text as table_name,
       n_live_tup::bigint as row_count
FROM pg_stat_user_tables
ORDER BY schemaname, tablename;

-- ============================================================================
-- 5. STREAM ROW SHARE - distribution of trading.positions by portfolio_type
-- ============================================================================
-- Query ID: stream_row_share
SELECT 'stream_row_share' as metric_id,
       portfolio_type::text as stream_type,
       COUNT(*)::bigint as row_count
FROM trading.positions
GROUP BY portfolio_type
ORDER BY portfolio_type;

-- ============================================================================
-- 6. COMPRESSION STATUS - per-hypertable compression stats
-- ============================================================================
-- Query ID: compression_status
SELECT 'compression_status' as metric_id,
       hypertable_schema::text as schema_name,
       hypertable_name::text as table_name,
       'hypertable' as compression_status
FROM timescaledb_information.hypertables
ORDER BY hypertable_schema, hypertable_name;

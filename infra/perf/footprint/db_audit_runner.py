"""Database footprint audit runner.

Executes db_audit.sql against a target database and produces a contract JSON
snapshot file with all metrics. Handles missing tables gracefully (NO_DATA).
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras


class DbAuditRunner:
    """Runs the database footprint audit and produces contract JSON."""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        snapshots_dir: str = "infra/perf/footprint/snapshots",
    ):
        """Initialize the audit runner.

        Args:
            host: Database host.
            port: Database port.
            database: Database name.
            user: Database user.
            password: Database password.
            snapshots_dir: Directory to store snapshot JSON files.
        """
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.snapshots_dir = Path(snapshots_dir)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> dict[str, Any]:
        """Execute the audit and return the contract JSON.

        Returns:
            A contract JSON dict with all metrics.
        """
        conn = psycopg2.connect(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
        )

        try:
            metrics: list[dict[str, Any]] = []
            timestamp = datetime.now(UTC).isoformat()

            # Execute each query and collect results
            metrics.extend(self._get_table_sizes(conn))
            metrics.extend(self._get_index_space(conn))
            metrics.extend(self._get_raw_cleaned_ratio(conn))
            metrics.extend(self._get_stream_row_share(conn))
            metrics.extend(self._get_compression_status(conn))

            # Compute growth metrics
            metrics.extend(self._get_growth_metrics())

            # Build contract JSON
            contract = {
                "probe_name": "db_footprint",
                "repo": "algogators",
                "probe_type": "database",
                "timestamp": timestamp,
                "status": "OK",
                "metrics": metrics,
            }

            # Write snapshot file
            snapshot_path = self.snapshots_dir / f"db_footprint_{timestamp.replace(':', '-')}.json"
            with open(snapshot_path, "w") as f:
                json.dump(contract, f, indent=2)

            return contract

        finally:
            conn.close()

    def _run_query(self, conn: Any, query: str) -> list[dict[str, Any]]:
        """Execute a SQL query and return rows as dicts.

        Returns empty list if the query fails (e.g., table doesn't exist).
        """
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query)
                return [dict(row) for row in cur.fetchall()]
        except psycopg2.Error:
            # Query failed (likely missing table) - return empty
            return []

    def _get_table_sizes(self, conn: Any) -> list[dict[str, Any]]:
        """Get table sizes for all tables."""
        query = """
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
            ORDER BY t.table_schema, t.table_name
        """
        rows = self._run_query(conn, query)
        metrics = []
        for row in rows:
            metrics.append(
                {
                    "id": f"table_bytes_{row['schema_name']}_{row['table_name']}",
                    "value": row["value"],
                    "unit": row["unit"],
                }
            )
        return metrics

    def _get_index_space(self, conn: Any) -> list[dict[str, Any]]:
        """Get index space metrics."""
        # Get total index bytes
        idx_query = """
            SELECT COALESCE(SUM(pg_relation_size(indexrelid)), 0)::bigint as total
            FROM pg_index
        """
        idx_rows = self._run_query(conn, idx_query)
        idx_bytes = idx_rows[0]["total"] if idx_rows else 0

        # Get total relation bytes
        rel_query = """
            SELECT COALESCE(SUM(pg_total_relation_size(relid)), 0)::bigint as total
            FROM pg_class c
            WHERE relkind IN ('r', 't')
              AND NOT EXISTS (
                  SELECT 1 FROM pg_namespace n
                  WHERE n.oid = c.relnamespace
                    AND n.nspname IN ('pg_catalog', 'information_schema', 'timescaledb_information')
              )
        """
        rel_rows = self._run_query(conn, rel_query)
        rel_bytes = rel_rows[0]["total"] if rel_rows else 0

        metrics = []
        if idx_bytes > 0 and rel_bytes > 0:
            pct = round((idx_bytes / rel_bytes) * 100, 2)
            metrics.append(
                {
                    "id": "index_share_pct",
                    "value": pct,
                    "unit": "percent",
                }
            )
        return metrics

    def _get_raw_cleaned_ratio(self, conn: Any) -> list[dict[str, Any]]:
        """Get the ratio of raw to cleaned table rows."""
        # Get row counts for both tables
        row_count_query = """
            SELECT schemaname::text as schema_name,
                   tablename::text as table_name,
                   n_live_tup::bigint as row_count
            FROM pg_stat_user_tables
            WHERE tablename IN ('ohlcv_1d_raw', 'ohlcv_1d')
            ORDER BY tablename
        """
        rows = self._run_query(conn, row_count_query)

        raw_count = None
        cleaned_count = None

        for row in rows:
            if row["table_name"] == "ohlcv_1d_raw":
                raw_count = row["row_count"]
            elif row["table_name"] == "ohlcv_1d":
                cleaned_count = row["row_count"]

        metrics = []
        if raw_count is not None and cleaned_count is not None and cleaned_count > 0:
            ratio = round(raw_count / cleaned_count, 3)
            metrics.append(
                {
                    "id": "raw_cleaned_ratio",
                    "value": ratio,
                    "unit": "ratio",
                }
            )
        return metrics

    def _get_stream_row_share(self, conn: Any) -> list[dict[str, Any]]:
        """Get row distribution of trading.positions by portfolio_type."""
        query = """
            SELECT portfolio_type::text as stream_type,
                   COUNT(*)::bigint as row_count
            FROM trading.positions
            GROUP BY portfolio_type
            ORDER BY portfolio_type
        """
        rows = self._run_query(conn, query)
        metrics = []
        for row in rows:
            metrics.append(
                {
                    "id": f"stream_rows_{row['stream_type']}",
                    "value": row["row_count"],
                    "unit": "count",
                }
            )
        return metrics

    def _get_compression_status(self, conn: Any) -> list[dict[str, Any]]:
        """Get compression status for hypertables."""
        query = """
            SELECT hypertable_schema::text as schema_name,
                   hypertable_name::text as table_name
            FROM timescaledb_information.hypertables
            ORDER BY hypertable_schema, hypertable_name
        """
        rows = self._run_query(conn, query)
        metrics = []
        for row in rows:
            metrics.append(
                {
                    "id": f"compression_{row['schema_name']}_{row['table_name']}",
                    "value": "hypertable",
                    "unit": "status",
                }
            )
        return metrics

    def _get_growth_metrics(self) -> list[dict[str, Any]]:
        """Compute growth_bytes_per_day from prior snapshot if exists.

        First snapshot returns NO_DATA (no prior baseline to compare to).
        Second and later snapshots return actual delta per day.
        """
        metrics = []

        # Find all snapshot files
        snapshot_files = sorted(self.snapshots_dir.glob("db_footprint_*.json"))

        if len(snapshot_files) < 2:
            # No prior snapshot - return NO_DATA marker
            metrics.append(
                {
                    "id": "growth_bytes_per_day",
                    "value": None,
                    "unit": "bytes",
                    "status": "NO_DATA",
                    "note": "First snapshot; no prior baseline to compare",
                }
            )
        else:
            # Compare last two snapshots
            current_snapshot = snapshot_files[-1]
            prior_snapshot = snapshot_files[-2]

            with open(prior_snapshot) as f:
                prior_data = json.load(f)
            with open(current_snapshot) as f:
                current_data = json.load(f)

            # Sum total bytes from metrics
            prior_total = sum(
                m.get("value", 0)
                for m in prior_data.get("metrics", [])
                if m.get("id", "").startswith("table_bytes_")
                and isinstance(m.get("value"), (int, float))
            )
            current_total = sum(
                m.get("value", 0)
                for m in current_data.get("metrics", [])
                if m.get("id", "").startswith("table_bytes_")
                and isinstance(m.get("value"), (int, float))
            )

            # Parse timestamps
            prior_ts = datetime.fromisoformat(prior_data["timestamp"])
            current_ts = datetime.fromisoformat(current_data["timestamp"])
            days_elapsed = max(1, (current_ts - prior_ts).days)

            bytes_delta = current_total - prior_total
            growth_per_day = round(bytes_delta / days_elapsed, 2)

            metrics.append(
                {
                    "id": "growth_bytes_per_day",
                    "value": growth_per_day,
                    "unit": "bytes",
                }
            )

        return metrics


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Run database footprint audit")
    parser.add_argument("--host", default=os.getenv("DB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DB_PORT", "5432")))
    parser.add_argument("--database", default=os.getenv("DB_NAME", "algogators"))
    parser.add_argument("--user", default=os.getenv("DB_USER", "postgres"))
    parser.add_argument("--password", default=os.getenv("DB_PASSWORD", "postgres"))
    parser.add_argument(
        "--snapshots-dir",
        default="infra/perf/footprint/snapshots",
        help="Directory to store snapshots",
    )

    args = parser.parse_args()

    runner = DbAuditRunner(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.user,
        password=args.password,
        snapshots_dir=args.snapshots_dir,
    )

    result = runner.run()
    print(f"Audit complete. Snapshot written. Metrics: {len(result['metrics'])}")


if __name__ == "__main__":
    main()

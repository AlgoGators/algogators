"""Writes the per-dataset, per-bar-date readiness row consumers wait on.

The table, ops.data_ready in new_algo_data, is owned and created by a
trade-ngin migration, not by data-ngin; this repository only upserts into it.
The role data-ngin connects as (DB_USER) needs INSERT and UPDATE on it.

    ops.data_ready(
        dataset     text,
        bar_date    date,
        expected    int,
        loaded      int,
        missing     text[],
        status      text,         -- 'complete' | 'partial' | 'failed'
        run_id      text,
        finished_at timestamptz,
        PRIMARY KEY (dataset, bar_date)
    )
"""

from typing import Any

from data_ngin.infrastructure.repository.ohlcv_repository import OhlcvRepository

READINESS_TABLE = "ops.data_ready"


class ReadinessRepository(OhlcvRepository):
    """Shares OhlcvRepository's per-database connection pool."""

    def upsert_readiness(self, row: dict[str, Any]) -> None:
        """
        Insert or replace the row for (row["dataset"], row["bar_date"]). A
        re-run for the same bar date overwrites the earlier verdict.
        """
        self._ensure_connected()
        query = f"""
            INSERT INTO {READINESS_TABLE}
                (dataset, bar_date, expected, loaded, missing, status, run_id, finished_at)
            VALUES (%(dataset)s, %(bar_date)s, %(expected)s, %(loaded)s, %(missing)s,
                    %(status)s, %(run_id)s, %(finished_at)s)
            ON CONFLICT (dataset, bar_date) DO UPDATE SET
                expected = EXCLUDED.expected,
                loaded = EXCLUDED.loaded,
                missing = EXCLUDED.missing,
                status = EXCLUDED.status,
                run_id = EXCLUDED.run_id,
                finished_at = EXCLUDED.finished_at
        """
        with self.connection.cursor() as cur:
            cur.execute(query, row)
        self.logger.info(
            "Readiness %s %s: %s (%s/%s loaded)",
            row["dataset"],
            row["bar_date"],
            row["status"],
            row["loaded"],
            row["expected"],
        )

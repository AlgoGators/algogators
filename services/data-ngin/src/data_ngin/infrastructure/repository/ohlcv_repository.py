import contextlib
import logging
import os
import threading
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, ClassVar

import psycopg2
import psycopg2.extras
from platform_db import DatabaseConfig
from psycopg2 import pool as psycopg2_pool

from data_ngin.infrastructure.inserter import Inserter

# Unique keys of the OHLCV tables: (time, symbol) on the cleaned tables,
# (ts_event, symbol) on the raw ones, and (instrument_id, date) on contract_ohlcv_1d.
CONFLICT_KEYS = (("time", "symbol"), ("ts_event", "symbol"), ("instrument_id", "date"))


def upsert_clause(columns: list[str]) -> str:
    """ON CONFLICT ... DO UPDATE for rows with these columns, keyed on whichever
    OHLCV unique key the columns contain."""
    key = next((k for k in CONFLICT_KEYS if all(c in columns for c in k)), None)
    if key is None:
        raise ValueError(f"No known unique key among columns {columns}")
    updates = [c for c in columns if c not in key]
    if not updates:
        return f"ON CONFLICT ({', '.join(key)}) DO NOTHING"
    assignments = ", ".join(f"{c} = EXCLUDED.{c}" for c in updates)
    return f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {assignments}"


class OhlcvRepository(Inserter):
    """
    Consolidated repository for the OHLCV tables: reads (previously
    `DataAccess`, SQLAlchemy ORM) and writes (previously `TimescaleDBInserter`,
    raw psycopg2 with `ON CONFLICT DO NOTHING` upsert) reimplemented against a
    pooled psycopg2 connection.

    Consolidated *toward* the psycopg2/upsert implementation deliberately --
    it's the one with correct semantics (idempotent multi-table writes for
    raw vs. cleaned data); the ORM side never had upsert handling and was
    bound to a single table, so merging the other way would have regressed
    write idempotency.

    Connections are pooled (class-level `psycopg2.pool.ThreadedConnectionPool`,
    shared by every `OhlcvRepository` instance in the process): the
    orchestrator builds a fresh repository per symbol and the API server
    builds one per GraphQL request, and both used to pay a full TCP+auth
    handshake plus 3 diagnostic queries on every single `connect()`.
    connect()/close() now borrow/return a connection from the pool instead of
    opening/closing a real one each time; the diagnostic queries only run
    once per process (see `_diagnostics_logged`), not once per connect().
    """

    # One pool per database: a config's `database.db_name` picks the database
    # (algo_data for the futures pipeline, new_algo_data for the equities and
    # new-futures pipelines), so a single process can talk to more than one.
    _pools: ClassVar[dict[str | None, "psycopg2_pool.ThreadedConnectionPool"]] = {}
    _pool_lock = threading.Lock()
    _diagnostics_logged: bool = False
    _verified_tables: ClassVar[set[tuple[str | None, str, str]]] = set()

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config=config)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.INFO)

    @classmethod
    def _get_pool(cls, db_name: str | None = None) -> "psycopg2_pool.ThreadedConnectionPool":
        """
        Returns the shared pool for `db_name`, building it on first use.
        `db_name=None` means the database named by the DB_NAME env var.
        """
        pool = cls._pools.get(db_name)
        if pool is None:
            with cls._pool_lock:
                pool = cls._pools.get(db_name)
                if pool is None:
                    # DB_* credentials go through the shared DatabaseConfig so
                    # they are validated (missing/blank vars raise instead of
                    # silently building a pool with None values); the pool
                    # sizing knobs stay a local concern.
                    env = dict(os.environ)
                    if db_name:
                        env["DB_NAME"] = db_name
                    pool = psycopg2_pool.ThreadedConnectionPool(
                        int(os.getenv("DB_POOL_MIN_CONN", "1")),
                        int(os.getenv("DB_POOL_MAX_CONN", "10")),
                        **DatabaseConfig.from_env(env).connect_kwargs(),
                    )
                    cls._pools[db_name] = pool
        return pool

    def _db_name(self) -> str | None:
        return (self.config.get("database") or {}).get("db_name") or None

    @classmethod
    def reset_pool_for_testing(cls) -> None:
        """
        Test-only: drop the cached pool and cached diagnostic/verification
        state. The pool and its caches are process-lifetime singletons by
        design (that's the point -- one real pool, not one per instance);
        tests that patch psycopg2.connect need a clean slate per test rather
        than reusing whatever pool an earlier test's mock built.
        """
        for pool in cls._pools.values():
            with contextlib.suppress(Exception):
                pool.closeall()
        cls._pools = {}
        cls._diagnostics_logged = False
        cls._verified_tables = set()

    def connect(self) -> None:
        """
        Borrows a connection from the shared pool (creating the pool on first
        use). Diagnostic queries (server endpoint, search_path, schema list)
        only run once per process, the first time any repository connects --
        not on every connect() call, since with pooling that could be once
        per symbol/request instead of once ever.

        Raises:
            ConnectionError: If a connection cannot be acquired.
        """
        try:
            self.connection = self._get_pool(self._db_name()).getconn()
            self.connection.autocommit = True

            if not OhlcvRepository._diagnostics_logged:
                with self.connection.cursor() as cur:
                    cur.execute(
                        "SELECT inet_server_addr(), inet_server_port(), current_database(), current_user"
                    )
                    host, port, dbname, dbuser = cur.fetchone()
                    self.logger.info(
                        "DB endpoint -> host=%s port=%s db=%s user=%s", host, port, dbname, dbuser
                    )

                    cur.execute("SHOW search_path")
                    sp = cur.fetchone()[0]
                    self.logger.info("search_path=%s", sp)

                    cur.execute("""
                        SELECT schema_name FROM information_schema.schemata ORDER BY schema_name
                    """)
                    schemas = [r[0] for r in cur.fetchall()]
                    self.logger.info("Schemas present: %s", ", ".join(schemas))
                OhlcvRepository._diagnostics_logged = True

            self.logger.info("Connected to TimescaleDB successfully (pooled).")
        except psycopg2.OperationalError as e:
            self.connection = None
            raise ConnectionError(f"Failed to connect to TimescaleDB: {e}") from e
        except psycopg2_pool.PoolError as e:
            self.connection = None
            raise ConnectionError(f"Failed to acquire a pooled connection: {e}") from e

    def _ensure_connected(self) -> None:
        if not self.connection:
            self.connect()

    def insert_data(
        self,
        data: list[dict[str, Any]],
        schema: str,
        table: str,
        update_on_conflict: bool = False,
    ) -> None:
        """
        Inserts data into the specified TimescaleDB schema and table dynamically.

        Args:
            data (List[Dict[str, Any]]): A list of dictionaries representing data rows.
            schema (str): The target schema in TimescaleDB.
            table (str): The target table in TimescaleDB.
            update_on_conflict (bool): False (default) keeps an existing row
                with the same key (ON CONFLICT DO NOTHING). True overwrites its
                other columns with the new values (ON CONFLICT DO UPDATE), on
                the table's (time, symbol) or (ts_event, symbol) unique key.

        Raises:
            RuntimeError: If the connection is missing or the insertion into the database fails.
        """
        if not data:
            # Routine, not an error: a symbol can legitimately have no rows
            # for a given window (holiday-only range, newly-listed/delisted
            # contract, empty batch). No DB work needed either -- skip before
            # the connection check so this no-op doesn't require a connection.
            self.logger.info(f"No rows to insert into {schema}.{table}; skipping.")
            return
        if not self.connection:
            raise RuntimeError("Database connection is not established.")

        # Schema/table existence only needs checking once per (schema, table)
        # for the process's lifetime, not on every insert -- these don't
        # change between calls, and the orchestrator calls insert_data twice
        # per symbol (raw + cleaned tables) for every symbol in a run.
        table_key = (self._db_name(), schema, table)
        if table_key not in OhlcvRepository._verified_tables:
            schema_exists_sql = """
            SELECT 1 FROM information_schema.schemata WHERE schema_name = %s
            """
            table_exists_sql = """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            """
            with self.connection.cursor() as cur:
                cur.execute(schema_exists_sql, (schema,))
                if cur.fetchone() is None:
                    raise RuntimeError(
                        f"Target schema '{schema}' not found. You are likely on the wrong DB/host/port. "
                        "Check DB_HOST/DB_PORT in docker-compose for data-engine."
                    )
                cur.execute(table_exists_sql, (schema, table))
                if cur.fetchone() is None:
                    cur.execute("""
                        SELECT table_schema||'.'||table_name
                        FROM information_schema.tables
                        WHERE table_schema NOT IN ('pg_catalog','information_schema')
                        ORDER BY 1
                    """)
                    existing = [r[0] for r in cur.fetchall()]
                    raise RuntimeError(
                        f"Target table '{schema}.{table}' not found. Existing tables: {existing[:50]}"
                    )
            OhlcvRepository._verified_tables.add(table_key)

        columns = list(data[0].keys())
        column_names = ", ".join(columns)
        placeholders = ", ".join([f"%({col})s" for col in columns])
        conflict_clause = "ON CONFLICT DO NOTHING"
        if update_on_conflict:
            conflict_clause = upsert_clause(columns)
        query = f"""
        INSERT INTO {schema}.{table} ({column_names})
        VALUES ({placeholders})
        {conflict_clause};
        """.strip()

        try:
            with self.connection.cursor() as cursor:
                cursor.executemany(query, data)
            self.logger.info(f"Inserted {len(data)} rows into {schema}.{table}")
        except psycopg2.Error as e:
            self.connection.rollback()
            self.logger.error(
                "PG error code=%s detail=%s",
                getattr(e, "pgcode", None),
                getattr(getattr(e, "diag", None), "message_detail", None),
            )
            raise RuntimeError(f"Failed to insert into {schema}.{table}: {e}") from e
        except Exception as e:
            self.connection.rollback()
            raise RuntimeError(f"Failed to insert data into {schema}.{table}: {e}") from e

    def insert_contract_bars(
        self,
        records: list[dict[str, Any]],
        chunk_size: int = 5000,
        schema: str = "futures_data",
        table: str = "contract_ohlcv_1d",
    ) -> None:
        """
        Batch upserts per-contract OHLCV records using execute_values for high throughput.
        Matches primary key on (instrument_id, date) with ON CONFLICT DO UPDATE.
        """
        if not records:
            self.logger.info("No contract records to insert; skipping.")
            return

        self._ensure_connected()
        if not self.connection:
            raise RuntimeError("Database connection is not established.")

        columns = [
            "instrument_id",
            "date",
            "root",
            "raw_symbol",
            "expiration",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        cols_sql = ", ".join(f'"{c}"' if c == "date" else c for c in columns)
        update_cols = [c for c in columns if c not in ("instrument_id", "date")]
        updates_sql = ", ".join(
            f'"{c}" = EXCLUDED."{c}"' if c == "date" else f"{c} = EXCLUDED.{c}" for c in update_cols
        )

        insert_sql = f"""
        INSERT INTO {schema}.{table} ({cols_sql})
        VALUES %s
        ON CONFLICT (instrument_id, "date") DO UPDATE SET
            {updates_sql},
            loaded_at = now()
        """

        tuple_values = [tuple(r.get(col) for col in columns) for r in records]

        try:
            with self.connection.cursor() as cur:
                for i in range(0, len(tuple_values), chunk_size):
                    chunk = tuple_values[i : i + chunk_size]
                    psycopg2.extras.execute_values(
                        cur,
                        insert_sql,
                        chunk,
                        template=None,
                        page_size=chunk_size,
                    )
            self.logger.info(f"Successfully upserted {len(records)} records into {schema}.{table}")
        except Exception as e:
            self.connection.rollback()
            self.logger.error("Failed to batch insert contract bars: %s", e)
            raise RuntimeError(f"Failed to batch insert contract bars: {e}") from e

    def get_contract_bar(
        self,
        instrument_id: str,
        bar_date: Any,
        schema: str = "futures_data",
        table: str = "contract_ohlcv_1d",
    ) -> dict[str, Any] | None:
        """
        Fetch a single contract bar by (instrument_id, date).
        """
        self._ensure_connected()
        query = f"""
        SELECT instrument_id, "date", root, raw_symbol, expiration, open, high, low, close, volume, loaded_at
        FROM {schema}.{table}
        WHERE instrument_id = %s AND "date" = %s
        """
        with self.connection.cursor() as cur:
            cur.execute(query, (str(instrument_id), bar_date))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "instrument_id": row[0],
                "date": row[1],
                "root": row[2],
                "raw_symbol": row[3],
                "expiration": row[4],
                "open": row[5],
                "high": row[6],
                "low": row[7],
                "close": row[8],
                "volume": row[9],
                "loaded_at": row[10],
            }

    def close(self) -> None:
        """
        Returns the connection to the shared pool (rather than closing the
        underlying TCP connection) so the next connect() -- by this instance
        or another -- can reuse it.
        """
        if self.connection:
            try:
                self._get_pool(self._db_name()).putconn(self.connection)
                self.logger.info("Connection returned to pool.")
            except Exception:
                # Pool may be unusable (e.g. already closed) -- fall back to
                # closing the connection directly so it isn't leaked.
                with contextlib.suppress(Exception):
                    self.connection.close()
            self.connection = None

    def _default_schema_table(self, schema: str | None, table: str | None) -> tuple:
        database_config = self.config.get("database", {})
        return (
            schema or database_config.get("target_schema"),
            table or database_config.get("table"),
        )

    def get_ohlcv_data(
        self,
        start_date: str,
        end_date: str,
        symbols: list[str] | None = None,
        schema: str | None = None,
        table: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieves OHLCV rows for the given date range and (optional) symbols,
        ordered by symbol then time -- same contract as the old
        DataAccess.get_ohlcv_data.
        """
        self._ensure_connected()
        schema, table = self._default_schema_table(schema, table)

        query = f"""
            SELECT time, symbol, open, high, low, close, volume
            FROM {schema}.{table}
            WHERE time BETWEEN %s AND %s
        """
        params: list[Any] = [start_date, end_date]
        if symbols:
            query += " AND symbol = ANY(%s)"
            params.append(symbols)
        query += " ORDER BY symbol, time"

        with self.connection.cursor() as cur:
            cur.execute(query, params)
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]

    def get_symbols(self, schema: str | None = None, table: str | None = None) -> list[str]:
        """Retrieves all unique symbols in the target table."""
        self._ensure_connected()
        schema, table = self._default_schema_table(schema, table)

        with self.connection.cursor() as cur:
            cur.execute(f"SELECT DISTINCT symbol FROM {schema}.{table}")
            return [row[0] for row in cur.fetchall()]

    def get_earliest_date(self, schema: str | None = None, table: str | None = None) -> str | None:
        """Retrieves the earliest date in the target table, or None if empty."""
        self._ensure_connected()
        schema, table = self._default_schema_table(schema, table)

        with self.connection.cursor() as cur:
            cur.execute(f"SELECT MIN(time) FROM {schema}.{table}")
            row = cur.fetchone()
            if row and row[0]:
                self.logger.info(f"Earliest available date in the database: {row[0]}")
                return row[0].strftime("%Y-%m-%d")
            return None

    def get_latest_date(self, schema: str | None = None, table: str | None = None) -> str | None:
        """Retrieves the latest date in the target table, or None if empty."""
        self._ensure_connected()
        schema, table = self._default_schema_table(schema, table)

        with self.connection.cursor() as cur:
            cur.execute(f"SELECT MAX(time) FROM {schema}.{table}")
            row = cur.fetchone()
            if row and row[0]:
                self.logger.info(f"Latest available date in the database: {row[0]}")
                return row[0].strftime("%Y-%m-%d")
            return None

    def get_bar_dates(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        schema: str | None = None,
        table: str | None = None,
    ) -> set[tuple[str, date]]:
        """
        The (symbol, UTC bar date) pairs present for `symbols` between
        `start_date` and `end_date`, both inclusive. Daily bars are stamped at
        00:00 UTC of their day, so the bar date is the UTC date of `time`.
        """
        if not symbols:
            return set()
        self._ensure_connected()
        schema, table = self._default_schema_table(schema, table)

        query = f"""
            SELECT symbol, (time AT TIME ZONE 'UTC')::date
            FROM {schema}.{table}
            WHERE time >= %s AND time < %s AND symbol = ANY(%s)
        """
        window_start = datetime.combine(start_date, time.min, tzinfo=UTC)
        window_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)
        with self.connection.cursor() as cur:
            cur.execute(query, (window_start, window_end, list(symbols)))
            return {(row[0], row[1]) for row in cur.fetchall()}

    def get_latest_data(
        self, symbol: str, schema: str | None = None, table: str | None = None
    ) -> dict[str, Any] | None:
        """Retrieves the most recent OHLCV row for a given symbol."""
        self._ensure_connected()
        schema, table = self._default_schema_table(schema, table)

        query = f"""
            SELECT time, symbol, open, high, low, close, volume
            FROM {schema}.{table}
            WHERE symbol = %s
            ORDER BY time DESC
            LIMIT 1
        """
        with self.connection.cursor() as cur:
            cur.execute(query, (symbol,))
            row = cur.fetchone()
            if not row:
                return None
            columns = [desc[0] for desc in cur.description]
            return dict(zip(columns, row, strict=False))

    def delete_data(
        self,
        start_date: str,
        end_date: str,
        symbols: list[str] | None = None,
        schema: str | None = None,
        table: str | None = None,
    ) -> None:
        """Deletes rows in the given date range and (optional) symbols."""
        self._ensure_connected()
        schema, table = self._default_schema_table(schema, table)

        query = f"DELETE FROM {schema}.{table} WHERE time BETWEEN %s AND %s"
        params: list[Any] = [start_date, end_date]
        if symbols:
            query += " AND symbol = ANY(%s)"
            params.append(symbols)

        with self.connection.cursor() as cur:
            cur.execute(query, params)
            self.logger.info(f"Deleted {cur.rowcount} records successfully.")

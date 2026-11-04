"""Postgres portfolio readers."""

import time
from typing import Any

import psycopg2

from research_api.application.portfolio.ports import (
    IncubationError,
    IncubationPerformanceRows,
    PortfolioDetailRows,
)
from research_api.domain.investor.allocation import parse_contract_size
from research_api.domain.portfolio.sleeves import run_on_or_before, sleeve_changes
from research_api.domain.portfolio.streams import (
    DEFAULT_BOOK,
    FALLBACK_BOOK,
    PORTFOLIO_STREAMS,
)
from research_api.infrastructure.db.postgres import get_db_connection

_PORTFOLIO_TYPE_CACHE_TTL_SECONDS = 300
_has_portfolio_type_cache = None
_has_portfolio_type_expires_at = 0.0


class PostgresPortfolioRepository:
    def __init__(self, connection_factory=None):
        self.connection_factory = connection_factory or get_db_connection

    def _fetch_summary_row(self, cursor, portfolio_id):
        """The book's newest results row, found by portfolio id (issue #59).

        Where results carry a book, the system book's row wins a date it shares with
        another book's, so a QT portfolio's list card shows the system numbers.
        """
        book_order = (
            ", (portfolio_type = 'system') DESC"
            if self._has_column(cursor, "live_results", "portfolio_type")
            else ""
        )
        cursor.execute(
            f"""
            SELECT current_portfolio_value, total_annualized_return,
                   volatility, total_cumulative_return
            FROM trading.live_results
            WHERE portfolio_id = %s
            ORDER BY date DESC{book_order}
            LIMIT 1
            """,
            (portfolio_id,),
        )
        return cursor.fetchone()

    def _has_portfolio_type(self, cursor):
        global _has_portfolio_type_cache, _has_portfolio_type_expires_at

        now = time.monotonic()
        if _has_portfolio_type_cache is not None and now < _has_portfolio_type_expires_at:
            return _has_portfolio_type_cache

        cursor.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'trading' AND table_name = 'equity_curve'
              AND column_name = 'portfolio_type'
            """
        )
        _has_portfolio_type_cache = cursor.fetchone() is not None
        _has_portfolio_type_expires_at = now + _PORTFOLIO_TYPE_CACHE_TTL_SECONDS
        return _has_portfolio_type_cache

    # --- reads keyed by portfolio id (AlgoLens#102, issue #59) -------------
    #
    # One portfolio is one book: every read finds a book by its portfolio id,
    # and a sleeve by the portfolio id and its strategy_name. The engine's
    # strategy id is a description of the book and is never matched on
    # (tests/test_no_strategy_id_filter.py). A book with several sleeves is
    # netted per symbol.

    def fetch_summary_row(self, portfolio_id):
        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                return self._fetch_summary_row(cursor, portfolio_id)
        finally:
            conn.close()

    def _contract_sizes(self, cursor, *row_lists):
        """D-32: contract size by symbol root for the symbols in these rows.

        metadata.contract_metadata."Contract Size" is TEXT ('1000', '0.1'); a root it does
        not list (equities) is left out and counts with size 1.
        """
        roots = sorted(
            {(row["symbol"] or "").split(".", 1)[0] for rows in row_lists for row in rows}
        )
        if not roots:
            return {}
        cursor.execute("SELECT to_regclass('metadata.contract_metadata') IS NOT NULL AS present")
        present = cursor.fetchone()
        if not present or not present["present"]:
            return {}
        cursor.execute(
            """
            SELECT DISTINCT ON (cm."Databento Symbol")
                   cm."Databento Symbol" AS root, cm."Contract Size" AS contract_size
            FROM metadata.contract_metadata cm
            WHERE cm."Databento Symbol" = ANY(%s)
            ORDER BY cm."Databento Symbol"
            """,
            (roots,),
        )
        sizes = {}
        for row in cursor.fetchall():
            size = parse_contract_size(row["contract_size"])
            if size:
                sizes[row["root"]] = size
        return sizes

    def _has_column(self, cursor, table, column):
        cursor.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'trading' AND table_name = %s AND column_name = %s
            """,
            (table, column),
        )
        return cursor.fetchone() is not None

    def _portfolio_book_has_positions(self, cursor, portfolio_id, book):
        cursor.execute(
            """
            SELECT 1 FROM trading.positions
            WHERE portfolio_id = %s AND portfolio_type = %s
            LIMIT 1
            """,
            (portfolio_id, book),
        )
        return cursor.fetchone() is not None

    def _portfolio_latest_results(self, cursor, portfolio_id, book, results_have_book):
        if results_have_book:
            # The served book's own row; a book whose results are not written
            # (qt_proposal never has any) shows the system row's numbers.
            cursor.execute(
                """
                SELECT * FROM trading.live_results
                WHERE portfolio_id = %s AND portfolio_type IN (%s, 'system')
                ORDER BY date DESC, (portfolio_type = %s) DESC
                LIMIT 1
                """,
                (portfolio_id, book, book),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM trading.live_results
                WHERE portfolio_id = %s
                ORDER BY date DESC
                LIMIT 1
                """,
                (portfolio_id,),
            )
        return cursor.fetchone()

    def _portfolio_equity_curve(self, cursor, portfolio_id, book, has_portfolio_type):
        if has_portfolio_type:
            cursor.execute(
                """
                SELECT timestamp, equity FROM trading.equity_curve
                WHERE portfolio_id = %s AND portfolio_type = %s
                ORDER BY timestamp ASC
                """,
                (portfolio_id, book),
            )
        else:
            cursor.execute(
                """
                SELECT timestamp, equity FROM trading.equity_curve
                WHERE portfolio_id = %s
                ORDER BY timestamp ASC
                """,
                (portfolio_id,),
            )
        return cursor.fetchall()

    def _portfolio_equity_by_stream(self, cursor, portfolio_id, has_portfolio_type):
        if not has_portfolio_type:
            return {}
        by_stream = {}
        for stream in PORTFOLIO_STREAMS:
            rows = self._portfolio_equity_curve(cursor, portfolio_id, stream, True)
            if rows:
                by_stream[stream] = rows
        return by_stream

    def _portfolio_positions(self, cursor, portfolio_id, book, has_portfolio_type):
        """The book's positions on its latest date, netted across sleeves.

        A symbol whose net quantity is zero (a flatten row) is left out.
        """
        book_filter = "AND portfolio_type = %s" if has_portfolio_type else ""
        params = (portfolio_id, book) if has_portfolio_type else (portfolio_id,)
        cursor.execute(
            f"""
            SELECT symbol,
                   SUM(quantity) AS quantity,
                   CASE WHEN SUM(quantity) <> 0
                        THEN SUM(quantity * average_price) / SUM(quantity)
                        ELSE MAX(average_price) END AS average_price,
                   SUM(daily_unrealized_pnl) AS daily_unrealized_pnl,
                   SUM(daily_realized_pnl) AS daily_realized_pnl
            FROM trading.positions
            WHERE portfolio_id = %s {book_filter}
            AND date = (
                SELECT max(date) FROM trading.positions
                WHERE portfolio_id = %s {book_filter}
            )
            GROUP BY symbol
            HAVING SUM(quantity) <> 0
            ORDER BY ABS(SUM(quantity * average_price)) DESC, symbol
            """,
            params * 2,
        )
        return cursor.fetchall()

    def _portfolio_yesterday_positions(self, cursor, portfolio_id, book, has_portfolio_type):
        """The book's positions on its previous date, netted across sleeves as today's are,
        so a symbol two sleeves hold compares net with net. A net-zero symbol is kept: it is
        what the comparison with today reads as closed."""
        book_filter = "AND portfolio_type = %s" if has_portfolio_type else ""
        params = (portfolio_id, book) if has_portfolio_type else (portfolio_id,)
        cursor.execute(
            f"""
            SELECT symbol,
                   SUM(quantity) AS quantity,
                   CASE WHEN SUM(quantity) <> 0
                        THEN SUM(quantity * average_price) / SUM(quantity)
                        ELSE MAX(average_price) END AS average_price,
                   SUM(daily_unrealized_pnl) AS daily_unrealized_pnl,
                   SUM(daily_realized_pnl) AS daily_realized_pnl,
                   MAX(updated_at) AS updated_at
            FROM trading.positions
            WHERE portfolio_id = %s {book_filter}
            AND date = (
                SELECT max(date) FROM trading.positions
                WHERE portfolio_id = %s {book_filter}
                AND date < (
                    SELECT max(date) FROM trading.positions
                    WHERE portfolio_id = %s {book_filter}
                )
            )
            GROUP BY symbol
            ORDER BY symbol
            """,
            params * 3,
        )
        return cursor.fetchall()

    def _portfolio_executions(self, cursor, portfolio_id, book, executions_have_book):
        book_filter = "AND portfolio_type = %s" if executions_have_book else ""
        params = (portfolio_id, book) if executions_have_book else (portfolio_id,)
        cursor.execute(
            f"""
            SELECT symbol, side, quantity, price, execution_time, commissions_fees,
                   strategy_name
            FROM trading.executions
            WHERE portfolio_id = %s {book_filter}
            ORDER BY execution_time DESC
            LIMIT 100
            """,
            params,
        )
        return cursor.fetchall()

    def _portfolio_sleeves(self, cursor, portfolio_id, book, has_portfolio_type, run=None):
        """D-20: the strategies (sleeves) of the book's latest date, with how many symbols each
        holds there.

        Issue #59: which sleeves ran that day is the run's strategy_allocations (`run`, the
        newest run metadata row on or before that date), so a sleeve that ran flat is listed
        with 0 positions and its allocation. A sleeve holding rows the run does not name (days
        before the run metadata was written) is still listed, with no allocation.
        """
        book_filter = "AND portfolio_type = %s" if has_portfolio_type else ""
        params = (portfolio_id, book) if has_portfolio_type else (portfolio_id,)
        cursor.execute(
            f"""
            SELECT strategy_name AS name, count(*) FILTER (WHERE quantity <> 0) AS positions
            FROM trading.positions
            WHERE portfolio_id = %s {book_filter}
            AND date = (
                SELECT max(date) FROM trading.positions
                WHERE portfolio_id = %s {book_filter}
            )
            GROUP BY strategy_name
            ORDER BY strategy_name
            """,
            params * 2,
        )
        held = {r["name"]: int(r["positions"]) for r in cursor.fetchall()}
        allocations = (run or {}).get("allocations") or {}
        names = sorted(set(held) | set(allocations), key=lambda name: (name is None, name or ""))
        sleeves = []
        for name in names:
            sleeve = {"name": name, "positions": held.get(name, 0)}
            if name in allocations:
                sleeve["allocation"] = allocations[name]
            sleeves.append(sleeve)
        return sleeves

    def _run_allocations(self, cursor, portfolio_id):
        """Issue #59: the sleeves each run of the book allocated to, one entry per date.

        trading.live_run_metadata holds a row per portfolio, run date and strategy id;
        `strategy_allocations` is a JSON object of sleeve name to allocation. A date with several
        rows (BASE's leftover strategy ids of late 2025) is read as the union of their sleeves,
        each with its allocation from the newest row naming it, so leftover rows never read as a
        sleeve switching off and on. Returns [{date, allocations}] oldest first, or [] where the
        table is absent.
        """
        cursor.execute("SELECT to_regclass('trading.live_run_metadata') IS NOT NULL AS present")
        present = cursor.fetchone()
        if not present or not present["present"]:
            return []
        cursor.execute(
            """
            SELECT date, strategy_allocations
            FROM trading.live_run_metadata
            WHERE portfolio_id = %s AND strategy_allocations IS NOT NULL
            ORDER BY date, created_at DESC NULLS LAST, id DESC
            """,
            (portfolio_id,),
        )
        by_date: dict[Any, dict[str, float | None]] = {}
        for row in cursor.fetchall():
            raw = row["strategy_allocations"]
            if not isinstance(raw, dict):
                continue
            allocations = by_date.setdefault(row["date"], {})
            for name, weight in raw.items():
                if str(name) in allocations:
                    continue  # a newer row of the same date already named it
                try:
                    allocations[str(name)] = float(weight)
                except (TypeError, ValueError):
                    allocations[str(name)] = None
        return [{"date": day, "allocations": allocations} for day, allocations in by_date.items()]

    def fetch_portfolio_rows(self, portfolio_id, book=DEFAULT_BOOK, allow_fallback=True):
        """Detail rows for one portfolio's book, found by portfolio id alone."""
        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                has_portfolio_type = self._has_portfolio_type(cursor)
                if (
                    not allow_fallback
                    and book != FALLBACK_BOOK
                    and has_portfolio_type
                    and not self._portfolio_book_has_positions(cursor, portfolio_id, book)
                ):
                    # D-37: a named book with no rows of its own is not served as another
                    # book's numbers under its name.
                    return PortfolioDetailRows(
                        latest=None,
                        equity_curve=[],
                        equity_by_stream={},
                        positions=[],
                        executions=[],
                        yesterday_positions=[],
                        book=book,
                        book_missing=True,
                    )
                if not allow_fallback or book == FALLBACK_BOOK:
                    served_book, fell_back = book, False
                elif not has_portfolio_type:
                    served_book, fell_back = FALLBACK_BOOK, True
                elif self._portfolio_book_has_positions(cursor, portfolio_id, book):
                    served_book, fell_back = book, False
                else:
                    served_book, fell_back = FALLBACK_BOOK, True

                results_have_book = self._has_column(cursor, "live_results", "portfolio_type")
                latest = self._portfolio_latest_results(
                    cursor, portfolio_id, served_book, results_have_book
                )
                if not latest:
                    return PortfolioDetailRows(
                        latest=None,
                        equity_curve=[],
                        equity_by_stream={},
                        positions=[],
                        executions=[],
                        yesterday_positions=[],
                        book=served_book,
                        fell_back=fell_back,
                    )

                if has_portfolio_type or served_book == FALLBACK_BOOK:
                    equity_curve = self._portfolio_equity_curve(
                        cursor, portfolio_id, served_book, has_portfolio_type
                    )
                    positions = self._portfolio_positions(
                        cursor, portfolio_id, served_book, has_portfolio_type
                    )
                    yesterday = self._portfolio_yesterday_positions(
                        cursor, portfolio_id, served_book, has_portfolio_type
                    )
                else:
                    # Unmigrated schema: no rows for a non-system book.
                    equity_curve, positions, yesterday = [], [], []
                equity_by_stream = self._portfolio_equity_by_stream(
                    cursor, portfolio_id, has_portfolio_type
                )
                executions_have_book = self._has_column(cursor, "executions", "portfolio_type")
                if executions_have_book or served_book == FALLBACK_BOOK:
                    executions = self._portfolio_executions(
                        cursor, portfolio_id, served_book, executions_have_book
                    )
                else:
                    executions = []
                contract_sizes = self._contract_sizes(cursor, positions, executions)
                runs = self._run_allocations(cursor, portfolio_id)
                run = run_on_or_before(runs, latest.get("date"))
                sleeves = (
                    self._portfolio_sleeves(
                        cursor, portfolio_id, served_book, has_portfolio_type, run
                    )
                    if has_portfolio_type or served_book == FALLBACK_BOOK
                    else []
                )
                changes = sleeve_changes(runs)
        finally:
            conn.close()

        return PortfolioDetailRows(
            latest=latest,
            equity_curve=equity_curve,
            equity_by_stream=equity_by_stream,
            positions=positions,
            executions=executions,
            yesterday_positions=yesterday,
            book=served_book,
            fell_back=fell_back,
            contract_sizes=contract_sizes,
            sleeves=sleeves,
            sleeve_changes=changes,
        )

    def list_incubating_strategies(self):
        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, strategy_type, portfolio_id, name, description,
                           mock_capital, incubation_started_at
                    FROM trading.strategy_registry
                    WHERE lifecycle = 'incubating'
                    ORDER BY incubation_started_at ASC NULLS LAST, sort_order ASC, id ASC
                    """
                )
                return cursor.fetchall()
        finally:
            conn.close()

    def _fetch_incubating_registry_row(self, cursor, strategy_id):
        cursor.execute(
            """
            SELECT portfolio_id, incubation_started_at
            FROM trading.strategy_registry
            WHERE id = %s AND lifecycle = 'incubating'
            """,
            (strategy_id,),
        )
        return cursor.fetchone()

    def fetch_incubation_performance(self, strategy_id):
        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                registry_row = self._fetch_incubating_registry_row(cursor, strategy_id)
                if registry_row is None or registry_row["incubation_started_at"] is None:
                    return IncubationPerformanceRows(positions=[], equity_curve=[])

                portfolio_id = registry_row["portfolio_id"]
                incubation_start = registry_row["incubation_started_at"]
                # Issue #59: the book by its portfolio id; with books, the system book only
                # (a QT portfolio's desk copies are not the incubating strategy's record).
                positions_book = (
                    "AND portfolio_type = 'system'"
                    if self._has_column(cursor, "positions", "portfolio_type")
                    else ""
                )
                curve_book = (
                    "AND portfolio_type = 'system'" if self._has_portfolio_type(cursor) else ""
                )

                cursor.execute(
                    f"""
                    SELECT updated_at AS date, symbol, quantity,
                           average_price AS entry_price
                    FROM trading.positions
                    WHERE portfolio_id = %s AND updated_at >= %s {positions_book}
                    ORDER BY updated_at ASC, symbol ASC
                    """,
                    (portfolio_id, incubation_start),
                )
                positions = cursor.fetchall()

                cursor.execute(
                    f"""
                    SELECT timestamp AS date, equity
                    FROM trading.equity_curve
                    WHERE portfolio_id = %s AND timestamp >= %s {curve_book}
                    ORDER BY timestamp ASC
                    """,
                    (portfolio_id, incubation_start),
                )
                equity_curve = cursor.fetchall()
        finally:
            conn.close()

        return IncubationPerformanceRows(
            positions=positions,
            equity_curve=equity_curve,
        )

    def _fetch_lifecycle(self, cursor, strategy_id):
        cursor.execute(
            "SELECT lifecycle FROM trading.strategy_registry WHERE id = %s",
            (strategy_id,),
        )
        return cursor.fetchone()

    def _insert_lifecycle_audit(
        self, cursor, strategy_id, before_state, after_state, reason, user_id
    ):
        cursor.execute(
            """
            INSERT INTO trading.strategy_lifecycle_log
                (strategy_id, before_state, after_state, reason, user_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (strategy_id, before_state, after_state, reason, user_id),
        )

    def start_incubation(self, strategy_id, mock_capital, reason, user_id):
        if mock_capital <= 0:
            raise IncubationError("mock_capital must be positive")
        if not reason or not reason.strip():
            raise IncubationError("reason must be non-empty")

        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                row = self._fetch_lifecycle(cursor, strategy_id)
                if row is None:
                    raise IncubationError(f"Strategy {strategy_id} not found")

                current_state = row["lifecycle"]
                if current_state == "incubating":
                    raise IncubationError(f"Strategy {strategy_id} is already incubating")

                cursor.execute(
                    """
                    UPDATE trading.strategy_registry
                    SET lifecycle = %s, mock_capital = %s,
                        incubation_started_at = now(), updated_at = now()
                    WHERE id = %s
                    """,
                    ("incubating", mock_capital, strategy_id),
                )
                self._insert_lifecycle_audit(
                    cursor,
                    strategy_id,
                    current_state,
                    "incubating",
                    reason,
                    user_id,
                )
            conn.commit()
        except psycopg2.Error as exc:
            conn.rollback()
            raise IncubationError(f"Database error: {exc}") from exc
        finally:
            conn.close()

    def promote_to_live(self, strategy_id, reason, user_id):
        if not reason or not reason.strip():
            raise IncubationError("reason must be non-empty")

        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                row = self._fetch_lifecycle(cursor, strategy_id)
                if row is None:
                    raise IncubationError(f"Strategy {strategy_id} not found")

                current_state = row["lifecycle"]
                if current_state != "incubating":
                    raise IncubationError(f"Strategy {strategy_id} is not currently incubating")

                cursor.execute(
                    """
                    UPDATE trading.strategy_registry
                    SET lifecycle = %s, mock_capital = NULL,
                        incubation_started_at = NULL, updated_at = now()
                    WHERE id = %s
                    """,
                    ("live", strategy_id),
                )
                self._insert_lifecycle_audit(
                    cursor,
                    strategy_id,
                    "incubating",
                    "live",
                    reason,
                    user_id,
                )
            conn.commit()
        except psycopg2.Error as exc:
            conn.rollback()
            raise IncubationError(f"Database error: {exc}") from exc
        finally:
            conn.close()

    def retire_strategy(self, strategy_id, reason, user_id):
        if not reason or not reason.strip():
            raise IncubationError("reason must be non-empty")

        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                row = self._fetch_lifecycle(cursor, strategy_id)
                if row is None:
                    raise IncubationError(f"Strategy {strategy_id} not found")

                current_state = row["lifecycle"]
                cursor.execute(
                    """
                    UPDATE trading.strategy_registry
                    SET lifecycle = %s, mock_capital = NULL,
                        incubation_started_at = NULL, updated_at = now()
                    WHERE id = %s
                    """,
                    ("retired", strategy_id),
                )
                self._insert_lifecycle_audit(
                    cursor,
                    strategy_id,
                    current_state,
                    "retired",
                    reason,
                    user_id,
                )
            conn.commit()
        except psycopg2.Error as exc:
            conn.rollback()
            raise IncubationError(f"Database error: {exc}") from exc
        finally:
            conn.close()

"""Unit tests for PostgresPortfolioRepository (no database).

The repository is driven end to end through a scripted cursor double that
returns canned rows keyed on the SQL text, plus a MagicMock connection for the
``get_db_connection`` default-factory boundary. psycopg2 is imported only for
its Error type; no real connection is ever opened.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NoReturn
from unittest.mock import MagicMock

import psycopg2
import pytest
from research_api.application.portfolio.ports import (
    IncubationError,
    IncubationPerformanceRows,
    PortfolioDetailRows,
)
from research_api.infrastructure.portfolio import repositories
from research_api.infrastructure.portfolio.repositories import PostgresPortfolioRepository

Handler = Callable[[str, "tuple[Any, ...] | None"], Any]


class ScriptedCursor:
    """Cursor double: dispatches canned results (or exceptions) keyed on SQL text."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self._result: Any = None

    def __enter__(self) -> "ScriptedCursor":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.calls.append((sql, params))
        self._result = self.handler(sql, params)

    def fetchone(self) -> Any:
        return self._result

    def fetchall(self) -> Any:
        return self._result

    def calls_matching(self, fragment: str) -> list[tuple[str, tuple[Any, ...] | None]]:
        return [call for call in self.calls if fragment in call[0]]


class FakeConnection:
    def __init__(self, cursor: ScriptedCursor) -> None:
        self._cursor = cursor
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> ScriptedCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def make_repo(cursor: ScriptedCursor) -> tuple[PostgresPortfolioRepository, FakeConnection]:
    conn = FakeConnection(cursor)
    repo = PostgresPortfolioRepository(connection_factory=lambda: conn)
    return repo, conn


def _unusable_factory() -> NoReturn:
    raise AssertionError("connection factory must not be called")


@pytest.fixture(autouse=True)
def _reset_portfolio_type_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The has-portfolio-type probe is cached at module level; isolate each test."""
    monkeypatch.setattr(repositories, "_has_portfolio_type_cache", None)
    monkeypatch.setattr(repositories, "_has_portfolio_type_expires_at", 0.0)


# ---------------------------------------------------------------------------
# fetch_summary_row
# ---------------------------------------------------------------------------


def test_fetch_summary_row_returns_latest_row_and_closes_connection() -> None:
    row = {
        "current_portfolio_value": 1_250_000.0,
        "total_annualized_return": 0.12,
        "volatility": 0.18,
        "total_cumulative_return": 0.34,
    }
    cursor = ScriptedCursor(lambda sql, params: row)
    repo, conn = make_repo(cursor)

    result = repo.fetch_summary_row("LIVE_TREND_FOLLOWING", "MAIN")

    assert result == row
    assert conn.closed
    (sql, params), *rest = cursor.calls
    assert not rest
    assert "FROM trading.live_results" in sql
    assert params == ("LIVE_TREND_FOLLOWING", "MAIN")


def test_fetch_summary_row_returns_none_when_no_rows() -> None:
    cursor = ScriptedCursor(lambda sql, params: None)
    repo, conn = make_repo(cursor)

    assert repo.fetch_summary_row("LIVE_TREND_FOLLOWING", "MAIN") is None
    assert conn.closed


def test_fetch_summary_row_closes_connection_when_query_raises() -> None:
    def boom(sql: str, params: tuple[Any, ...] | None) -> Any:
        raise RuntimeError("db down")

    cursor = ScriptedCursor(boom)
    repo, conn = make_repo(cursor)

    with pytest.raises(RuntimeError, match="db down"):
        repo.fetch_summary_row("LIVE_TREND_FOLLOWING", "MAIN")
    assert conn.closed


def test_default_connection_factory_is_get_db_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = MagicMock()
    mock_cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"current_portfolio_value": 42.0}
    monkeypatch.setattr(repositories, "get_db_connection", lambda: conn)

    repo = PostgresPortfolioRepository()
    result = repo.fetch_summary_row("STRAT", "PORT")

    assert result == {"current_portfolio_value": 42.0}
    conn.close.assert_called_once()
    assert mock_cursor.execute.call_args is not None
    assert mock_cursor.execute.call_args.args[1] == ("STRAT", "PORT")


# ---------------------------------------------------------------------------
# fetch_detail_rows
# ---------------------------------------------------------------------------


def detail_handler(
    *,
    latest: dict[str, Any] | None,
    portfolio_type_column: bool,
    equity: dict[Any, list[dict[str, Any]]],
    positions: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    yesterday: list[dict[str, Any]],
) -> Handler:
    """Canned-row dispatch for every query fetch_detail_rows can issue.

    ``equity`` is keyed by portfolio-type param (stream name, or None for the
    untyped equity query).
    """

    def handle(sql: str, params: tuple[Any, ...] | None) -> Any:
        if "FROM trading.live_results" in sql:
            return latest
        if "information_schema.columns" in sql:
            return {"?column?": 1} if portfolio_type_column else None
        if "FROM trading.equity_curve" in sql:
            assert params is not None
            stream = params[2] if len(params) == 3 else None
            return equity.get(stream, [])
        if "CURRENT_DATE" in sql:
            return yesterday
        if "latest_positions" in sql:
            return positions
        if "FROM trading.executions" in sql:
            return executions
        raise AssertionError(f"Unexpected SQL: {sql}")

    return handle


def test_fetch_detail_rows_short_circuits_when_no_live_results() -> None:
    cursor = ScriptedCursor(
        detail_handler(
            latest=None,
            portfolio_type_column=True,
            equity={},
            positions=[],
            executions=[],
            yesterday=[],
        )
    )
    repo, conn = make_repo(cursor)

    result = repo.fetch_detail_rows("LIVE_TREND_FOLLOWING", "MAIN")

    assert result == PortfolioDetailRows(
        latest=None,
        equity_curve=[],
        equity_by_stream={},
        positions=[],
        executions=[],
        yesterday_positions=[],
    )
    # Only the live_results probe ran; nothing else was queried.
    assert len(cursor.calls) == 1
    assert conn.closed


def test_fetch_detail_rows_assembles_all_sections_with_stream_scoping() -> None:
    latest = {"date": "2026-08-13", "current_portfolio_value": 1_000_000.0}
    qt_rows = [{"timestamp": "2026-08-12", "equity": 990_000.0}]
    system_rows = [{"timestamp": "2026-08-12", "equity": 995_000.0}]
    positions = [{"symbol": "ES", "quantity": 2, "average_price": 5000.0}]
    executions = [{"symbol": "ES", "side": "BUY", "quantity": 2, "price": 5000.0}]
    yesterday = [{"symbol": "NQ", "quantity": 1, "average_price": 18_000.0}]
    cursor = ScriptedCursor(
        detail_handler(
            latest=latest,
            portfolio_type_column=True,
            equity={"qt": qt_rows, "system": system_rows, "benchmark": []},
            positions=positions,
            executions=executions,
            yesterday=yesterday,
        )
    )
    repo, conn = make_repo(cursor)

    result = repo.fetch_detail_rows("LIVE_TREND_FOLLOWING", "MAIN")

    assert result.latest == latest
    assert result.equity_curve == qt_rows  # headline curve is the primary "qt" stream
    # Streams with no rows (benchmark) are omitted from the per-stream map.
    assert result.equity_by_stream == {"qt": qt_rows, "system": system_rows}
    assert result.positions == positions
    assert result.executions == executions
    assert result.yesterday_positions == yesterday
    assert conn.closed

    # The column probe ran exactly once for the whole call (cached in-call).
    assert len(cursor.calls_matching("information_schema.columns")) == 1
    equity_params = [params for _, params in cursor.calls_matching("FROM trading.equity_curve")]
    assert equity_params == [
        ("LIVE_TREND_FOLLOWING", "MAIN", "qt"),  # primary curve
        ("LIVE_TREND_FOLLOWING", "MAIN", "qt"),  # per-stream pass
        ("LIVE_TREND_FOLLOWING", "MAIN", "system"),
        ("LIVE_TREND_FOLLOWING", "MAIN", "benchmark"),
    ]


def test_fetch_detail_rows_without_portfolio_type_column_uses_unscoped_curve() -> None:
    latest = {"date": "2026-08-13", "current_portfolio_value": 500_000.0}
    legacy_rows = [{"timestamp": "2026-08-12", "equity": 499_000.0}]
    cursor = ScriptedCursor(
        detail_handler(
            latest=latest,
            portfolio_type_column=False,
            equity={None: legacy_rows},
            positions=[],
            executions=[],
            yesterday=[],
        )
    )
    repo, _conn = make_repo(cursor)

    result = repo.fetch_detail_rows("LIVE_TREND_FOLLOWING", "MAIN")

    assert result.equity_curve == legacy_rows
    assert result.equity_by_stream == {}
    equity_calls = cursor.calls_matching("FROM trading.equity_curve")
    assert len(equity_calls) == 1  # no per-stream queries on legacy schemas
    assert equity_calls[0][1] == ("LIVE_TREND_FOLLOWING", "MAIN")
    assert "portfolio_type" not in equity_calls[0][0]


# ---------------------------------------------------------------------------
# _has_portfolio_type caching
# ---------------------------------------------------------------------------


def test_has_portfolio_type_caches_result_within_ttl() -> None:
    cursor = ScriptedCursor(lambda sql, params: {"?column?": 1})
    repo = PostgresPortfolioRepository(connection_factory=_unusable_factory)

    assert repo._has_portfolio_type(cursor) is True
    assert repo._has_portfolio_type(cursor) is True
    assert len(cursor.calls) == 1


def test_has_portfolio_type_negative_result_is_cached_and_requeried_after_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = ScriptedCursor(lambda sql, params: None)
    repo = PostgresPortfolioRepository(connection_factory=_unusable_factory)

    assert repo._has_portfolio_type(cursor) is False
    assert repo._has_portfolio_type(cursor) is False
    assert len(cursor.calls) == 1

    monkeypatch.setattr(repositories, "_has_portfolio_type_expires_at", 0.0)
    assert repo._has_portfolio_type(cursor) is False
    assert len(cursor.calls) == 2


def test_stream_helpers_probe_for_column_when_not_told() -> None:
    """The equity helpers resolve has_portfolio_type themselves when not passed in."""
    qt_rows = [{"timestamp": "2026-08-12", "equity": 1.0}]
    cursor = ScriptedCursor(
        detail_handler(
            latest=None,
            portfolio_type_column=True,
            equity={"qt": qt_rows, "system": [], "benchmark": []},
            positions=[],
            executions=[],
            yesterday=[],
        )
    )
    repo = PostgresPortfolioRepository(connection_factory=_unusable_factory)

    assert repo._fetch_equity_curve(cursor, "ST", "PF", "qt") == qt_rows
    assert len(cursor.calls_matching("information_schema.columns")) == 1

    assert repo._fetch_equity_by_stream(cursor, "ST", "PF") == {"qt": qt_rows}
    # The second helper reuses the cached probe result instead of re-querying.
    assert len(cursor.calls_matching("information_schema.columns")) == 1


# ---------------------------------------------------------------------------
# list_incubating_strategies
# ---------------------------------------------------------------------------


def test_list_incubating_strategies_returns_rows_and_closes_connection() -> None:
    rows = [
        {"id": "meanrev", "strategy_type": "MOCK_MEAN_REV", "portfolio_id": "MOCK"},
        {"id": "trend", "strategy_type": "MOCK_TREND", "portfolio_id": "MOCK"},
    ]
    cursor = ScriptedCursor(lambda sql, params: rows)
    repo, conn = make_repo(cursor)

    assert repo.list_incubating_strategies() == rows
    assert conn.closed
    (sql, params), *rest = cursor.calls
    assert not rest
    assert "lifecycle = 'incubating'" in sql
    assert params is None


# ---------------------------------------------------------------------------
# fetch_incubation_performance
# ---------------------------------------------------------------------------


def incubation_handler(
    registry_row: dict[str, Any] | None,
    positions: list[dict[str, Any]],
    equity: list[dict[str, Any]],
) -> Handler:
    def handle(sql: str, params: tuple[Any, ...] | None) -> Any:
        if "FROM trading.strategy_registry" in sql:
            return registry_row
        if "FROM trading.positions" in sql:
            return positions
        if "FROM trading.equity_curve" in sql:
            return equity
        raise AssertionError(f"Unexpected SQL: {sql}")

    return handle


def test_fetch_incubation_performance_returns_empty_when_not_incubating() -> None:
    cursor = ScriptedCursor(incubation_handler(None, [], []))
    repo, conn = make_repo(cursor)

    result = repo.fetch_incubation_performance("unknown")

    assert result == IncubationPerformanceRows(positions=[], equity_curve=[])
    assert len(cursor.calls) == 1  # no positions/equity queries were attempted
    assert conn.closed


def test_fetch_incubation_performance_returns_empty_when_start_date_missing() -> None:
    registry_row = {
        "strategy_type": "MOCK_TREND",
        "portfolio_id": "MOCK",
        "incubation_started_at": None,
    }
    cursor = ScriptedCursor(incubation_handler(registry_row, [], []))
    repo, _conn = make_repo(cursor)

    result = repo.fetch_incubation_performance("trend")

    assert result == IncubationPerformanceRows(positions=[], equity_curve=[])
    assert len(cursor.calls) == 1


def test_fetch_incubation_performance_scopes_queries_to_incubation_window() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    registry_row = {
        "strategy_type": "MOCK_TREND",
        "portfolio_id": "MOCK",
        "incubation_started_at": start,
    }
    positions = [{"date": start, "symbol": "ES", "quantity": 1, "entry_price": 5000.0}]
    equity = [{"date": start, "equity": 250_000.0}]
    cursor = ScriptedCursor(incubation_handler(registry_row, positions, equity))
    repo, conn = make_repo(cursor)

    result = repo.fetch_incubation_performance("trend")

    assert result.positions == positions
    assert result.equity_curve == equity
    assert conn.closed
    registry_calls = cursor.calls_matching("FROM trading.strategy_registry")
    assert registry_calls[0][1] == ("trend",)
    position_calls = cursor.calls_matching("FROM trading.positions")
    equity_calls = cursor.calls_matching("FROM trading.equity_curve")
    assert position_calls[0][1] == ("MOCK_TREND", "MOCK", start)
    assert equity_calls[0][1] == ("MOCK_TREND", "MOCK", start)


# ---------------------------------------------------------------------------
# Lifecycle write paths
# ---------------------------------------------------------------------------


def lifecycle_handler(
    lifecycle_row: dict[str, Any] | None,
    fail_on: str | None = None,
) -> Handler:
    def handle(sql: str, params: tuple[Any, ...] | None) -> Any:
        if fail_on is not None and fail_on in sql:
            raise psycopg2.Error("boom")
        if "SELECT lifecycle" in sql:
            return lifecycle_row
        if "UPDATE trading.strategy_registry" in sql:
            return None
        if "INSERT INTO trading.strategy_lifecycle_log" in sql:
            return None
        raise AssertionError(f"Unexpected SQL: {sql}")

    return handle


def test_start_incubation_rejects_non_positive_mock_capital() -> None:
    repo = PostgresPortfolioRepository(connection_factory=_unusable_factory)

    with pytest.raises(IncubationError, match="mock_capital must be positive"):
        repo.start_incubation("trend", 0, "Testing", "1")


def test_start_incubation_rejects_blank_reason_before_connecting() -> None:
    repo = PostgresPortfolioRepository(connection_factory=_unusable_factory)

    with pytest.raises(IncubationError, match="reason must be non-empty"):
        repo.start_incubation("trend", 100_000.0, "   ", "1")


def test_start_incubation_raises_when_strategy_missing() -> None:
    cursor = ScriptedCursor(lifecycle_handler(None))
    repo, conn = make_repo(cursor)

    with pytest.raises(IncubationError, match="Strategy trend not found"):
        repo.start_incubation("trend", 100_000.0, "Testing", "1")
    assert conn.closed
    assert not conn.committed


def test_start_incubation_raises_when_already_incubating() -> None:
    cursor = ScriptedCursor(lifecycle_handler({"lifecycle": "incubating"}))
    repo, conn = make_repo(cursor)

    with pytest.raises(IncubationError, match="already incubating"):
        repo.start_incubation("trend", 100_000.0, "Testing", "1")
    assert not conn.committed
    assert conn.closed


def test_start_incubation_updates_registry_writes_audit_and_commits() -> None:
    cursor = ScriptedCursor(lifecycle_handler({"lifecycle": "candidate"}))
    repo, conn = make_repo(cursor)

    repo.start_incubation("trend", 250_000.0, "Ready for paper trading", "42")

    update_calls = cursor.calls_matching("UPDATE trading.strategy_registry")
    audit_calls = cursor.calls_matching("INSERT INTO trading.strategy_lifecycle_log")
    assert update_calls[0][1] == ("incubating", 250_000.0, "trend")
    assert audit_calls[0][1] == (
        "trend",
        "candidate",
        "incubating",
        "Ready for paper trading",
        "42",
    )
    assert conn.committed
    assert not conn.rolled_back
    assert conn.closed


def test_start_incubation_wraps_db_error_and_rolls_back() -> None:
    cursor = ScriptedCursor(
        lifecycle_handler({"lifecycle": "candidate"}, fail_on="UPDATE trading.strategy_registry")
    )
    repo, conn = make_repo(cursor)

    with pytest.raises(IncubationError, match="Database error"):
        repo.start_incubation("trend", 250_000.0, "Testing", "1")
    assert conn.rolled_back
    assert not conn.committed
    assert conn.closed


def test_promote_to_live_rejects_blank_reason_before_connecting() -> None:
    repo = PostgresPortfolioRepository(connection_factory=_unusable_factory)

    with pytest.raises(IncubationError, match="reason must be non-empty"):
        repo.promote_to_live("trend", "", "1")


def test_promote_to_live_raises_when_strategy_missing() -> None:
    cursor = ScriptedCursor(lifecycle_handler(None))
    repo, conn = make_repo(cursor)

    with pytest.raises(IncubationError, match="Strategy trend not found"):
        repo.promote_to_live("trend", "Graduating", "1")
    assert not conn.committed
    assert conn.closed


def test_promote_to_live_requires_currently_incubating_state() -> None:
    cursor = ScriptedCursor(lifecycle_handler({"lifecycle": "live"}))
    repo, conn = make_repo(cursor)

    with pytest.raises(IncubationError, match="not currently incubating"):
        repo.promote_to_live("trend", "Graduating", "1")
    assert not conn.committed


def test_promote_to_live_updates_registry_writes_audit_and_commits() -> None:
    cursor = ScriptedCursor(lifecycle_handler({"lifecycle": "incubating"}))
    repo, conn = make_repo(cursor)

    repo.promote_to_live("trend", "Passed incubation window", "42")

    update_calls = cursor.calls_matching("UPDATE trading.strategy_registry")
    audit_calls = cursor.calls_matching("INSERT INTO trading.strategy_lifecycle_log")
    assert update_calls[0][1] == ("live", "trend")
    assert audit_calls[0][1] == ("trend", "incubating", "live", "Passed incubation window", "42")
    assert conn.committed
    assert not conn.rolled_back
    assert conn.closed


def test_promote_to_live_wraps_db_error_and_rolls_back() -> None:
    cursor = ScriptedCursor(
        lifecycle_handler(
            {"lifecycle": "incubating"},
            fail_on="INSERT INTO trading.strategy_lifecycle_log",
        )
    )
    repo, conn = make_repo(cursor)

    with pytest.raises(IncubationError, match="Database error"):
        repo.promote_to_live("trend", "Graduating", "1")
    assert conn.rolled_back
    assert not conn.committed
    assert conn.closed


def test_retire_strategy_rejects_blank_reason_before_connecting() -> None:
    repo = PostgresPortfolioRepository(connection_factory=_unusable_factory)

    with pytest.raises(IncubationError, match="reason must be non-empty"):
        repo.retire_strategy("trend", " ", "1")


def test_retire_strategy_raises_when_strategy_missing() -> None:
    cursor = ScriptedCursor(lifecycle_handler(None))
    repo, conn = make_repo(cursor)

    with pytest.raises(IncubationError, match="Strategy trend not found"):
        repo.retire_strategy("trend", "Underperforming", "1")
    assert not conn.committed
    assert conn.closed


def test_retire_strategy_retires_from_any_state_and_commits() -> None:
    cursor = ScriptedCursor(lifecycle_handler({"lifecycle": "live"}))
    repo, conn = make_repo(cursor)

    repo.retire_strategy("trend", "Underperforming", "42")

    update_calls = cursor.calls_matching("UPDATE trading.strategy_registry")
    audit_calls = cursor.calls_matching("INSERT INTO trading.strategy_lifecycle_log")
    assert update_calls[0][1] == ("retired", "trend")
    assert audit_calls[0][1] == ("trend", "live", "retired", "Underperforming", "42")
    assert conn.committed
    assert not conn.rolled_back
    assert conn.closed


def test_retire_strategy_wraps_db_error_and_rolls_back() -> None:
    cursor = ScriptedCursor(
        lifecycle_handler({"lifecycle": "live"}, fail_on="UPDATE trading.strategy_registry")
    )
    repo, conn = make_repo(cursor)

    with pytest.raises(IncubationError, match="Database error"):
        repo.retire_strategy("trend", "Underperforming", "1")
    assert conn.rolled_back
    assert not conn.committed
    assert conn.closed

"""Tests for the abstract infrastructure base classes and their concrete helpers.

Covers:
- data_ngin.infrastructure.loader.loader.Loader
- data_ngin.infrastructure.inserter (Inserter + module-level helpers)
- data_ngin.infrastructure.cleaner.cleaner.Cleaner
- data_ngin.infrastructure.fetcher.fetcher.Fetcher

Abstract classes are exercised through minimal in-test subclasses; all database
connections are MagicMocks (no live I/O anywhere).
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest
from data_ngin.infrastructure import inserter as inserter_module
from data_ngin.infrastructure.cleaner.cleaner import Cleaner
from data_ngin.infrastructure.fetcher.fetcher import Fetcher
from data_ngin.infrastructure.inserter import Inserter
from data_ngin.infrastructure.loader.loader import Loader

# ---------------------------------------------------------------------------
# Minimal concrete subclasses
# ---------------------------------------------------------------------------


class DummyLoader(Loader):
    """Minimal Loader: implements the single abstract method."""

    def load_symbols(self) -> dict[str, str]:
        return {"AAPL": "EQUITY"}


class DummyInserter(Inserter):
    """Minimal Inserter: implements both abstract methods."""

    def connect(self) -> None:
        self.connection = "connected"

    def insert_data(self, data: list[dict[str, Any]], schema: str, table: str) -> None:
        self.last_insert = (data, schema, table)


class RecordingCleaner(Cleaner):
    """Cleaner subclass that records the order its hooks are called in."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_fields(self, data: pd.DataFrame) -> pd.DataFrame:
        self.calls.append("validate")
        return data

    def handle_missing_data(self, data: pd.DataFrame) -> pd.DataFrame:
        self.calls.append("missing")
        return data.dropna()

    def transform_data(self, data: pd.DataFrame) -> pd.DataFrame:
        self.calls.append("transform")
        return data.assign(transformed=True)


class SyncFetcher(Fetcher):
    """Minimal concrete Fetcher: implements the single abstract method."""

    def fetch_data(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return [
            {"time": "2023-01-01", "close": 1.0},
            {"time": "2023-01-03", "close": 2.0},
        ]


class AsyncFetcher(Fetcher):
    """Fetcher with an async fetch_data (for the retrieve delegation path)."""

    async def fetch_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        loaded_asset_type: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "symbol": symbol,
                "asset": loaded_asset_type,
                "start": start_date,
                "end": end_date,
            }
        ]


# ---------------------------------------------------------------------------
# Abstract-class instantiation errors
# ---------------------------------------------------------------------------


def test_loader_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Loader({})


def test_inserter_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Inserter({})


def test_cleaner_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Cleaner()


def test_fetcher_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Fetcher({})


def test_partial_cleaner_subclass_still_abstract() -> None:
    class PartialCleaner(Cleaner):
        def validate_fields(self, data: pd.DataFrame) -> pd.DataFrame:
            return data

    with pytest.raises(TypeError):
        PartialCleaner()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _loader_with_mock_logger() -> DummyLoader:
    loader = DummyLoader({"provider": {"name": "test"}})
    # Loader.validate_data_quality references self.logger but the base class
    # never sets one; tests supply it explicitly (see final report note).
    loader.logger = MagicMock()
    return loader


def _mock_db_conn(fetchone_results: list[tuple[int]]) -> tuple[MagicMock, MagicMock]:
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.side_effect = fetchone_results
    return conn, cursor


def test_loader_stores_config_and_loads_symbols() -> None:
    config = {"source": "csv"}
    loader = DummyLoader(config)
    assert loader.config is config
    assert loader.load_symbols() == {"AAPL": "EQUITY"}


def test_validate_data_quality_all_checks_pass() -> None:
    loader = _loader_with_mock_logger()
    conn, cursor = _mock_db_conn([(0,), (0,), (0,)])

    assert loader.validate_data_quality(conn) is True
    assert cursor.execute.call_count == 3
    cursor.close.assert_called_once()
    loader.logger.info.assert_called_once()


def test_validate_data_quality_fails_on_nulls() -> None:
    loader = _loader_with_mock_logger()
    conn, cursor = _mock_db_conn([(3,)])

    assert loader.validate_data_quality(conn) is False
    assert cursor.execute.call_count == 1
    cursor.close.assert_called_once()
    loader.logger.error.assert_called_once()


def test_validate_data_quality_fails_on_zero_values() -> None:
    loader = _loader_with_mock_logger()
    conn, cursor = _mock_db_conn([(0,), (2,)])

    assert loader.validate_data_quality(conn) is False
    assert cursor.execute.call_count == 2
    loader.logger.error.assert_called_once()


def test_validate_data_quality_fails_on_time_gaps() -> None:
    loader = _loader_with_mock_logger()
    conn, cursor = _mock_db_conn([(0,), (0,), (7,)])

    assert loader.validate_data_quality(conn) is False
    assert cursor.execute.call_count == 3
    loader.logger.error.assert_called_once()


def test_validate_data_quality_returns_false_on_query_error() -> None:
    loader = _loader_with_mock_logger()
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.execute.side_effect = Exception("db down")

    assert loader.validate_data_quality(conn) is False
    cursor.close.assert_called_once()
    loader.logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# Inserter
# ---------------------------------------------------------------------------


def test_inserter_subclass_init_and_methods() -> None:
    config = {"host": "localhost"}
    ins = DummyInserter(config)
    assert ins.config is config
    assert ins.connection is None

    ins.connect()
    assert ins.connection == "connected"

    rows = [{"time": "2023-01-01"}]
    ins.insert_data(rows, "public", "ohlcv")
    assert ins.last_insert == (rows, "public", "ohlcv")


def test_log_insertion_status_success_logs_info() -> None:
    stub = SimpleNamespace(logger=MagicMock())
    inserter_module.log_insertion_status(stub, success=True, num_rows=42)
    stub.logger.info.assert_called_once()
    assert "42" in stub.logger.info.call_args[0][0]
    stub.logger.error.assert_not_called()


def test_log_insertion_status_failure_logs_error() -> None:
    stub = SimpleNamespace(logger=MagicMock())
    inserter_module.log_insertion_status(stub, success=False, num_rows=7)
    stub.logger.error.assert_called_once()
    assert "7" in stub.logger.error.call_args[0][0]
    stub.logger.info.assert_not_called()


def _insertion_stub(fetchone_result: tuple[int]) -> tuple[SimpleNamespace, MagicMock]:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = fetchone_result
    stub = SimpleNamespace(connection=conn, logger=MagicMock())
    return stub, cursor


def test_validate_insertion_row_count_matches() -> None:
    stub, cursor = _insertion_stub((5,))

    assert inserter_module.validate_insertion(stub, "public", "ohlcv", expected_rows=5) is True
    cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM public.ohlcv;")
    stub.logger.info.assert_called_once()


def test_validate_insertion_row_count_mismatch() -> None:
    stub, _ = _insertion_stub((3,))

    assert inserter_module.validate_insertion(stub, "public", "ohlcv", expected_rows=5) is False
    stub.logger.warning.assert_called_once()


def test_validate_insertion_returns_false_on_error() -> None:
    conn = MagicMock()
    conn.cursor.side_effect = Exception("connection lost")
    stub = SimpleNamespace(connection=conn, logger=MagicMock())

    assert inserter_module.validate_insertion(stub, "public", "ohlcv", expected_rows=5) is False
    stub.logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# Cleaner concrete helpers
# ---------------------------------------------------------------------------


def test_cleaner_clean_orchestrates_hooks_in_order() -> None:
    cleaner = RecordingCleaner()
    data = pd.DataFrame({"time": ["2023-01-01", "2023-01-02"], "close": [1.0, None]})

    result = cleaner.clean(data)

    assert cleaner.calls == ["validate", "missing", "transform"]
    # handle_missing_data dropped the NaN row, transform added a column
    assert len(result) == 1
    assert "transformed" in result.columns


# Cleaner.detect_time_gaps / log_missing_data were deleted (gap detection is
# consolidated in StalenessChecker.detect_date_gaps, tested in
# tests/domain/test_services.py), so their tests went with them.


# ---------------------------------------------------------------------------
# Fetcher concrete helpers
# ---------------------------------------------------------------------------


def test_fetcher_init_sets_config_and_logger() -> None:
    config = {"provider": {"name": "databento"}}
    fetcher = SyncFetcher(config)
    assert fetcher.config is config
    assert fetcher.logger.name == "SyncFetcher"
    assert fetcher.logger.level == logging.INFO


def test_fetcher_retrieve_delegates_to_fetch_data() -> None:
    fetcher = AsyncFetcher({})

    result = asyncio.run(
        fetcher.retrieve(
            symbol="ES",
            loaded_asset_type="FUTURE",
            start_date="2023-01-01",
            end_date="2023-01-31",
            batch_config={"unit": "month", "max_units": 1},
        )
    )

    assert result == [
        {"symbol": "ES", "asset": "FUTURE", "start": "2023-01-01", "end": "2023-01-31"}
    ]


# Fetcher.detect_time_gaps / log_missing_data / fetch_and_validate were deleted
# (gap detection is consolidated in StalenessChecker.detect_date_gaps, tested
# in tests/domain/test_services.py), so their tests went with them.

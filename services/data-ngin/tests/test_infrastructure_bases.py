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
    """Fetcher with a synchronous fetch_data (for fetch_and_validate)."""

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


def test_cleaner_detect_time_gaps_finds_missing_days() -> None:
    cleaner = RecordingCleaner()
    data = pd.DataFrame({"time": ["2023-01-03", "2023-01-01", "2023-01-04"], "close": [3, 1, 4]})

    missing = cleaner.detect_time_gaps(data, time_column="time", freq="D")

    assert list(missing) == [pd.Timestamp("2023-01-02")]


def test_cleaner_detect_time_gaps_no_gaps() -> None:
    cleaner = RecordingCleaner()
    data = pd.DataFrame({"time": ["2023-01-01", "2023-01-02"], "close": [1, 2]})

    missing = cleaner.detect_time_gaps(data, time_column="time", freq="D")

    assert len(missing) == 0


def test_cleaner_log_missing_data_warns_per_timestamp(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Prevent basicConfig from installing a FileHandler writing data_quality.log
    # into the working directory.
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)
    cleaner = RecordingCleaner()
    stamps = [pd.Timestamp("2023-01-02"), pd.Timestamp("2023-01-05")]

    with caplog.at_level(logging.WARNING):
        cleaner.log_missing_data(stamps)

    warned = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 2
    assert "2023-01-02" in warned[0]
    assert "2023-01-05" in warned[1]


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


def test_fetcher_detect_time_gaps_returns_missing_strings() -> None:
    fetcher = SyncFetcher({})
    data = [
        {"time": "2023-01-01", "close": 1.0},
        {"time": "2023-01-04", "close": 4.0},
    ]

    missing = fetcher.detect_time_gaps(data, time_column="time", freq="D")

    assert missing == ["2023-01-02", "2023-01-03"]


def test_fetcher_detect_time_gaps_missing_column_raises() -> None:
    fetcher = SyncFetcher({})
    data = [{"date": "2023-01-01", "close": 1.0}]

    with pytest.raises(ValueError, match="Time column 'time' not found"):
        fetcher.detect_time_gaps(data, time_column="time", freq="D")


def test_fetcher_log_missing_data_warns_when_gaps(caplog: pytest.LogCaptureFixture) -> None:
    fetcher = SyncFetcher({})

    with caplog.at_level(logging.WARNING, logger="SyncFetcher"):
        fetcher.log_missing_data(["2023-01-02", "2023-01-03"])

    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "2023-01-02" in warnings[0]


def test_fetcher_log_missing_data_info_when_none(caplog: pytest.LogCaptureFixture) -> None:
    fetcher = SyncFetcher({})

    with caplog.at_level(logging.INFO, logger="SyncFetcher"):
        fetcher.log_missing_data([])

    infos = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert infos == ["No missing data detected."]


def test_fetcher_fetch_and_validate_returns_data_and_logs_gaps(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fetcher = SyncFetcher({})

    with caplog.at_level(logging.WARNING, logger="SyncFetcher"):
        data = fetcher.fetch_and_validate(
            symbol="ES",
            start_date="2023-01-01",
            end_date="2023-01-03",
            time_column="time",
            freq="D",
        )

    assert len(data) == 2
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("2023-01-02" in message for message in warnings)

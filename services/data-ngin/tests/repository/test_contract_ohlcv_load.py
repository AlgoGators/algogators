from typing import Any
from unittest.mock import MagicMock, patch

from data_ngin.infrastructure.repository.ohlcv_repository import OhlcvRepository, upsert_clause


def test_upsert_clause_for_contract_ohlcv():
    cols = ["instrument_id", "date", "root", "raw_symbol", "open", "high", "low", "close", "volume"]
    clause = upsert_clause(cols)
    assert "ON CONFLICT (instrument_id, date) DO UPDATE SET" in clause
    assert "open = EXCLUDED.open" in clause
    assert "volume = EXCLUDED.volume" in clause


def test_batch_upsert_contract_ohlcv():
    config: dict[str, Any] = {
        "database": {
            "target_schema": "futures_data",
            "table": "contract_ohlcv_1d",
        }
    }
    repo = OhlcvRepository(config=config)
    repo.connection = MagicMock()
    mock_cursor = repo.connection.cursor.return_value.__enter__.return_value

    records = [
        {
            "instrument_id": "12345",
            "date": "2026-10-01",
            "root": "ES",
            "raw_symbol": "ESZ6",
            "expiration": "2026-12-18",
            "open": 5800.0,
            "high": 5850.0,
            "low": 5790.0,
            "close": 5840.0,
            "volume": 120000,
        }
    ]

    with patch("psycopg2.extras.execute_values") as mock_exec_values:
        repo.insert_contract_bars(records)
        assert mock_exec_values.called
        args, _kwargs = mock_exec_values.call_args
        assert args[0] == mock_cursor
        assert "futures_data.contract_ohlcv_1d" in args[1]
        assert len(args[2]) == 1

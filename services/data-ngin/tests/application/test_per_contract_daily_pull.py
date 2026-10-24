from datetime import date
from unittest.mock import MagicMock, patch
import pytest

from data_ngin.application.pipeline_tasks import write_contract_futures_readiness


def test_write_contract_readiness_marks_complete():
    mock_readiness_repo = MagicMock()
    with patch("data_ngin.infrastructure.repository.readiness_repository.ReadinessRepository", return_value=mock_readiness_repo):
        row = write_contract_futures_readiness(
            "new_config.yaml",
            date(2026, 10, 21),
            "run-123",
            expected=40,
            loaded=40,
            missing=[],
        )
        assert row is not None
        assert row["status"] == "complete"
        assert row["dataset"] == "futures_contract_ohlcv_1d"
        assert row["expected"] == 40
        assert row["loaded"] == 40
        assert row["missing"] == []
        mock_readiness_repo.upsert_readiness.assert_called_once_with(row)


def test_write_contract_readiness_marks_partial():
    mock_readiness_repo = MagicMock()
    with patch("data_ngin.infrastructure.repository.readiness_repository.ReadinessRepository", return_value=mock_readiness_repo):
        row = write_contract_futures_readiness(
            "new_config.yaml",
            date(2026, 10, 21),
            "run-123",
            expected=40,
            loaded=38,
            missing=["ESZ6", "CLV6"],
        )
        assert row is not None
        assert row["status"] == "partial"
        assert row["dataset"] == "futures_contract_ohlcv_1d"
        assert row["loaded"] == 38
        assert row["missing"] == ["ESZ6", "CLV6"]

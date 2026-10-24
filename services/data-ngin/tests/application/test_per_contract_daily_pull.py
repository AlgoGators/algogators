from datetime import date
from unittest.mock import MagicMock, patch

from data_ngin.application.pipeline_tasks import write_contract_futures_readiness


def test_write_contract_readiness_marks_complete():
    mock_readiness_repo = MagicMock()
    with patch(
        "data_ngin.infrastructure.repository.readiness_repository.ReadinessRepository",
        return_value=mock_readiness_repo,
    ):
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
    with patch(
        "data_ngin.infrastructure.repository.readiness_repository.ReadinessRepository",
        return_value=mock_readiness_repo,
    ):
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


def test_should_retry_contract_run():
    from data_ngin.application.pipeline_tasks import should_retry_contract_run

    # Missing roots and retries remain -> should retry
    res_missing = {"missing": ["ESZ6"], "loaded": 39, "expected": 40}
    assert should_retry_contract_run(res_missing, try_number=1, max_tries=3) is True
    assert should_retry_contract_run(res_missing, try_number=3, max_tries=3) is True
    # Max tries exhausted -> should not retry
    assert should_retry_contract_run(res_missing, try_number=4, max_tries=3) is False

    # Complete -> should not retry
    res_complete = {"missing": [], "loaded": 40, "expected": 40}
    assert should_retry_contract_run(res_complete, try_number=1, max_tries=3) is False


def test_run_contract_close_pipeline_skips_weekend():
    from data_ngin.application.close_pipeline import run_contract_close_pipeline

    with (
        patch("data_ngin.application.close_pipeline.expected_contracts", return_value=[]),
        patch("data_ngin.application.close_pipeline.load_contracts", return_value=[]),
    ):
        res = run_contract_close_pipeline({}, date(2026, 10, 24))
        assert res["skipped"] is True
        assert res["status"] == "complete"
        assert res["expected"] == 40
        assert res["loaded"] == 0
        assert res["missing"] == []


def test_run_contract_close_pipeline_evaluates_readiness():
    from data_ngin.application.close_pipeline import run_contract_close_pipeline

    mock_repo = MagicMock()
    # Mock present roots in database
    mock_repo.get_contract_roots_for_date.return_value = {"ES", "NQ", "YM", "RTY"}

    with (
        patch(
            "data_ngin.application.close_pipeline.expected_contracts",
            return_value=[{"dataSymbol": "ES"}],
        ),
        patch("data_ngin.application.close_pipeline.load_contracts", return_value=[]),
        patch(
            "data_ngin.infrastructure.repository.ohlcv_repository.OhlcvRepository",
            return_value=mock_repo,
        ),
    ):
        res = run_contract_close_pipeline(
            {}, date(2026, 10, 21), roots=["ES", "NQ", "YM", "RTY", "CL"]
        )
        assert res["skipped"] is False
        assert res["expected"] == 5
        assert res["loaded"] == 4
        assert res["missing"] == ["CL"]
        assert res["status"] == "partial"

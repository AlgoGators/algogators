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


def _client_for(bars, definitions):
    """A fake databento.Historical whose get_range returns `bars` or `definitions` by schema."""
    import pandas as pd

    def get_range(**kwargs):
        frame = definitions if kwargs["schema"] == "definition" else bars
        store = MagicMock()
        store.to_df.return_value = pd.DataFrame(frame)
        return store

    client = MagicMock()
    client.timeseries.get_range.side_effect = get_range
    return client


def test_daily_pull_takes_expiration_from_the_definitions(monkeypatch):
    import pandas as pd
    from data_ngin.application.close_pipeline import run_contract_close_pipeline

    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")
    bars = {
        "ts_event": pd.to_datetime(["2026-10-21"], utc=True),
        "symbol": ["GCZ6"],
        "instrument_id": [41271],
        "open": [4100.0],
        "high": [4120.0],
        "low": [4090.0],
        "close": [4110.0],
        "volume": [150000],
    }
    definitions = {
        "ts_recv": pd.to_datetime(["2026-10-21"], utc=True),
        "instrument_id": [41271],
        "raw_symbol": ["GCZ6"],
        "instrument_class": ["F"],
        "expiration": pd.to_datetime(["2026-12-29 17:30"], utc=True),
    }
    mock_repo = MagicMock()
    mock_repo.get_contract_roots_for_date.return_value = {"GC"}
    with (
        patch(
            "data_ngin.application.close_pipeline.expected_contracts",
            return_value=[{"dataSymbol": "GC"}],
        ),
        patch("data_ngin.application.close_pipeline.load_contracts", return_value=[]),
        patch(
            "data_ngin.infrastructure.repository.ohlcv_repository.OhlcvRepository",
            return_value=mock_repo,
        ),
        patch("databento.Historical", return_value=_client_for(bars, definitions)),
    ):
        res = run_contract_close_pipeline({}, date(2026, 10, 21), roots=["GC"])

    (records,), _ = mock_repo.insert_contract_bars.call_args
    # the definition's date, not the third-Friday guess (2026-12-18)
    assert records[0]["expiration"] == date(2026, 12, 29)
    assert records[0]["raw_symbol"] == "GCZ6"
    assert res["status"] == "complete"


def test_daily_pull_leaves_expiration_empty_without_a_definition(monkeypatch):
    import pandas as pd
    from data_ngin.application.close_pipeline import run_contract_close_pipeline

    monkeypatch.setenv("DATABENTO_API_KEY", "test-key")
    bars = {
        "ts_event": pd.to_datetime(["2026-10-21"], utc=True),
        "symbol": ["ZNZ6"],
        "instrument_id": [555],
        "open": [111.0],
        "high": [111.5],
        "low": [110.5],
        "close": [111.25],
        "volume": [900000],
    }
    definitions = {
        "ts_recv": pd.to_datetime([], utc=True),
        "instrument_id": [],
        "raw_symbol": [],
        "instrument_class": [],
        "expiration": pd.to_datetime([], utc=True),
    }
    mock_repo = MagicMock()
    mock_repo.get_contract_roots_for_date.return_value = set()
    with (
        patch(
            "data_ngin.application.close_pipeline.expected_contracts",
            return_value=[{"dataSymbol": "ZN"}],
        ),
        patch("data_ngin.application.close_pipeline.load_contracts", return_value=[]),
        patch(
            "data_ngin.infrastructure.repository.ohlcv_repository.OhlcvRepository",
            return_value=mock_repo,
        ),
        patch("databento.Historical", return_value=_client_for(bars, definitions)),
    ):
        res = run_contract_close_pipeline({}, date(2026, 10, 21), roots=["ZN"])

    (records,), _ = mock_repo.insert_contract_bars.call_args
    assert records[0]["expiration"] is None
    assert res["missing"] == ["ZN"]


def test_failed_contract_row_counts_the_roots_from_the_table():
    # The failure callback passes no counts. The row must still say 40 expected and how
    # many roots are in the table, not 'failed, expected 0' (production's 2026-10-12 row).
    from data_ngin.application.close_pipeline import CONTRACT_ROOTS

    mock_readiness_repo = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_contract_roots_for_date.return_value = set(CONTRACT_ROOTS) - {"ZC", "ZS"}
    with (
        patch(
            "data_ngin.infrastructure.repository.readiness_repository.ReadinessRepository",
            return_value=mock_readiness_repo,
        ),
        patch(
            "data_ngin.infrastructure.repository.ohlcv_repository.OhlcvRepository",
            return_value=mock_repo,
        ),
    ):
        row = write_contract_futures_readiness(
            "new_config.yaml", date(2026, 10, 12), "scheduled__2026-10-13", failed=True
        )
    assert row["expected"] == 40
    assert row["loaded"] == 38
    assert row["missing"] == ["ZC", "ZS"]
    assert row["status"] == "failed"
    mock_readiness_repo.upsert_readiness.assert_called_once_with(row)

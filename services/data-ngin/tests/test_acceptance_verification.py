from unittest.mock import MagicMock

import pytest
from data_ngin.ops.contract_verification import (
    continuous_tables_unchanged,
    table_fingerprints,
    verify_close_parity,
    verify_immutability,
    verify_roll_coverage,
)


def _conn_returning(*fingerprints):
    """A connection whose fingerprint queries return the given (count, md5) pairs in order."""
    mock_conn = MagicMock()
    cur = mock_conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = list(fingerprints)
    return mock_conn, cur


def test_fingerprint_covers_every_column_of_both_tables():
    mock_conn, cur = _conn_returning((10, "a"), (10, "b"))
    assert table_fingerprints(mock_conn) == {
        "futures_data.ohlcv_1d": {"count": 10, "fingerprint": "a"},
        "futures_data.ohlcv_1d_raw": {"count": 10, "fingerprint": "b"},
    }
    kept, raw = (" ".join(c[0][0].split()) for c in cur.execute.call_args_list)
    assert "ROW(time, symbol, open, high, low, close, volume)" in kept
    assert (
        "ROW(ts_event, symbol, instrument_id, open, high, low, close, volume, rtype, publisher_id)"
        in raw
    )


def test_fingerprints_end_their_read_transaction():
    mock_conn, cur = _conn_returning((10, "a"), (10, "b"))
    table_fingerprints(mock_conn)
    assert cur.execute.call_count == 2
    mock_conn.rollback.assert_called_once_with()
    mock_conn.commit.assert_not_called()


def test_verify_immutability_compares_with_the_baseline_taken_before():
    baseline = {
        "futures_data.ohlcv_1d": {"count": 10, "fingerprint": "a"},
        "futures_data.ohlcv_1d_raw": {"count": 10, "fingerprint": "b"},
    }
    mock_conn, _ = _conn_returning((10, "a"), (10, "b-changed"))
    res = verify_immutability(mock_conn, baseline)
    assert res["futures_data.ohlcv_1d"]["passed"] is True
    assert res["futures_data.ohlcv_1d_raw"]["passed"] is False


def test_a_load_that_changes_a_continuous_table_fails():
    mock_conn, _ = _conn_returning((10, "a"), (10, "b"), (10, "a"), (10, "b-changed"))
    with (
        pytest.raises(RuntimeError, match=r"futures_data\.ohlcv_1d_raw"),
        continuous_tables_unchanged(mock_conn),
    ):
        pass
    mock_conn, _ = _conn_returning((10, "a"), (10, "b"), (10, "a"), (10, "b"))
    with continuous_tables_unchanged(mock_conn) as baseline:
        assert baseline["futures_data.ohlcv_1d"]["fingerprint"] == "a"


def test_verify_close_parity_passes_with_zero_mismatches():
    mock_conn = MagicMock()
    mock_cursor = mock_conn.cursor.return_value.__enter__.return_value
    mock_cursor.fetchone.return_value = (150000, 0)

    res = verify_close_parity(mock_conn)
    assert res["total_compared"] == 150000
    assert res["mismatch_count"] == 0
    assert res["passed"] is True


def test_verify_roll_coverage_checks_roll_counts():
    mock_conn = MagicMock()
    mock_cursor = mock_conn.cursor.return_value.__enter__.return_value
    mock_cursor.fetchone.return_value = (5505, 5505, 0)

    res = verify_roll_coverage(mock_conn)
    assert res["total_rolls"] == 5505
    assert res["covered_rolls"] == 5505
    assert res["missing_rolls"] == 0
    assert res["passed"] is True


def test_close_parity_compares_exactly():
    # An absolute 1e-4 tolerance hid every 6J mismatch (6J trades near 0.0065, a tick is
    # 0.0000005): 6J 2020-02-28 0.0092815 against 0.0092615 was not counted.
    mock_conn = MagicMock()
    mock_cursor = mock_conn.cursor.return_value.__enter__.return_value
    mock_cursor.fetchone.return_value = (10, 1)
    mock_cursor.fetchall.return_value = []
    verify_close_parity(mock_conn)
    sql = mock_cursor.execute.call_args_list[0][0][0]
    assert "1e-4" not in sql
    assert "v.close IS DISTINCT FROM c.close" in sql


def test_checks_run_in_utc(monkeypatch):
    from unittest.mock import patch

    from data_ngin.ops import contract_verification

    with patch.object(contract_verification.psycopg2, "connect") as connect:
        contract_verification.get_connection()
    assert "TimeZone=UTC" in connect.call_args.kwargs["options"]

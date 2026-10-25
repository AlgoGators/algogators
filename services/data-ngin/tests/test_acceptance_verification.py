from unittest.mock import MagicMock

from data_ngin.ops.contract_verification import (
    verify_close_parity,
    verify_immutability,
    verify_roll_coverage,
)


def test_verify_immutability_passes_on_exact_match():
    mock_conn = MagicMock()
    mock_cursor = mock_conn.cursor.return_value.__enter__.return_value
    mock_cursor.fetchall.return_value = [
        ("futures_data.ohlcv_1d", 166477, "ec786348663adab6c2690d065f909922"),
        ("futures_data.ohlcv_1d_raw", 166477, "d2d47dfa1c65e0bffa086dd4e0974e0a"),
    ]

    res = verify_immutability(mock_conn)
    assert res["futures_data.ohlcv_1d"]["passed"] is True
    assert res["futures_data.ohlcv_1d_raw"]["passed"] is True


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

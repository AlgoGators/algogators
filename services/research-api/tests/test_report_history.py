"""The report history reader: SQL shape and the rows it turns into series."""

import datetime as dt

from research_api.infrastructure.reports.history import PostgresReportHistory


class Cursor:
    def __init__(self):
        self.sql = []
        self.rows = [
            [
                (dt.date(2026, 10, 22), 2.5, -1.0, 71000.0),
                (dt.date(2026, 10, 21), None, None, 70000.0),
            ],
            [(dt.date(2026, 10, 22), 4)],
        ]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.sql.append((sql, params))

    def fetchall(self):
        return self.rows.pop(0)


class Conn:
    def __init__(self):
        self.cur, self.closed = Cursor(), False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def test_history_reads_oldest_first_skips_missing_values_and_scopes_by_book():
    conn = Conn()
    out = PostgresReportHistory(lambda: conn)("QT_CONSERVATIVE_PORTFOLIO", "LIVE_TREND_FOLLOWING")
    assert out["leverage"] == [{"date": "2026-10-22", "value": 2.5}]
    assert out["net_leverage"] == [{"date": "2026-10-22", "value": -1.0}]
    assert [p["date"] for p in out["margin_posted"]] == ["2026-10-21", "2026-10-22"]
    assert out["contracts_per_day"] == [{"date": "2026-10-22", "value": 4.0}]
    results_sql, params = conn.cur.sql[0]
    assert "COALESCE(gross_leverage, portfolio_leverage)" in results_sql
    assert params == {"p": "QT_CONSERVATIVE_PORTFOLIO", "s": "LIVE_TREND_FOLLOWING"}
    assert conn.cur.sql[1][1]["since"] == dt.date(2026, 10, 21)  # oldest of the fetched days
    assert conn.closed

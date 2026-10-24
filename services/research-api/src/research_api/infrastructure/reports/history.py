"""Daily history the report charts need beyond the portfolio detail: leverage, margin, contracts."""

from research_api.infrastructure.db.postgres import get_db_connection

DAYS = 30

# A QT portfolio keeps its own 'qt' rows next to the engine's 'system' rows; use the book's own
# when it has any, else the system's. strategy_id is the strategy_type the detail reads by. The
# futures runners leave gross_leverage NULL and write portfolio_leverage (D-28), so gross falls
# back to it, as the screens do.
BOOK_SQL = """
(SELECT CASE WHEN EXISTS (
          SELECT 1 FROM trading.live_results
           WHERE portfolio_id = %(p)s AND strategy_id = %(s)s AND portfolio_type = 'qt')
        THEN 'qt' ELSE 'system' END)
"""

RESULTS_SQL = f"""
SELECT date, COALESCE(gross_leverage, portfolio_leverage), net_leverage, margin_posted
  FROM trading.live_results
 WHERE portfolio_id = %(p)s AND strategy_id = %(s)s AND portfolio_type = {BOOK_SQL}
 ORDER BY date DESC
 LIMIT {DAYS}
"""

CONTRACTS_SQL = f"""
SELECT date, SUM(quantity)
  FROM trading.executions
 WHERE portfolio_id = %(p)s AND strategy_id = %(s)s AND portfolio_type = {BOOK_SQL}
   AND date >= %(since)s
 GROUP BY date
 ORDER BY date
"""


def _num(value):
    return None if value is None else float(value)


class PostgresReportHistory:
    """series for the payload: leverage, net_leverage, margin_posted, contracts_per_day."""

    def __init__(self, connection_factory=None):
        self.connection_factory = connection_factory or get_db_connection

    def __call__(self, portfolio_id, strategy_type):
        params = {"p": portfolio_id, "s": strategy_type}
        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                cursor.execute(RESULTS_SQL, params)
                rows = list(reversed(cursor.fetchall()))
                since = rows[0][0] if rows else None
                contracts = []
                if since is not None:
                    cursor.execute(CONTRACTS_SQL, {**params, "since": since})
                    contracts = cursor.fetchall()
        finally:
            conn.close()
        return {
            "leverage": _points(rows, 1),
            "net_leverage": _points(rows, 2),
            "margin_posted": _points(rows, 3),
            "contracts_per_day": _points(contracts, 1),
        }


def _points(rows, index):
    return [
        {"date": r[0].isoformat(), "value": _num(r[index])} for r in rows if r[index] is not None
    ]

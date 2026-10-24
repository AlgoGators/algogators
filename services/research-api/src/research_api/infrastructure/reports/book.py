"""Record the book email as sent, where trade-ngin's engine looks for it."""

from research_api.application.reports.ports import BookSentMarker
from research_api.infrastructure.db.postgres import get_db_connection


class PostgresBookSentMarker(BookSentMarker):
    """trading.live_run_metadata.sent_at (trade-ngin migration 026), set once.

    The engine reads max(sent_at) per portfolio and day to know the email went out and to refuse
    a rescind afterwards, so this is the same write its own sender makes. Rows that already have
    a sent_at keep it, which makes a repeat harmless.
    """

    def __init__(self, connection_factory=None):
        self.connection_factory = connection_factory or get_db_connection

    def mark_book_sent(self, portfolio_id, report_date):
        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE trading.live_run_metadata SET sent_at = now() "
                    "WHERE portfolio_id = %s AND date = %s "
                    "AND published_at IS NOT NULL AND sent_at IS NULL",
                    (portfolio_id, report_date),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

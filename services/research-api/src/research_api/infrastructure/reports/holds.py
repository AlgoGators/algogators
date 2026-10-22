"""D-66: the desk can approve a day without its e-mail and send it later with Send."""

from research_api.infrastructure.db.postgres import get_db_connection

# The newest approval of the day (a done publish row that published it) says payload.email=false.
HELD_SQL = """
SELECT (SELECT payload->>'email' FROM trading.position_overrides
         WHERE portfolio_id = %(p)s AND date = %(d)s AND kind = 'publish'
           AND status = 'done' AND result ? 'published_at'
         ORDER BY id DESC LIMIT 1) = 'false'
   AND NOT EXISTS (
         SELECT 1 FROM trading.position_overrides
          WHERE portfolio_id = %(p)s AND date = %(d)s AND kind = 'publish'
            AND payload->>'flow' = 'send' AND status = 'done'
            AND result->>'handoff' = 'true')
"""


class PostgresHeldDays:
    """Whether a published day is still held back from its e-mail.

    With QT_BOOK_EMAIL_HANDOFF the engine's Send records a done publish row with flow 'send' and
    result.handoff instead of sending; that row is the release.
    """

    def __init__(self, connection_factory=None):
        self.connection_factory = connection_factory or get_db_connection

    def __call__(self, portfolio_id, day):
        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                cursor.execute(HELD_SQL, {"p": portfolio_id, "d": day})
                row = cursor.fetchone()
                return bool(row and row[0])
        finally:
            conn.close()

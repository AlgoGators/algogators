"""The symbols reference: metadata for the contracts a book holds."""

from research_api.infrastructure.db.postgres import get_db_connection

# Contract Months is read through to_jsonb so a database without that column still answers.
SYMBOLS_SQL = """
SELECT m."Databento Symbol", m."IB Symbol", m."Name", COALESCE(to_jsonb(m) ->> 'Contract Months', '')
  FROM metadata.contract_metadata m
 WHERE m."Databento Symbol" = ANY(%(s)s) OR m."IB Symbol" = ANY(%(s)s)
 ORDER BY m."Name"
"""


class PostgresContractSymbols:
    """lookup(roots) -> {"rows": [{db, ib, name, months}], "missing": [roots with no row]}."""

    def __init__(self, connection_factory=None):
        self.connection_factory = connection_factory or get_db_connection

    def __call__(self, roots):
        roots = sorted(set(roots))
        if not roots:
            return {"rows": [], "missing": []}
        conn = self.connection_factory()
        try:
            with conn.cursor() as cursor:
                cursor.execute(SYMBOLS_SQL, {"s": roots})
                found = cursor.fetchall()
        finally:
            conn.close()
        rows = [
            {"db": r[0], "ib": r[1] or "", "name": r[2] or "", "months": r[3] or ""} for r in found
        ]
        known = {r["db"] for r in rows} | {r["ib"] for r in rows}
        return {"rows": rows, "missing": [s for s in roots if s not in known]}

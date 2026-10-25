"""
Verification routines for Issue #37 Acceptance Criteria:
  (i)   Roll date dual-contract coverage
  (ii)  Close price parity against .v.0 continuous series
  (iii) Fingerprint immutability on ohlcv_1d and ohlcv_1d_raw
"""

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg2
from dotenv import load_dotenv

logger = logging.getLogger("contract_verification")

# Check (iii) fingerprints every column of both continuous tables, row by row in key order. There
# is no stored baseline: a load takes one before it writes and compares after it
# (continuous_tables_unchanged), so the check proves that load changed nothing.
FINGERPRINT_SQL = {
    "futures_data.ohlcv_1d": """
        SELECT count(*), md5(string_agg(
            ROW(time, symbol, open, high, low, close, volume)::text, E'\\n' ORDER BY time, symbol))
        FROM futures_data.ohlcv_1d
    """,
    "futures_data.ohlcv_1d_raw": """
        SELECT count(*), md5(string_agg(
            ROW(ts_event, symbol, instrument_id, open, high, low, close, volume, rtype, publisher_id)::text,
            E'\\n' ORDER BY ts_event, symbol, instrument_id))
        FROM futures_data.ohlcv_1d_raw
    """,
}


def get_connection() -> Any:
    env_paths = [
        os.path.join(os.path.dirname(__file__), "../../../.env"),
        os.path.join(os.path.dirname(__file__), "../../../../../.env"),
    ]
    for p in env_paths:
        if os.path.exists(p):
            load_dotenv(p)
            break

    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        # time::date and time::text follow the session time zone; the tables' dates are UTC.
        options="-c TimeZone=UTC",
    )


def table_fingerprints(conn: Any) -> dict[str, dict[str, Any]]:
    """{table: {"count", "fingerprint"}} over every column of both continuous tables."""
    out: dict[str, dict[str, Any]] = {}
    with conn.cursor() as cur:
        for table, sql in FINGERPRINT_SQL.items():
            cur.execute(sql)
            count, fingerprint = cur.fetchone()
            out[table] = {"count": count, "fingerprint": fingerprint}
    # End the read: with autocommit off it would stay idle in transaction, holding its locks on
    # both tables for the whole load.
    conn.rollback()
    return out


def verify_immutability(conn: Any, baseline: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check (iii): both continuous tables match the fingerprints taken before the load."""
    results = {}
    for table, now in table_fingerprints(conn).items():
        before = baseline.get(table, {})
        results[table] = {
            "count": now["count"],
            "expected_count": before.get("count"),
            "fingerprint": now["fingerprint"],
            "expected_fingerprint": before.get("fingerprint"),
            "passed": now == before,
        }
    return results


@contextmanager
def continuous_tables_unchanged(conn: Any) -> Iterator[dict[str, dict[str, Any]]]:
    """
    A step of a per-contract load: fingerprint ohlcv_1d and ohlcv_1d_raw before it writes and
    raise after it if either changed. It detects a change, it cannot stop one: the load's writes are
    committed by then. The daily pull writes both tables at 04:30 ET, so a load run across that
    time fails here without having changed anything; run backfills outside it.
    """
    baseline = table_fingerprints(conn)
    logger.info("Check (iii) baseline before the load: %s", json.dumps(baseline))
    yield baseline
    results = verify_immutability(conn, baseline)
    logger.info("Check (iii) after the load: %s", json.dumps(results))
    changed = [table for table, r in results.items() if not r["passed"]]
    if changed:
        raise RuntimeError(f"check (iii) failed: the load changed {', '.join(changed)}")


def verify_close_parity(conn: Any) -> dict[str, Any]:
    """Check (ii): Close price parity against continuous .v.0 series"""
    sql = """
    SELECT count(*) AS total_compared,
           count(*) FILTER (WHERE v.close IS DISTINCT FROM c.close) AS mismatch_count
    FROM futures_data.ohlcv_1d v
    JOIN futures_data.ohlcv_1d_raw r
      ON r.ts_event = v.time AND r.symbol = v.symbol
    JOIN futures_data.contract_ohlcv_1d c
      ON c.instrument_id = r.instrument_id AND c.date = v.time::date;
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        total, mismatches = cur.fetchone()
        result: dict[str, Any] = {
            "total_compared": total,
            "mismatch_count": mismatches,
            "passed": mismatches == 0,
        }
        if mismatches > 0:
            try:
                cur.execute(
                    """
                    SELECT v.time::date AS mismatch_date, count(*) AS cnt
                    FROM futures_data.ohlcv_1d v
                    JOIN futures_data.ohlcv_1d_raw r
                      ON r.ts_event = v.time AND r.symbol = v.symbol
                    JOIN futures_data.contract_ohlcv_1d c
                      ON c.instrument_id = r.instrument_id AND c.date = v.time::date
                    WHERE v.close IS DISTINCT FROM c.close
                    GROUP BY mismatch_date
                    ORDER BY mismatch_date;
                    """
                )
                result["mismatches_by_date"] = {str(d): cnt for d, cnt in cur.fetchall()}
            except Exception:
                pass
        return result


def verify_roll_coverage(conn: Any) -> dict[str, Any]:
    """Check (i): Roll date dual-contract coverage"""
    sql = """
    WITH roll_events AS (
        SELECT
            ts_event::date AS roll_date,
            symbol AS root,
            instrument_id AS new_id,
            LAG(instrument_id) OVER (PARTITION BY symbol ORDER BY ts_event) AS prior_id
        FROM futures_data.ohlcv_1d_raw
    ),
    rolls_only AS (
        SELECT *
        FROM roll_events
        WHERE prior_id IS NOT NULL AND prior_id <> new_id
    )
    SELECT
        count(*) AS total_rolls,
        count(*) FILTER (WHERE c_prior.instrument_id IS NOT NULL AND c_new.instrument_id IS NOT NULL) AS covered_rolls,
        count(*) FILTER (WHERE c_prior.instrument_id IS NULL OR c_new.instrument_id IS NULL) AS missing_rolls
    FROM rolls_only r
    LEFT JOIN futures_data.contract_ohlcv_1d c_prior
           ON c_prior.instrument_id = r.prior_id AND c_prior.date = r.roll_date
    LEFT JOIN futures_data.contract_ohlcv_1d c_new
           ON c_new.instrument_id = r.new_id AND c_new.date = r.roll_date;
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        total, covered, missing = cur.fetchone()
        return {
            "total_rolls": total,
            "covered_rolls": covered,
            "missing_rolls": missing,
            "passed": missing == 0 if total > 0 else False,
        }


def run_all_checks() -> None:
    conn = get_connection()
    logger.info("Connected to database. Running acceptance checks...")

    # Check (iii) is enforced by the loads themselves (continuous_tables_unchanged); here the
    # current fingerprints are printed for the record.
    logger.info("Check (iii) fingerprints now: %s", json.dumps(table_fingerprints(conn), indent=2))

    parity = verify_close_parity(conn)
    logger.info("Check (ii) Close Parity: %s", json.dumps(parity, indent=2))

    roll_cov = verify_roll_coverage(conn)
    logger.info("Check (i) Roll Coverage: %s", json.dumps(roll_cov, indent=2))

    conn.close()

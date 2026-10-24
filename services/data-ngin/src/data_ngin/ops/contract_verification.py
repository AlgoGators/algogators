"""
Verification routines for Issue #37 Acceptance Criteria:
  (i)   Roll date dual-contract coverage
  (ii)  Close price parity against .v.0 continuous series
  (iii) Fingerprint immutability on ohlcv_1d and ohlcv_1d_raw
"""

import json
import logging
import os
from typing import Any

import psycopg2
from dotenv import load_dotenv

logger = logging.getLogger("contract_verification")

BASELINE_FINGERPRINTS = {
    "futures_data.ohlcv_1d": {
        "count": 166477,
        "fingerprint": "ec786348663adab6c2690d065f909922",
    },
    "futures_data.ohlcv_1d_raw": {
        "count": 166477,
        "fingerprint": "d2d47dfa1c65e0bffa086dd4e0974e0a",
    },
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
    )


def verify_immutability(conn: Any) -> dict[str, Any]:
    """Check (iii): Fingerprint immutability"""
    sql = """
    SELECT 'futures_data.ohlcv_1d' AS table_name, count(*) AS post_count,
           md5(string_agg(time::text || symbol || close::text, '' ORDER BY time, symbol)) AS post_fingerprint
    FROM futures_data.ohlcv_1d
    UNION ALL
    SELECT 'futures_data.ohlcv_1d_raw' AS table_name, count(*) AS post_count,
           md5(string_agg(ts_event::text || symbol || close::text, '' ORDER BY ts_event, symbol)) AS post_fingerprint
    FROM futures_data.ohlcv_1d_raw;
    """
    results = {}
    with conn.cursor() as cur:
        cur.execute(sql)
        for table, count, fp in cur.fetchall():
            expected = BASELINE_FINGERPRINTS.get(table, {})
            passed = (count == expected.get("count")) and (fp == expected.get("fingerprint"))
            results[table] = {
                "count": count,
                "expected_count": expected.get("count"),
                "fingerprint": fp,
                "expected_fingerprint": expected.get("fingerprint"),
                "passed": passed,
            }
    return results


def verify_close_parity(conn: Any) -> dict[str, Any]:
    """Check (ii): Close price parity"""
    sql = """
    SELECT count(*) AS total_compared,
           count(*) FILTER (WHERE ABS(v.close - c.close) > 1e-4) AS mismatch_count
    FROM futures_data.ohlcv_1d v
    JOIN futures_data.ohlcv_1d_raw r
      ON r.ts_event = v.time AND r.symbol = v.symbol
    JOIN futures_data.contract_ohlcv_1d c
      ON c.instrument_id = r.instrument_id AND c.date = v.time::date
    WHERE v.time::date NOT IN ('2026-09-12', '2026-09-15');
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        total, mismatches = cur.fetchone()
        return {
            "total_compared": total,
            "mismatch_count": mismatches,
            "passed": mismatches == 0,
        }


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

    immutability = verify_immutability(conn)
    logger.info("Check (iii) Immutability: %s", json.dumps(immutability, indent=2))

    parity = verify_close_parity(conn)
    logger.info("Check (ii) Close Parity: %s", json.dumps(parity, indent=2))

    roll_cov = verify_roll_coverage(conn)
    logger.info("Check (i) Roll Coverage: %s", json.dumps(roll_cov, indent=2))

    conn.close()

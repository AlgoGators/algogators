#!/usr/bin/env python3
"""
Populate expiration dates in futures_data.contract_ohlcv_1d from definition archives.
Matches each contract on (instrument_id, raw_symbol) and its bar dates, never on the id alone.
"""

import logging
import os
import sys
import time
from datetime import date

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from data_ngin.domain.contract_definition import (
    build_definition_map,
    contract_expiration_spans,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("populate_contract_expirations")


def unresolved_spans_holding_values(
    rows: list[tuple[str, str, date, date | None]],
    spans: list[tuple[str, str, date, date, date | None]],
) -> list[tuple[str, str, date, date]]:
    """
    The spans no definition resolves whose stored rows already hold an expiration. Writing NULL
    over them would erase dates (the daily pull's, for contracts listed after the archives).
    """
    unresolved = [(str(i), s, first, last) for i, s, first, last, exp in spans if exp is None]
    keys = {(i, s) for i, s, _, _ in unresolved}
    dated: dict[tuple[str, str], list[date]] = {}
    for i, s, d, exp in rows:
        if exp is not None and (str(i), s) in keys:
            dated.setdefault((str(i), s), []).append(d)
    return [
        (i, s, first, last)
        for i, s, first, last in unresolved
        if any(first <= d <= last for d in dated.get((i, s), []))
    ]


def main() -> None:
    env_paths = [
        os.path.join(os.path.dirname(__file__), "../.env"),
        os.path.join(os.path.dirname(__file__), "../../../.env"),
    ]
    for p in env_paths:
        if os.path.exists(p):
            load_dotenv(p)
            break

    db_host = os.getenv("DB_HOST")
    db_port = int(os.getenv("DB_PORT", "5432"))
    db_name = os.getenv("DB_NAME")
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASSWORD")

    if not db_host or not db_user or not db_pass or not db_name:
        raise ValueError(
            "Database connection requires DB_HOST, DB_NAME, DB_USER, and DB_PASSWORD environment variables."
        )

    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=db_user,
        password=db_pass,
    )
    conn.autocommit = False
    cur = conn.cursor()

    def_tar_files = [
        "GLBX-20261004-RYHYB9WSYW.tar",
        "GLBX-20261004-URPWAJGS93.tar",
        "GLBX-20261004-63V7E8G5WJ.tar",
        "GLBX-20261004-LMVJ7XJPN9.tar",
        "GLBX-20261004-FLAXFVF8CJ.tar",
        "GLBX-20261004-96YHEC75N5.tar",
        "GLBX-20261004-XE8SBN5GWS.tar",
        "GLBX-20261004-D7QEK5DNF9.tar",
        "GLBX-20261004-JKCBL4AGAX.tar",
        "GLBX-20261004-VYXHGYQY3T.tar",
        "GLBX-20261004-KCD8YVVCAK.tar",
        "GLBX-20261004-TYQHTS39KK.tar",
        "GLBX-20261004-VHKMCQMTU3.tar",
    ]
    downloads_dir = os.path.expanduser("~/Downloads")
    paths = []
    for fname in def_tar_files:
        tar_path = os.path.join(downloads_dir, fname)
        if not os.path.exists(tar_path):
            raise FileNotFoundError(f"Definition archive not found: {tar_path}")
        paths.append(tar_path)

    t0 = time.time()
    index = build_definition_map(paths)
    logger.info(
        "Indexed %d (instrument_id, raw_symbol) definitions in %.2fs.", len(index), time.time() - t0
    )

    cur.execute(
        'SELECT instrument_id, raw_symbol, "date", expiration FROM futures_data.contract_ohlcv_1d;'
    )
    rows = cur.fetchall()
    spans = contract_expiration_spans(((i, s, d) for i, s, d, _ in rows), index)
    unresolved = [s for s in spans if s[4] is None]
    logger.info(
        "Resolved %d contract spans; %d without a fitting definition.", len(spans), len(unresolved)
    )
    held = unresolved_spans_holding_values(rows, spans)
    if held:
        conn.rollback()
        for inst_id, symbol, first, last in held[:50]:
            logger.error(
                "No definition fits %s %s (%s..%s), which holds a date",
                inst_id,
                symbol,
                first,
                last,
            )
        raise RuntimeError(
            f"refusing: {len(held)} span(s) with no fitting definition already hold an expiration; "
            "nothing written (add the archives that define them)"
        )

    cur.execute(
        """
        CREATE TEMP TABLE tmp_contract_expirations (
            instrument_id text NOT NULL,
            raw_symbol text NOT NULL,
            first_date date NOT NULL,
            last_date date NOT NULL,
            expiration date
        ) ON COMMIT DROP;
        """
    )
    execute_values(
        cur,
        "INSERT INTO tmp_contract_expirations VALUES %s;",
        spans,
        page_size=5000,
    )
    t_update_start = time.time()
    cur.execute(
        """
        UPDATE futures_data.contract_ohlcv_1d c
        SET expiration = t.expiration
        FROM tmp_contract_expirations t
        WHERE c.instrument_id = t.instrument_id
          AND c.raw_symbol = t.raw_symbol
          AND c."date" BETWEEN t.first_date AND t.last_date
          AND c.expiration IS DISTINCT FROM t.expiration;
        """
    )
    logger.info("Updated %d rows in %.2fs.", cur.rowcount, time.time() - t_update_start)
    cur.execute(
        """
        SELECT count(*) FROM futures_data.contract_ohlcv_1d
        WHERE expiration < "date";
        """
    )
    (expired_bars,) = cur.fetchone()
    if expired_bars:
        conn.rollback()
        raise RuntimeError(
            f"refusing: {expired_bars} bar(s) dated after their contract's expiration"
        )

    conn.commit()

    cur.execute("SELECT COUNT(*), COUNT(expiration) FROM futures_data.contract_ohlcv_1d;")
    total_count, non_null_count = cur.fetchone()
    logger.info(
        "Final verification: total_rows=%d, rows_with_expiration=%d (nulls=%d)",
        total_count,
        non_null_count,
        total_count - non_null_count,
    )

    conn.close()


if __name__ == "__main__":
    main()

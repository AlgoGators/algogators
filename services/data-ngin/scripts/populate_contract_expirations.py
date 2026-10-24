#!/usr/bin/env python3
"""
Populate expiration dates in futures_data.contract_ohlcv_1d from definition archives.
Maps all instrument_ids to their official vendor expiration date.
"""

import logging
import os
import tarfile
import time
from datetime import UTC, datetime

import databento as db
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("populate_contract_expirations")


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

    cur.execute("SELECT DISTINCT instrument_id FROM futures_data.contract_ohlcv_1d;")
    db_inst_ids = {str(r[0]) for r in cur.fetchall()}
    logger.info("Found %d distinct instrument_ids in DB.", len(db_inst_ids))

    def_tar_files = [
        "GLBX-20261004-RYHYB9WSYW.tar",
        "GLBX-20261004-URPWAJGS93.tar",
        "GLBX-20261004-63V7E8G5WJ.tar",
        "GLBX-20261004-LMVJ7XJPN9.tar",
        "GLBX-20261004-FLAXFVF8CJ.tar",
        "GLBX-20261004-96YHEC75N5.tar",
        "GLBX-20261004-XE8SBN5GWS.tar",
        "GLBX-20261004-F7BGLFMDHN.tar",
        "GLBX-20261004-D7QEK5DNF9.tar",
        "GLBX-20261004-JKCBL4AGAX.tar",
        "GLBX-20261004-VYXHGYQY3T.tar",
        "GLBX-20261004-KCD8YVVCAK.tar",
        "GLBX-20261004-TYQHTS39KK.tar",
        "GLBX-20261004-VHKMCQMTU3.tar",
    ]
    downloads_dir = os.path.expanduser("~/Downloads")

    inst_map: dict[str, str] = {}
    t0 = time.time()
    for fname in def_tar_files:
        tar_path = os.path.join(downloads_dir, fname)
        if not os.path.exists(tar_path):
            logger.warning("Archive not found: %s", tar_path)
            continue
        logger.info("Extracting definitions from %s...", fname)
        with tarfile.open(tar_path, "r") as tar:
            for m in tar.getmembers():
                if m.name.endswith(".definition.dbn.zst"):
                    f = tar.extractfile(m)
                    if not f:
                        continue
                    arr = db.DBNStore.from_bytes(f.read()).to_ndarray()
                    for rec in arr:
                        exp_ns = rec["expiration"]
                        if exp_ns > 0:
                            exp_date = datetime.fromtimestamp(exp_ns / 1e9, UTC).date()
                            inst_map[str(rec["instrument_id"])] = str(exp_date)

    t1 = time.time()
    logger.info("Extracted %d definitions in %.2fs.", len(inst_map), t1 - t0)

    matched = db_inst_ids.intersection(inst_map.keys())
    logger.info(
        "Matched %d / %d (%.2f%%) DB instrument IDs.",
        len(matched),
        len(db_inst_ids),
        len(matched) / len(db_inst_ids) * 100,
    )

    # Filter to only the instrument_ids that exist in the DB
    update_data = [(inst_id, inst_map[inst_id]) for inst_id in matched]

    logger.info("Creating temporary table and staging %d expiration records...", len(update_data))
    cur.execute(
        """
        CREATE TEMP TABLE tmp_instrument_expirations (
            instrument_id text PRIMARY KEY,
            expiration date NOT NULL
        ) ON COMMIT DROP;
        """
    )
    execute_values(
        cur,
        "INSERT INTO tmp_instrument_expirations (instrument_id, expiration) VALUES %s;",
        update_data,
        page_size=5000,
    )

    logger.info("Updating futures_data.contract_ohlcv_1d in batch...")
    t_update_start = time.time()
    cur.execute(
        """
        UPDATE futures_data.contract_ohlcv_1d c
        SET expiration = t.expiration
        FROM tmp_instrument_expirations t
        WHERE c.instrument_id = t.instrument_id
          AND (c.expiration IS NULL OR c.expiration != t.expiration);
        """
    )
    updated_rows = cur.rowcount
    t_update_end = time.time()
    logger.info("Updated %d rows in %.2fs.", updated_rows, t_update_end - t_update_start)

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

#!/usr/bin/env python3
"""
Historical loader for individual per-contract daily futures bars (AlgoGators/algogators#37).
Streams monthly .ohlcv-1d.dbn.zst files from Databento tar archives, filters out calendar spreads,
extracts and resolves expiration dates, and batch upserts outright contract bars into
futures_data.contract_ohlcv_1d.
"""

import argparse
import glob
import logging
import os
import sys
import tarfile
from datetime import date, datetime
from typing import Any

import databento as db
from dotenv import load_dotenv

# Ensure local imports work whether run from repo root or package dir
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../platform/db/src"))
)

from data_ngin.domain.contract_definition import (
    extract_definitions_from_dbn_bytes,
    extract_definitions_from_tar,
    get_cme_expiration_fallback,
)
from data_ngin.domain.contract_filter import is_outright_contract, parse_contract_symbol
from data_ngin.infrastructure.repository.ohlcv_repository import OhlcvRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("load_historical_contract_bars")


def resolve_expiration(
    inst_id: str,
    raw_sym: str,
    bar_date: date,
    root: str,
    definitions: dict[str, Any] | None,
) -> date | None:
    """Resolve expiration date from definition lookup or fallback CME specification."""
    if definitions and inst_id in definitions:
        val = definitions[inst_id]
        if isinstance(val, date):
            return val
        if isinstance(val, dict):
            return val.get("expiration")

    try:
        _, month_code, year_str = parse_contract_symbol(raw_sym)
        if len(year_str) == 1:
            decade = (bar_date.year // 10) * 10
            full_year = decade + int(year_str)
            if full_year < bar_date.year - 1:
                full_year += 10
            elif full_year > bar_date.year + 8:
                full_year -= 10
        elif len(year_str) == 2:
            full_year = 2000 + int(year_str)
        else:
            full_year = int(year_str)
        return get_cme_expiration_fallback(root, month_code, full_year)
    except Exception:
        return None


def process_dbn_stream(
    dbn_bytes: bytes,
    repo: OhlcvRepository,
    definitions: dict[str, Any] | None = None,
    chunk_size: int = 5000,
) -> tuple[int, int]:
    """
    Parse a DBN stream, filter outright contracts, and batch insert into DB.
    Returns (outrights_inserted, spreads_dropped).
    """
    store = db.DBNStore.from_bytes(dbn_bytes)
    df = store.to_df()
    if df.empty:
        return 0, 0

    records: list[dict[str, Any]] = []
    spreads_dropped = 0

    # Ensure index (ts_event) is accessible as a column or series
    df = df.reset_index()

    for row in df.itertuples(index=False):
        raw_sym = getattr(row, "symbol", None)
        if not raw_sym or not is_outright_contract(str(raw_sym)):
            spreads_dropped += 1
            continue

        try:
            root, _, _ = parse_contract_symbol(str(raw_sym))
        except ValueError:
            spreads_dropped += 1
            continue

        ts_event = row.ts_event
        if isinstance(ts_event, datetime):
            bar_date = ts_event.date()
        else:
            bar_date = datetime.fromisoformat(str(ts_event)).date()

        inst_id = str(row.instrument_id)

        # Resolve expiration date from definition mapping or fallback calculation
        expiration = resolve_expiration(inst_id, str(raw_sym), bar_date, root, definitions)

        records.append(
            {
                "instrument_id": inst_id,
                "date": bar_date,
                "root": root,
                "raw_symbol": str(raw_sym),
                "expiration": expiration,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": int(row.volume),
            }
        )

    if records:
        repo.insert_contract_bars(records, chunk_size=chunk_size)

    return len(records), spreads_dropped


def process_tar_archive(
    tar_path: str,
    repo: OhlcvRepository,
    definitions: dict[str, Any] | None = None,
    chunk_size: int = 5000,
) -> tuple[int, int]:
    """
    Process all .ohlcv-1d.dbn.zst members inside a tar archive.
    If the archive also contains .definition.dbn.zst members, pre-loads them into definitions.
    """
    logger.info("Opening archive: %s", tar_path)
    total_outrights = 0
    total_spreads = 0

    defs = dict(definitions) if definitions else {}

    with tarfile.open(tar_path, "r") as tar:
        # Pre-load definition files if present in this archive
        def_members = [m for m in tar.getmembers() if m.name.endswith(".definition.dbn.zst")]
        if def_members:
            logger.info("Pre-loading %d definition files from %s", len(def_members), tar_path)
            for dm in def_members:
                f_def = tar.extractfile(dm)
                if f_def:
                    defs.update(extract_definitions_from_dbn_bytes(f_def.read()))

        ohlcv_members = [m for m in tar.getmembers() if m.name.endswith(".ohlcv-1d.dbn.zst")]
        logger.info("Found %d OHLCV DBN files in archive", len(ohlcv_members))

        for idx, member in enumerate(ohlcv_members, start=1):
            f = tar.extractfile(member)
            if f is None:
                continue
            data_bytes = f.read()
            outrights, spreads = process_dbn_stream(
                data_bytes, repo, definitions=defs, chunk_size=chunk_size
            )
            total_outrights += outrights
            total_spreads += spreads
            logger.info(
                "[%d/%d] %s: inserted %d outright bars, dropped %d spreads",
                idx,
                len(ohlcv_members),
                os.path.basename(member.name),
                outrights,
                spreads,
            )

    return total_outrights, total_spreads


def main() -> None:
    parser = argparse.ArgumentParser(description="Load historical per-contract futures bars.")
    parser.add_argument(
        "--tar", nargs="+", help="Path(s) to OHLCV .tar archive(s) or glob pattern."
    )
    parser.add_argument(
        "--definition-tar", nargs="+", help="Path(s) to definition .tar archive(s) or glob pattern."
    )
    parser.add_argument("--chunk-size", type=int, default=5000, help="Batch upsert chunk size.")
    args = parser.parse_args()

    # Find .env file
    env_paths = [
        os.path.join(os.path.dirname(__file__), "../.env"),
        os.path.join(os.path.dirname(__file__), "../../../.env"),
    ]
    for p in env_paths:
        if os.path.exists(p):
            load_dotenv(p)
            break

    repo = OhlcvRepository(
        config={
            "database": {
                "target_schema": "futures_data",
                "table": "contract_ohlcv_1d",
            }
        }
    )
    repo.connect()

    # Pre-load definition archives if supplied or in Downloads
    definitions: dict[str, Any] = {}
    def_tar_files: list[str] = []
    if args.definition_tar:
        for dt in args.definition_tar:
            def_tar_files.extend(glob.glob(dt))
    else:
        # Check standard downloads for definition archives
        known_def_tars = [
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
        for dt_name in known_def_tars:
            p = os.path.join(downloads_dir, dt_name)
            if os.path.exists(p):
                def_tar_files.append(p)

    if def_tar_files:
        logger.info("Pre-loading definitions from %d archives...", len(def_tar_files))
        for dtf in def_tar_files:
            m = extract_definitions_from_tar(dtf)
            definitions.update(m)
        logger.info("Loaded %d unique instrument definition records.", len(definitions))

    tar_files: list[str] = []
    if args.tar:
        for t in args.tar:
            tar_files.extend(glob.glob(t))

    if not tar_files:
        default_pattern = os.path.expanduser("~/Downloads/GLBX-*.tar")
        tar_files = sorted(glob.glob(default_pattern))

    if not tar_files:
        logger.error("No tar files found matching pattern. Please supply --tar argument.")
        sys.exit(1)

    logger.info("Processing %d tar archives: %s", len(tar_files), tar_files)
    grand_outrights = 0
    grand_spreads = 0

    for tf in tar_files:
        try:
            outrights, spreads = process_tar_archive(
                tf, repo, definitions=definitions, chunk_size=args.chunk_size
            )
            grand_outrights += outrights
            grand_spreads += spreads
        except Exception as e:
            logger.exception("Failed processing archive %s: %s", tf, e)

    logger.info(
        "HISTORICAL LOAD COMPLETE: Total Outright Bars Inserted = %d | Total Spreads Dropped = %d",
        grand_outrights,
        grand_spreads,
    )
    repo.close()


if __name__ == "__main__":
    main()

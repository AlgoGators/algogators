#!/usr/bin/env python3
"""
Historical loader for individual per-contract daily futures bars (AlgoGators/algogators#37).
Streams monthly .ohlcv-1d.dbn.zst files from Databento tar archives, filters out calendar spreads,
and batch upserts outright contract bars into futures_data.contract_ohlcv_1d.
"""

import argparse
import glob
import logging
import os
import sys
import tarfile
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

import databento as db
from dotenv import load_dotenv

# Ensure local imports work whether run from repo root or package dir
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../platform/db/src")))

from data_ngin.domain.contract_filter import is_outright_contract, parse_contract_symbol
from data_ngin.infrastructure.repository.ohlcv_repository import OhlcvRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("load_historical_contract_bars")


def process_dbn_stream(
    dbn_bytes: bytes,
    repo: OhlcvRepository,
    definitions: Optional[Dict[str, Any]] = None,
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

    records: List[Dict[str, Any]] = []
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

        ts_event = getattr(row, "ts_event")
        if isinstance(ts_event, datetime):
            bar_date = ts_event.date()
        else:
            bar_date = datetime.fromisoformat(str(ts_event)).date()

        inst_id = str(getattr(row, "instrument_id"))
        
        # Resolve expiration date if definition record is available
        expiration = None
        if definitions and inst_id in definitions:
            expiration = definitions[inst_id].get("expiration")

        records.append({
            "instrument_id": inst_id,
            "date": bar_date,
            "root": root,
            "raw_symbol": str(raw_sym),
            "expiration": expiration,
            "open": float(getattr(row, "open")),
            "high": float(getattr(row, "high")),
            "low": float(getattr(row, "low")),
            "close": float(getattr(row, "close")),
            "volume": int(getattr(row, "volume")),
        })

    if records:
        repo.insert_contract_bars(records, chunk_size=chunk_size)

    return len(records), spreads_dropped


def process_tar_archive(tar_path: str, repo: OhlcvRepository, chunk_size: int = 5000) -> tuple[int, int]:
    """
    Process all .ohlcv-1d.dbn.zst members inside a tar archive.
    """
    logger.info("Opening archive: %s", tar_path)
    total_outrights = 0
    total_spreads = 0

    with tarfile.open(tar_path, "r") as tar:
        members = [m for m in tar.getmembers() if m.name.endswith(".ohlcv-1d.dbn.zst")]
        logger.info("Found %d OHLCV DBN files in archive", len(members))

        for idx, member in enumerate(members, start=1):
            f = tar.extractfile(member)
            if f is None:
                continue
            data_bytes = f.read()
            outrights, spreads = process_dbn_stream(data_bytes, repo, chunk_size=chunk_size)
            total_outrights += outrights
            total_spreads += spreads
            logger.info(
                "[%d/%d] %s: inserted %d outright bars, dropped %d spreads",
                idx,
                len(members),
                os.path.basename(member.name),
                outrights,
                spreads,
            )

    return total_outrights, total_spreads


def main() -> None:
    parser = argparse.ArgumentParser(description="Load historical per-contract futures bars.")
    parser.add_argument("--tar", nargs="+", help="Path(s) to .tar archive(s) or glob pattern.")
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

    repo = OhlcvRepository(config={
        "database": {
            "target_schema": "futures_data",
            "table": "contract_ohlcv_1d",
        }
    })
    repo.connect()

    tar_files: List[str] = []
    if args.tar:
        for t in args.tar:
            tar_files.extend(glob.glob(t))

    if not tar_files:
        # Default to Downloads directory if present
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
            outrights, spreads = process_tar_archive(tf, repo, chunk_size=args.chunk_size)
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

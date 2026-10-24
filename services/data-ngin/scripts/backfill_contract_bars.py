#!/usr/bin/env python3
"""
CLI utility to backfill per-contract futures bars for the 40 roots across a date range.
Resolves outright contracts, fills expiration dates, and upserts into futures_data.contract_ohlcv_1d.
"""

import argparse
import logging
import os
import sys
from datetime import date, timedelta

# Ensure local imports resolve
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../platform/db/src"))
)

from data_ngin.application.close_pipeline import CONTRACT_ROOTS, run_contract_close_pipeline
from data_ngin.utils.dynamic_loader import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("backfill_contract_bars")


def daterange(start_date: date, end_date: date):
    for n in range(int((end_date - start_date).days) + 1):
        yield start_date + timedelta(n)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill daily per-contract futures bars into contract_ohlcv_1d."
    )
    parser.add_argument("--start", type=str, required=True, help="Start date (YYYY-MM-DD).")
    parser.add_argument("--end", type=str, required=True, help="End date (YYYY-MM-DD).")
    parser.add_argument(
        "--config", type=str, default="new_config.yaml", help="Configuration file name."
    )
    parser.add_argument(
        "--roots", nargs="+", default=None, help="Explicit list of roots (defaults to 40 roots)."
    )
    parser.add_argument("--api-key", type=str, default=None, help="Optional Databento API key.")
    args = parser.parse_args()

    if args.api_key:
        os.environ["DATABENTO_API_KEY"] = args.api_key

    config_path = os.path.join(os.path.dirname(__file__), "../src/data_ngin/config", args.config)
    config = load_config(config_path)

    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)
    target_roots = args.roots or list(CONTRACT_ROOTS)

    logger.info(
        "Starting per-contract backfill: %s to %s for %d roots",
        start_d,
        end_d,
        len(target_roots),
    )

    total_pulled = 0
    total_skipped = 0

    for current_d in daterange(start_d, end_d):
        result = run_contract_close_pipeline(config, current_d, roots=target_roots)
        if result.get("skipped"):
            total_skipped += 1
            logger.info("Date %s skipped (non-trading day).", current_d)
        else:
            total_pulled += 1
            logger.info(
                "Date %s: %s (%d/%d loaded, missing: %s)",
                current_d,
                result["status"],
                result["loaded"],
                result["expected"],
                result["missing"],
            )

    logger.info(
        "Backfill complete: %d days processed (%d active trading days, %d skipped).",
        int((end_d - start_d).days) + 1,
        total_pulled,
        total_skipped,
    )


if __name__ == "__main__":
    main()

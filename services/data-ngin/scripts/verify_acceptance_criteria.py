#!/usr/bin/env python3
"""
CLI runner for Issue #37 Acceptance Criteria.
"""

import logging
import os
import sys

# Ensure local imports resolve
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../platform/db/src")))

from data_ngin.ops.contract_verification import run_all_checks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")

if __name__ == "__main__":
    run_all_checks()

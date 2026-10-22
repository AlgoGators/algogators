"""Shim: the Kalshi tape reader now lives in ``predict_ngin.tapes.kalshi`` (lake-aware, adds ``count_fp``).

Kept so existing ``from src.trading.arbitrage.kalshi_tape import tape_sql`` imports keep working unchanged.
"""
from __future__ import annotations

from pathlib import Path

from predict_ngin.tapes.kalshi import (  # noqa: F401
    ALL_COLUMNS,
    COLUMNS,
    CUTOVER_TS,
    EXTRA_COLUMNS,
    ext_dir,
    ext_files,
    lake_ready,
    old_dir,
    old_files,
    read_tape,
    tape_end_ts,
    tape_sql,
)

ROOT = Path(__file__).resolve().parents[3]
OLD_DIR = old_dir()
EXT_DIR = ext_dir()

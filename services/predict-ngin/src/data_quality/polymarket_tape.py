"""Shim + builder helpers. The fill-tape *reader* now lives in ``predict_ngin.tapes.polymarket`` (lake-aware).

Existing ``from src.data_quality.polymarket_tape import clean_trades_sql`` imports keep working unchanged. The
builder-side helpers below (``to_ext_frame``, ``utc_day``) stay here because they need the pmfix token map.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from predict_ngin.tapes.polymarket import (  # noqa: F401
    _OLD_EXPR,
    _SQL_TYPES,
    ALL_COLUMNS,
    COMMON_COLUMNS,
    CUTOVER_TS,
    EXT_COLUMNS,
    EXT_ONLY,
    EXT_START_TS,
    LAKE_ONLY,
    OLD_ONLY,
    clean_trades_sql,
    ext_dir,
    ext_files,
    ext_start_ts,
    lake_ready,
    old_dir,
    old_files,
    read_clean_trades,
    tape_end_ts,
)

from .polymarket_tokens import TokenMap, normalize_trades

ROOT = Path(__file__).resolve().parents[2]
OLD_DIR = old_dir()
EXT_DIR = ext_dir()


# ----------------------------------------------------------------------------------------------- builder side


def utc_day(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d")


def to_ext_frame(df: pd.DataFrame, tm: TokenMap, source: str, anchors: pd.DataFrame | None = None) -> pd.DataFrame:
    """HF-2026 ``trades.parquet`` rows (17 columns) -> ext rows, YES-normalised with the pmfix helper.

    From :data:`CUTOVER_TS` on, the release's timestamps are true block times (checked against the pmfix
    block anchors), so ``ts_chain = ts_local = timestamp``. Before it they are not (typically ~100 s off,
    up to ~11 h), so pass ``anchors`` (``polygon_blocks.load_anchors()``) when converting earlier rows:
    those get ``ts_chain`` from the block number, NaN and ``timestamp_unreliable`` outside the anchors.
    Without ``anchors`` earlier rows keep the release time (fine for the unserved seam overlap only).
    """
    n = normalize_trades(df, tm, schema="onchain")
    o0 = tm.frame.loc[tm.frame.outcome_index == 0].set_index("condition_id")["outcome_label"]
    o0 = o0[~o0.index.duplicated()]
    ts = n["timestamp"].astype("int64")
    ts_chain = ts.to_numpy(dtype="float64")
    early = ts.to_numpy() < CUTOVER_TS
    if anchors is not None and early.any():
        from .polygon_blocks import block_to_ts

        ts_chain = np.where(early, block_to_ts(n["block_number"].to_numpy(), anchors), ts_chain)
    out = pd.DataFrame({
        "block_number": n["block_number"].astype("int64"),
        "ts_local": ts,
        "ts_chain": ts_chain,
        "timestamp_unreliable": np.isnan(ts_chain),
        "transaction_hash": n["transaction_hash"].astype(str).str.lower(),
        "market_id": n["market_id"].astype(str),
        "condition_id": n["condition_id"].astype(str),
        "token_id": n["token_id"].astype(str).where(n["token_id"].notna(), None),
        "outcome_index": n["outcome_index"].astype("float32"),
        "outcome0_label": n["condition_id"].map(o0),
        "price_raw": n["price"].astype("float64"),
        "yes_price": n["yes_price"].astype("float64"),
        "yes_side": n["yes_side"],
        "is_taker_order_row": n["is_taker_order_row"].astype(bool),
        "usd_amount": n["usd_amount"].astype("float64"),
        "token_amount": n["token_amount"].astype("float64"),
        "maker": n["maker"].astype(str).str.lower(),
        "taker": n["taker"].astype(str).str.lower(),
        "token_conflict": n["token_conflict"].astype(bool),
        "log_index": n["log_index"].astype("int64"),
        "contract": n["contract"].astype(str),
        "event_id": n["event_id"].astype(str),
        "nonusdc_side": n["nonusdc_side"].astype(str),
        "maker_direction": n["maker_direction"].astype(str),
        "taker_direction": n["taker_direction"].astype(str),
        "source": source,
    })
    return out.sort_values(["block_number", "log_index"], kind="mergesort").reset_index(drop=True)

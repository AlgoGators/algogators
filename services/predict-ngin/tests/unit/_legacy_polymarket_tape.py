# FROZEN COPY (2026-10-17) of src/data_quality/polymarket_tape.py before it became a shim. Never edit (D2).
"""Full Polymarket fill tape = polymarket_clean/trades (pmfix build of the Feb-2026 HF snapshot) + trades_ext.

``data/polymarket/trades.parquet`` (HF ``SII-WANGZJ/Polymarket_data``, first snapshot) ends 2025-12-30 05:55 UTC and
was YES-normalised by ``scripts/research/pmfix_build_clean.py`` into ``data/polymarket_clean/trades/part-*.parquet``.
``scripts/data/polymarket_extend.py`` appends newer fills to ``data/polymarket_clean/trades_ext/`` with the
same pmfix helper (``normalize_trades``), from the HF 2026-10-05 release (``hf/``) and later sources (``tail/``).
The originals are never rewritten.

Old rows are taken with ``ts_chain < CUTOVER_TS`` and ext rows with ``ts_chain >= CUTOVER_TS``, so there are no
duplicates. With no ext files the old clean tape is returned unchanged.

Format differences that the union hides (old rows are converted on read):
* ``transaction_hash`` is ``0x``-prefixed lowercase hex in both branches (old files stored it without ``0x``).
* ``maker`` / ``taker`` are lowercase in both branches (old files stored checksummed addresses).
* ``order_hash`` exists only in the old branch (NULL for ext rows); ``log_index``, ``contract``, ``event_id``,
  ``nonusdc_side``, ``maker_direction``, ``taker_direction`` only in the ext branch (NULL for old rows).
* The old branch contains the taker order's aggregate OrderFilled rows (``is_taker_order_row``); the HF 2026-10-05
  ``trades.parquet`` omits them, so ext rows from ``hf/`` are all maker fills. **Filter
  ``NOT is_taker_order_row`` for trade counts and volume** - that is consistent across the seam.

Usage (DuckDB)::

    from src.data_quality.polymarket_tape import clean_trades_sql, tape_end_ts
    con.execute(f"CREATE VIEW t AS {clean_trades_sql(start_ts=..., maker_rows_only=True)}")
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from src.data_quality.polymarket_tokens import TokenMap, normalize_trades  # frozen copy: absolute import

ROOT = Path(__file__).resolve().parents[2]
OLD_DIR = ROOT / "data" / "polymarket_clean" / "trades"
EXT_DIR = ROOT / "data" / "polymarket_clean" / "trades_ext"
# Seam: the old snapshot is complete through 2025-12-29 (verified day by day against the 2026-10-05 release in
# scripts/data/polymarket_extend.py seam); its last day 2025-12-30 is partial (ends 05:55 UTC).
CUTOVER_TS = 1767052800  # 2025-12-30 00:00:00 UTC
EXT_START_TS = 1766448000  # 2025-12-23 00:00:00 UTC: ext keeps a 7-day overlap for seam checks (not served)

COMMON_COLUMNS = ["block_number", "ts_local", "ts_chain", "timestamp_unreliable", "transaction_hash",
                  "market_id", "condition_id", "token_id", "outcome_index", "outcome0_label", "price_raw",
                  "yes_price", "yes_side", "is_taker_order_row", "usd_amount", "token_amount", "maker", "taker",
                  "token_conflict"]
OLD_ONLY = ["order_hash"]
EXT_ONLY = ["log_index", "contract", "event_id", "nonusdc_side", "maker_direction", "taker_direction", "source"]
ALL_COLUMNS = COMMON_COLUMNS + OLD_ONLY + EXT_ONLY
EXT_COLUMNS = COMMON_COLUMNS + EXT_ONLY

_OLD_EXPR = {
    "transaction_hash": "CASE WHEN transaction_hash LIKE '0x%' THEN lower(transaction_hash) "
                        "ELSE '0x' || lower(transaction_hash) END",
    "maker": "lower(maker)",
    "taker": "lower(taker)",
}
_SQL_TYPES = {"log_index": "BIGINT", "order_hash": "VARCHAR"}


def _q(p: Path | str) -> str:
    return "'" + str(p).replace("\\", "/").replace("'", "''") + "'"


def ext_files(ext_dir: Path | str = EXT_DIR) -> List[Path]:
    """Finished ext partitions (``hf/part-*.parquet``, ``tail/fills_YYYY-MM-DD.parquet``); tmp files excluded."""
    d = Path(ext_dir)
    if not d.is_dir():
        return []
    return sorted(list(d.glob("hf/part-*.parquet")) + list(d.glob("tail/fills_????-??-??.parquet")))


def old_files(old_dir: Path | str = OLD_DIR) -> List[Path]:
    return sorted(Path(old_dir).glob("part-*.parquet"))


def clean_trades_sql(columns: Optional[Sequence[str]] = None, start_ts: Optional[float] = None,
                     end_ts: Optional[float] = None, maker_rows_only: bool = False,
                     old_dir: Path | str = OLD_DIR, ext_dir: Path | str = EXT_DIR,
                     cutover_ts: int = CUTOVER_TS) -> str:
    """DuckDB SELECT over old clean + ext; optional ``[start_ts, end_ts)`` filter on ``ts_chain``.

    ``maker_rows_only`` drops taker-order aggregate rows (use for counts/volume).
    """
    cols = list(columns or ALL_COLUMNS)
    unknown = [c for c in cols if c not in ALL_COLUMNS]
    if unknown:
        raise ValueError(f"unknown columns {unknown}")
    rng = ""
    if start_ts is not None:
        rng += f" AND ts_chain >= {float(start_ts)!r}"
    if end_ts is not None:
        rng += f" AND ts_chain < {float(end_ts)!r}"
    if maker_rows_only:
        rng += " AND NOT is_taker_order_row"

    def sel(branch: str) -> str:
        out = []
        for c in cols:
            if branch == "old":
                if c in EXT_ONLY:
                    out.append(f"CAST(NULL AS {_SQL_TYPES.get(c, 'VARCHAR')}) AS \"{c}\"")
                else:
                    out.append(f"{_OLD_EXPR[c]} AS \"{c}\"" if c in _OLD_EXPR else f"\"{c}\"")
            else:
                if c in OLD_ONLY:
                    out.append(f"CAST(NULL AS {_SQL_TYPES.get(c, 'VARCHAR')}) AS \"{c}\"")
                else:
                    out.append(f"\"{c}\"")
        return ", ".join(out)

    parts = []
    olds, exts = old_files(old_dir), ext_files(ext_dir)
    if olds and (start_ts is None or start_ts < cutover_ts or not exts):
        lst = ", ".join(_q(f) for f in olds)
        bound = f"ts_chain < {cutover_ts}" if exts else "TRUE"
        parts.append(f"SELECT {sel('old')} FROM read_parquet([{lst}]) WHERE {bound}{rng}")
    if exts and (end_ts is None or end_ts > cutover_ts):
        lst = ", ".join(_q(f) for f in exts)
        parts.append(f"SELECT {sel('ext')} FROM read_parquet([{lst}], union_by_name=true) "
                     f"WHERE ts_chain >= {cutover_ts}{rng}")
    if not parts:
        raise FileNotFoundError(f"no Polymarket clean trade files in {old_dir} or {ext_dir}")
    return " UNION ALL ".join(parts)


def tape_end_ts(ext_dir: Path | str = EXT_DIR) -> Optional[int]:
    """Exclusive end (UTC seconds) of contiguous ext coverage, from ``coverage.json`` written by the builder."""
    p = Path(ext_dir) / "coverage.json"
    if not p.exists():
        return None
    return int(json.loads(p.read_text())["end_ts"])


def read_clean_trades(columns: Optional[Sequence[str]] = None, start_ts: Optional[float] = None,
                      end_ts: Optional[float] = None, **kw) -> pd.DataFrame:
    """Materialise a (filtered) slice as pandas. Prefer ``clean_trades_sql`` inside DuckDB for big reads."""
    import duckdb

    return duckdb.sql(clean_trades_sql(columns, start_ts, end_ts, **kw)).df()


# ----------------------------------------------------------------------------------------------- builder side


def utc_day(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d")


def to_ext_frame(df: pd.DataFrame, tm: TokenMap, source: str) -> pd.DataFrame:
    """HF-2026 ``trades.parquet`` rows (17 columns) -> ext rows, YES-normalised with the pmfix helper.

    Timestamps in the 2026 release are true block times (checked against the pmfix block anchors), so
    ``ts_chain = ts_local = timestamp``.
    """
    n = normalize_trades(df, tm, schema="onchain")
    o0 = tm.frame.loc[tm.frame.outcome_index == 0].set_index("condition_id")["outcome_label"]
    o0 = o0[~o0.index.duplicated()]
    ts = n["timestamp"].astype("int64")
    out = pd.DataFrame({
        "block_number": n["block_number"].astype("int64"),
        "ts_local": ts,
        "ts_chain": ts.astype("float64"),
        "timestamp_unreliable": np.zeros(len(n), dtype=bool),
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

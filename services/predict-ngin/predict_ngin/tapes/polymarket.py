"""Polymarket fill tape: every on-chain fill since Nov 2022, YES-normalised, one SQL source for
DuckDB.

Two layouts on disk, same rows:

* **lake** (preferred, once built):
  ``<data>/lake/polymarket/fills/year=YYYY/month=MM/*.parquet``, sorted inside each file by
  category, event, market, time, with extra ``category`` / ``is_updown`` columns; served when
  ``fills_state.json`` there says the build is complete (``scripts/data/build_pm_lake.py``).
* **legacy**: ``<data>/polymarket_clean/trades/part-*.parquet`` (pmfix build of the Feb-2026 HF
  snapshot) + ``<data>/polymarket_clean/trades_ext/{hf,tail}/`` (later fills). Old rows before
  :data:`CUTOVER_TS`, ext rows from it on, so there are no duplicates. A full-history ext build
  (new setups: the Feb-2026 snapshot is no longer published) records ``start_ts`` 0 in its
  ``coverage.json`` and then serves every row.

Conventions the union hides (old rows are converted on read): ``transaction_hash`` is
``0x``-prefixed lowercase in both; ``maker`` / ``taker`` lowercase; ``order_hash`` only in old
rows; ``log_index``, ``contract``, ``event_id``, ``nonusdc_side``, ``maker_direction``,
``taker_direction``, ``source`` only in ext rows (the lake fills ``event_id`` for old rows from the
market list). Old rows include the taker order's aggregate rows (``is_taker_order_row``), ext rows
do not: **filter ``NOT is_taker_order_row`` for counts and volume** (``maker_rows_only=True``).

    from predict_ngin.tapes.polymarket import clean_trades_sql
    con.execute(f"CREATE VIEW t AS {clean_trades_sql(start_ts=..., maker_rows_only=True)}")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Sequence

from ..shared.duck import sql_path
from ..shared.paths import data_root
from ._lake import files_in, is_complete, month_dirs

# Seam: the old snapshot is complete through 2025-12-29; its last day 2025-12-30 is partial
# (ends 05:55 UTC).
CUTOVER_TS = 1767052800  # 2025-12-30 00:00:00 UTC
EXT_START_TS = (
    1766448000  # 2025-12-23 00:00:00 UTC: ext keeps a 7-day overlap for seam checks (not served)
)

COMMON_COLUMNS = [
    "block_number",
    "ts_local",
    "ts_chain",
    "timestamp_unreliable",
    "transaction_hash",
    "market_id",
    "condition_id",
    "token_id",
    "outcome_index",
    "outcome0_label",
    "price_raw",
    "yes_price",
    "yes_side",
    "is_taker_order_row",
    "usd_amount",
    "token_amount",
    "maker",
    "taker",
    "token_conflict",
]
OLD_ONLY = ["order_hash"]
EXT_ONLY = [
    "log_index",
    "contract",
    "event_id",
    "nonusdc_side",
    "maker_direction",
    "taker_direction",
    "source",
]
ALL_COLUMNS = COMMON_COLUMNS + OLD_ONLY + EXT_ONLY
EXT_COLUMNS = COMMON_COLUMNS + EXT_ONLY
LAKE_ONLY = ["category", "is_updown"]  # requestable only when the lake is served

_OLD_EXPR = {
    "transaction_hash": "CASE WHEN transaction_hash LIKE '0x%' THEN lower(transaction_hash) "
    "ELSE '0x' || lower(transaction_hash) END",
    "maker": "lower(maker)",
    "taker": "lower(taker)",
}
_SQL_TYPES = {"log_index": "BIGINT", "order_hash": "VARCHAR"}


# Hugging Face release the ext tape is built from (SII-WANGZJ/Polymarket_data, data through
# 2026-10-04 UTC). The Feb-2026 release behind the old tape is no longer on the Hub, so a new setup
# builds the whole history from this one (``polymarket_extend.py hf --full``).
HF_REPO = "SII-WANGZJ/Polymarket_data"
HF_REV = "73bc1adc43b05709d2cde3bf41ef4111eda3f4fc"  # 2026-10-05 release
HF_SNAPSHOT = "hf_20261005"


def hf_snapshot_dir() -> Path:
    """Folder of the release's ``markets.parquet`` (``scripts/data/download_hf_polymarket.py``)."""
    return data_root() / "polymarket" / HF_SNAPSHOT


def old_dir() -> Path:
    return data_root() / "polymarket_clean" / "trades"


def ext_dir() -> Path:
    return data_root() / "polymarket_clean" / "trades_ext"


def lake_dir() -> Path:
    return data_root() / "lake" / "polymarket" / "fills"


def lake_ready() -> bool:
    return is_complete(lake_dir().parent / "fills_state.json")


def ext_files(d: Optional[Path | str] = None) -> List[Path]:
    """Finished ext partitions (``hf/part-*.parquet``, ``tail/fills_YYYY-MM-DD.parquet``); tmp files
    excluded."""
    d = Path(d) if d else ext_dir()
    if not d.is_dir():
        return []
    return sorted(list(d.glob("hf/part-*.parquet")) + list(d.glob("tail/fills_????-??-??.parquet")))


def old_files(d: Optional[Path | str] = None) -> List[Path]:
    return sorted(Path(d or old_dir()).glob("part-*.parquet"))


def _filters(start_ts, end_ts, maker_rows_only) -> str:
    rng = ""
    if start_ts is not None:
        rng += f" AND ts_chain >= {float(start_ts)!r}"
    if end_ts is not None:
        rng += f" AND ts_chain < {float(end_ts)!r}"
    if maker_rows_only:
        rng += " AND NOT is_taker_order_row"
    return rng


def _legacy_sql(cols, start_ts, end_ts, maker_rows_only, old, ext, cutover_ts) -> str:
    def sel(branch: str) -> str:
        out = []
        for c in cols:
            if branch == "old":
                if c in EXT_ONLY:
                    out.append(f'CAST(NULL AS {_SQL_TYPES.get(c, "VARCHAR")}) AS "{c}"')
                else:
                    out.append(f'{_OLD_EXPR[c]} AS "{c}"' if c in _OLD_EXPR else f'"{c}"')
            else:
                if c in OLD_ONLY:
                    out.append(f'CAST(NULL AS {_SQL_TYPES.get(c, "VARCHAR")}) AS "{c}"')
                else:
                    out.append(f'"{c}"')
        return ", ".join(out)

    rng = _filters(start_ts, end_ts, maker_rows_only)
    parts = []
    olds, exts = old_files(old), ext_files(ext)
    if cutover_ts is None:
        cutover_ts = ext_start_ts(ext)
    if olds and (start_ts is None or start_ts < cutover_ts or not exts):
        lst = ", ".join(sql_path(f) for f in olds)
        bound = f"ts_chain < {cutover_ts}" if exts else "TRUE"
        parts.append(f"SELECT {sel('old')} FROM read_parquet([{lst}]) WHERE {bound}{rng}")
    if exts and (end_ts is None or end_ts > cutover_ts):
        lst = ", ".join(sql_path(f) for f in exts)
        parts.append(
            f"SELECT {sel('ext')} FROM read_parquet([{lst}], union_by_name=true) "
            f"WHERE ts_chain >= {cutover_ts}{rng}"
        )
    if not parts:
        raise FileNotFoundError(
            f"no Polymarket clean trade files in {old or old_dir()} or {ext or ext_dir()}"
        )
    return " UNION ALL ".join(parts)


def _lake_sql(cols, start_ts, end_ts, maker_rows_only) -> str:
    files = (
        files_in(month_dirs(lake_dir(), start_ts, end_ts)) or files_in(month_dirs(lake_dir()))[:1]
    )
    where = "TRUE" if files_in(month_dirs(lake_dir(), start_ts, end_ts)) else "FALSE"
    cols_sql = ", ".join(f'"{c}"' for c in cols)
    return (
        f"SELECT {cols_sql} FROM read_parquet([{', '.join(sql_path(f) for f in files)}]) "
        f"WHERE {where}{_filters(start_ts, end_ts, maker_rows_only)}"
    )


def clean_trades_sql(
    columns: Optional[Sequence[str]] = None,
    start_ts: Optional[float] = None,
    end_ts: Optional[float] = None,
    maker_rows_only: bool = False,
    old_dir: Optional[Path | str] = None,
    ext_dir: Optional[Path | str] = None,
    cutover_ts: Optional[int] = None,
    source: str = "auto",
) -> str:
    """DuckDB SELECT over the whole fill tape; optional ``[start_ts, end_ts)`` filter on
    ``ts_chain``.

    ``cutover_ts`` defaults to :func:`ext_start_ts` (old rows before it, ext rows from it on).
    ``maker_rows_only`` drops taker-order aggregate rows (use for counts / volume). ``source``:
    ``"auto"`` (lake when complete, else legacy), ``"lake"`` or ``"legacy"``; passing ``old_dir`` /
    ``ext_dir`` implies legacy. ``category`` and ``is_updown`` can be requested only from the lake.
    """
    use_lake = source == "lake" or (
        source == "auto" and old_dir is None and ext_dir is None and lake_ready()
    )
    cols = list(columns or ALL_COLUMNS)
    allowed = ALL_COLUMNS + (LAKE_ONLY if use_lake else [])
    unknown = [c for c in cols if c not in allowed]
    if unknown:
        raise ValueError(
            f"unknown columns {unknown}"
            + ("" if use_lake else " (category/is_updown need the lake)")
        )
    if use_lake:
        return _lake_sql(cols, start_ts, end_ts, maker_rows_only)
    return _legacy_sql(cols, start_ts, end_ts, maker_rows_only, old_dir, ext_dir, cutover_ts)


def tape_end_ts(ext: Optional[Path | str] = None) -> Optional[int]:
    """Exclusive end (UTC seconds) of contiguous ext coverage, from ``coverage.json`` written by
    the builder."""
    p = Path(ext or ext_dir()) / "coverage.json"
    if not p.exists():
        return None
    return int(json.loads(p.read_text())["end_ts"])


def ext_start_ts(ext: Optional[Path | str] = None) -> int:
    """First second the ext tape serves: ``start_ts`` in ``coverage.json`` (0 for a full-history
    build), else :data:`CUTOVER_TS`."""
    p = Path(ext or ext_dir()) / "coverage.json"
    if p.exists():
        start = json.loads(p.read_text()).get("start_ts")
        if start is not None:
            return int(start)
    return CUTOVER_TS


def read_clean_trades(
    columns: Optional[Sequence[str]] = None,
    start_ts: Optional[float] = None,
    end_ts: Optional[float] = None,
    **kw,
):
    """Materialise a (filtered) slice as pandas.

    Prefer :func:`clean_trades_sql` inside DuckDB for big reads.
    """
    from ..shared.duck import connect

    return connect().sql(clean_trades_sql(columns, start_ts, end_ts, **kw)).df()

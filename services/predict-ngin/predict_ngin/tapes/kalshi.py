"""Kalshi trade tape: every executed trade since 2021, one SQL source for DuckDB.

Two layouts on disk, same rows:

* **lake** (preferred, once built): ``<data>/lake/kalshi/trades/year=YYYY/month=MM/*.parquet``,
  sorted inside each month by series, ticker, time; served when ``trades_state.json`` there says
  the build is complete.
* **legacy**: ``<data>/kalshi/trades_fixed/trades_YYYY-MM.parquet`` (repaired HF snapshot, complete
  through 2026-01-27) + ``<data>/kalshi/trades_fixed_ext/trades_YYYY-MM-DD.parquet`` (API refresh
  from the cutover on). Old rows are taken before :data:`CUTOVER_TS` and new rows from it on, so
  trade ids never repeat.

``count`` is the integer contract count (trades_fixed parity, rounded). ``count_fp`` is the exact
count: Kalshi started fractional contracts around 2026-03-13; before that ``count_fp == count``.
Use ``count_fp`` for volume.

    from predict_ngin.tapes.kalshi import tape_sql
    con.execute(
        f"SELECT ticker, yes_price FROM ({tape_sql(start_ts=..., end_ts=...)}) WHERE ..."
    )
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from ..shared.duck import sql_path
from ..shared.paths import data_root
from ._lake import files_in, is_complete, month_dirs, utc_ts

CUTOVER_TS = 1769558400  # 2026-01-28 00:00:00 UTC
COLUMNS = [
    "trade_id",
    "ticker",
    "event_ticker",
    "yes_price",
    "no_price",
    "count",
    "taker_side",
    "created_time",
    "timestamp_unix",
    "timestamp_unix_orig",
]
EXTRA_COLUMNS = [
    "count_fp"
]  # requestable, not in the default column list (keeps old callers' schemas)
ALL_COLUMNS = COLUMNS + EXTRA_COLUMNS


def old_dir() -> Path:
    return data_root() / "kalshi" / "trades_fixed"


def ext_dir() -> Path:
    return data_root() / "kalshi" / "trades_fixed_ext"


def lake_dir() -> Path:
    return data_root() / "lake" / "kalshi" / "trades"


def ext_files(d: Optional[Path | str] = None) -> List[Path]:
    """Finished daily partitions (staging / tmp / verify files are excluded)."""
    d = Path(d) if d else ext_dir()
    return sorted(d.glob("trades_????-??-??.parquet")) if d.is_dir() else []


def old_files(d: Optional[Path | str] = None) -> List[Path]:
    return sorted(Path(d or old_dir()).glob("trades_*.parquet"))


def lake_ready() -> bool:
    return is_complete(lake_dir().parent / "trades_state.json")


def _check(cols: Sequence[str]) -> None:
    bad = [c for c in cols if c not in ALL_COLUMNS]
    if bad:
        raise ValueError(f"unknown Kalshi tape columns {bad}")


def _rng(start_ts, end_ts) -> str:
    r = ""
    if start_ts is not None:
        r += f" AND timestamp_unix >= {int(start_ts)}"
    if end_ts is not None:
        r += f" AND timestamp_unix < {int(end_ts)}"
    return r


def _legacy_sql(
    cols, start_ts, end_ts, old: Optional[Path | str], ext: Optional[Path | str], cutover_ts: int
) -> str:
    def sel(branch: str, files: List[Path]) -> str:
        has_fp = branch == "ext" and bool(files)
        out = []
        for c in cols:
            if c == "count_fp":
                out.append(
                    'coalesce(CAST("count_fp" AS DOUBLE), CAST("count" AS DOUBLE)) AS "count_fp"'
                    if has_fp
                    else 'CAST("count" AS DOUBLE) AS "count_fp"'
                )
            else:
                out.append(f'"{c}"')
        return ", ".join(out)

    rng = _rng(start_ts, end_ts)
    olds, exts = old_files(old), ext_files(ext)
    lst = lambda fs: ", ".join(sql_path(f) for f in fs)  # noqa: E731
    if not exts and olds:  # no refresh yet: exactly the old glob
        return f"SELECT {sel('old', olds)} FROM read_parquet([{lst(olds)}]) WHERE TRUE{rng}"
    parts = []
    if olds and (start_ts is None or start_ts < cutover_ts):
        parts.append(
            f"SELECT {sel('old', olds)} FROM read_parquet([{lst(olds)}]) "
            f"WHERE timestamp_unix < {cutover_ts}{rng}"
        )
    if exts and (end_ts is None or end_ts > cutover_ts):
        parts.append(
            f"SELECT {sel('ext', exts)} FROM read_parquet([{lst(exts)}], union_by_name=true) "
            f"WHERE timestamp_unix >= {cutover_ts}{rng}"
        )
    if not parts:
        raise FileNotFoundError(f"no Kalshi tape files in {old or old_dir()} or {ext or ext_dir()}")
    return " UNION ALL ".join(parts)


def _lake_sql(cols, start_ts, end_ts) -> str:
    files = files_in(month_dirs(lake_dir(), start_ts, end_ts))
    if not files:
        cols_sql = ", ".join(f'"{c}"' for c in cols)
        return (
            f"SELECT {cols_sql} FROM read_parquet("
            f"[{sql_path(files_in(month_dirs(lake_dir()))[0])}]) WHERE FALSE"
        )
    cols_sql = ", ".join(f'"{c}"' for c in cols)
    return (
        f"SELECT {cols_sql} FROM read_parquet([{', '.join(sql_path(f) for f in files)}]) "
        f"WHERE TRUE{_rng(start_ts, end_ts)}"
    )


def tape_sql(
    columns: Optional[Sequence[str]] = None,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    old_dir: Optional[Path | str] = None,
    ext_dir: Optional[Path | str] = None,
    cutover_ts: int = CUTOVER_TS,
    source: str = "auto",
) -> str:
    """DuckDB SELECT over the whole tape; optional ``[start_ts, end_ts)`` filter on
    ``timestamp_unix``.

    ``source``: ``"auto"`` (lake when complete, else legacy), ``"lake"`` or ``"legacy"``. Passing
    ``old_dir`` / ``ext_dir`` implies legacy (tests and one-off roots such as a second refresh
    worker).
    """
    cols = list(columns or COLUMNS)
    _check(cols)
    use_lake = source == "lake" or (
        source == "auto" and old_dir is None and ext_dir is None and lake_ready()
    )
    if use_lake:
        return _lake_sql(cols, start_ts, end_ts)
    return _legacy_sql(cols, start_ts, end_ts, old_dir, ext_dir, cutover_ts)


def tape_end_ts(ext: Optional[Path | str] = None) -> Optional[int]:
    """Exclusive end (UTC seconds) of the refreshed coverage, from the last finished day file."""
    days = [dt.date.fromisoformat(f.stem[len("trades_") :]) for f in ext_files(ext)]
    return utc_ts(max(days) + dt.timedelta(days=1)) if days else None


def missing_days(
    ext: Optional[Path | str] = None, until: Optional[dt.date] = None
) -> List[dt.date]:
    """UTC days from the cutover to ``until`` (default: yesterday) with no finished day file.

    Non-empty while a refresh is running or has gaps: reads over those days return no trades.
    """
    have = {dt.date.fromisoformat(f.stem[len("trades_") :]) for f in ext_files(ext)}
    day = dt.datetime.fromtimestamp(CUTOVER_TS, dt.timezone.utc).date()
    end = until or (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1))
    out = []
    while day <= end:
        if day not in have:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def read_tape(
    columns: Optional[Iterable[str]] = None,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    **kw,
):
    """Materialise a (filtered) slice as pandas.

    Prefer :func:`tape_sql` inside DuckDB for big reads.
    """
    from ..shared.duck import connect

    return connect().sql(tape_sql(list(columns) if columns else None, start_ts, end_ts, **kw)).df()

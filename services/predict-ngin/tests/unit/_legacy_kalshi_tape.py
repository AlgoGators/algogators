# FROZEN COPY (2026-10-17) of src/trading/arbitrage/kalshi_tape.py before it became a shim. Never edit (D2).
"""Full Kalshi trade tape = trades_fixed (HF snapshot) + trades_fixed_ext (API refresh), with no overlap.

trades_fixed is complete through 2026-01-28 (verified) but from 2026-01-29 holds only the ~1.7k tickers/day in the
HF snapshot. scripts/research/tape_refresh.py re-fetches everything from CUTOVER_TS (2026-01-28 00:00 UTC)
into data/kalshi/trades_fixed_ext/trades_YYYY-MM-DD.parquet with the same 10-column schema. Taking old rows
with timestamp_unix < CUTOVER_TS and new rows with timestamp_unix >= CUTOVER_TS gives one tape with no
duplicate trade_ids. If the ext directory is absent or empty this returns exactly the old tape.

Usage (DuckDB):
    from src.trading.arbitrage.kalshi_tape import tape_sql
    con.execute(f"SELECT ticker, yes_price FROM ({tape_sql()}) WHERE ...")
Replaces read_parquet('data/kalshi/trades_fixed/*.parquet') one-for-one.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[3]
OLD_DIR = ROOT / "data" / "kalshi" / "trades_fixed"
EXT_DIR = ROOT / "data" / "kalshi" / "trades_fixed_ext"
CUTOVER_TS = 1769558400  # 2026-01-28 00:00:00 UTC
COLUMNS = ["trade_id", "ticker", "event_ticker", "yes_price", "no_price", "count", "taker_side",
           "created_time", "timestamp_unix", "timestamp_unix_orig"]


def _q(p: Path | str) -> str:
    return "'" + str(p).replace("\\", "/").replace("'", "''") + "'"


def ext_files(ext_dir: Path | str = EXT_DIR) -> List[Path]:
    """Finished daily partitions (staging/tmp/verify files are excluded)."""
    d = Path(ext_dir)
    return sorted(d.glob("trades_????-??-??.parquet")) if d.is_dir() else []


def old_files(old_dir: Path | str = OLD_DIR) -> List[Path]:
    return sorted(Path(old_dir).glob("trades_*.parquet"))


def tape_sql(columns: Optional[Sequence[str]] = None, start_ts: Optional[int] = None,
             end_ts: Optional[int] = None, old_dir: Path | str = OLD_DIR, ext_dir: Path | str = EXT_DIR,
             cutover_ts: int = CUTOVER_TS) -> str:
    """DuckDB SELECT over the unioned tape; optional [start_ts, end_ts) filter on timestamp_unix."""
    cols = ", ".join(f'"{c}"' for c in (columns or COLUMNS))
    rng = ""
    if start_ts is not None:
        rng += f" AND timestamp_unix >= {int(start_ts)}"
    if end_ts is not None:
        rng += f" AND timestamp_unix < {int(end_ts)}"
    parts = []
    olds = old_files(old_dir)
    if olds and (start_ts is None or start_ts < cutover_ts):
        lst = ", ".join(_q(f) for f in olds)
        parts.append(f"SELECT {cols} FROM read_parquet([{lst}]) WHERE timestamp_unix < {cutover_ts}{rng}")
    exts = ext_files(ext_dir)
    if exts and (end_ts is None or end_ts > cutover_ts):
        lst = ", ".join(_q(f) for f in exts)
        parts.append(f"SELECT {cols} FROM read_parquet([{lst}]) WHERE timestamp_unix >= {cutover_ts}{rng}")
    if not exts and olds:  # no refresh yet: behave exactly like the old glob
        lst = ", ".join(_q(f) for f in olds)
        parts = [f"SELECT {cols} FROM read_parquet([{lst}]) WHERE TRUE{rng}"]
    if not parts:
        raise FileNotFoundError(f"no Kalshi tape files in {old_dir} or {ext_dir}")
    return " UNION ALL ".join(parts)


def tape_end_ts(ext_dir: Path | str = EXT_DIR) -> Optional[int]:
    """Exclusive end (UTC seconds) of the contiguous ext coverage, from the last finished day file."""
    import datetime as dt

    days = [dt.date.fromisoformat(f.stem[len("trades_"):]) for f in ext_files(ext_dir)]
    if not days:
        return None
    last = max(days) + dt.timedelta(days=1)
    return int(dt.datetime(last.year, last.month, last.day, tzinfo=dt.timezone.utc).timestamp())


def read_tape(columns: Optional[Iterable[str]] = None, start_ts: Optional[int] = None,
              end_ts: Optional[int] = None, **kw):
    """Convenience: materialise a (filtered) slice as pandas. Prefer tape_sql inside DuckDB for big reads."""
    import duckdb

    return duckdb.sql(tape_sql(list(columns) if columns else None, start_ts, end_ts, **kw)).df()

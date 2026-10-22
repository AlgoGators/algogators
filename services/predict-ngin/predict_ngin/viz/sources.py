"""Time-bounded data sources for the explorer: SQL ``FROM`` fragments that open only the months
(or recorder hours) a page needs.

The catalog views (``predict_ngin.lake.catalog``) list every file on disk. That is right for
ad-hoc SQL, but a page that looks at one market for one month should not ask DuckDB to plan over
every month. The functions here return a parenthesised ``(SELECT ...)`` over just the files in
range, or ``None`` when the data is not on this machine, so pages can show how to get it instead.

No Streamlit here; everything works against any data folder (``PREDICT_NGIN_DATA``).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from pathlib import Path
from typing import List, Optional, Sequence

from ..shared.duck import sql_path
from ..shared.paths import data_root
from ..tapes import kalshi, polymarket
from ..tapes._lake import is_partition

VENUES = ("polymarket", "kalshi")

# How to create each view when it is missing (shown to the user).
BUILD_HINTS = {
    "polymarket_fills": "uv run predict-ngin data update polymarket",
    "polymarket_maker_fills": "uv run predict-ngin data update polymarket",
    "polymarket_markets": "uv run predict-ngin data build polymarket --markets",
    "polymarket_bars_1h": "uv run predict-ngin data build polymarket",
    "polymarket_bars_1m": "uv run predict-ngin data build polymarket",
    "kalshi_trades": "uv run predict-ngin data update kalshi",
    "kalshi_markets": "uv run predict-ngin data update kalshi (the tape refresh saves markets)",
    "kalshi_lake_markets": "uv run predict-ngin data build kalshi --markets",
    "kalshi_bars_1h": "uv run predict-ngin data build kalshi",
    "kalshi_bars_1m": "uv run predict-ngin data build kalshi",
    "kalshi_books": "the order-book recorder (<data>/live_books/kalshi/<day>/)",
    "polymarket_books": "the order-book recorder (<data>/live_books/polymarket/<day>/)",
    "kalshi_live_trades": "the order-book recorder (<data>/live_books/kalshi/<day>/)",
    "polymarket_live_trades": "the order-book recorder (<data>/live_books/polymarket/<day>/)",
}

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _read(files: Sequence[Path], hive: bool = False) -> Optional[str]:
    if not files:
        return None
    opts = ", union_by_name=true" + (", hive_partitioning=true" if hive else "")
    return f"(SELECT * FROM read_parquet([{', '.join(sql_path(f) for f in files)}]{opts}))"


def month_start(ts: float) -> int:
    d = dt.datetime.fromtimestamp(int(ts), dt.timezone.utc)
    return calendar.timegm((d.year, d.month, 1, 0, 0, 0))


def month_bounds(month: str) -> tuple[int, int]:
    """``[first second, next month's first second)`` of a ``YYYY-MM`` month."""
    y, m = (int(x) for x in month.split("-"))
    return calendar.timegm((y, m, 1, 0, 0, 0)), calendar.timegm(
        (y + (m == 12), m % 12 + 1, 1, 0, 0, 0)
    )


def months_between(start: float, end: float) -> List[str]:
    """``YYYY-MM`` of every month overlapping ``[start, end)``."""
    out = []
    d = dt.datetime.fromtimestamp(month_start(start), dt.timezone.utc)
    while calendar.timegm(d.timetuple()) < end:
        out.append(d.strftime("%Y-%m"))
        d = dt.datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=dt.timezone.utc)
    return out


# ------------------------------------------------------------------------------------------ bars
def bars_dir(venue: str, size: str) -> Path:
    return data_root() / "lake" / venue / f"bars_{size}"


def bar_months(venue: str, size: str = "1h") -> List[str]:
    """``YYYY-MM`` months with published bar files (``.building`` folders are ignored)."""
    out = []
    for ydir in sorted(bars_dir(venue, size).glob("year=*")):
        for mdir in sorted(ydir.glob("month=*")):
            if mdir.is_dir() and is_partition(mdir) and any(mdir.glob("*.parquet")):
                out.append(f"{ydir.name[5:]}-{int(mdir.name[6:]):02d}")
    return out


def bars_cover(venue: str, size: str, start: float, end: float) -> bool:
    """Whether bar files exist for every month in ``[start, end)``."""
    have = set(bar_months(venue, size))
    return bool(have) and all(m in have for m in months_between(start, end))


def bars_source(venue: str, size: str, start: float, end: float) -> Optional[str]:
    """Bar files of the months in ``[start, end)`` (with hive ``year`` / ``month`` columns)."""
    files = []
    for m in months_between(start, end):
        y, mm = m.split("-")
        d = bars_dir(venue, size) / f"year={y}" / f"month={mm}"
        files += sorted(d.glob("*.parquet"))
    return _read(files, hive=True)


# ---------------------------------------------------------------------------------------- tapes
PM_FILL_COLUMNS = [
    "ts_chain",
    "market_id",
    "yes_price",
    "yes_side",
    "usd_amount",
    "token_amount",
    "maker",
    "taker",
]
KALSHI_TRADE_COLUMNS = [
    "trade_id",
    "ticker",
    "event_ticker",
    "yes_price",
    "no_price",
    "count",
    "count_fp",
    "taker_side",
    "timestamp_unix",
]


def pm_fills_source(start: float, end: float) -> Optional[str]:
    """Polymarket maker fills in ``[start, end)``; only the months in range are opened."""
    try:
        sql = polymarket.clean_trades_sql(PM_FILL_COLUMNS, start, end, maker_rows_only=True)
    except (FileNotFoundError, IndexError, ValueError):
        return None
    return f"({sql})" if "read_parquet([]" not in sql else None


def kalshi_trades_source(start: float, end: float) -> Optional[str]:
    """Kalshi trades in ``[start, end)``; only the months / days in range are opened."""
    try:
        sql = kalshi.tape_sql(KALSHI_TRADE_COLUMNS, int(start), int(end))
    except (FileNotFoundError, IndexError, ValueError):
        return None
    return f"({sql})" if "read_parquet([]" not in sql else None


# ------------------------------------------------------------------------------------- recorder
def recorder_dir(venue: str) -> Path:
    return data_root() / "live_books" / venue


def recorder_days(venue: str) -> List[str]:
    """``YYYY-MM-DD`` folders with recorded books for ``venue``, oldest first."""
    d = recorder_dir(venue)
    return sorted(
        p.name for p in d.glob("*") if _DAY.match(p.name) and any(p.glob("books-*.parquet"))
    )


def _recorder_files(venue: str, day: str, kind: str, hours: Optional[range]) -> List[Path]:
    files = sorted((recorder_dir(venue) / day).glob(f"{kind}-*.parquet"))
    if hours is None:
        return files
    keep = []
    for f in files:
        m = re.match(rf"^{kind}-(\d{{2}})\.parquet$", f.name)
        # files not named by hour are kept: the time filter in each query still applies
        if not m or int(m.group(1)) in hours:
            keep.append(f)
    return keep


def books_source(venue: str, day: str, hours: Optional[range] = None) -> Optional[str]:
    """Recorder book snapshots of one UTC day (optionally only the hour files in ``hours``)."""
    return _read(_recorder_files(venue, day, "books", hours))


def live_trades_source(venue: str, day: str, hours: Optional[range] = None) -> Optional[str]:
    """Recorder trades of one UTC day (optionally only the hour files in ``hours``)."""
    return _read(_recorder_files(venue, day, "trades", hours))


def day_bounds_ns(day: str) -> tuple[int, int]:
    """``[00:00, 24:00)`` of a UTC day in nanoseconds."""
    d = dt.date.fromisoformat(day)
    lo = calendar.timegm((d.year, d.month, d.day, 0, 0, 0))
    return lo * 10**9, (lo + 86400) * 10**9

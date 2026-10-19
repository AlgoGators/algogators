"""Shared helpers for month-partitioned lake datasets
(``<dataset>/year=YYYY/month=MM/*.parquet``)."""

from __future__ import annotations

import calendar
import datetime as dt
import json
import re
from pathlib import Path
from typing import List, Optional


def is_complete(state_file: Path) -> bool:
    """A lake dataset is served only once its builder has marked a full build complete."""
    if not state_file.exists():
        return False
    try:
        return bool(json.loads(state_file.read_text()).get("_complete"))
    except (OSError, ValueError):
        return False


_YEAR = re.compile(r"^year=(\d{4})$")
_MONTH = re.compile(r"^month=(\d{2})$")


def is_partition(path: Path) -> bool:
    """A published ``year=YYYY/month=MM`` folder (or a file inside one). Staging folders of a
    running build (``month=03.building``) and swap leftovers never match."""
    p = path if path.is_dir() else path.parent
    return bool(_MONTH.match(p.name) and _YEAR.match(p.parent.name))


def month_dirs(
    root: Path, start_ts: Optional[float] = None, end_ts: Optional[float] = None
) -> List[Path]:
    """Month partitions of ``root`` that can hold rows in ``[start_ts, end_ts)`` (all if
    unbounded)."""
    out = []
    for ydir in sorted(root.glob("year=*")):
        for mdir in sorted(ydir.glob("month=*")):
            if not (mdir.is_dir() and is_partition(mdir)):
                continue  # e.g. month=03.building while a build runs
            y, m = int(ydir.name[5:]), int(mdir.name[6:])
            lo = calendar.timegm((y, m, 1, 0, 0, 0))
            hi = calendar.timegm((y + (m == 12), m % 12 + 1, 1, 0, 0, 0))
            if start_ts is not None and hi <= start_ts:
                continue
            if end_ts is not None and lo >= end_ts:
                continue
            out.append(mdir)
    return out


def files_in(dirs: List[Path]) -> List[Path]:
    return [f for d in dirs for f in sorted(d.glob("*.parquet"))]


def utc_ts(day: dt.date) -> int:
    return calendar.timegm((day.year, day.month, day.day, 0, 0, 0))

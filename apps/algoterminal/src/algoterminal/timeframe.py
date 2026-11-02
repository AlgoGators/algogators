"""Shared timeframe presets for how much history to look at, used by both
the Research tab (data pulled before a backtest) and the Compare tab
(the window two things are compared over).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

TIMEFRAME_OPTIONS: list[tuple[str, str]] = [
    ("1 Month", "1m"),
    ("3 Months", "3m"),
    ("6 Months", "6m"),
    ("Year to date", "ytd"),
    ("1 Year", "1y"),
    ("3 Years", "3y"),
    ("5 Years", "5y"),
    ("10 Years", "10y"),
    ("Max", "max"),
]

DEFAULT_TIMEFRAME = "3y"

_MAX_START = date(1990, 1, 1)

_OFFSETS = {
    "1m": pd.DateOffset(months=1),
    "3m": pd.DateOffset(months=3),
    "6m": pd.DateOffset(months=6),
    "1y": pd.DateOffset(years=1),
    "3y": pd.DateOffset(years=3),
    "5y": pd.DateOffset(years=5),
    "10y": pd.DateOffset(years=10),
}


def resolve_timeframe(key: str, today: date | None = None) -> tuple[date, date]:
    """Turn a timeframe key into a concrete (start, end) date range."""
    today = today or date.today()
    if key == "max":
        return _MAX_START, today
    if key == "ytd":
        return date(today.year, 1, 1), today
    offset = _OFFSETS.get(key)
    if offset is None:
        return _MAX_START, today
    start = (pd.Timestamp(today) - offset).date()
    return start, today

"""Streamlit plumbing shared by the explorer pages: one DuckDB connection, cached queries,
availability checks and date pickers.

Queries are cached with ``st.cache_data`` keyed on the SQL text, which already encodes every
page parameter (venue, market, time range, filters), so revisiting a view is instant. The
sidebar's *Reload data* button clears the caches and reopens the catalog, which picks up months
a running lake build has published since.
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Iterable, Optional, Tuple

import pandas as pd
import streamlit as st

from predict_ngin import lake
from predict_ngin.viz.sources import BUILD_HINTS


@st.cache_resource(show_spinner=False)
def connection():
    """The app's DuckDB connection (standard limits, UTC, catalog views)."""
    return lake.connect()


@st.cache_data(show_spinner=False, ttl=3600, max_entries=256)
def run(sql: str) -> pd.DataFrame:
    """Run ``sql`` on a cursor of the shared connection (cursors are safe across sessions)."""
    return connection().cursor().sql(sql).df()


def query(sql: str, what: str = "Querying") -> Optional[pd.DataFrame]:
    """:func:`run` with a spinner; shows the error and returns None if the query fails."""
    try:
        with st.spinner(f"{what}..."):
            return run(sql)
    except Exception as e:  # DuckDB IO / binder errors: show them, keep the page alive
        st.error(f"{what} failed: {e}")
        with st.expander("SQL"):
            st.code(sql, language="sql")
        return None


@st.cache_data(show_spinner=False, ttl=600)
def available_views() -> frozenset:
    return frozenset(v["view"] for v in lake.views(connection()) if v["available"])


def has(view: str) -> bool:
    return view in available_views()


def need(*views: str) -> bool:
    """True when every view exists; otherwise explain how to build the missing ones."""
    missing = [v for v in views if not has(v)]
    for v in missing:
        st.info(f"`{v}` is not available in this data folder. To create it: `{BUILD_HINTS[v]}`.")
    return not missing


def reload_button() -> None:
    if st.sidebar.button("Reload data", help="Re-scan the data folder (new lake months, books)"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()


# ------------------------------------------------------------------------------------ time
def utc_ts(d: dt.date) -> int:
    return calendar.timegm((d.year, d.month, d.day, 0, 0, 0))


def utc_day(ts: float) -> dt.date:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).date()


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def date_range(
    label: str, default_days: int, key: str, default_end: Optional[dt.date] = None
) -> Tuple[int, int]:
    """A UTC date-range picker; returns ``[start, end)`` in seconds (end day inclusive)."""
    end = default_end or today()
    picked = st.date_input(
        label, value=(end - dt.timedelta(days=default_days), end), key=key, format="YYYY-MM-DD"
    )
    seq = list(picked) if isinstance(picked, (tuple, list)) else [picked]
    lo = seq[0]
    hi = seq[1] if len(seq) == 2 else lo  # mid-selection: only the start is chosen
    return utc_ts(lo), utc_ts(hi) + 86400


def top_n(df: pd.DataFrame, key: str, value: str, n: int, other: str = "Other") -> pd.DataFrame:
    """Keep the ``n`` largest ``key`` groups by total ``value``; fold the rest into ``other``."""
    if df.empty:
        return df
    keep = set(df.groupby(key)[value].sum().nlargest(n).index)
    out = df.copy()
    out[key] = out[key].where(out[key].isin(keep), other)
    return out


def fmt_usd(x: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(x) >= div:
            return f"${x / div:,.1f}{unit}"
    return f"${x:,.0f}"


def caption_list(items: Iterable[str], limit: int = 12) -> str:
    items = list(items)
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more

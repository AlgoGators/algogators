"""Kalshi market category from its series.

Kalshi assigns every series one category (``GET /series``: Sports, Elections, Politics, Financials,
Economics, Mentions, Climate and Weather, ...). The lake uses those names, with multi-leg combos
(``KXMVE*``) as ``Parlay`` and series missing from the series table as ``Other``. Bump
:data:`VERSION` when the rules change: fill files are sorted by category, so the affected months
should then be rebuilt.
"""

from __future__ import annotations

from typing import Mapping, Optional

VERSION = 1

PARLAY = "Parlay"
OTHER = "Other"


def category_of(series: str, series_category: Mapping[str, str]) -> str:
    """Category of a series ticker, given a ``series -> Kalshi category`` mapping."""
    if not isinstance(series, str) or not series:
        return OTHER
    if series.startswith("KXMVE"):
        return PARLAY
    cat: Optional[str] = series_category.get(series)
    return cat.strip() if isinstance(cat, str) and cat.strip() else OTHER


def category_case_sql(series_col: str = "series", table: str = "sc") -> str:
    """The same rule as SQL, for a join to a ``(series, category)`` table aliased ``table``."""
    return (
        f"CASE WHEN {series_col} LIKE 'KXMVE%' THEN '{PARLAY}' "
        f"ELSE coalesce(nullif(trim({table}.category), ''), '{OTHER}') END"
    )

"""The data lake: one place to query every dataset.

    from predict_ngin import lake
    con = lake.connect()  # DuckDB with standard limits + named views
    con.sql(
        "SELECT category, sum(usd_amount) FROM polymarket_maker_fills "
        "WHERE ts_chain >= 1767225600 GROUP BY 1"
    )
    lake.views(con)  # what is available on this machine

Views read the month-partitioned lake (``<data>/lake``) when it is built and the original files
otherwise, so code written against them keeps working through the migration. Layout, update
commands and design choices: docs/DATA_LAKE.md. Command line:
``uv run predict-ngin data --help``.
"""

from __future__ import annotations

from typing import List, Optional

from ..shared.duck import connect as _duck_connect
from . import catalog, contracts, manifest

__all__ = ["catalog", "connect", "contracts", "manifest", "views"]


def connect(
    database: str = ":memory:",
    *,
    memory: Optional[str] = None,
    threads: Optional[int] = None,
    temp_max: Optional[str] = None,
    register_views: bool = True,
):
    """A DuckDB connection with the standard limits (see ``predict_ngin.shared.duck``) and the
    catalog views."""
    con = _duck_connect(database, memory=memory, threads=threads, temp_max=temp_max)
    if register_views:
        catalog.register(con)
    return con


def views(con=None) -> List[dict]:
    """Catalog views on ``con`` (default: a fresh connection): name, available, description."""
    con = con if con is not None else connect()
    rows = con.sql("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()
    have = {r[0] for r in rows}
    return [
        {"view": v.name, "available": v.name in have, "description": v.description}
        for v in catalog.VIEWS
    ]

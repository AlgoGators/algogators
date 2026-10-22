"""One way to open DuckDB: bounded memory, threads and temp spill, in the data folder.

Studies used to open DuckDB with their own ad-hoc settings, and one run with no temp cap filled
48 GB of disk. Use :func:`connect` instead. Every limit can be changed per machine without code
changes:

* ``PREDICT_NGIN_DUCK_MEMORY``  memory limit (default ``4GB``)
* ``PREDICT_NGIN_DUCK_THREADS`` worker threads (default: min(4, CPU count))
* ``PREDICT_NGIN_DUCK_TEMP_MAX`` cap on spilled temp data (default ``10GiB``)

Temp files go to ``<data>/_duck_tmp``. The session time zone is UTC, so month and day boundaries
are UTC whatever the machine's zone.
"""

from __future__ import annotations

import os
from typing import Optional

from .paths import data_root


def sql_path(p) -> str:
    """A filesystem path as a single-quoted DuckDB string literal (forward slashes, quotes
    escaped)."""
    return "'" + str(p).replace("\\", "/").replace("'", "''") + "'"


def connect(
    database: str = ":memory:",
    *,
    memory: Optional[str] = None,
    threads: Optional[int] = None,
    temp_max: Optional[str] = None,
    read_only: bool = False,
    progress_bar: bool = False,
):
    import duckdb

    con = duckdb.connect(database, read_only=read_only)
    tmp = data_root() / "_duck_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    mem = memory or os.environ.get("PREDICT_NGIN_DUCK_MEMORY", "4GB")
    thr = (
        threads
        or int(os.environ.get("PREDICT_NGIN_DUCK_THREADS", 0))
        or min(4, os.cpu_count() or 1)
    )
    cap = temp_max or os.environ.get("PREDICT_NGIN_DUCK_TEMP_MAX", "10GiB")
    con.execute(
        f"SET memory_limit='{mem}'; SET threads={int(thr)}; SET temp_directory={sql_path(tmp)}; "
        f"SET max_temp_directory_size='{cap}'; SET preserve_insertion_order=false; "
        f"SET enable_progress_bar={'true' if progress_bar else 'false'}; "
        # UTC everywhere: without it strftime / date_trunc on timestamps use the machine's zone
        "SET TimeZone='UTC'"
    )
    return con

"""Settlement and result access: read settled binary markets into a typed, filtered form.

The frame convention is the one of ``scripts/research/settle_common.load_markets`` (12 research
importers): ``close_ts`` and ``settle_ts`` in UNIX seconds (floor of the nanosecond timestamp),
``win`` = 1.0 for ``result == "yes"``, and only rows with a ``yes``/``no`` result are kept.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
import pandas as pd

from .domain import Settlement

__all__ = ["BINARY_RESULTS", "win_of", "settled_frame", "read_settled_markets", "iter_settlements"]

BINARY_RESULTS = ("yes", "no")


def win_of(result) -> Optional[float]:
    """1.0 for ``"yes"``, 0.0 for ``"no"``, None for anything else (void, unsettled, missing)."""
    if result == "yes":
        return 1.0
    if result == "no":
        return 0.0
    return None


def settled_frame(d: pd.DataFrame) -> pd.DataFrame:
    """Add ``close_ts``, ``settle_ts`` and ``win`` to a Kalshi markets frame; keep yes/no results.

    Needs columns ``close_time``, ``settlement_ts`` (ISO 8601 strings) and ``result``. The input
    frame is not modified.
    """
    d = d.copy()
    ct = pd.to_datetime(d.close_time, utc=True, format="ISO8601")
    d["close_ts"] = ct.map(lambda x: x.value // 10**9)
    st = pd.to_datetime(d.settlement_ts, utc=True, format="ISO8601", errors="coerce")
    d["settle_ts"] = st.map(lambda x: x.value // 10**9 if pd.notna(x) else np.nan)
    d["win"] = (d.result == "yes").astype(float)
    return d[d.result.isin(list(BINARY_RESULTS))].copy()


def read_settled_markets(path) -> pd.DataFrame:
    """:func:`settled_frame` of a markets Parquet file."""
    return settled_frame(pd.read_parquet(Path(path)))


def iter_settlements(d: pd.DataFrame) -> Iterator[Settlement]:
    """One :class:`Settlement` per row of a :func:`settled_frame` result."""
    has_ev = "event_ticker" in d.columns
    for r in d.itertuples(index=False):
        st = r.settle_ts
        yield Settlement(
            ticker=str(r.ticker), result=str(r.result), win=float(r.win),
            close_ts=int(r.close_ts),
            settle_ts=None if st is None or (isinstance(st, float) and math.isnan(st)) else int(st),
            event_ticker=str(r.event_ticker) if has_ev else None,
        )

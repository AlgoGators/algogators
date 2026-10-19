"""Polygon block number -> true UTC timestamp, for repairing Polymarket on-chain trade times.

``data/polymarket/{trades,orderfilled,quant}.parquet`` carry a ``timestamp`` that is a linear 2.000 s/block
extrapolation, not the block's real time (real Polygon blocks are ~2.0-2.3 s apart and vary), so local times
drift by minutes to hours. :func:`block_to_ts` interpolates between true anchor timestamps fetched from the
public Polygon RPC / Dune:

* ``data/vendor/alpha_decay/block_anchors.parquet``  blocks 56,438,000-80,978,000, every 100th block
* ``data/vendor/pmfix/block_anchors_lo.parquet``     blocks 35,800,000-56,438,000, every 500th block

Both are also committed, merged, as ``polygon_block_anchors.parquet`` next to this module (1.4 MB), so a new
checkout needs no RPC backfill (``scripts/research/pmfix_block_anchors.py`` rebuilds the low range).

Blocks outside the anchor range get NaN (callers should flag ``timestamp_unreliable``). Interpolation error
was measured on held-out anchors (see docs/PMFIX_POLYMARKET_TOKEN_NORMALISATION.md).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from predict_ngin.shared.paths import data_root

SHIPPED_ANCHORS = Path(__file__).with_name("polygon_block_anchors.parquet")
ANCHOR_FILES = (
    data_root() / "vendor" / "pmfix" / "block_anchors_lo.parquet",
    data_root() / "vendor" / "alpha_decay" / "block_anchors.parquet",
    SHIPPED_ANCHORS,
)

__all__ = ["load_anchors", "block_to_ts", "ANCHOR_FILES"]


@lru_cache(maxsize=2)
def _load(paths: tuple) -> pd.DataFrame:
    parts = [pd.read_parquet(p) for p in paths if Path(p).exists()]
    if not parts:
        raise FileNotFoundError(f"no block anchor files found: {paths}")
    a = pd.concat(parts, ignore_index=True)
    a["block"] = a["block"].astype("int64")
    a["ts"] = a["ts"].astype("float64")
    a = a.drop_duplicates("block").sort_values("block").reset_index(drop=True)
    if not np.all(np.diff(a["ts"].to_numpy()) >= 0):
        raise ValueError("block anchors are not monotone in time")
    return a


def load_anchors(paths=ANCHOR_FILES) -> pd.DataFrame:
    return _load(tuple(str(p) for p in paths))


def block_to_ts(blocks, anchors: pd.DataFrame | None = None, max_gap_blocks: int = 1000) -> np.ndarray:
    """True epoch seconds (float) for each block number; NaN outside anchor coverage or across anchor gaps
    wider than ``max_gap_blocks``."""
    a = load_anchors() if anchors is None else anchors
    ab = a["block"].to_numpy(dtype=np.int64)
    at = a["ts"].to_numpy(dtype=float)
    b = np.asarray(blocks, dtype=float)
    ts = np.interp(b, ab.astype(float), at)
    j = np.searchsorted(ab, b, side="right")
    inside = (j > 0) & (j < len(ab))
    lo = ab[np.clip(j - 1, 0, len(ab) - 1)]
    hi = ab[np.clip(j, 0, len(ab) - 1)]
    exact = np.isin(b, ab)
    ok = exact | (inside & ((hi - lo) <= max_gap_blocks))
    return np.where(ok, ts, np.nan)

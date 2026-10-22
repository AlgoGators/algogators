"""
Polymarket hourly volume/OHLC for matched arb pairs, sourced from Dune.

Source table: ``polymarket_polygon.market_trades`` (Polygon CLOB fills), taker
rows only (``is_taker_side = true``) so each fill is counted exactly once.
Pulled by ``scripts/research/dune_pull_pair_hourly.py`` and normalised by
``scripts/research/dune_crosscheck.py`` into
``data/vendor/dune/pm_pair_hourly.parquet`` with columns
``market_id`` (Gamma id, str), ``condition_id``, ``hr`` (hours since epoch, UTC
hour start), ``usd``, ``shares``, ``n``, ``o/h/l/c`` (price of outcome 0 =
local YES/answer1), ``last_ts``.

Volume scale note: the local ``data/polymarket/trades`` files are built from
OrderFilled events, which log a fill once per maker order AND once for the
taker order, so their ``usd_amount`` sums run ~2x the Dune taker-only volume
(median monthly ratio Dune/local = 0.51 over 2024-05..2025-09). Use
``scale`` to put the two on the same footing when splicing.

The output of ``hourly_volume`` is a drop-in for the ``pm_hourly_vol`` input of
``pair_observations.build_observations`` (columns market_id, hr, usd).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

DEFAULT_PATH = Path("data/vendor/dune/pm_pair_hourly.parquet")
EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def _to_hours(s: pd.Series) -> pd.Series:
    if s.dtype.kind == "M":
        return ((pd.to_datetime(s, utc=True) - EPOCH) // pd.Timedelta(hours=1)).astype("int64")
    return s.astype("int64")


def load_dune_hourly(path: Path | str = DEFAULT_PATH) -> pd.DataFrame:
    d = pd.read_parquet(path)
    d["market_id"] = d["market_id"].astype(str)
    d["hr"] = _to_hours(d["hr"])
    return d


def hourly_volume(
    dune: pd.DataFrame,
    local: Optional[pd.DataFrame] = None,
    splice_from_ts: Optional[int] = None,
    scale: float = 1.0,
) -> pd.DataFrame:
    """Return (market_id, hr, usd) hourly PM volume.

    * ``local is None``: Dune volume for the whole period, multiplied by ``scale``.
    * otherwise: local volume for hours < ``splice_from_ts`` and Dune volume
      (x ``scale``) from then on. Use ``scale≈2`` to match the local
      double-counted convention, ``scale=1`` for real once-counted volume.
    """
    dv = dune[["market_id", "hr", "usd"]].copy()
    dv["usd"] = dv["usd"].astype(float) * scale
    if local is None:
        return dv.reset_index(drop=True)
    if splice_from_ts is None:
        raise ValueError("splice_from_ts required when splicing with local volume")
    lv = local[["market_id", "hr", "usd"]].copy()
    lv["market_id"] = lv["market_id"].astype(str)
    lv["hr"] = _to_hours(lv["hr"])
    cut = splice_from_ts // 3600
    out = pd.concat([lv[lv["hr"] < cut], dv[dv["hr"] >= cut]], ignore_index=True)
    return out.groupby(["market_id", "hr"], as_index=False)["usd"].sum()

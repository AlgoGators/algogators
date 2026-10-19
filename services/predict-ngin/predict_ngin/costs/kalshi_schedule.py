"""Point-in-time Kalshi fee schedule: which fee type and multiplier a series had at a given time.

Built from two API payloads (feeaudit, 2026-10-15):

* ``GET /series`` -- the current ``fee_type`` / ``fee_multiplier`` of every series;
* ``GET /series/fee_changes?show_historical=true`` -- logged changes (the endpoint launched
  2025-09-21, so changes before ~2025-10 are missing).

Rules for the time before a series' first logged change:

* quadratic x1, except the INX* / NASDAQ100* family, which the 2026-02-05 fee-schedule PDF prices
  at 0.035 (= quadratic x0.5);
* series that charge maker fees today but have no logged change: maker fees from 2025-04-01
  (``MAKER_FEE_START``); :func:`pit` can shift that date for sensitivity runs.

The schedule is a DataFrame with columns ``series, effective_from, fee_type, multiplier, source``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from predict_ngin.costs.kalshi import MAKER_FEE_START
from predict_ngin.shared.paths import data_root

__all__ = [
    "SCHEDULE_START",
    "half_rate_family",
    "build_schedule",
    "build_schedule_from_files",
    "schedule_path",
    "load_schedule",
    "pit",
]

SCHEDULE_START = pd.Timestamp("2021-01-01", tz="UTC")


def half_rate_family(series: str) -> bool:
    """INX / NASDAQ100 index series: 0.035 (= quadratic x0.5) before their logged changes."""
    s = series
    return (
        s.startswith("KXINX")
        or s.startswith("INX")
        or s.startswith("KXNASDAQ100")
        or s.startswith("NASDAQ100")
    )


def build_schedule(
    series_all: list[dict],
    fee_changes: list[dict],
    extra_series: Iterable[str] = (),
) -> pd.DataFrame:
    """Fee schedule from the ``/series`` list and the ``series_fee_change_arr`` list.

    ``extra_series`` adds series missing from ``/series`` (quadratic x1). ``attrs["inconsistent"]``
    lists logged series whose last change disagrees with the current snapshot.
    """
    sa = series_all
    cur = {
        s["ticker"]: (
            s.get("fee_type") or "quadratic",
            float(s["fee_multiplier"]) if s.get("fee_multiplier") is not None else 1.0,
        )
        for s in sa
    }
    chg = pd.DataFrame(fee_changes)
    chg["effective_from"] = pd.to_datetime(chg.scheduled_ts, utc=True, format="ISO8601")
    rows = []
    logged = set(chg.series_ticker)
    for s in sorted(set(cur) | set(extra_series)):
        ft, fm = cur.get(s, ("quadratic", 1.0))
        if s in logged:
            g = chg[chg.series_ticker == s].sort_values("effective_from")
            half = half_rate_family(s)
            rows.append(
                (
                    s,
                    SCHEDULE_START,
                    "quadratic",
                    0.5 if half else 1.0,
                    "fee_schedule_pdf_2026-02-05" if half else "assumed_pre_log",
                )
            )
            for r in g.itertuples():
                rows.append(
                    (s, r.effective_from, r.fee_type, float(r.fee_multiplier), "api_fee_change")
                )
        else:
            src = "api_series_current" if s in cur else "missing_default"
            if "maker" in ft:
                rows.append((s, SCHEDULE_START, "quadratic", fm, "assumed_pre_maker"))
                rows.append((s, MAKER_FEE_START, ft, fm, "assumed_maker_start_2025-04-01"))
            else:
                rows.append((s, SCHEDULE_START, ft, fm, src))
    d = pd.DataFrame(rows, columns=["series", "effective_from", "fee_type", "multiplier", "source"])
    # consistency: the last row per logged series should match the current snapshot
    last = d.sort_values("effective_from").groupby("series").tail(1).set_index("series")
    bad = [
        (s, tuple(last.loc[s, ["fee_type", "multiplier"]]), cur[s])
        for s in logged
        if s in cur and (last.loc[s, "fee_type"], float(last.loc[s, "multiplier"])) != cur[s]
    ]
    d.attrs["inconsistent"] = bad
    return d


def build_schedule_from_files(raw_dir: Path, extra_series: Iterable[str] = ()) -> pd.DataFrame:
    """:func:`build_schedule` from the two JSON payloads saved in ``raw_dir``."""
    raw_dir = Path(raw_dir)
    sa = json.loads((raw_dir / "series_all.json").read_text())["series"]
    ch = json.loads((raw_dir / "series_fee_changes.json").read_text())["series_fee_change_arr"]
    return build_schedule(sa, ch, extra_series)


def schedule_path() -> Path:
    """Default location of the saved schedule: ``<data>/vendor/feeaudit/fee_schedule.parquet``."""
    return data_root() / "vendor" / "feeaudit" / "fee_schedule.parquet"


def load_schedule(path: Path | None = None) -> pd.DataFrame:
    return pd.read_parquet(path or schedule_path())


def pit(o: pd.DataFrame, sched: pd.DataFrame, maker_start=None, maker_series=None) -> pd.DataFrame:
    """Add ``pit_fee_type`` / ``pit_mult`` to rows of ``o`` (columns ``series``, ``ts`` in epoch s).

    The schedule row in force is the latest with ``effective_from <= ts`` for that series; rows with
    no match get quadratic x1. ``maker_start`` (epoch s) moves the assumed start of maker fees for
    ``maker_series`` (default: every series whose maker row is assumed).
    """
    s = sched.copy()
    s["eff"] = (
        (s.effective_from.astype("int64") // 10**9)
        if str(s.effective_from.dtype).startswith("datetime64[ns")
        else s.effective_from.map(lambda t: int(pd.Timestamp(t).timestamp()))
    )
    if maker_start is not None:
        m = s.source.str.startswith("assumed_maker_start")
        if maker_series is not None:
            m &= s.series.isin(maker_series)
        s.loc[m, "eff"] = int(maker_start)
    s = s.sort_values("eff")
    x = o[["series", "ts"]].copy()
    x["_i"] = np.arange(len(x))
    x["ts"] = x.ts.astype("int64")
    x = x.sort_values("ts")
    y = pd.merge_asof(
        x,
        s[["series", "eff", "fee_type", "multiplier"]].rename(columns={"eff": "ts_eff"}),
        left_on="ts",
        right_on="ts_eff",
        by="series",
        direction="backward",
    ).sort_values("_i")
    o = o.copy()
    o["pit_fee_type"] = y.fee_type.fillna("quadratic").to_numpy()
    o["pit_mult"] = y.multiplier.fillna(1.0).to_numpy()
    return o

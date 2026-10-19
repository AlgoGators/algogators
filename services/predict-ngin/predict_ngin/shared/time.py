"""Unit-safe datetime -> integer epoch conversion.

pandas >= 2 keeps the resolution of datetime64 data (s / ms / us / ns), and
pandas 3 defaults to ``datetime64[us]`` for strings, Python datetimes and many
constructors (``ns`` only when a string carries > 6 fractional digits;
``unit="s"`` / ``unit="ms"`` give ``[s]`` / ``[ms]``).  The old idioms

    ts.astype("int64") // 10**9        ts.view("int64") // 10**9

therefore return values 1,000x (us) to 10**9x ([s]) too small *without any
error*.  Use :func:`to_epoch` instead; it subtracts the epoch and floor-divides
by a Timedelta, which is correct for every resolution.

``Timestamp.value`` is always nanoseconds and is safe for scalars.
"""

from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd

__all__ = ["to_epoch", "to_epoch_seconds", "EPOCH_UTC"]

EPOCH_UTC = pd.Timestamp("1970-01-01", tz="UTC")
_UNITS = {"s": "1s", "ms": "1ms", "us": "1us", "ns": "1ns"}

ArrayLike = Union[pd.Series, pd.Index, np.ndarray, list]


def _as_utc(x):
    """Datetime-like (naive -> assumed UTC, aware -> converted) at its own resolution."""
    if isinstance(x, pd.Series):
        if not pd.api.types.is_datetime64_any_dtype(x):
            if pd.api.types.is_numeric_dtype(x):
                raise TypeError(
                    "to_epoch expects datetimes or date strings, got numeric data "
                    "(ambiguous epoch unit); convert it explicitly"
                )
        return pd.to_datetime(x, utc=True)
    if isinstance(x, (pd.Index, np.ndarray, list, tuple)):
        idx = pd.Index(x)
        if not pd.api.types.is_datetime64_any_dtype(idx) and pd.api.types.is_numeric_dtype(idx):
            raise TypeError(
                "to_epoch expects datetimes or date strings, got numeric data "
                "(ambiguous epoch unit); convert it explicitly"
            )
        return pd.DatetimeIndex(pd.to_datetime(idx, utc=True))
    raise TypeError(f"unsupported type {type(x)!r}")


def to_epoch(x, unit: str = "s"):
    """Integer time since 1970-01-01 UTC in ``unit`` ('s', 'ms', 'us', 'ns'), floor-rounded.

    Accepts a Series / Index / array / list of datetimes or date strings, or a
    scalar (Timestamp, datetime, np.datetime64, str).  Naive values are taken
    as UTC.  Returns int64 (``Int64`` for a Series/Index with NaT; ``None`` for
    a NaT scalar).
    """
    if unit not in _UNITS:
        raise ValueError(f"unit must be one of {sorted(_UNITS)}")
    step = pd.Timedelta(_UNITS[unit])

    if isinstance(x, (pd.Series, pd.Index, np.ndarray, list, tuple)):
        ts = _as_utc(x)
        out = (ts - EPOCH_UTC) // step
        if isinstance(out, pd.Series):
            return out.astype("Int64") if out.isna().any() else out.astype("int64")
        out = pd.Series(out, index=None)
        out = out.astype("Int64") if out.isna().any() else out.astype("int64")
        return pd.Index(out)

    if isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool):
        raise TypeError("to_epoch expects a datetime-like scalar, got a number")
    t = pd.Timestamp(x)
    if t is pd.NaT:
        return None
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return int((t - EPOCH_UTC) // step)


def to_epoch_seconds(x):
    """Shorthand for ``to_epoch(x, "s")``."""
    return to_epoch(x, "s")

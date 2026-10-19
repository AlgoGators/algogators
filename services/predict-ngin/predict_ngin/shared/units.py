"""Price and money units: dollars, cents and the cent round-up used by every fee rule."""

from __future__ import annotations

import math

import numpy as np

__all__ = ["CENT", "ceil_cents"]

CENT = 0.01


def ceil_cents(x, *, eps: float | None = None):
    """Round a dollar amount up to the next cent, tolerant of float noise.

    Two noise guards exist in the code base, and both are kept so old results reproduce exactly:

    * ``eps=None`` (default): ``ceil(round(100 x, 6)) / 100``. Values within 5e-7 cents of a whole
      cent snap to it before the round-up. Used by the Kalshi fee schedule (feeaudit, kalshi_flb,
      capbook).
    * ``eps=1e-9``: ``ceil(100 x - eps) / 100``. Used by the arbitrage engine and some screens.
      On cent prices with whole contract counts both give the same result; they can differ for
      sub-cent prices.

    Scalars (Python or NumPy) go through ``math.ceil`` and the built-in ``round`` and return a
    Python float. Arrays and pandas objects go through NumPy and keep their type (a Series stays a
    Series). Lists and tuples are converted to arrays.
    """
    if isinstance(x, (list, tuple)):
        x = np.asarray(x, dtype=float)
    if np.ndim(x) == 0 and not isinstance(x, np.ndarray):
        if eps is None:
            return math.ceil(round(x * 100.0, 6)) / 100.0
        return math.ceil(x * 100.0 - eps) / 100.0
    if eps is None:
        return np.ceil(np.round(x * 100.0, 6)) / 100.0
    return np.ceil(x * 100.0 - eps) / 100.0

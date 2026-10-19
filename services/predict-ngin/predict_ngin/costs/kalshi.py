"""Kalshi trading fees: rates, the per-order cent round-up and who pays the maker fee.

Kalshi fee schedule (effective 2026-02-05, checked against the API in feeaudit, 2026-10-15)::

    taker fee per order = round_up_to_cent(0.07   x multiplier x C x P x (1 - P))
    maker fee per order = round_up_to_cent(0.0175 x multiplier x C x P x (1 - P))

``C`` is the number of contracts and ``P`` the price in dollars. The maker fee applies only on
series whose ``fee_type`` contains ``"maker"``, and only from the date those series began charging
it (assumed 2025-04-01 unless the fee-change log says otherwise; see ``kalshi_schedule``).
Some series use a multiplier (e.g. the INX/NASDAQ100 family at 0.5).

Since 2026-05-28 direct members are charged on a $0.0001 grid with a per-order accumulator, which
is close to no rounding (``rounding="exact"``).

Two legacy conventions survive and are kept so old studies reproduce:

* the multiplier *inside* the round-up (the fee schedule; feeaudit, capbook) or *outside* it
  (``kalshi_flb``, fin_D, mkgap): ``multiplier_in_ceil``;
* the float-noise guard of the round-up: ``eps`` (see :func:`predict_ngin.shared.units.ceil_cents`).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from predict_ngin.shared.units import ceil_cents

__all__ = [
    "TAKER_RATE",
    "MAKER_RATE",
    "MAKER_FEE_START",
    "MAKER_FEE_START_TS",
    "DIRECT_PRECISION_START",
    "DIRECT_PRECISION_TS",
    "ARB_EPS",
    "ceil_cents",
    "kalshi_order_fee",
    "kalshi_fill_fee",
    "maker_fee_applies",
]

TAKER_RATE = 0.07
MAKER_RATE = 0.0175
# Maker fees on maker-fee series, assumed start (Whelan 2025, "Makers and Takers").
MAKER_FEE_START = pd.Timestamp("2025-04-01", tz="UTC")
MAKER_FEE_START_TS = 1743465600  # == MAKER_FEE_START in epoch seconds
# Direct members: $0.0001 balance grid + per-order accumulator from this date.
DIRECT_PRECISION_START = pd.Timestamp("2026-05-28", tz="UTC")
DIRECT_PRECISION_TS = 1779926400  # == DIRECT_PRECISION_START in epoch seconds
# Noise guard used by the arbitrage engine and some screens (``ceil(100 x - 1e-9)``).
ARB_EPS = 1e-9

ROUNDINGS = ("cent", "exact")


def kalshi_order_fee(
    contracts,
    price,
    *,
    maker: bool = False,
    multiplier=1.0,
    rate: float | None = None,
    rounding: str = "cent",
    multiplier_in_ceil: bool = True,
    eps: float | None = None,
):
    """Kalshi fee in dollars for one order (or fill) of ``contracts`` at ``price``.

    ``rate`` defaults to the taker or maker rate. ``rounding="cent"`` rounds up to the cent per
    order; ``"exact"`` returns the unrounded fee. Works on scalars (returns a float) and on arrays
    or Series (keeps the type). Does not decide whether a maker fee applies: see
    :func:`maker_fee_applies` and :func:`kalshi_fill_fee`.
    """
    if rounding not in ROUNDINGS:
        raise ValueError(f"rounding must be one of {ROUNDINGS}, got {rounding!r}")
    r = rate if rate is not None else (MAKER_RATE if maker else TAKER_RATE)
    if multiplier_in_ceil:
        raw = r * multiplier * contracts * price * (1.0 - price)
        return raw if rounding == "exact" else ceil_cents(raw, eps=eps)
    raw = r * contracts * price * (1.0 - price)
    return (raw if rounding == "exact" else ceil_cents(raw, eps=eps)) * multiplier


def kalshi_fill_fee(
    contracts: float,
    price: float,
    maker: bool,
    rate_applies: bool,
    multiplier: float,
    rounding: str,
    ts: int,
) -> tuple[float, float]:
    """(charged fee, exact fee) in dollars for one fill, multiplier inside the round-up.

    ``rounding``: ``"fill_cent"`` (every fill is its own order, round up to the cent),
    ``"exact"`` (no rounding) or ``"regime"`` (``fill_cent`` before 2026-05-28, ``exact`` after).
    A maker fill pays nothing unless ``rate_applies``.
    """
    if maker and not rate_applies:
        return 0.0, 0.0
    ex = (MAKER_RATE if maker else TAKER_RATE) * multiplier * contracts * price * (1.0 - price)
    if rounding == "exact" or (rounding == "regime" and ts >= DIRECT_PRECISION_TS):
        return ex, ex
    return math.ceil(round(ex * 100.0, 6)) / 100.0, ex


def maker_fee_applies(fee_type, ts_unix, mode: str = "series", start: int = MAKER_FEE_START_TS):
    """Boolean array: does a maker fill pay a fee? ``mode`` in {'series', 'all', 'none'}.

    ``'series'``: the series charges maker fees (``fee_type`` contains ``"maker"``) and the fill is
    at or after ``start`` (epoch seconds).
    """
    ts = np.asarray(ts_unix)
    if mode == "none":
        return np.zeros(ts.shape, dtype=bool)
    if mode == "all":
        return np.ones(ts.shape, dtype=bool)
    ft = (
        pd.Series(np.asarray(fee_type, dtype=object))
        .fillna("")
        .astype(str)
        .str.contains("maker")
        .to_numpy()
    )
    return ft & (ts >= start)

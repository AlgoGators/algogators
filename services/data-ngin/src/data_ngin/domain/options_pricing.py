"""Black-76 pricing, implied vol and greeks for the options EOD pass (#32 O5).

Everything here works on numpy arrays (plain floats work too) and has no side
effects, so running the pass twice gives the exact same numbers. T is in years
(ACT/365) and r is a continuous rate.

Greeks are vs the forward, not spot. We don't fetch dividends, so the forward we
back out of the option prices is what everything gets priced off.
"""

from enum import StrEnum
from typing import NamedTuple

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.special import ndtr

# Vol range the solver searches. If a price needs a vol outside this we flag it
# instead of clipping.
MIN_VOL = 1e-4
MAX_VOL = 5.0
_MAX_ITERATIONS = 100
_PRICE_TOLERANCE = 1e-10  # relative to time value (price minus discounted intrinsic)
_BRACKET_TOLERANCE = 1e-12
_SQRT_2PI = np.sqrt(2.0 * np.pi)


class IvStatus(StrEnum):
    """Whether we got an implied vol for a contract, and if not, why."""

    OK = "ok"
    INVALID_INPUT = "invalid_input"  # NaN, zero or negative price / forward / strike / T
    BELOW_INTRINSIC = "below_intrinsic"  # price <= discounted intrinsic, no time value
    ABOVE_MAX_PRICE = "above_max_price"  # price >= the most the option could be worth
    VOL_BELOW_MIN = "vol_below_min"  # would need a vol under MIN_VOL
    VOL_ABOVE_MAX = "vol_above_max"  # would need a vol over MAX_VOL
    NO_CONVERGENCE = "no_convergence"


class Greeks(NamedTuple):
    """Black-76 greeks.

    Units: delta and gamma are per 1 unit of the forward, vega is per 1.00 of vol
    (divide by 100 for one vol point), theta is per year (divide by 365 for per day).
    """

    delta: NDArray[np.float64]
    gamma: NDArray[np.float64]
    vega: NDArray[np.float64]
    theta: NDArray[np.float64]


def _norm_pdf(x: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.exp(-0.5 * x * x) / _SQRT_2PI


def _as_arrays(
    *values: ArrayLike, is_call: ArrayLike
) -> tuple[list[NDArray[np.float64]], NDArray[np.bool_]]:
    arrays = np.broadcast_arrays(*(np.asarray(v, dtype=np.float64) for v in values))
    calls = np.broadcast_to(np.asarray(is_call, dtype=bool), arrays[0].shape)
    return [np.array(a) for a in arrays], np.array(calls)


def _d1(
    F: NDArray[np.float64],
    K: NDArray[np.float64],
    T: NDArray[np.float64],
    sigma: NDArray[np.float64],
) -> NDArray[np.float64]:
    vol_t = sigma * np.sqrt(T)
    return (np.log(F / K) + 0.5 * vol_t * vol_t) / vol_t


def discount_yield_to_continuous(rate_pct: ArrayLike, days: int = 91) -> NDArray[np.float64]:
    """Turn FRED's DTB3 (T-bill discount yield, in percent) into a continuous rate.

    DTB3 is a bank-discount quote, so the bill's price is 1 - d * days / 360. The
    continuous rate is whatever grows that price back to 1 over days / 365 years.
    """
    d = np.asarray(rate_pct, dtype=np.float64) / 100.0
    price = 1.0 - d * days / 360.0
    return -np.log(price) * 365.0 / days


def black76_price(
    F: ArrayLike, K: ArrayLike, T: ArrayLike, r: ArrayLike, sigma: ArrayLike, is_call: ArrayLike
) -> NDArray[np.float64]:
    """Black-76 price of a European option on forward F."""
    (F, K, T, r, sigma), calls = _as_arrays(F, K, T, r, sigma, is_call=is_call)
    d1 = _d1(F, K, T, sigma)
    d2 = d1 - sigma * np.sqrt(T)
    discount = np.exp(-r * T)
    call = discount * (F * ndtr(d1) - K * ndtr(d2))
    put = discount * (K * ndtr(-d2) - F * ndtr(-d1))
    return np.where(calls, call, put)


def black76_greeks(
    F: ArrayLike, K: ArrayLike, T: ArrayLike, r: ArrayLike, sigma: ArrayLike, is_call: ArrayLike
) -> Greeks:
    """Delta, gamma, vega and theta vs the forward. Units are on Greeks."""
    (F, K, T, r, sigma), calls = _as_arrays(F, K, T, r, sigma, is_call=is_call)
    sqrt_t = np.sqrt(T)
    d1 = _d1(F, K, T, sigma)
    discount = np.exp(-r * T)
    density = _norm_pdf(d1)
    price = black76_price(F, K, T, r, sigma, calls)

    delta = np.where(calls, discount * ndtr(d1), -discount * ndtr(-d1))
    gamma = discount * density / (F * sigma * sqrt_t)
    vega = discount * F * density * sqrt_t
    # theta = -dV/dT holding F fixed. Same formula for calls and puts: r * V from
    # discounting, minus the time decay term.
    theta = r * price - discount * F * density * sigma / (2.0 * sqrt_t)
    return Greeks(delta=delta, gamma=gamma, vega=vega, theta=theta)


def implied_vol(
    price: ArrayLike, F: ArrayLike, K: ArrayLike, T: ArrayLike, r: ArrayLike, is_call: ArrayLike
) -> tuple[NDArray[np.float64], NDArray[np.object_]]:
    """Find the vol that gives back each price under Black-76.

    Returns (vols, statuses). Vol is NaN wherever the status isn't OK.

    Prices that can't have a vol (below intrinsic, above the max, etc.) are flagged
    first. The rest go to Newton's method, kept inside a [lo, hi] range that shrinks
    every step. If a Newton step would jump outside the range we bisect instead, so it
    always lands as long as the answer is between MIN_VOL and MAX_VOL.
    """
    (price, F, K, T, r), calls = _as_arrays(price, F, K, T, r, is_call=is_call)
    n = price.size
    vols = np.full(price.shape, np.nan)
    status = np.full(price.shape, IvStatus.OK.value, dtype=object)

    with np.errstate(invalid="ignore", divide="ignore"):
        valid = (
            np.isfinite(price) & np.isfinite(F) & np.isfinite(K) & np.isfinite(T) & np.isfinite(r)
        )
        valid &= (price > 0) & (F > 0) & (K > 0) & (T > 0)
        status[~valid] = IvStatus.INVALID_INPUT.value
        if n == 0 or not valid.any():
            return vols, status

        discount = np.exp(-r * T)
        intrinsic = discount * np.where(calls, np.maximum(F - K, 0.0), np.maximum(K - F, 0.0))
        ceiling = discount * np.where(calls, F, K)
        pending = valid.copy()
        for mask, reason in (
            (price <= intrinsic, IvStatus.BELOW_INTRINSIC),
            (price >= ceiling, IvStatus.ABOVE_MAX_PRICE),
            (price < black76_price(F, K, T, r, MIN_VOL, calls), IvStatus.VOL_BELOW_MIN),
            (price > black76_price(F, K, T, r, MAX_VOL, calls), IvStatus.VOL_ABOVE_MAX),
        ):
            hit = pending & mask
            status[hit] = reason.value
            pending &= ~hit

    idx = np.flatnonzero(pending)
    if idx.size:
        solved, converged = _solve(
            price.ravel()[idx],
            F.ravel()[idx],
            K.ravel()[idx],
            T.ravel()[idx],
            r.ravel()[idx],
            calls.ravel()[idx],
        )
        flat_vols = vols.ravel()
        flat_status = status.ravel()
        flat_vols[idx[converged]] = solved[converged]
        flat_status[idx[~converged]] = IvStatus.NO_CONVERGENCE.value
    return vols, status


def _solve(
    price: NDArray[np.float64],
    F: NDArray[np.float64],
    K: NDArray[np.float64],
    T: NDArray[np.float64],
    r: NDArray[np.float64],
    calls: NDArray[np.bool_],
) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
    discount = np.exp(-r * T)
    sqrt_t = np.sqrt(T)
    lo = np.full(price.shape, MIN_VOL)
    hi = np.full(price.shape, MAX_VOL)
    # Starting guess: Brenner-Subrahmanyam ATM approximation.
    sigma = np.clip(np.sqrt(2.0 * np.pi / T) * price / (discount * F), MIN_VOL * 2, MAX_VOL / 2)
    converged = np.zeros(price.shape, dtype=bool)
    # Tolerance is relative to time value (vol doesn't move intrinsic), so cheap wing
    # options still solve precisely. If floats run out of precision first, the range
    # getting tiny stops the loop instead.
    intrinsic = discount * np.where(calls, np.maximum(F - K, 0.0), np.maximum(K - F, 0.0))
    tolerance = _PRICE_TOLERANCE * (price - intrinsic)

    for _ in range(_MAX_ITERATIONS):
        diff = black76_price(F, K, T, r, sigma, calls) - price
        converged = (np.abs(diff) <= tolerance) | (hi - lo <= _BRACKET_TOLERANCE)
        if converged.all():
            break
        # Price goes up with vol, so the sign of diff tells us which side to move.
        hi = np.where(diff > 0, sigma, hi)
        lo = np.where(diff < 0, sigma, lo)
        vega = discount * F * _norm_pdf(_d1(F, K, T, sigma)) * sqrt_t
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            newton = sigma - diff / vega
        inside = np.isfinite(newton) & (newton > lo) & (newton < hi)
        stepped = np.where(inside, newton, 0.5 * (lo + hi))
        sigma = np.where(converged, sigma, stepped)
    return sigma, converged


def forward_from_parity(
    strikes: ArrayLike, call_prices: ArrayLike, put_prices: ArrayLike, T: float, r: float
) -> float:
    """Forward for one expiry from put-call parity.

    Takes the strike where call and put are closest in price and does
    F = K + e^(rT) (C - P), per #32 O5. If two strikes tie we use the lower one.
    Strikes missing a call or put price are skipped; returns NaN if none are left.
    """
    K = np.asarray(strikes, dtype=np.float64)
    C = np.asarray(call_prices, dtype=np.float64)
    P = np.asarray(put_prices, dtype=np.float64)
    usable = np.isfinite(K) & np.isfinite(C) & np.isfinite(P)
    if not usable.any():
        return float("nan")
    K, C, P = K[usable], C[usable], P[usable]
    best = np.lexsort((K, np.abs(C - P)))[0]
    return float(K[best] + np.exp(r * T) * (C[best] - P[best]))


def atm_vol(forward: float, strikes: ArrayLike, vols: ArrayLike) -> float:
    """ATM vol for one expiry: linear interpolation of the smile (in strike) at the forward.

    Returns NaN if the forward isn't between two strikes that have a vol.
    """
    K = np.asarray(strikes, dtype=np.float64)
    V = np.asarray(vols, dtype=np.float64)
    usable = np.isfinite(K) & np.isfinite(V)
    if not np.isfinite(forward) or not usable.any():
        return float("nan")
    K, V = K[usable], V[usable]
    at = forward == K
    if at.any():
        return float(V[at].mean())
    below, above = forward > K, forward < K
    if not below.any() or not above.any():
        return float("nan")
    k_lo = K[below].max()
    k_hi = K[above].min()
    v_lo = V[below & (k_lo == K)].mean()
    v_hi = V[above & (k_hi == K)].mean()
    weight = (forward - k_lo) / (k_hi - k_lo)
    return float(v_lo + weight * (v_hi - v_lo))


def vol_at_tenor(years: ArrayLike, vols: ArrayLike, target_years: float) -> float:
    """Vol at target_years from a term structure, interpolating linearly in total variance.

    Total variance = vol^2 * T. No extrapolation: returns NaN if target_years isn't
    between two expiries.
    """
    T = np.asarray(years, dtype=np.float64)
    V = np.asarray(vols, dtype=np.float64)
    usable = np.isfinite(T) & np.isfinite(V) & (T > 0)
    T, V = T[usable], V[usable]
    if T.size == 0:
        return float("nan")
    order = np.argsort(T, kind="stable")
    T, V = T[order], V[order]
    exact = target_years == T
    if exact.any():
        return float(V[exact][0])
    below = np.flatnonzero(target_years > T)
    above = np.flatnonzero(target_years < T)
    if below.size == 0 or above.size == 0:
        return float("nan")
    i, j = below[-1], above[0]
    w_i, w_j = V[i] ** 2 * T[i], V[j] ** 2 * T[j]
    w = w_i + (w_j - w_i) * (target_years - T[i]) / (T[j] - T[i])
    return float(np.sqrt(w / target_years))

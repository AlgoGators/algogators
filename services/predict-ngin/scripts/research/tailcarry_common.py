"""tailcarry: shared rule, exit, netting, APY and fee helpers for the long-dated tail-NO carry study (research only).

Rule (user brief #6, pre-registered in docs/TAILCARRY_LONG_DATED_NO_APY.md):
  rest a NO bid (causal Fa/Fb/Fc level from src/data/fills.py) while
    - YES has traded at or below 8c continuously for >= 7 days: the last YES print > 8c (if any) was followed by a
      <= 8c print at least 7 days before now, and the market's first print is >= 7 days old;
    - more than 30 days remain to the market's latest_expiration_time (set at listing; NOT the actual close, which
      moves earlier when a market is decided early and would look ahead);
  all state from prints in EARLIER seconds only. Hold to settlement, or exit (taker, sell NO) when a YES print
  >= 21c (i.e. above 20c) occurs in a later second than the fill: sell at the first print >= 60 s after the trigger
  at NO = 100 - yes - 1c, paying the taker fee.
Sizing: 2% of equity per event (gross cost), 25% per Kalshi category, one event = one bet.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numba as nb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts/research"))
from src.timeconv import to_epoch_seconds  # noqa: E402

OUT = ROOT / "data/vendor/tailcarry"
DAY = 86400
TAIL_C = 8          # YES <= 8c
LOOKBACK = 7 * DAY  # for >= 7 days
MIN_LEFT = 30 * DAY  # > 30 days remaining
EXIT_C = 21         # YES > 20c
EXIT_LAT = 60       # seconds from trigger to exit print


def ts(s: str) -> int:
    return int(to_epoch_seconds(pd.Series([pd.Timestamp(s, tz="UTC")]))[0])


# Pre-registered split (by fill / entry time); positions are held to settlement whenever that is.
T0 = ts("2021-07-01")
SPLIT = ts("2025-07-01")
T_END = 1772409600  # 2026-03-02 00:00 UTC: tape (trades_fixed + _ext) end at study time; frozen
MAKER_FEE_START = ts("2025-04-01")  # repo convention (mkgap): maker-fee series charged from 2025-04-01

# Kalshi APY on net portfolio value (cash + positions at last trade). Dated points: 4.05% from Thu 2024-10-10
# (launch FAQ); 3.50% cited 2026-01-21; 3.50% on the help page 2026-10-15. Change dates between are NOT published;
# the steps below are an assumption (Fed cuts 2025-09-17 and 2025-12-10).
APY_PATH = [(ts("2024-10-10"), 0.0405), (ts("2025-09-17"), 0.0375), (ts("2025-12-10"), 0.0350)]


def apy_at(t) -> np.ndarray:
    t = np.asarray(t, np.int64)
    r = np.zeros(t.shape, float)
    for t0, v in APY_PATH:
        r = np.where(t >= t0, v, r)
    return r


def apy_accrual(t_in, t_out, value) -> np.ndarray:
    """Interest on `value` dollars held from t_in to t_out under APY_PATH (simple daily accrual, act/365)."""
    t_in = np.asarray(t_in, np.int64); t_out = np.asarray(t_out, np.int64); value = np.asarray(value, float)
    edges = [t for t, _ in APY_PATH]
    out = np.zeros(len(t_in))
    bounds = edges + [2 ** 62]
    for (t0, v), t1 in zip(APY_PATH, bounds[1:]):
        lo = np.maximum(t_in, t0); hi = np.minimum(t_out, t1)
        out += np.where(hi > lo, (hi - lo) / DAY / 365.0 * v, 0.0)
    return out * value


@nb.njit(cache=True)
def signal_elig(tk, sec, yc, lexp, t_lo, t_hi, tail_c, lookback, min_left):
    """Per print: rule state at the START of its second (earlier seconds only). Inputs sorted by (ticker, sec)."""
    n = len(tk)
    out = np.zeros(n, np.bool_)
    a = 0
    while a < n:
        b = a
        while b < n and tk[b] == tk[a]:
            b += 1
        first = sec[a]
        low_since = -1  # time the current run of <= tail_c prints began; -1 = currently above
        k = a
        while k < b:
            s = sec[k]; m = k
            while m < b and sec[m] == s:
                m += 1
            ok = (first <= s - lookback) and (low_since >= 0) and (low_since <= s - lookback) \
                and (lexp[k] - s > min_left) and (s >= t_lo) and (s < t_hi)
            for r in range(k, m):
                out[r] = ok
            for r in range(k, m):
                if yc[r] > tail_c:
                    low_since = -1
                elif low_since < 0:
                    low_since = sec[r]
            k = m
        a = b
    return out


@nb.njit(cache=True)
def exit_rows(tk, sec, yc, rows, exit_c, lat):
    """For each fill row (first print of its fill second): index of the exit print, or -1 (hold).
    Trigger = first print with yc >= exit_c in a LATER second of the same ticker; exit print = first print with
    sec >= trigger_sec + lat."""
    n = len(tk)
    out = np.full(len(rows), -1, np.int64)
    trig = np.full(len(rows), -1, np.int64)
    for i in range(len(rows)):
        r = rows[i]
        t = tk[r]; s0 = sec[r]
        j = r
        while j < n and tk[j] == t and sec[j] == s0:
            j += 1
        while j < n and tk[j] == t and yc[j] < exit_c:
            j += 1
        if j >= n or tk[j] != t:
            continue
        trig[i] = j
        st = sec[j] + lat
        while j < n and tk[j] == t and sec[j] < st:
            j += 1
        if j < n and tk[j] == t:
            out[i] = j
    return out, trig


def no_payoff(result, settle_value_yes):
    """NO payoff per contract in $: 'no' -> 1, 'yes' -> 0, anything else (scalar / void / last-fair-price) ->
    1 - settlement_value (YES value in $). NaN if unknown."""
    r = np.asarray(result, object)
    v = pd.to_numeric(pd.Series(settle_value_yes), errors="coerce").to_numpy()
    out = np.where(r == "no", 1.0, np.where(r == "yes", 0.0, 1.0 - v))
    return out


def maker_fee_total(q_cents, n, fee_maker, mult=1.0):
    """Kalshi maker fee in $ on an n-contract fill: ceil(0.0175 n p (1-p) * 100) / 100 x multiplier."""
    p = np.asarray(q_cents, float) / 100.0
    n = np.asarray(n, float)
    f = np.ceil(np.round(0.0175 * n * p * (1 - p) * 100.0, 6)) / 100.0 * np.asarray(mult, float)
    return np.where(np.asarray(fee_maker, bool) & (n > 0), f, 0.0)


def taker_fee_total(p_cents, n, mult=1.0):
    p = np.asarray(p_cents, float) / 100.0
    n = np.asarray(n, float)
    f = np.ceil(np.round(0.07 * n * p * (1 - p) * 100.0, 6)) / 100.0 * np.asarray(mult, float)
    return np.where(n > 0, f, 0.0)


def netted_locked(qty, cost, mutually_exclusive: bool) -> float:
    """Collateral locked by open NO positions in ONE event. Without netting: total cost. With Kalshi collateral
    return on a mutually exclusive event (at most one market resolves YES), the worst case pays every NO except the
    largest one, so locked = cost - (sum q - max q), floored at 0."""
    qty = np.asarray(qty, float); cost = np.asarray(cost, float)
    if len(qty) == 0:
        return 0.0
    c = float(cost.sum())
    if not mutually_exclusive or len(qty) < 2:
        return c
    return max(0.0, c - (float(qty.sum()) - float(qty.max())))


def cluster_ci(num, den=None, n_boot=2000, seed=0):
    """Event-cluster bootstrap 95% CI of sum(num)/sum(den) (den None -> sum(num))."""
    num = np.asarray(num, float)
    if len(num) < 2:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(num), size=(n_boot, len(num)))
    if den is None:
        r = num[idx].sum(1)
    else:
        den = np.asarray(den, float)
        r = num[idx].sum(1) / np.maximum(den[idx].sum(1), 1e-12)
    return float(np.quantile(r, 0.025)), float(np.quantile(r, 0.975))

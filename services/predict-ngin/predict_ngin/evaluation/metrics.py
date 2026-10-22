"""Sharpe ratio, probabilistic and deflated Sharpe (PSR/DSR), and the cluster bootstrap.

Canonical arithmetic is the one of ``src/strategies_legal/kalshi_flb.py`` (used through
``audit_flb_common`` by about 50 research scripts): ``statistics.NormalDist``, the full
Euler-Mascheroni constant, a 1e-9 floor on the PSR denominator, ``max(n_obs - 1, 1)``.

Annualisation is a parameter, never implied: ``periods_per_year`` is 365 for the calendar-day
P&L series of the Kalshi studies, 252 for trading-day series, 8760 for hourly ones, and ``None``
for per-observation (non-annualised) inputs. Studies that used other conventions are listed in
docs/ARCHITECTURE.md (deliberate variants, metrics).
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

__all__ = [
    "EULER_GAMMA",
    "sharpe",
    "expected_max_sharpe",
    "psr_z",
    "probabilistic_sharpe",
    "deflated_sharpe",
    "bootstrap_totals",
    "cluster_bootstrap_totals",
    "cluster_bootstrap_ci",
]

EULER_GAMMA = 0.5772156649015329
_ND = NormalDist()


def sharpe(returns, periods_per_year: Optional[float] = 365.0, ddof: int = 1) -> float:
    """Mean over standard deviation, times ``sqrt(periods_per_year)`` (``None``: per period).

    Returns 0.0 when the standard deviation is zero or undefined (fewer than ``ddof + 1`` values).
    """
    s = pd.Series(returns, dtype=float)
    sd = s.std(ddof=ddof)
    if not (sd and sd > 0):
        return 0.0
    if periods_per_year is None:
        return float(s.mean() / sd)
    return float(s.mean() / sd * math.sqrt(periods_per_year))


def expected_max_sharpe(n_trials: int, sr_var: float) -> float:
    """Expected maximum of ``n_trials`` null Sharpe estimates with variance ``sr_var``
    (Bailey and Lopez de Prado 2014, eq. 6), in the units of ``sr_var``. 0 for one trial."""
    if n_trials > 1 and sr_var > 0:
        g = EULER_GAMMA
        return math.sqrt(sr_var) * ((1 - g) * _ND.inv_cdf(1 - 1 / n_trials)
                                    + g * _ND.inv_cdf(1 - 1 / (n_trials * math.e)))
    return 0.0


def psr_z(sr: float, n_obs: int, skew: float = 0.0, kurt: float = 3.0,
          sr_benchmark: float = 0.0) -> float:
    """The z statistic of the probabilistic Sharpe ratio; ``sr`` per observation, ``kurt``
    non-excess (3 for a normal)."""
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    return (sr - sr_benchmark) * math.sqrt(max(n_obs - 1, 1)) / math.sqrt(max(den, 1e-9))


def probabilistic_sharpe(sr: float, n_obs: int, skew: float = 0.0, kurt: float = 3.0,
                         sr_benchmark: float = 0.0) -> float:
    """PSR: probability that the true per-observation Sharpe exceeds ``sr_benchmark``."""
    return _ND.cdf(psr_z(sr, n_obs, skew, kurt, sr_benchmark))


def deflated_sharpe(observed_sr: float, n_trials: int, sr_var: float, n_obs: int,
                    skew: float = 0.0, kurt: float = 3.0,
                    periods_per_year: Optional[float] = 365.0) -> Dict[str, float]:
    """Deflated Sharpe ratio (Bailey and Lopez de Prado 2014).

    ``observed_sr`` and ``sr_var`` (the variance of the Sharpe ratios across trials) are annualised
    with ``periods_per_year`` and converted to per-observation units; pass ``None`` when they are
    already per observation. Returns ``cutoff_sr_ann`` (the expected maximum null Sharpe, in the
    input units), ``dsr`` (the probability) and ``z``.
    """
    ppy = 1.0 if periods_per_year is None else periods_per_year
    sr = observed_sr / math.sqrt(ppy)
    var = sr_var / ppy
    cutoff = expected_max_sharpe(n_trials, var)
    z = psr_z(sr, n_obs, skew, kurt, cutoff)
    return {"cutoff_sr_ann": cutoff * math.sqrt(ppy), "dsr": _ND.cdf(z), "z": z}


def bootstrap_totals(values, n_boot: int = 2000, seed: int = 0) -> np.ndarray:
    """``n_boot`` bootstrap totals of ``values`` (one value per cluster, resampled with
    replacement, ``numpy.random.default_rng(seed)``)."""
    g = np.asarray(values)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(n_boot, len(g)))
    return g[idx].sum(axis=1)


def cluster_bootstrap_totals(fills: pd.DataFrame, n_boot: int = 2000, seed: int = 0,
                             col: str = "pnl", cluster: str = "event_ticker") -> np.ndarray:
    """Bootstrap distribution of the total of ``col``, resampling ``cluster`` groups with
    replacement (``n_boot`` draws, ``numpy.random.default_rng(seed)``)."""
    return bootstrap_totals(fills.groupby(cluster)[col].sum().to_numpy(), n_boot, seed)


def cluster_bootstrap_ci(fills: pd.DataFrame, n_boot: int = 2000, seed: int = 0,
                         col: str = "pnl", cluster: str = "event_ticker",
                         alpha: float = 0.05) -> Tuple[float, float]:
    """Two-sided ``1 - alpha`` percentile interval of the cluster-bootstrap total.

    Returns (0.0, 0.0) for an empty frame.
    """
    if fills.empty:
        return (0.0, 0.0)
    tot = cluster_bootstrap_totals(fills, n_boot, seed, col, cluster)
    return (float(np.quantile(tot, alpha / 2)), float(np.quantile(tot, 1 - alpha / 2)))

"""
Statistics for honest strategy evaluation.

- Calendar-daily returns (zero-PnL days included over the whole evaluation window).
- Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014) on per-period (daily) SR.
- Bootstrap CI for a difference in means (optionally clustered and/or stratified).
"""

from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
import pandas as pd
from scipy import stats

EULER_GAMMA = 0.5772156649015329


def daily_returns_full_calendar(
    trades_df: pd.DataFrame,
    capital: float,
    start,
    end,
    date_col: str = "exit_date",
    pnl_col: str = "net_pnl",
) -> pd.Series:
    """Daily equity returns over every calendar day in [start, end] (PnL realised on exit)."""
    idx = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), freq="D")
    if trades_df is None or trades_df.empty:
        return pd.Series(0.0, index=idx)
    pnl = trades_df.groupby(pd.to_datetime(trades_df[date_col]).dt.normalize())[pnl_col].sum()
    pnl = pnl.reindex(idx, fill_value=0.0)
    equity_prev = capital + pnl.cumsum().shift(1, fill_value=0.0)
    return (pnl / equity_prev).astype(float)


def expected_max_sr(n_trials: int, sr_variance: float) -> float:
    """E[max SR] over N independent null trials (False Strategy Theorem)."""
    if n_trials is None or n_trials <= 1 or sr_variance <= 0:
        return 0.0
    n = float(n_trials)
    return math.sqrt(sr_variance) * (
        (1 - EULER_GAMMA) * stats.norm.ppf(1 - 1.0 / n)
        + EULER_GAMMA * stats.norm.ppf(1 - 1.0 / (n * math.e))
    )


def deflated_sharpe_ratio(
    returns: pd.Series,
    n_trials: int,
    sr_variance: Optional[float] = None,
) -> Dict[str, float]:
    """
    Deflated Sharpe Ratio on per-period returns (no annualisation).

    sr_variance: cross-trial variance of SR estimates. Unknown here (sweep results not
    stored), so defaults to the null sampling variance of the SR estimator, 1/(n-1).
    """
    r = pd.Series(returns).astype(float).dropna().to_numpy()
    n = len(r)
    out = {"n": n, "sr": 0.0, "skew": 0.0, "kurt": 3.0, "sr0": 0.0, "psr": 0.0, "dsr": 0.0,
           "n_trials": n_trials, "sr_annual": 0.0}
    if n < 3:
        return out
    sd = r.std(ddof=1)
    if not np.isfinite(sd) or sd <= 0:
        return out
    sr = float(r.mean() / sd)
    g3 = float(stats.skew(r))
    g4 = float(stats.kurtosis(r, fisher=False))
    if sr_variance is None:
        sr_variance = 1.0 / (n - 1)
    sr0 = expected_max_sr(n_trials, sr_variance)
    denom = 1 - g3 * sr + (g4 - 1) / 4.0 * sr ** 2
    denom = math.sqrt(max(denom, 1e-12))
    z = (sr - sr0) * math.sqrt(n - 1) / denom
    z_psr = sr * math.sqrt(n - 1) / denom
    out.update(sr=sr, skew=g3, kurt=g4, sr0=sr0, dsr=float(stats.norm.cdf(z)),
               psr=float(stats.norm.cdf(z_psr)), sr_annual=sr * math.sqrt(365))
    return out


def _stratified_mean(vals: np.ndarray, strata: np.ndarray, weights: Dict) -> float:
    total = 0.0
    wsum = 0.0
    for k, w in weights.items():
        m = strata == k
        if m.any():
            total += w * vals[m].mean()
            wsum += w
    return total / wsum if wsum > 0 else float("nan")


def bootstrap_mean_diff_ci(
    a,
    b,
    n_boot: int = 5000,
    seed: int = 0,
    alpha: float = 0.05,
    a_groups=None,
    b_groups=None,
    a_strata=None,
    b_strata=None,
) -> Dict[str, float]:
    """
    Percentile bootstrap CI for mean(a) - mean(b).

    *_groups: resample clusters (e.g. market ids) instead of observations.
    *_strata: if given, mean(b) is reweighted to a's stratum mix (e.g. entry-price bucket),
              so the difference is not driven by a different price mix.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    rng = np.random.default_rng(seed)
    stratified = a_strata is not None and b_strata is not None
    if stratified:
        a_strata = np.asarray(a_strata)
        b_strata = np.asarray(b_strata)

    def _weights(sa):
        ks, cnt = np.unique(sa, return_counts=True)
        return dict(zip(ks, cnt / cnt.sum()))

    def _stat(ai, bi):
        if stratified:
            w = _weights(a_strata[ai])
            mb = _stratified_mean(b[bi], b_strata[bi], w)
        else:
            mb = b[bi].mean()
        return a[ai].mean() - mb

    def _index_sampler(n, groups):
        if groups is None:
            return lambda: rng.integers(0, n, n)
        groups = np.asarray(groups)
        uniq, inv = np.unique(groups, return_inverse=True)
        members = [np.flatnonzero(inv == g) for g in range(len(uniq))]
        return lambda: np.concatenate([members[i] for i in rng.integers(0, len(uniq), len(uniq))])

    diff = float(_stat(np.arange(len(a)), np.arange(len(b))))
    sa = _index_sampler(len(a), a_groups)
    sb = _index_sampler(len(b), b_groups)
    boots = np.array([_stat(sa(), sb()) for _ in range(n_boot)], dtype=float)
    boots = boots[np.isfinite(boots)]
    lo, hi = np.quantile(boots, [alpha / 2, 1 - alpha / 2])
    return {"diff": diff, "lo": float(lo), "hi": float(hi),
            "p_le_0": float((boots <= 0).mean()), "n_a": int(len(a)), "n_b": int(len(b))}

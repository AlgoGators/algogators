"""Frozen copies of the metrics replaced in step 5.6 (2026-10-15), verbatim as of commit b452506.
Do not edit; the golden tests compare against these."""
from __future__ import annotations

import math
from typing import Dict

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- src/strategies_legal/kalshi_flb.py:201-241
def sharpe_daily(s: pd.Series) -> float:
    s = pd.Series(s, dtype=float)
    sd = s.std(ddof=1)
    return float(s.mean() / sd * math.sqrt(365)) if sd and sd > 0 else 0.0


def cluster_bootstrap_ci(fills: pd.DataFrame, n_boot: int = 2000, seed: int = 0,
                         col: str = "pnl", cluster: str = "event_ticker", alpha: float = 0.05):
    """CI of total PnL resampling events with replacement."""
    if fills.empty:
        return (0.0, 0.0)
    g = fills.groupby(cluster)[col].sum().to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(n_boot, len(g)))
    tot = g[idx].sum(axis=1)
    return (float(np.quantile(tot, alpha / 2)), float(np.quantile(tot, 1 - alpha / 2)))


def deflated_sharpe(observed_sr_ann: float, n_trials: int, sr_var_ann: float, n_obs: int,
                    skew: float = 0.0, kurt: float = 3.0, ppy: float = 365.0) -> Dict[str, float]:
    """Bailey & Lopez de Prado (2014) DSR; inputs annualised, converted per-observation."""
    from statistics import NormalDist
    nd = NormalDist()
    sr = observed_sr_ann / math.sqrt(ppy)
    var = sr_var_ann / ppy
    if n_trials > 1 and var > 0:
        g = 0.5772156649015329
        cutoff = math.sqrt(var) * ((1 - g) * nd.inv_cdf(1 - 1 / n_trials)
                                   + g * nd.inv_cdf(1 - 1 / (n_trials * math.e)))
    else:
        cutoff = 0.0
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    z = (sr - cutoff) * math.sqrt(max(n_obs - 1, 1)) / math.sqrt(max(den, 1e-9))
    return {"cutoff_sr_ann": cutoff * math.sqrt(ppy), "dsr": nd.cdf(z), "z": z}


# ---------------------------------------------------------------- scripts/research/audit_flb_common.py:175
def audit_metrics(f, t0, t1, cap, n_boot=2000):
    yrs = (t1 - t0) / 86400 / 365.25
    idx = pd.date_range(pd.to_datetime(t0, unit="s").normalize(), pd.to_datetime(t1, unit="s").normalize(), freq="D")
    if f.empty:
        return dict(fills=0, events=0, pnl=0.0, pnl_yr=0.0, ci_lo=0.0, ci_hi=0.0, avg_dep=0.0, peak_dep=0.0,
                    ret_turn=np.nan, sharpe=0.0, ndays=len(idx), skew=0.0, kurt=3.0, loss_fills=0, yrs=yrs)
    s = f.groupby(pd.to_datetime(f.settle_ts, unit="s").dt.normalize()).pnl.sum().reindex(idx, fill_value=0.0)
    ev = pd.concat([pd.DataFrame({"t": f.ts.clip(t0, t1), "v": f.cost}),
                    pd.DataFrame({"t": f.settle_ts.clip(t0, t1), "v": -f.cost})]).sort_values("t", kind="mergesort")
    lvl = ev.v.cumsum().to_numpy(); dt = np.diff(ev.t.to_numpy(), append=ev.t.to_numpy()[-1])
    g = f.groupby("event_ticker").pnl.sum().to_numpy()
    rng = np.random.default_rng(0)
    tot = g[rng.integers(0, len(g), size=(n_boot, len(g)))].sum(1) / yrs
    sd = s.std()
    return dict(fills=len(f), events=f.event_ticker.nunique(), pnl=float(f.pnl.sum()), pnl_yr=float(f.pnl.sum() / yrs),
                ci_lo=float(np.quantile(tot, .025)), ci_hi=float(np.quantile(tot, .975)),
                avg_dep=float((lvl * dt).sum() / (t1 - t0)), peak_dep=float(lvl.max()),
                ret_turn=float(f.pnl.sum() / f.cost.sum()), sharpe=float(s.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0,
                ndays=len(s), skew=float(s.skew()) if sd > 0 else 0.0, kurt=float(s.kurt() + 3) if sd > 0 else 3.0,
                loss_fills=int((f.pnl < 0).sum()), yrs=yrs, avg_px=float((f.price * f.qty).sum() / f.qty.sum()),
                win_rate=float(f.payout.sum() / f.qty.sum()))


# ---------------------------------------------------------------- scripts/research/capbook_forward.py:1171
def capbook_boot_ci(ev_pnl: np.ndarray, n=2000):
    if len(ev_pnl) < 2:
        return (float(ev_pnl.sum()), float(ev_pnl.sum()))
    rng = np.random.default_rng(0)
    s = ev_pnl[rng.integers(0, len(ev_pnl), size=(n, len(ev_pnl)))].sum(1)
    return float(np.quantile(s, .025)), float(np.quantile(s, .975))

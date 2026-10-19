"""Golden tests for step 5.6 (Sharpe / PSR / DSR / bootstrap -> predict_ngin.evaluation).

The shims and repointed helpers are compared with their frozen copies (tests/unit/_legacy_metrics.py)
by exact equality: kalshi_flb.sharpe_daily / deflated_sharpe / cluster_bootstrap_ci,
audit_flb_common.metrics (Sharpe and bootstrap CI now from the core) and capbook_forward._boot_ci.
"""
from __future__ import annotations

import itertools
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "research"))

import _legacy_metrics as LG  # noqa: E402

from predict_ngin.evaluation import (  # noqa: E402
    bootstrap_totals,
    cluster_bootstrap_ci,
    deflated_sharpe,
    expected_max_sharpe,
    probabilistic_sharpe,
    sharpe,
)


def _series():
    rng = np.random.default_rng(42)
    out = [[], [1.5], [2.0, 2.0, 2.0], [0.0] * 30, [1.0, np.nan, 3.0, -2.0], list(range(10))]
    for n in (2, 3, 30, 365, 1000):
        out.append(list(rng.normal(0.3, 2.0, n)))
        out.append(list(rng.standard_t(3, n) * 50))
        out.append(list(np.where(rng.random(n) < 0.9, 0.0, rng.normal(5, 20, n))))
    return out


def test_sharpe_daily_identical():
    K = pytest.importorskip("src.strategies_legal.kalshi_flb")  # private module: skipped in a public clone
    for s in _series():
        assert K.sharpe_daily(pd.Series(s, dtype=float)) == LG.sharpe_daily(pd.Series(s, dtype=float))
        assert K.sharpe_daily(s) == LG.sharpe_daily(s)
        assert sharpe(s) == LG.sharpe_daily(s)


def test_sharpe_annualisation_is_a_parameter():
    s = [0.1, -0.2, 0.3, 0.05]
    per = sharpe(s, periods_per_year=None)
    assert per == pytest.approx(np.mean(s) / np.std(s, ddof=1))
    assert sharpe(s, periods_per_year=252) == pytest.approx(per * math.sqrt(252))
    assert sharpe([1.0]) == 0.0 and sharpe([]) == 0.0


GRID = list(itertools.product(
    [-3.0, -0.5, 0.0, 0.4, 1.1, 2.5, 6.0],      # observed annualised SR
    [1, 2, 7, 100, 8000],                       # trials
    [0.0, 0.05, 0.6, 3.0],                      # annualised cross-trial SR variance
    [0, 1, 2, 30, 365, 2000],                   # observations
    [(0.0, 3.0), (-1.7, 9.5), (2.2, 1.2), (0.5, -40.0)],  # skew, kurt (last: den below the floor)
    [365.0, 252.0, 1.0],                        # periods per year
))


def test_deflated_sharpe_identical():
    K = pytest.importorskip("src.strategies_legal.kalshi_flb")  # private module: skipped in a public clone
    for sr, nt, var, n, (sk, ku), ppy in GRID:
        exp = LG.deflated_sharpe(sr, nt, var, n, sk, ku, ppy)
        assert K.deflated_sharpe(sr, nt, var, n, sk, ku, ppy) == exp
        assert K.deflated_sharpe(sr, nt, var, n, skew=sk, kurt=ku, ppy=ppy) == exp
        assert deflated_sharpe(sr, nt, var, n, sk, ku, periods_per_year=ppy) == exp
    assert K.deflated_sharpe(1.0, 10, 0.5, 100) == LG.deflated_sharpe(1.0, 10, 0.5, 100)


def test_dsr_pieces_consistent():
    d = deflated_sharpe(0.03, 50, 0.0004, 500, periods_per_year=None)
    assert d["cutoff_sr_ann"] == expected_max_sharpe(50, 0.0004)
    assert d["dsr"] == probabilistic_sharpe(0.03, 500, sr_benchmark=d["cutoff_sr_ann"])
    assert expected_max_sharpe(1, 1.0) == 0.0 and expected_max_sharpe(10, 0.0) == 0.0


def _fills(seed, n, n_ev):
    rng = np.random.default_rng(seed)
    t0 = 1_700_000_000
    ts = np.sort(rng.integers(t0, t0 + 200 * 86400, n))
    qty = rng.integers(1, 50, n).astype(float)
    price = rng.uniform(0.05, 0.95, n).round(2)
    cost = qty * price + rng.uniform(0, 0.5, n).round(2)
    payout = qty * (rng.random(n) < price)
    return pd.DataFrame({"oid": np.arange(n), "ts": ts, "ticker": [f"T{i % 17}" for i in range(n)],
                         "event_ticker": [f"E{rng.integers(0, n_ev)}" for _ in range(n)],
                         "settle_ts": ts + rng.integers(3600, 20 * 86400, n), "qty": qty, "price": price,
                         "fee": 0.0, "cost": cost, "payout": payout, "pnl": payout - cost})


FILLS = [_fills(s, n, e) for s, n, e in ((0, 1, 1), (1, 5, 2), (2, 60, 9), (3, 400, 80), (4, 2000, 700))]


def test_cluster_bootstrap_ci_identical():
    K = pytest.importorskip("src.strategies_legal.kalshi_flb")  # private module: skipped in a public clone
    for f in FILLS + [FILLS[0].iloc[:0]]:
        for nb, seed, alpha in ((2000, 0, 0.05), (500, 7, 0.1), (1, 3, 0.05)):
            exp = LG.cluster_bootstrap_ci(f, nb, seed, alpha=alpha)
            assert K.cluster_bootstrap_ci(f, nb, seed, alpha=alpha) == exp
            assert cluster_bootstrap_ci(f, nb, seed, alpha=alpha) == exp
        assert K.cluster_bootstrap_ci(f, col="cost", cluster="ticker") == LG.cluster_bootstrap_ci(
            f, col="cost", cluster="ticker")


def _same(a, b):
    assert a.keys() == b.keys()
    for k in a:
        x, y = a[k], b[k]
        assert (x == y) or (isinstance(x, float) and math.isnan(x) and math.isnan(y)), k


def test_audit_flb_metrics_identical():
    A = pytest.importorskip("audit_flb_common")  # private module: skipped in a public clone
    t0, t1 = 1_700_000_000, 1_700_000_000 + 260 * 86400
    for f in FILLS + [FILLS[0].iloc[:0]]:
        for nb in (2000, 300):
            _same(A.metrics(f, t0, t1, 1000, n_boot=nb), LG.audit_metrics(f, t0, t1, 1000, n_boot=nb))
    const = FILLS[2].assign(pnl=0.0)  # zero-variance daily series
    _same(A.metrics(const, t0, t1, 1000), LG.audit_metrics(const, t0, t1, 1000))


def test_capbook_boot_ci_identical():
    CF = pytest.importorskip("capbook_forward")  # private module: skipped in a public clone
    rng = np.random.default_rng(5)
    for v in (np.array([]), np.array([3.5]), rng.normal(0, 10, 2), rng.normal(1, 10, 300)):
        assert CF._boot_ci(v) == LG.capbook_boot_ci(v)
        assert CF._boot_ci(v, n=77) == LG.capbook_boot_ci(v, n=77)
    assert (bootstrap_totals([1.0, 2.0], 4, seed=1) == bootstrap_totals(np.array([1.0, 2.0]), 4, seed=1)).all()

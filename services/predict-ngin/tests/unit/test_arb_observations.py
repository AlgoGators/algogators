"""Tests for pair observation building (as-of joins, outcomes, polarity)."""

import numpy as np
import pandas as pd
import pytest

from src.trading.arbitrage.pair_observations import build_observations, polarity_check

T0 = int(pd.Timestamp("2025-06-01T00:00:00Z").timestamp())
H = 3600


def _setup(tmp_path, pm_yes_is_k_yes=True):
    kt = pd.DataFrame(
        {
            "ticker": ["K"] * 5,
            "ts": [T0 + 100, T0 + 3000, T0 + H + 200, T0 + 5 * H, T0 + 5 * H + 60],
            "p": [0.40, 0.42, 0.50, 0.99, 0.99],
            "n": [10, 20, 5, 7, 1],
            "side": ["yes", "no", "yes", "yes", "yes"],
        }
    )
    path = tmp_path / "k.parquet"
    kt.to_parquet(path)
    pairs = pd.DataFrame(
        {"family": ["sports_nba"], "pm_id": ["7"], "pm_q": ["A vs. B"], "k_ticker": ["K"],
         "pm_yes_is_k_yes": [pm_yes_is_k_yes], "pm_end": [None]}
    )
    pm_prices = pd.DataFrame(
        {"market_id": ["7"] * 3, "ts": [T0 + 2950, T0 + 3010, T0 + H + 190], "price": [0.45, 0.90, 0.52]}
    )
    km = pd.DataFrame({"ticker": ["K"], "result": ["yes"]})
    pmm = pd.DataFrame({"id": ["7"], "outcome_prices": ["['1', '0']"]})
    return pairs, path, pm_prices, km, pmm


def test_asof_join_never_uses_future_pm_price(tmp_path):
    pairs, path, pm_prices, km, pmm = _setup(tmp_path)
    out = build_observations(pairs, path, pm_prices, None, km, pmm, pm_max_staleness_s=120)
    o = out["obs"]
    first = o[o.hr == T0 // H].iloc[0]
    assert first["k_price"] == pytest.approx(0.42)  # last Kalshi trade of the hour
    assert first["pm_price"] == pytest.approx(0.45)  # PM at 2950 <= 3000, not 0.90 at 3010
    assert first["k_usd_vol"] == pytest.approx(10 * 0.40 + 20 * (1 - 0.42))


def test_outcomes_and_resolve_time(tmp_path):
    pairs, path, pm_prices, km, pmm = _setup(tmp_path)
    P = build_observations(pairs, path, pm_prices, None, km, pmm)["pairs"]
    r = P.iloc[0]
    assert r["k_outcome"] == 1.0 and r["pm_outcome"] == 1.0
    assert r["resolve_ts"] == T0 + 5 * H  # first trade after last in-range print
    assert r["category"] == "sports"


def test_polarity_flip(tmp_path):
    pairs, path, pm_prices, km, pmm = _setup(tmp_path, pm_yes_is_k_yes=False)
    out = build_observations(pairs, path, pm_prices, None, km, pmm)
    assert out["obs"].iloc[0]["pm_price"] == pytest.approx(0.55)
    assert out["pairs"].iloc[0]["pm_outcome"] == 0.0


def test_polarity_check_flags_negative_corr():
    obs = pd.DataFrame({"pair_id": ["a"] * 12, "k_price": np.linspace(0.1, 0.9, 12),
                        "pm_price": np.linspace(0.9, 0.1, 12)})
    assert polarity_check(obs).iloc[0]["corr"] < 0

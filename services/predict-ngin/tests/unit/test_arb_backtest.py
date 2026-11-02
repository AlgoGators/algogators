"""Tests for hold-to-resolution cross-platform arb accounting (fees, spreads, lockup)."""

import math

import numpy as np
import pandas as pd
import pytest

from src.trading.arbitrage.fees import FeeModel, kalshi_fee, polymarket_fee
from src.trading.arbitrage.resolution_arb import (
    ResolutionArbConfig,
    executable_legs,
    run_resolution_arb,
)

H = 3600
DAY = 86400
T0 = int(pd.Timestamp("2025-06-01T00:00:00Z").timestamp())

NOFEE = FeeModel(kalshi_taker_coef=0.0, poly_fees_enabled=False)


def _pairs(**over):
    base = dict(
        pair_id=["p1"],
        category=["sports"],
        resolve_ts=[T0 + 10 * DAY],
        k_outcome=[1.0],
        pm_outcome=[1.0],
        k_last_price=[0.99],
        pm_last_price=[0.99],
    )
    base.update(over)
    return pd.DataFrame(base)


def _obs(rows):
    return pd.DataFrame(rows, columns=["pair_id", "ts", "k_price", "pm_price", "k_usd_vol", "pm_usd_vol"])


class TestExecutableLegs:
    def test_half_spread_applied_to_each_leg(self):
        cfg = ResolutionArbConfig(kalshi_half_spread=0.01, poly_half_spread=0.005, fees=NOFEE)
        legs = executable_legs(k_price=0.40, pm_price=0.50, cfg=cfg)
        # Kalshi cheap: buy K YES at ask, buy PM NO at its ask
        assert legs["direction"] == "k_yes_pm_no"
        assert legs["k_fill"] == pytest.approx(0.41)
        assert legs["pm_fill"] == pytest.approx(0.505)
        assert legs["gross_cost"] == pytest.approx(0.915)

    def test_other_direction(self):
        cfg = ResolutionArbConfig(kalshi_half_spread=0.01, poly_half_spread=0.005, fees=NOFEE)
        legs = executable_legs(k_price=0.60, pm_price=0.50, cfg=cfg)
        assert legs["direction"] == "pm_yes_k_no"
        assert legs["k_fill"] == pytest.approx(0.41)  # NO on Kalshi at 1-0.60+0.01
        assert legs["pm_fill"] == pytest.approx(0.505)


class TestSettlement:
    def test_both_resolve_yes_pays_one_per_contract_pair(self):
        cfg = ResolutionArbConfig(
            total_capital=1000, max_capital_per_pair=1000, kalshi_half_spread=0.0,
            poly_half_spread=0.0, volume_cap_frac=1.0, fees=NOFEE, min_net_edge_per_contract=0.0,
        )
        obs = _obs([["p1", T0, 0.40, 0.50, 1e9, 1e9]])
        res = run_resolution_arb(obs, _pairs(), cfg)
        t = res.trades.iloc[0]
        assert t["direction"] == "k_yes_pm_no"
        C = t["contracts"]
        assert C == math.floor(1000 / 0.90)
        assert t["payout"] == pytest.approx(C * 1.0)
        assert t["pnl"] == pytest.approx(C * 1.0 - C * 0.90)
        assert t["status"] == "settled"
        assert not t["outcome_mismatch"]

    def test_outcome_mismatch_settles_each_leg_on_its_own_platform(self):
        cfg = ResolutionArbConfig(
            total_capital=1000, kalshi_half_spread=0.0, poly_half_spread=0.0,
            volume_cap_frac=1.0, fees=NOFEE, min_net_edge_per_contract=0.0,
        )
        obs = _obs([["p1", T0, 0.40, 0.50, 1e9, 1e9]])
        # bought K YES + PM NO; Kalshi resolves NO, Polymarket resolves YES -> both legs lose
        res = run_resolution_arb(obs, _pairs(k_outcome=[0.0], pm_outcome=[1.0]), cfg)
        t = res.trades.iloc[0]
        assert t["payout"] == 0.0
        assert t["pnl"] == pytest.approx(-t["cost"])
        assert t["outcome_mismatch"]

    def test_fees_charged_per_fill(self):
        fm = FeeModel()
        cfg = ResolutionArbConfig(
            total_capital=100, max_capital_per_pair=100, kalshi_half_spread=0.0, poly_half_spread=0.0,
            volume_cap_frac=1.0, fees=fm, min_net_edge_per_contract=0.0,
        )
        obs = _obs([["p1", T0, 0.30, 0.50, 1e9, 1e9]])
        res = run_resolution_arb(obs, _pairs(), cfg)
        t = res.trades.iloc[0]
        C = int(t["contracts"])
        assert t["k_fee"] == pytest.approx(kalshi_fee(C, 0.30))
        assert t["pm_fee"] == pytest.approx(polymarket_fee(C, 0.50, "sports"))
        assert t["cost"] == pytest.approx(C * (0.30 + 0.50) + t["k_fee"] + t["pm_fee"])
        assert t["cost"] <= 100 + 1e-9

    def test_no_trade_when_costs_exceed_gap(self):
        cfg = ResolutionArbConfig(kalshi_half_spread=0.01, poly_half_spread=0.0075, fees=FeeModel())
        obs = _obs([["p1", T0, 0.49, 0.51, 1e9, 1e9]])
        res = run_resolution_arb(obs, _pairs(), cfg)
        assert res.trades.empty


class TestCapacityAndLockup:
    def test_volume_cap_limits_size(self):
        cfg = ResolutionArbConfig(
            total_capital=5000, max_capital_per_pair=5000, kalshi_half_spread=0.0, poly_half_spread=0.0,
            volume_cap_frac=0.05, fees=NOFEE, min_net_edge_per_contract=0.0,
        )
        obs = _obs([["p1", T0, 0.40, 0.50, 2000.0, 10000.0]])
        res = run_resolution_arb(obs, _pairs(), cfg)
        t = res.trades.iloc[0]
        # Kalshi leg notional capped at 5% of $2000 = $100 -> 250 contracts at 0.40
        # (PM leg: 5% of $10000 = $500 >= 250 * 0.50)
        assert t["contracts"] == 250
        assert t["contracts"] * t["k_fill"] <= 100.0 + 1e-9

    def test_volume_cap_measured_per_leg_notional(self):
        cfg = ResolutionArbConfig(
            total_capital=5000, max_capital_per_pair=5000, kalshi_half_spread=0.0, poly_half_spread=0.0,
            volume_cap_frac=0.10, fees=NOFEE, min_net_edge_per_contract=0.0,
        )
        obs = _obs([["p1", T0, 0.40, 0.50, 1000.0, 1e9]])
        res = run_resolution_arb(obs, _pairs(), cfg)
        t = res.trades.iloc[0]
        assert t["contracts"] * 0.40 <= 100 + 1e-9  # Kalshi leg notional within 10% of $1000

    def test_capital_locked_until_resolution(self):
        cfg = ResolutionArbConfig(
            total_capital=100, max_capital_per_pair=100, kalshi_half_spread=0.0, poly_half_spread=0.0,
            volume_cap_frac=1.0, fees=NOFEE, min_net_edge_per_contract=0.0,
        )
        pairs = pd.concat(
            [_pairs(), _pairs(pair_id=["p2"], resolve_ts=[T0 + 20 * DAY])], ignore_index=True
        )
        obs = _obs(
            [
                ["p1", T0, 0.40, 0.50, 1e9, 1e9],
                ["p2", T0 + DAY, 0.40, 0.50, 1e9, 1e9],  # all capital locked in p1 -> no trade
                ["p2", T0 + 11 * DAY, 0.40, 0.50, 1e9, 1e9],  # p1 settled -> can trade
            ]
        )
        res = run_resolution_arb(obs, pairs, cfg)
        assert len(res.trades) == 2
        t2 = res.trades.sort_values("entry_ts").iloc[1]
        assert t2["entry_ts"] == T0 + 11 * DAY
        # cash after p1 settles includes p1 profit
        assert t2["cost"] > 100.0 * 0.99

    def test_open_trade_marked_not_dropped(self):
        cfg = ResolutionArbConfig(
            total_capital=100, kalshi_half_spread=0.0, poly_half_spread=0.0,
            volume_cap_frac=1.0, fees=NOFEE, min_net_edge_per_contract=0.0,
        )
        pairs = _pairs(k_outcome=[np.nan], pm_outcome=[np.nan], k_last_price=[0.45], pm_last_price=[0.47])
        obs = _obs([["p1", T0, 0.40, 0.50, 1e9, 1e9]])
        res = run_resolution_arb(obs, pairs, cfg)
        t = res.trades.iloc[0]
        assert t["status"] == "open_marked"
        C = t["contracts"]
        # K YES marked at 0.45, PM NO marked at 1-0.47
        assert t["payout"] == pytest.approx(C * (0.45 + 0.53))

    def test_annualized_return_on_locked_capital(self):
        cfg = ResolutionArbConfig(
            total_capital=1000, kalshi_half_spread=0.0, poly_half_spread=0.0,
            volume_cap_frac=1.0, fees=NOFEE, min_net_edge_per_contract=0.0,
        )
        obs = _obs([["p1", T0, 0.40, 0.50, 1e9, 1e9]])
        res = run_resolution_arb(obs, _pairs(resolve_ts=[T0 + 73 * DAY]), cfg)
        t = res.trades.iloc[0]
        ret = t["pnl"] / t["cost"]
        assert t["lock_days"] == pytest.approx(73.0)
        assert res.summary["ann_return_on_locked"] == pytest.approx(ret * 365 / 73, rel=1e-6)

    def test_no_entry_after_resolution(self):
        cfg = ResolutionArbConfig(fees=NOFEE, kalshi_half_spread=0, poly_half_spread=0, volume_cap_frac=1.0)
        obs = _obs([["p1", T0 + 11 * DAY, 0.40, 0.50, 1e9, 1e9]])
        res = run_resolution_arb(obs, _pairs(), cfg)
        assert res.trades.empty

    def test_monthly_table(self):
        cfg = ResolutionArbConfig(fees=NOFEE, kalshi_half_spread=0, poly_half_spread=0, volume_cap_frac=1.0,
                                  min_net_edge_per_contract=0.0)
        obs = _obs([["p1", T0, 0.40, 0.50, 1e9, 1e9]])
        res = run_resolution_arb(obs, _pairs(), cfg)
        assert {"month", "net_pnl", "deployed", "n_trades"}.issubset(res.monthly.columns)
        assert res.monthly["net_pnl"].sum() == pytest.approx(res.trades["pnl"].sum())

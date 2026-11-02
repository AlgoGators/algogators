"""Tests for fixes in ArbitrageStrategy / run_arbitrage_backtest (signal-driven engine)."""

import numpy as np
import pandas as pd
import pytest

from src.trading.arbitrage.arbitrage_strategy import ArbitrageSignal, ArbitrageStrategy
from src.trading.arbitrage.cross_platform_backtest import (
    ArbitrageBacktestConfig,
    run_arbitrage_backtest,
)
from src.trading.arbitrage.fees import FeeModel, kalshi_fee, polymarket_fee

T0 = int(pd.Timestamp("2025-06-01T00:00:00Z").timestamp())
DAY = 86400


class _Store:
    """Minimal price store stub: fixed prices, no extra data."""

    def __init__(self, poly=None, kalshi=None, kalshi_volume=None):
        self.poly, self.kalshi, self.kvol = poly, kalshi, kalshi_volume

    # as-of lookups: nothing known at entry time -> engine falls back to signal prices
    def get_polymarket_price(self, market_id, ts, outcome="YES"):
        return None

    def get_kalshi_price(self, ticker, ts):
        return None

    def get_polymarket_last_price(self, market_id, outcome="YES"):
        return None if self.poly is None else {"timestamp": T0 + DAY, "price": self.poly}

    def get_kalshi_last_price(self, ticker):
        return None if self.kalshi is None else {"timestamp": T0 + DAY, "price": self.kalshi}

    def get_kalshi_volume(self, ticker, ts):
        return self.kvol


def _entry(poly=0.50, kalshi=0.40, ts=T0):
    return ArbitrageSignal(
        pair_id="p", polymarket_id="P", kalshi_ticker="K", signal_type="entry", timestamp=ts,
        poly_price=poly, kalshi_price=kalshi, spread=poly - kalshi, abs_spread=abs(poly - kalshi),
        direction="buy_kalshi" if poly > kalshi else "buy_poly", confidence=0.5, z_score=2.0,
        expected_profit=0.05,
    )


class TestStrategyCosts:
    def test_cost_is_per_contract_fee_plus_half_spreads_not_profit_fee(self):
        s = ArbitrageStrategy(fee_model=FeeModel(), kalshi_half_spread=0.01, poly_half_spread=0.005)
        cost = s.estimate_cost_per_contract(poly_price=0.50, kalshi_price=0.40, category="sports")
        expected = 0.07 * 0.41 * 0.59 + 0.05 * 0.505 * 0.495 + 0.01 + 0.005
        assert cost == pytest.approx(expected, abs=2e-3)


class TestLegacyBacktest:
    def test_hold_to_resolution_settles_both_legs(self):
        cfg = ArbitrageBacktestConfig(
            position_size=90.0, starting_capital=1000.0, fill_latency_seconds=0,
            kalshi_half_spread=0.0, poly_half_spread=0.0, fee_model=FeeModel(kalshi_taker_coef=0.0, poly_fees_enabled=False),
            hold_to_resolution=True,
        )
        res = run_arbitrage_backtest(
            [_entry()], _Store(), cfg,
            resolutions={"p": {"k_outcome": 1.0, "pm_outcome": 1.0, "resolve_ts": T0 + 10 * DAY}},
        )
        t = res.trades_df.iloc[0]
        assert t["status"] == "settled"
        C = t["contracts"]
        assert C == 100  # $90 / 0.90 per pair
        assert t["net_pnl"] == pytest.approx(100 - 90.0)

    def test_fees_are_per_fill(self):
        cfg = ArbitrageBacktestConfig(
            position_size=90.0, starting_capital=1000.0, fill_latency_seconds=0,
            kalshi_half_spread=0.0, poly_half_spread=0.0, fee_model=FeeModel(), hold_to_resolution=True,
            category="sports",
        )
        res = run_arbitrage_backtest(
            [_entry()], _Store(), cfg,
            resolutions={"p": {"k_outcome": 0.0, "pm_outcome": 0.0, "resolve_ts": T0 + DAY}},
        )
        t = res.trades_df.iloc[0]
        C = int(t["contracts"])
        assert t["kalshi_fees"] == pytest.approx(kalshi_fee(C, 0.40))
        assert t["poly_fees"] == pytest.approx(polymarket_fee(C, 0.50, "sports"))

    def test_open_positions_marked_with_flag(self):
        cfg = ArbitrageBacktestConfig(
            position_size=90.0, starting_capital=1000.0, fill_latency_seconds=0,
            kalshi_half_spread=0.0, poly_half_spread=0.0, fee_model=FeeModel(kalshi_taker_coef=0.0, poly_fees_enabled=False),
        )
        store = _Store(poly=0.48, kalshi=0.44)
        res = run_arbitrage_backtest([_entry()], store, cfg)
        t = res.trades_df.iloc[0]
        assert t["status"] == "open"
        assert bool(t["marked_to_last"]) is True
        C = t["contracts"]
        # K YES 0.40 -> 0.44 ; PM NO 0.50 -> 0.52
        assert t["gross_pnl"] == pytest.approx(C * (0.04 + 0.02))

    def test_size_capped_by_kalshi_volume(self):
        cfg = ArbitrageBacktestConfig(
            position_size=900.0, starting_capital=10000.0, max_capital_per_pair=10000, fill_latency_seconds=0,
            kalshi_half_spread=0.0, poly_half_spread=0.0, fee_model=FeeModel(kalshi_taker_coef=0.0, poly_fees_enabled=False),
            volume_cap_frac=0.1, hold_to_resolution=True,
        )
        res = run_arbitrage_backtest(
            [_entry()], _Store(kalshi_volume=200), cfg,
            resolutions={"p": {"k_outcome": 1.0, "pm_outcome": 1.0, "resolve_ts": T0 + DAY}},
        )
        assert res.trades_df.iloc[0]["contracts"] == 20

"""
Unit tests for whale_surprise module.

Tests rolling whale identification, surprise metrics, and performance scoring.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root and src are in path
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Import whale_surprise directly to avoid whale_strategy __init__ pulling in trading
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "whale_surprise",
    SRC / "whale_strategy" / "whale_surprise.py",
)
_whale_surprise = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_whale_surprise)

identify_whales_rolling = _whale_surprise.identify_whales_rolling
calculate_surprise_metrics = _whale_surprise.calculate_surprise_metrics
calculate_performance_score_with_surprise = _whale_surprise.calculate_performance_score_with_surprise
MIN_CAPITAL_WHALE = _whale_surprise.MIN_CAPITAL_WHALE
CAPITAL_MULTIPLIER = _whale_surprise.CAPITAL_MULTIPLIER


def _make_trades(
    n: int,
    traders: list,
    market_ids: list,
    base_usd: float = 1000,
    direction: str = "BUY",
) -> pd.DataFrame:
    """Create synthetic trades for testing."""
    base_time = pd.Timestamp("2026-01-01 12:00:00")
    rows = []
    for i in range(n):
        t_idx = i % len(traders)
        m_idx = i % len(market_ids)
        rows.append({
            "maker": traders[t_idx],
            "market_id": market_ids[m_idx],
            "datetime": base_time + pd.Timedelta(minutes=i),
            "price": 0.50 + (i % 10) * 0.02,
            "usd_amount": base_usd * (1 + i % 5),
            "maker_direction": direction if i % 2 == 0 else ("SELL" if direction == "BUY" else "BUY"),
        })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_trades_for_whale_id():
    """Trades with one clear whale (50k+ capital) and many small traders."""
    whale = "0xwhale123"
    small = [f"0xsmall{i:02d}" for i in range(20)]
    traders = [whale] + small
    markets = [f"0xmarket{i:02d}" for i in range(5)]

    # Whale: 15 trades of 15k each (max 15k -> 5*15k=75k capital)
    whale_trades = []
    base_time = pd.Timestamp("2026-01-01 12:00:00")
    for i in range(15):
        whale_trades.append({
            "maker": whale,
            "market_id": markets[i % 5],
            "datetime": base_time + pd.Timedelta(minutes=i),
            "price": 0.55,
            "usd_amount": 15_000,
            "maker_direction": "BUY",
        })

    # Small: many trades of 500 each
    small_trades = []
    for i in range(100):
        small_trades.append({
            "maker": small[i % 20],
            "market_id": markets[i % 5],
            "datetime": base_time + pd.Timedelta(minutes=100 + i),
            "price": 0.50,
            "usd_amount": 500,
            "maker_direction": "BUY",
        })

    df = pd.concat([
        pd.DataFrame(whale_trades),
        pd.DataFrame(small_trades),
    ], ignore_index=True)
    return df.sort_values("datetime").reset_index(drop=True)


@pytest.fixture
def resolved_trades_with_known_outcomes():
    """Trades with known resolutions for surprise calculation."""
    base_time = pd.Timestamp("2026-01-01 12:00:00")
    rows = []
    # 10 BUY @ 0.50 in YES market -> expected 50%, actual 100%
    for i in range(10):
        rows.append({
            "maker": "0xwhale1",
            "market_id": "0xmarket_yes",
            "datetime": base_time + pd.Timedelta(minutes=i),
            "price": 0.50,
            "usd_amount": 5000,
            "maker_direction": "BUY",
        })
    # 10 SELL @ 0.50 in NO market -> expected 50%, actual 100% (SELL NO = bet YES loses = bet NO wins)
    for i in range(10):
        rows.append({
            "maker": "0xwhale1",
            "market_id": "0xmarket_no",
            "datetime": base_time + pd.Timedelta(minutes=10 + i),
            "price": 0.50,
            "usd_amount": 5000,
            "maker_direction": "SELL",
        })

    return pd.DataFrame(rows)


@pytest.fixture
def resolution_winners():
    """Market_id -> winner mapping."""
    return {
        "0xmarket_yes": "YES",
        "0xmarket_no": "NO",
    }


class TestIdentifyWhalesRolling:
    """Tests for rolling whale identification."""

    def test_whale_identified_by_capital(self, sample_trades_for_whale_id):
        """Trader with 15k max position (5x=75k) should be whale."""
        result = identify_whales_rolling(sample_trades_for_whale_id, trader_col="maker")
        whale_trades = result[result["is_whale"]]
        assert "0xwhale123" in whale_trades["maker"].values
        # Whale's first trade: not yet whale (no prior capital). Later trades: whale.
        whale_first_idx = result[result["maker"] == "0xwhale123"].index[0]
        # After first 15k trade, capital = 75k, so next trade is whale
        assert whale_trades["maker"].nunique() >= 1

    def test_small_traders_not_whales(self, sample_trades_for_whale_id):
        """Traders with <50k capital should not be whales."""
        result = identify_whales_rolling(sample_trades_for_whale_id, trader_col="maker")
        whale_trades = result[result["is_whale"]]
        small_addrs = [f"0xsmall{i:02d}" for i in range(20)]
        for addr in small_addrs:
            assert addr not in whale_trades["maker"].values

    def test_no_lookahead(self, sample_trades_for_whale_id):
        """Whale status uses only data before current trade."""
        result = identify_whales_rolling(sample_trades_for_whale_id, trader_col="maker")
        # First trade of whale: before any trades, capital=0, so is_whale=False
        first_idx = result[result["maker"] == "0xwhale123"].index[0]
        # At first trade, whale has no prior trades -> capital 0 -> not whale
        assert not result.loc[first_idx, "is_whale"]
        # After first 15k trade, next trade should be whale
        second_whale_idx = result[result["maker"] == "0xwhale123"].index[1]
        assert bool(result.loc[second_whale_idx, "is_whale"])


class TestCalculateSurpriseMetrics:
    """Tests for surprise win rate calculation."""

    def test_expected_equals_actual_when_perfect(self, resolved_trades_with_known_outcomes, resolution_winners):
        """When all trades win, surprise = actual - expected."""
        # BUY @ 0.50 in YES market: expected 50%, actual 100% -> surprise +50%
        buy_trades = resolved_trades_with_known_outcomes[
            (resolved_trades_with_known_outcomes["maker_direction"] == "BUY") &
            (resolved_trades_with_known_outcomes["market_id"] == "0xmarket_yes")
        ].copy()
        metrics = calculate_surprise_metrics(
            buy_trades, resolution_winners, direction_col="maker_direction"
        )
        assert abs(metrics["expected_win_rate"] - 0.50) < 0.01
        assert abs(metrics["actual_win_rate"] - 1.0) < 0.01
        assert abs(metrics["surprise_win_rate"] - 0.50) < 0.01

    def test_sell_no_expected_correct(self, resolution_winners):
        """SELL @ price p means expected = 1-p (betting on YES via short NO)."""
        # Need 5+ rows for metrics (calculate_surprise_metrics returns NaN for small samples)
        df = pd.DataFrame([{
            "maker": "0xw",
            "market_id": "0xmarket_no",
            "price": 0.70,  # NO at 70c -> YES at 30c, we're short NO so bet NO wins
            "maker_direction": "SELL",
        }] * 6)
        metrics = calculate_surprise_metrics(df, resolution_winners, direction_col="maker_direction")
        # SELL: expected = 1 - 0.70 = 0.30. Market resolved NO, so we win (short NO).
        assert abs(metrics["expected_win_rate"] - 0.30) < 0.01
        assert abs(metrics["actual_win_rate"] - 1.0) < 0.01
        assert abs(metrics["surprise_win_rate"] - 0.70) < 0.01

    def test_small_sample_returns_nan(self, resolution_winners):
        """Fewer than 5 resolved trades returns NaN metrics."""
        df = pd.DataFrame([
            {"maker": "0xw", "market_id": "0xmarket_yes", "price": 0.5, "maker_direction": "BUY"},
        ] * 2)
        metrics = calculate_surprise_metrics(df, resolution_winners, direction_col="maker_direction")
        assert np.isnan(metrics["expected_win_rate"])
        assert np.isnan(metrics["actual_win_rate"])
        assert np.isnan(metrics["surprise_win_rate"])


class TestCalculatePerformanceScoreWithSurprise:
    """Tests for surprise-based performance scoring."""

    def test_returns_none_for_few_trades(self, resolution_winners):
        """Whale with <10 resolved trades returns None."""
        df = _make_trades(5, ["0xwhale1"], ["0xmarket_yes"], base_usd=5000)
        df["market_id"] = "0xmarket_yes"
        result = calculate_performance_score_with_surprise(
            "0xwhale1", df, resolution_winners, direction_col="maker_direction"
        )
        assert result is None

    def test_positive_surprise_high_score(self, resolution_winners):
        """Whale beating expectations gets high score."""
        # 20 BUY @ 0.30 in YES market -> expected 30%, actual 100% -> surprise +70%
        rows = []
        for i in range(20):
            rows.append({
                "maker": "0xwhale1",
                "market_id": "0xmarket_yes",
                "datetime": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "price": 0.30,
                "usd_amount": 5000,
                "maker_direction": "BUY",
            })
        df = pd.DataFrame(rows)
        result = calculate_performance_score_with_surprise(
            "0xwhale1", df, resolution_winners, direction_col="maker_direction"
        )
        assert result is not None
        assert result["surprise_win_rate"] > 0.5
        assert result["score"] > 7.0
        assert result["actual_win_rate"] > result["expected_win_rate"]

    def test_negative_surprise_low_score(self, resolution_winners):
        """Whale underperforming gets low score."""
        # 20 BUY @ 0.80 in NO market -> expected 80%, actual 0% -> surprise -80%
        rows = []
        for i in range(20):
            rows.append({
                "maker": "0xwhale2",
                "market_id": "0xmarket_no",
                "datetime": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "price": 0.80,
                "usd_amount": 5000,
                "maker_direction": "BUY",
            })
        df = pd.DataFrame(rows)
        result = calculate_performance_score_with_surprise(
            "0xwhale2", df, resolution_winners, direction_col="maker_direction"
        )
        assert result is not None
        assert result["surprise_win_rate"] < -0.5
        assert result["score"] < 5.0

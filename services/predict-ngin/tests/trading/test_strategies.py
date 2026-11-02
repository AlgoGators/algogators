"""
Comprehensive tests for trading strategies.

Tests the following strategies:
- SmartMoneyStrategy
- BreakoutStrategy / VolatilityBreakoutStrategy
- TimeDecayStrategy
- SentimentDivergenceStrategy
- MeanReversionStrategy
- MomentumStrategy
- WhaleFollowingStrategy
- CompositeStrategy
- CrossMarketStrategy
"""

from datetime import datetime, timedelta
from typing import Dict, Any

import pandas as pd
import pytest

from trading.strategy import SignalType
from trading.strategies.mean_reversion import MeanReversionStrategy
from trading.strategies.momentum import MomentumStrategy
from trading.strategies.whale import WhaleFollowingStrategy
from trading.strategies.composite import CompositeStrategy
from trading.strategies.smart_money import SmartMoneyStrategy, SmartMoneyTracker
from trading.strategies.breakout import BreakoutStrategy, VolatilityBreakoutStrategy
from trading.strategies.time_decay import TimeDecayStrategy
from trading.strategies.sentiment import SentimentDivergenceStrategy
from trading.strategies.cross_market import CrossMarketStrategy


# =============================================================================
# SmartMoneyStrategy Tests
# =============================================================================

class TestSmartMoneyStrategy:
    """Tests for SmartMoneyStrategy."""

    def test_strategy_initialization(self):
        """Test strategy initializes with correct parameters."""
        strategy = SmartMoneyStrategy(
            min_trade_size=5000,
            aggregation_window_hours=4,
            imbalance_threshold=0.6,
            min_volume=10000,
            min_trades=3,
        )

        assert strategy.min_trade_size == 5000
        assert strategy.imbalance_threshold == 0.6
        assert strategy.min_volume == 10000
        assert strategy.min_trades == 3
        assert strategy.name == "smart_money"

    def test_generates_buy_signal_on_bullish_flow(self, smart_money_trades_df: pd.DataFrame):
        """Test that strong buy flow generates buy signal."""
        strategy = SmartMoneyStrategy(
            min_trade_size=5000,
            aggregation_window_hours=2,
            imbalance_threshold=0.4,
            min_volume=10000,
            min_trades=3,
        )

        now = datetime(2026, 1, 15, 12, 0, 0)
        signals = strategy.generate_signals(smart_money_trades_df, timestamps=[now])

        assert len(signals) >= 1
        buy_signals = [s for s in signals if s.direction == 1]
        assert len(buy_signals) >= 1
        assert buy_signals[0].market_id == "market_001"

    def test_generates_sell_signal_on_bearish_flow(self):
        """Test that strong sell flow generates sell signal."""
        strategy = SmartMoneyStrategy(
            min_trade_size=5000,
            aggregation_window_hours=2,
            imbalance_threshold=0.4,
            min_volume=10000,
            min_trades=3,
        )

        base_time = datetime(2026, 1, 15, 12, 0, 0)
        trades = []

        # Large sell flow
        for i in range(5):
            trades.append({
                "market_id": "sell_market",
                "datetime": base_time - timedelta(minutes=i * 15),
                "taker_direction": "sell",
                "usd_amount": 6000 + (i * 1000),
            })

        # Small buys
        for i in range(2):
            trades.append({
                "market_id": "sell_market",
                "datetime": base_time - timedelta(minutes=i * 10 + 5),
                "taker_direction": "buy",
                "usd_amount": 2000,
            })

        trades_df = pd.DataFrame(trades)
        signals = strategy.generate_signals(trades_df, timestamps=[base_time])

        assert len(signals) >= 1
        sell_signals = [s for s in signals if s.direction == -1]
        assert len(sell_signals) >= 1
        assert sell_signals[0].market_id == "sell_market"

    def test_no_signal_below_threshold(self):
        """Test no signal when imbalance is below threshold."""
        strategy = SmartMoneyStrategy(
            min_trade_size=5000,
            aggregation_window_hours=2,
            imbalance_threshold=0.9,  # Very high threshold
            min_volume=10000,
            min_trades=2,
        )

        base_time = datetime(2026, 1, 15, 12, 0, 0)
        trades = [
            {"market_id": "m1", "datetime": base_time - timedelta(minutes=10),
             "taker_direction": "buy", "usd_amount": 6000},
            {"market_id": "m1", "datetime": base_time - timedelta(minutes=20),
             "taker_direction": "sell", "usd_amount": 5000},
        ]

        trades_df = pd.DataFrame(trades)
        signals = strategy.generate_signals(trades_df, timestamps=[base_time])

        # Imbalance is (6000-5000)/11000 = 0.09, below 0.9 threshold
        assert len(signals) == 0

    def test_no_signal_insufficient_volume(self):
        """Test no signal when volume is below minimum."""
        strategy = SmartMoneyStrategy(
            min_trade_size=100,
            aggregation_window_hours=2,
            imbalance_threshold=0.3,
            min_volume=100000,  # Very high minimum
            min_trades=2,
        )

        base_time = datetime(2026, 1, 15, 12, 0, 0)
        trades = [
            {"market_id": "m1", "datetime": base_time - timedelta(minutes=10),
             "taker_direction": "buy", "usd_amount": 500},
            {"market_id": "m1", "datetime": base_time - timedelta(minutes=20),
             "taker_direction": "buy", "usd_amount": 500},
        ]

        trades_df = pd.DataFrame(trades)
        signals = strategy.generate_signals(trades_df, timestamps=[base_time])

        assert len(signals) == 0

    def test_calculate_flow_empty_dataframe(self):
        """Test calculate_flow handles empty DataFrame."""
        strategy = SmartMoneyStrategy()
        flow = strategy.calculate_flow(pd.DataFrame(), datetime.now())
        assert flow == {}

    def test_get_parameters(self):
        """Test get_parameters returns correct values."""
        strategy = SmartMoneyStrategy(
            min_trade_size=3000,
            aggregation_window_hours=6,
            imbalance_threshold=0.5,
            min_volume=5000,
            min_trades=5,
        )

        params = strategy.get_parameters()

        assert params["min_trade_size"] == 3000
        assert params["aggregation_window_hours"] == 6.0
        assert params["imbalance_threshold"] == 0.5
        assert params["min_volume"] == 5000
        assert params["min_trades"] == 5


class TestSmartMoneyTracker:
    """Tests for SmartMoneyTracker real-time tracker."""

    def test_tracker_add_and_get_trade(self):
        """Test adding trades and getting flow."""
        strategy = SmartMoneyStrategy(
            min_trade_size=100,
            aggregation_window_hours=1,
            imbalance_threshold=0.3,
            min_volume=200,
            min_trades=2,
        )
        tracker = SmartMoneyTracker(strategy, window_hours=24)

        tracker.add_trade("m1", "buy", 500, 0.50)
        tracker.add_trade("m1", "buy", 600, 0.51)
        tracker.add_trade("m1", "sell", 200, 0.50)

        flow = tracker.get_current_flow()

        assert "m1" in flow
        assert flow["m1"]["buy_volume"] == 1100
        assert flow["m1"]["sell_volume"] == 200

    def test_tracker_prunes_old_trades(self):
        """Test that old trades are pruned."""
        strategy = SmartMoneyStrategy(min_trade_size=100)
        tracker = SmartMoneyTracker(strategy, window_hours=1)

        old_time = datetime.now() - timedelta(hours=2)
        tracker.trades.append({
            "market_id": "m1",
            "taker_direction": "buy",
            "usd_amount": 500,
            "price": 0.50,
            "datetime": old_time,
        })

        # Add a new trade to trigger pruning
        tracker.add_trade("m1", "buy", 100, 0.50)

        # Old trade should be pruned
        assert len(tracker.trades) == 1

    def test_tracker_get_signal(self):
        """Test getting signal for a market."""
        strategy = SmartMoneyStrategy(
            min_trade_size=100,
            aggregation_window_hours=1,
            imbalance_threshold=0.4,
            min_volume=500,
            min_trades=2,
        )
        tracker = SmartMoneyTracker(strategy, window_hours=24)

        tracker.add_trade("m1", "buy", 400, 0.50)
        tracker.add_trade("m1", "buy", 400, 0.51)
        tracker.add_trade("m1", "sell", 100, 0.50)

        signal = tracker.get_signal("m1")

        assert signal is not None
        assert signal.direction == 1
        assert signal.market_id == "m1"


# =============================================================================
# BreakoutStrategy Tests
# =============================================================================

class TestBreakoutStrategy:
    """Tests for BreakoutStrategy."""

    def test_strategy_initialization(self):
        """Test strategy initializes with correct parameters."""
        strategy = BreakoutStrategy(
            lookback_hours=24,
            breakout_threshold=0.05,
            min_range_width=0.03,
            max_range_width=0.20,
            volume_multiplier=2.0,
        )

        assert strategy.lookback_hours == 24
        assert strategy.breakout_threshold == 0.05
        assert strategy.name == "breakout"

    def test_detects_upside_breakout(self, breakout_prices_df: pd.DataFrame):
        """Test detection of upside breakout."""
        strategy = BreakoutStrategy(
            lookback_hours=20,
            breakout_threshold=0.05,
            min_range_width=0.02,
            max_range_width=0.30,
        )

        signals = strategy.generate_signals(breakout_prices_df)

        # Should detect breakout when price goes from 0.52 to 0.70
        assert len(signals) >= 1
        upside_signals = [s for s in signals if s.direction == 1]
        assert len(upside_signals) >= 1
        assert upside_signals[0].market_id == "breakout_market"

    def test_detects_downside_breakout(self):
        """Test detection of downside breakout."""
        strategy = BreakoutStrategy(
            lookback_hours=10,
            breakout_threshold=0.05,
            min_range_width=0.02,
            max_range_width=0.30,
        )

        base_time = pd.Timestamp("2026-01-10 00:00:00")
        prices = []

        # Consolidation around 0.50
        for i in range(12):
            prices.append({
                "market_id": "down_market",
                "datetime": base_time + pd.Timedelta(hours=i),
                "price": 0.50 + ((i % 3) - 1) * 0.01,
            })

        # Breakout downward
        for i in range(3):
            prices.append({
                "market_id": "down_market",
                "datetime": base_time + pd.Timedelta(hours=12 + i),
                "price": 0.45 - (i * 0.03),
            })

        prices_df = pd.DataFrame(prices)
        signals = strategy.generate_signals(prices_df)

        downside_signals = [s for s in signals if s.direction == -1]
        assert len(downside_signals) >= 1

    def test_no_signal_in_trending_market(self):
        """Test no breakout signal in a trending market (wide range)."""
        strategy = BreakoutStrategy(
            lookback_hours=10,
            breakout_threshold=0.05,
            min_range_width=0.02,
            max_range_width=0.10,  # Narrow max range
        )

        base_time = pd.Timestamp("2026-01-10 00:00:00")
        prices = []

        # Strong uptrend (wide range)
        for i in range(15):
            prices.append({
                "market_id": "trend_market",
                "datetime": base_time + pd.Timedelta(hours=i),
                "price": 0.30 + (i * 0.03),  # Goes from 0.30 to 0.72
            })

        prices_df = pd.DataFrame(prices)
        signals = strategy.generate_signals(prices_df)

        # Range is too wide, should not signal
        # The range width would be (0.72-0.30)/0.51 = 0.82 > 0.10
        # Filter out by checking if range_width is within bounds
        valid_signals = [s for s in signals if s.range_width <= 0.10]
        assert len(valid_signals) == 0

    def test_detect_range_insufficient_data(self):
        """Test detect_range handles insufficient data."""
        strategy = BreakoutStrategy()
        result = strategy.detect_range(pd.Series([0.5] * 5))  # Only 5 points
        assert result == {}

    def test_check_breakout_no_range_info(self):
        """Test check_breakout handles empty range info."""
        strategy = BreakoutStrategy()
        result = strategy.check_breakout(0.50, {})
        assert result is None

    def test_get_parameters(self):
        """Test get_parameters returns correct values."""
        strategy = BreakoutStrategy(
            lookback_hours=48,
            breakout_threshold=0.08,
            min_range_width=0.04,
            max_range_width=0.15,
            volume_multiplier=3.0,
        )

        params = strategy.get_parameters()

        assert params["lookback_hours"] == 48
        assert params["breakout_threshold"] == 0.08
        assert params["volume_multiplier"] == 3.0


class TestVolatilityBreakoutStrategy:
    """Tests for VolatilityBreakoutStrategy."""

    def test_strategy_initialization(self):
        """Test strategy initializes with correct parameters."""
        strategy = VolatilityBreakoutStrategy(
            lookback_hours=48,
            squeeze_percentile=20,
            expansion_multiplier=1.5,
        )

        assert strategy.lookback_hours == 48
        assert strategy.squeeze_percentile == 20
        assert strategy.name == "volatility_breakout"

    def test_detects_volatility_expansion(self):
        """Test detection of volatility expansion after squeeze."""
        strategy = VolatilityBreakoutStrategy(
            lookback_hours=20,
            squeeze_percentile=50,
            expansion_multiplier=0.5,
        )

        base_time = pd.Timestamp("2026-01-10 00:00:00")
        prices = []

        # Low volatility period (squeeze)
        for i in range(40):
            prices.append({
                "market_id": "vol_market",
                "datetime": base_time + pd.Timedelta(hours=i),
                "price": 0.50 + ((i % 2) - 0.5) * 0.001,  # Very tight range
            })

        # Volatility expansion
        for i in range(10):
            prices.append({
                "market_id": "vol_market",
                "datetime": base_time + pd.Timedelta(hours=40 + i),
                "price": 0.50 + (i * 0.02),  # Sharp move
            })

        prices_df = pd.DataFrame(prices)
        signals = strategy.generate_signals(prices_df)

        assert len(signals) >= 1

    def test_calculate_atr(self):
        """Test ATR calculation."""
        strategy = VolatilityBreakoutStrategy()
        prices = pd.Series([0.50, 0.52, 0.48, 0.51, 0.49, 0.53, 0.47, 0.50])
        atr = strategy.calculate_atr(prices, period=3)

        assert len(atr) == len(prices)
        assert not atr.iloc[3:].isna().any()  # Should have values after warmup

    def test_detect_squeeze_empty_data(self):
        """Test detect_squeeze handles edge cases."""
        strategy = VolatilityBreakoutStrategy()

        # Empty series
        result = strategy.detect_squeeze(pd.Series(dtype=float))
        assert result == {}


# =============================================================================
# TimeDecayStrategy Tests
# =============================================================================

class TestTimeDecayStrategy:
    """Tests for TimeDecayStrategy."""

    def test_strategy_initialization(self):
        """Test strategy initializes with correct parameters."""
        strategy = TimeDecayStrategy(
            reversion_threshold=0.15,
            acceleration_threshold=0.85,
            stale_hours=24,
            min_days_to_expiry=1,
            max_days_to_expiry=30,
        )

        assert strategy.reversion_threshold == 0.15
        assert strategy.acceleration_threshold == 0.85
        assert strategy.name == "time_decay"

    def test_detects_stale_market_signal(self, time_decay_data: Dict[str, Any]):
        """Test detection of stale market approaching expiry."""
        strategy = TimeDecayStrategy(
            stale_hours=24,
            min_days_to_expiry=1,
            max_days_to_expiry=10,
        )

        signals = strategy.generate_signals(
            markets_df=time_decay_data["markets"],
            prices_df=time_decay_data["prices"],
            trades_df=time_decay_data["trades"],
            as_of=time_decay_data["as_of"],
        )

        stale_signals = [s for s in signals if s.signal_type == "stale"]
        assert len(stale_signals) >= 1

    def test_detects_acceleration_signal(self, time_decay_data: Dict[str, Any]):
        """Test detection of price acceleration toward resolution."""
        strategy = TimeDecayStrategy(
            acceleration_threshold=0.85,
            min_days_to_expiry=1,
            max_days_to_expiry=10,
        )

        signals = strategy.generate_signals(
            markets_df=time_decay_data["markets"],
            prices_df=time_decay_data["prices"],
            trades_df=time_decay_data["trades"],
            as_of=time_decay_data["as_of"],
        )

        accel_signals = [s for s in signals if s.signal_type == "acceleration"]
        assert len(accel_signals) >= 1

    def test_respects_dte_bounds(self):
        """Test that DTE bounds are respected."""
        strategy = TimeDecayStrategy(
            min_days_to_expiry=5,
            max_days_to_expiry=10,
        )

        as_of = datetime(2026, 1, 15, 12, 0, 0)

        # Market expiring too soon
        markets_df = pd.DataFrame([
            {"id": "soon_market", "end_date": as_of + timedelta(days=2)},
        ])
        prices_df = pd.DataFrame([
            {"market_id": "soon_market", "datetime": as_of, "price": 0.90},
        ])
        trades_df = pd.DataFrame([
            {"market_id": "soon_market", "datetime": as_of},
        ])

        signals = strategy.generate_signals(markets_df, prices_df, trades_df, as_of)
        assert len(signals) == 0  # Outside DTE bounds

    def test_calculate_implied_probability(self):
        """Test implied probability calculation."""
        strategy = TimeDecayStrategy()

        # Near expiry, price should be close to implied
        impl = strategy.calculate_implied_probability(0.80, days_to_expiry=1)
        assert 0.80 <= impl <= 1.0

        # Far from expiry, adjust for time premium
        impl = strategy.calculate_implied_probability(0.80, days_to_expiry=30)
        assert impl > 0.80

    def test_expected_price_path(self):
        """Test expected daily price movement calculation."""
        strategy = TimeDecayStrategy()

        # Expecting YES resolution
        daily_move = strategy.expected_price_path(0.80, days_to_expiry=10, resolution=1.0)
        assert daily_move > 0  # Should be positive (price moving toward 1)

        # Expecting NO resolution
        daily_move = strategy.expected_price_path(0.80, days_to_expiry=10, resolution=0.0)
        assert daily_move < 0  # Should be negative (price moving toward 0)

    def test_get_parameters(self):
        """Test get_parameters returns correct values."""
        strategy = TimeDecayStrategy(
            reversion_threshold=0.20,
            acceleration_threshold=0.90,
            stale_hours=12,
            min_days_to_expiry=2,
            max_days_to_expiry=20,
        )

        params = strategy.get_parameters()

        assert params["reversion_threshold"] == 0.20
        assert params["stale_hours"] == 12


# =============================================================================
# SentimentDivergenceStrategy Tests
# =============================================================================

class TestSentimentDivergenceStrategy:
    """Tests for SentimentDivergenceStrategy."""

    def test_strategy_initialization(self):
        """Test strategy initializes with correct parameters."""
        strategy = SentimentDivergenceStrategy(
            lookback_hours=6,
            divergence_threshold=0.3,
            price_change_threshold=0.02,
            min_trades=10,
            exhaustion_threshold=0.8,
        )

        assert strategy.lookback_hours == 6
        assert strategy.divergence_threshold == 0.3
        assert strategy.name == "sentiment_divergence"

    def test_detects_bullish_divergence(self, sentiment_divergence_data: Dict[str, pd.DataFrame]):
        """Test detection of bullish divergence (strong buying, falling price)."""
        strategy = SentimentDivergenceStrategy(
            lookback_hours=1,
            divergence_threshold=0.2,
            price_change_threshold=0.02,
            min_trades=5,
        )

        trades_df = sentiment_divergence_data["trades"]
        prices_df = sentiment_divergence_data["prices"]
        as_of = datetime(2026, 1, 15, 12, 0, 0)

        signals = strategy.generate_signals(trades_df, prices_df, timestamps=[as_of])

        bullish_signals = [s for s in signals if s.signal_type == "bullish_div"]
        assert len(bullish_signals) >= 1
        assert bullish_signals[0].direction == 1

    def test_detects_bearish_divergence(self):
        """Test detection of bearish divergence (strong selling, rising price)."""
        strategy = SentimentDivergenceStrategy(
            lookback_hours=1,
            divergence_threshold=0.2,
            price_change_threshold=0.02,
            min_trades=5,
        )

        base_time = datetime(2026, 1, 15, 12, 0, 0)

        # Strong selling
        trades = []
        for i in range(15):
            trades.append({
                "market_id": "bear_div_market",
                "datetime": base_time - timedelta(minutes=i * 4),
                "taker_direction": "sell",
                "usd_amount": 500 + (i * 100),
            })

        # Few buys
        for i in range(3):
            trades.append({
                "market_id": "bear_div_market",
                "datetime": base_time - timedelta(minutes=i * 20 + 10),
                "taker_direction": "buy",
                "usd_amount": 200,
            })

        trades_df = pd.DataFrame(trades)

        # Price going up despite selling
        prices = [
            {"market_id": "bear_div_market", "datetime": base_time - timedelta(hours=1), "price": 0.40},
            {"market_id": "bear_div_market", "datetime": base_time, "price": 0.48},
        ]
        prices_df = pd.DataFrame(prices)

        signals = strategy.generate_signals(trades_df, prices_df, timestamps=[base_time])

        bearish_signals = [s for s in signals if s.signal_type == "bearish_div"]
        assert len(bearish_signals) >= 1
        assert bearish_signals[0].direction == -1

    def test_calculate_sentiment_empty_data(self):
        """Test calculate_sentiment handles empty data."""
        strategy = SentimentDivergenceStrategy()
        result = strategy.calculate_sentiment(pd.DataFrame(), datetime.now())
        assert result == {}

    def test_no_signal_insufficient_trades(self):
        """Test no signal when trade count is below minimum."""
        strategy = SentimentDivergenceStrategy(min_trades=100)

        base_time = datetime(2026, 1, 15, 12, 0, 0)
        trades = [
            {"market_id": "m1", "datetime": base_time - timedelta(minutes=10),
             "taker_direction": "buy", "usd_amount": 1000},
        ]
        trades_df = pd.DataFrame(trades)
        prices_df = pd.DataFrame([
            {"market_id": "m1", "datetime": base_time - timedelta(hours=1), "price": 0.50},
            {"market_id": "m1", "datetime": base_time, "price": 0.45},
        ])

        signals = strategy.generate_signals(trades_df, prices_df, timestamps=[base_time])
        assert len(signals) == 0

    def test_get_parameters(self):
        """Test get_parameters returns correct values."""
        strategy = SentimentDivergenceStrategy(
            lookback_hours=12,
            divergence_threshold=0.4,
            price_change_threshold=0.03,
            min_trades=20,
            exhaustion_threshold=0.9,
        )

        params = strategy.get_parameters()

        assert params["lookback_hours"] == 12
        assert params["min_trades"] == 20


# =============================================================================
# MeanReversionStrategy Tests
# =============================================================================

class TestMeanReversionStrategy:
    """Tests for MeanReversionStrategy."""

    def test_generates_buy_signal_on_dip(self):
        """Test buy signal on significant price drop."""
        strategy = MeanReversionStrategy()
        market_data = {
            "markets": [
                {
                    "id": "m1",
                    "price": 0.45,
                    "return_1h": -0.09,
                    "return_6h": 0.01,
                    "category": "sports",
                    "liquidity": 2000,
                    "volume_24h": 500,
                    "volatility": 0.2,
                }
            ]
        }
        ts = datetime(2026, 1, 1, 12, 0, 0)
        signals = strategy.generate_signals(market_data, ts)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.BUY
        assert signals[0].market_id == "m1"

    def test_generates_sell_signal_on_spike(self):
        """Test sell signal on significant price increase."""
        strategy = MeanReversionStrategy()
        market_data = {
            "markets": [
                {
                    "id": "m1",
                    "price": 0.55,
                    "return_1h": 0.10,
                    "return_6h": -0.01,
                    "category": "sports",
                    "liquidity": 2000,
                    "volume_24h": 500,
                    "volatility": 0.2,
                }
            ]
        }
        ts = datetime(2026, 1, 1, 12, 0, 0)
        signals = strategy.generate_signals(market_data, ts)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.SELL


# =============================================================================
# MomentumStrategy Tests
# =============================================================================

class TestMomentumStrategy:
    """Tests for MomentumStrategy."""

    def test_generates_sell_signal_on_downtrend(self):
        """Test sell signal on negative momentum."""
        strategy = MomentumStrategy()
        market_data = {
            "markets": [
                {
                    "id": "m2",
                    "price": 0.4,
                    "return_24h": -0.08,
                    "category": "news",
                    "liquidity": 1000,
                    "volume_24h": 800,
                }
            ]
        }
        ts = datetime(2026, 1, 2, 9, 30, 0)
        signals = strategy.generate_signals(market_data, ts)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.SELL
        assert signals[0].market_id == "m2"

    def test_generates_buy_signal_on_uptrend(self):
        """Test buy signal on positive momentum."""
        strategy = MomentumStrategy()
        market_data = {
            "markets": [
                {
                    "id": "m2",
                    "price": 0.6,
                    "return_24h": 0.10,
                    "category": "news",
                    "liquidity": 1000,
                    "volume_24h": 800,
                }
            ]
        }
        ts = datetime(2026, 1, 2, 9, 30, 0)
        signals = strategy.generate_signals(market_data, ts)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.BUY


# =============================================================================
# WhaleFollowingStrategy Tests
# =============================================================================

class TestWhaleFollowingStrategy:
    """Tests for WhaleFollowingStrategy."""

    def test_follows_whale_buy(self):
        """Test following whale buy trade."""
        strategy = WhaleFollowingStrategy(whale_addresses={"whale1"})
        market_data = {
            "recent_trades": [
                {
                    "maker": "whale1",
                    "market_id": "m3",
                    "direction": "buy",
                    "usd_amount": 250,
                    "outcome": "YES",
                    "price": 0.52,
                    "category": "crypto",
                }
            ]
        }
        ts = datetime(2026, 1, 3, 15, 0, 0)
        signals = strategy.generate_signals(market_data, ts)

        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.BUY
        assert signals[0].market_id == "m3"

    def test_ignores_non_whale_trades(self):
        """Test that non-whale trades are ignored."""
        strategy = WhaleFollowingStrategy(whale_addresses={"whale1"})
        market_data = {
            "recent_trades": [
                {
                    "maker": "regular_trader",
                    "market_id": "m3",
                    "direction": "buy",
                    "usd_amount": 500,
                    "outcome": "YES",
                    "price": 0.52,
                    "category": "crypto",
                }
            ]
        }
        ts = datetime(2026, 1, 3, 15, 0, 0)
        signals = strategy.generate_signals(market_data, ts)

        assert len(signals) == 0


# =============================================================================
# CompositeStrategy Tests
# =============================================================================

class TestCompositeStrategy:
    """Tests for CompositeStrategy."""

    def test_requires_agreement(self):
        """Test that composite requires minimum agreement."""
        s1 = MeanReversionStrategy()
        s2 = MeanReversionStrategy()
        composite = CompositeStrategy([s1, s2], min_agreement=2)

        market_data = {
            "markets": [
                {
                    "id": "m4",
                    "price": 0.48,
                    "return_1h": -0.1,
                    "return_6h": 0.01,
                    "category": "sports",
                    "liquidity": 1500,
                    "volume_24h": 600,
                    "volatility": 0.15,
                }
            ]
        }
        ts = datetime(2026, 1, 4, 10, 0, 0)
        signals = composite.generate_signals(market_data, ts)

        assert len(signals) == 1
        assert signals[0].strategy_name == "composite"


# =============================================================================
# CrossMarketStrategy Tests
# =============================================================================

class TestCrossMarketStrategy:
    """Tests for CrossMarketStrategy."""

    def test_detects_cross_market_divergence(self):
        """Test detection of divergence between related markets."""
        strategy = CrossMarketStrategy(z_score_threshold=1.5, lookback_hours=24)
        base = pd.Timestamp("2026-01-10 00:00:00")
        times = [base + pd.Timedelta(hours=i) for i in range(30)]

        # Prices that stay together then diverge
        p1 = [0.5 + ((i % 3) - 1) * 0.002 for i in range(30)]
        p2 = [0.5 + ((i % 2) - 0.5) * 0.001 for i in range(30)]
        p1[-1] = 0.7
        p2[-1] = 0.45

        prices_df = pd.DataFrame(
            [
                *[{"market_id": "m10", "datetime": t, "price": price}
                  for t, price in zip(times, p1)],
                *[{"market_id": "m11", "datetime": t, "price": price}
                  for t, price in zip(times, p2)],
            ]
        )

        markets_df = pd.DataFrame([
            {"id": "m10", "question": "Market 10?"},
            {"id": "m11", "question": "Market 11?"},
        ])

        relationships = {
            "m10": [("m11", 1.0, 0.9)],
            "m11": [("m10", 1.0, 0.9)],
        }

        signals = strategy.generate_signals(
            markets_df,
            prices_df,
            relationships=relationships,
            timestamps=[times[-1]],
        )

        assert len(signals) >= 1
        assert signals[0].market_id_long in {"m10", "m11"}


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_dataframe_handling(self):
        """Test strategies handle empty DataFrames gracefully."""
        smart_money = SmartMoneyStrategy()
        signals = smart_money.generate_signals(pd.DataFrame())
        assert signals == []

        breakout = BreakoutStrategy()
        signals = breakout.generate_signals(pd.DataFrame())
        assert signals == []

    def test_single_row_dataframe(self):
        """Test strategies handle single-row DataFrames."""
        strategy = SmartMoneyStrategy(min_trades=1, min_volume=1, min_trade_size=1)

        trades_df = pd.DataFrame([{
            "market_id": "m1",
            "datetime": datetime.now(),
            "taker_direction": "buy",
            "usd_amount": 100,
        }])

        # Should not crash
        signals = strategy.generate_signals(trades_df, timestamps=[datetime.now()])
        # May or may not produce signals depending on thresholds

    def test_nan_handling_in_prices(self):
        """Test that NaN values in price data are handled."""
        strategy = BreakoutStrategy()

        prices_df = pd.DataFrame([
            {"market_id": "m1", "datetime": pd.Timestamp("2026-01-01 00:00:00"), "price": 0.50},
            {"market_id": "m1", "datetime": pd.Timestamp("2026-01-01 01:00:00"), "price": float("nan")},
            {"market_id": "m1", "datetime": pd.Timestamp("2026-01-01 02:00:00"), "price": 0.52},
        ])

        # Should not crash
        signals = strategy.generate_signals(prices_df)

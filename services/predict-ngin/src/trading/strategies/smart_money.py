"""
Smart Money Strategy

Tracks large institutional-sized trades that move markets.
Unlike whale following (which tracks specific traders), this
tracks trade SIZE regardless of who's trading.

Hypothesis:
- Large trades (>$1k) often indicate informed trading
- Markets move in the direction of large trades
- Following large trade flow can be profitable

Signals:
- Large buy: Price likely to increase
- Large sell: Price likely to decrease
- Size-weighted aggregation over time window

Parameters:
- min_trade_size: Minimum USD size to consider "smart money"
- aggregation_window: Time window to aggregate flow
- imbalance_threshold: Min buy/sell imbalance to trigger signal
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timedelta
from dataclasses import dataclass


@dataclass
class SmartMoneySignal:
    """Signal from smart money flow."""
    timestamp: datetime
    market_id: str
    direction: int  # 1 = buy, -1 = sell
    confidence: float
    flow_imbalance: float  # buy_volume - sell_volume
    total_volume: float
    large_trade_count: int
    avg_large_trade_size: float


class SmartMoneyStrategy:
    """
    Smart Money Flow Strategy.

    Tracks large trades and generates signals based on
    directional flow imbalance.
    """

    def __init__(
        self,
        min_trade_size: float = 1000,
        aggregation_window_hours: int = 4,
        imbalance_threshold: float = 0.6,
        min_volume: float = 10000,
        min_trades: int = 3,
    ):
        """
        Initialize strategy.

        Args:
            min_trade_size: Minimum USD to consider "smart money"
            aggregation_window_hours: Window for flow aggregation
            imbalance_threshold: Min imbalance ratio (0-1) to signal
            min_volume: Minimum total volume in window
            min_trades: Minimum large trades in window
        """
        self.min_trade_size = min_trade_size
        self.aggregation_window = timedelta(hours=aggregation_window_hours)
        self.imbalance_threshold = imbalance_threshold
        self.min_volume = min_volume
        self.min_trades = min_trades
        self.name = "smart_money"

    def calculate_flow(
        self,
        trades_df: pd.DataFrame,
        as_of: datetime,
    ) -> Dict[str, Dict[str, float]]:
        """
        Calculate smart money flow for each market.

        Args:
            trades_df: DataFrame with trades
            as_of: Point in time to calculate flow

        Returns:
            Dict of market_id -> {buy_volume, sell_volume, imbalance, ...}
        """
        if trades_df.empty or "datetime" not in trades_df.columns:
            return {}

        window_start = as_of - self.aggregation_window

        # Filter to window and large trades
        mask = (
            (trades_df["datetime"] >= window_start) &
            (trades_df["datetime"] <= as_of) &
            (trades_df["usd_amount"] >= self.min_trade_size)
        )
        large_trades = trades_df[mask]

        if large_trades.empty:
            return {}

        flow = {}

        for market_id, group in large_trades.groupby("market_id"):
            # Aggregate by direction
            # Use taker direction as it indicates aggressor
            buy_volume = group[
                group["taker_direction"].str.lower() == "buy"
            ]["usd_amount"].sum()

            sell_volume = group[
                group["taker_direction"].str.lower() == "sell"
            ]["usd_amount"].sum()

            total = buy_volume + sell_volume

            if total < self.min_volume:
                continue

            if len(group) < self.min_trades:
                continue

            imbalance = (buy_volume - sell_volume) / total if total > 0 else 0

            flow[market_id] = {
                "buy_volume": buy_volume,
                "sell_volume": sell_volume,
                "total_volume": total,
                "imbalance": imbalance,
                "trade_count": len(group),
                "avg_trade_size": group["usd_amount"].mean(),
            }

        return flow

    def generate_signals(
        self,
        trades_df: pd.DataFrame,
        timestamps: Optional[List[datetime]] = None,
    ) -> List[SmartMoneySignal]:
        """
        Generate signals from smart money flow.

        Args:
            trades_df: DataFrame with trades
            timestamps: Specific times to check (default: hourly)

        Returns:
            List of signals
        """
        if trades_df.empty:
            return []

        # Ensure datetime column
        if "datetime" not in trades_df.columns:
            trades_df["datetime"] = pd.to_datetime(trades_df["timestamp"])

        # Default: check every hour
        if timestamps is None:
            start = trades_df["datetime"].min()
            end = trades_df["datetime"].max()
            timestamps = pd.date_range(start, end, freq="1h").tolist()

        signals = []

        for ts in timestamps:
            flow = self.calculate_flow(trades_df, ts)

            for market_id, metrics in flow.items():
                imbalance = metrics["imbalance"]

                # Check if imbalance exceeds threshold
                if abs(imbalance) >= self.imbalance_threshold:
                    direction = 1 if imbalance > 0 else -1
                    confidence = min(abs(imbalance), 1.0)

                    signals.append(SmartMoneySignal(
                        timestamp=ts,
                        market_id=market_id,
                        direction=direction,
                        confidence=confidence,
                        flow_imbalance=imbalance,
                        total_volume=metrics["total_volume"],
                        large_trade_count=metrics["trade_count"],
                        avg_large_trade_size=metrics["avg_trade_size"],
                    ))

        return signals

    def get_parameters(self) -> Dict[str, Any]:
        """Get strategy parameters."""
        return {
            "min_trade_size": self.min_trade_size,
            "aggregation_window_hours": self.aggregation_window.total_seconds() / 3600,
            "imbalance_threshold": self.imbalance_threshold,
            "min_volume": self.min_volume,
            "min_trades": self.min_trades,
        }


class SmartMoneyTracker:
    """
    Real-time smart money flow tracker.

    Maintains rolling window of large trades for live signal generation.
    """

    def __init__(
        self,
        strategy: SmartMoneyStrategy,
        window_hours: int = 24,
    ):
        self.strategy = strategy
        self.window = timedelta(hours=window_hours)
        self.trades: List[Dict] = []

    def add_trade(
        self,
        market_id: str,
        direction: str,
        size_usd: float,
        price: float,
        timestamp: Optional[datetime] = None,
    ):
        """Add a trade to the tracker."""
        if timestamp is None:
            timestamp = datetime.now()

        self.trades.append({
            "market_id": market_id,
            "taker_direction": direction,
            "usd_amount": size_usd,
            "price": price,
            "datetime": timestamp,
        })

        # Prune old trades
        cutoff = datetime.now() - self.window
        self.trades = [t for t in self.trades if t["datetime"] > cutoff]

    def get_current_flow(self) -> Dict[str, Dict[str, float]]:
        """Get current flow for all markets."""
        if not self.trades:
            return {}

        df = pd.DataFrame(self.trades)
        return self.strategy.calculate_flow(df, datetime.now())

    def get_signal(self, market_id: str) -> Optional[SmartMoneySignal]:
        """Get current signal for a market."""
        flow = self.get_current_flow()

        if market_id not in flow:
            return None

        metrics = flow[market_id]
        imbalance = metrics["imbalance"]

        if abs(imbalance) < self.strategy.imbalance_threshold:
            return None

        return SmartMoneySignal(
            timestamp=datetime.now(),
            market_id=market_id,
            direction=1 if imbalance > 0 else -1,
            confidence=min(abs(imbalance), 1.0),
            flow_imbalance=imbalance,
            total_volume=metrics["total_volume"],
            large_trade_count=metrics["trade_count"],
            avg_large_trade_size=metrics["avg_trade_size"],
        )

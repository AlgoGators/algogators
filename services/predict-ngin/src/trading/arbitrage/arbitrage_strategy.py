"""
Cross-platform arbitrage strategy.

Detects price discrepancies between matched Polymarket and Kalshi markets
and generates entry/exit signals for convergence trades.

The core logic:
- When spread > entry_threshold: enter the arb (buy cheap side, sell expensive side)
- When spread < exit_threshold: close the arb (spread has converged)
- Accounts for per-fill platform fees (Kalshi 0.07*C*P*(1-P) rounded up to the
  cent; Polymarket category rate * C*p*(1-p)) and a half-spread on each leg,
  since historical data only has last/close prices (see fees.FeeModel)

Usage:
    from trading.arbitrage.arbitrage_strategy import ArbitrageStrategy

    strategy = ArbitrageStrategy(entry_spread=0.05, exit_spread=0.02)
    signals = strategy.generate_signals(pairs, price_store)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .market_matcher import MatchedPair
from .cross_platform_price_store import CrossPlatformPriceStore
from .fees import FeeModel


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------

@dataclass
class ArbitrageSignal:
    """A single arbitrage entry or exit signal."""

    # Identification
    pair_id: str
    polymarket_id: str
    kalshi_ticker: str
    signal_type: str  # "entry" or "exit"
    timestamp: int  # Unix seconds

    # Prices at signal time
    poly_price: float
    kalshi_price: float
    spread: float  # poly_price - kalshi_price
    abs_spread: float

    # Direction: which side to buy
    # "buy_kalshi" = Kalshi is cheap (spread > 0, Poly expensive)
    # "buy_poly"   = Poly is cheap (spread < 0, Kalshi expensive)
    direction: str

    # Sizing and confidence
    confidence: float  # 0-1, based on spread magnitude and history
    z_score: float  # Spread z-score relative to rolling window
    expected_profit: float  # Spread minus estimated fees

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fee models per platform
# ---------------------------------------------------------------------------

@dataclass
class PlatformFees:
    """DEPRECATED fee structure kept for import compatibility.

    The old default modelled Kalshi as "7% of profit", which is wrong: Kalshi
    charges ceil_to_cent(0.07 * C * P * (1-P)) on every fill. Use
    ``fees.FeeModel`` instead; these objects are ignored by the strategy and
    backtest unless no FeeModel is given AND profit_fee_rate is set explicitly.
    """

    name: str
    profit_fee_rate: float  # Fee on net profit (e.g., 0.02 = 2%)
    maker_fee_rate: float  # Fee on trade value for makers
    taker_fee_rate: float  # Fee on trade value for takers
    withdrawal_fee: float  # Fixed withdrawal fee (USD)

    def estimated_round_trip_cost(self, spread: float) -> float:
        """
        Estimate cost for a round-trip arb trade on this platform.

        For an arb, we enter at one price and the market resolves (or we exit).
        The main cost is the profit fee on gains.

        Args:
            spread: Expected profit per share (0-1 scale)

        Returns:
            Estimated cost as fraction of spread.
        """
        # Taker fee on entry + profit fee on gains
        entry_cost = self.taker_fee_rate
        profit_cost = max(spread, 0) * self.profit_fee_rate
        return entry_cost + profit_cost


# Default fee structures (as of 2025-2026)
POLYMARKET_FEES = PlatformFees(
    name="Polymarket",
    profit_fee_rate=0.0,  # Polymarket doesn't charge profit fees currently
    maker_fee_rate=0.0,
    taker_fee_rate=0.0,  # No trading fees on Polymarket
    withdrawal_fee=0.0,
)

KALSHI_FEES = PlatformFees(
    name="Kalshi",
    profit_fee_rate=0.0,  # Kalshi has no profit fee; per-fill fee lives in FeeModel
    maker_fee_rate=0.0,
    taker_fee_rate=0.0,
    withdrawal_fee=0.0,
)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class ArbitrageStrategy:
    """
    Cross-platform arbitrage strategy.

    Monitors price spreads between matched Polymarket/Kalshi markets and
    generates entry signals when spreads exceed thresholds (after fees),
    and exit signals when spreads converge.

    The strategy handles both directions:
    - Poly expensive: buy YES on Kalshi + buy NO on Polymarket
    - Kalshi expensive: buy YES on Polymarket + buy NO on Kalshi

    In binary markets, buy NO at price p is equivalent to sell YES at 1-p,
    so the arb profit per share is abs(spread) minus fees.

    Args:
        entry_spread: Minimum absolute spread to enter (default: 0.05 = 5 cents)
        exit_spread: Close position when spread narrows to this (default: 0.01)
        min_expected_profit: Minimum profit after fees to trade (default: 0.02)
        lookback_periods: Periods for rolling spread statistics (default: 30)
        z_score_threshold: Minimum z-score for entry (default: 1.5)
        max_holding_periods: Max periods to hold before forced exit (default: None)
        poly_fees: Polymarket fee structure
        kalshi_fees: Kalshi fee structure
    """

    def __init__(
        self,
        entry_spread: float = 0.05,
        exit_spread: float = 0.01,
        min_expected_profit: float = 0.02,
        lookback_periods: int = 30,
        z_score_threshold: float = 1.5,
        max_holding_periods: Optional[int] = None,
        poly_fees: Optional[PlatformFees] = None,
        kalshi_fees: Optional[PlatformFees] = None,
        fee_model: Optional[FeeModel] = None,
        kalshi_half_spread: float = 0.01,
        poly_half_spread: float = 0.0075,
        category: str = "other",
    ):
        self.entry_spread = entry_spread
        self.exit_spread = exit_spread
        self.min_expected_profit = min_expected_profit
        self.lookback_periods = lookback_periods
        self.z_score_threshold = z_score_threshold
        self.max_holding_periods = max_holding_periods
        self.poly_fees = poly_fees or POLYMARKET_FEES
        self.kalshi_fees = kalshi_fees or KALSHI_FEES
        self.fee_model = fee_model or FeeModel()
        self.kalshi_half_spread = kalshi_half_spread
        self.poly_half_spread = poly_half_spread
        self.category = category
        self.name = "cross_platform_arbitrage"

    def estimate_cost_per_contract(
        self,
        poly_price: float,
        kalshi_price: float,
        category: Optional[str] = None,
    ) -> float:
        """Per-contract-pair cost of entering the arb: fees on both fills + half-spreads.

        The cheap venue's YES and the expensive venue's NO are bought at
        last + half_spread; fees use those executable prices.
        """
        cat = category or self.category
        if poly_price >= kalshi_price:  # buy Kalshi YES + Poly NO
            k_fill = kalshi_price + self.kalshi_half_spread
            p_fill = (1.0 - poly_price) + self.poly_half_spread
        else:  # buy Poly YES + Kalshi NO
            k_fill = (1.0 - kalshi_price) + self.kalshi_half_spread
            p_fill = poly_price + self.poly_half_spread
        return (
            self.fee_model.kalshi_per_contract(k_fill)
            + self.fee_model.poly_per_contract(p_fill, cat)
            + self.kalshi_half_spread
            + self.poly_half_spread
        )

    def estimate_fees(self, spread: float, poly_price: float = 0.5, kalshi_price: Optional[float] = None) -> float:
        """Backward-compatible wrapper: per-contract entry cost (fees + half-spreads)."""
        if kalshi_price is None:
            kalshi_price = poly_price - spread
        return self.estimate_cost_per_contract(poly_price, kalshi_price)

    def generate_signals(
        self,
        pairs: List[MatchedPair],
        price_store: CrossPlatformPriceStore,
        resample_freq: str = "1D",
    ) -> List[ArbitrageSignal]:
        """
        Generate arbitrage signals for all matched pairs.

        For each pair, builds a spread time series and identifies entry/exit
        points based on spread thresholds and z-scores.

        Args:
            pairs: List of matched market pairs from MarketMatcher.
            price_store: CrossPlatformPriceStore with data from both platforms.
            resample_freq: Frequency for spread time series (default: daily).

        Returns:
            List of ArbitrageSignal objects, sorted by timestamp.
        """
        all_signals: List[ArbitrageSignal] = []

        for pair in pairs:
            pair_signals = self._analyze_pair(pair, price_store, resample_freq)
            all_signals.extend(pair_signals)

        # Sort by timestamp
        all_signals.sort(key=lambda s: s.timestamp)
        return all_signals

    def generate_signals_dataframe(
        self,
        pairs: List[MatchedPair],
        price_store: CrossPlatformPriceStore,
        resample_freq: str = "1D",
    ) -> pd.DataFrame:
        """Generate signals and return as DataFrame (convenient for backtesting)."""
        signals = self.generate_signals(pairs, price_store, resample_freq)
        if not signals:
            return pd.DataFrame()

        rows = []
        for s in signals:
            rows.append({
                "pair_id": s.pair_id,
                "polymarket_id": s.polymarket_id,
                "kalshi_ticker": s.kalshi_ticker,
                "signal_type": s.signal_type,
                "timestamp": s.timestamp,
                "datetime": pd.to_datetime(s.timestamp, unit="s", utc=True),
                "poly_price": s.poly_price,
                "kalshi_price": s.kalshi_price,
                "spread": s.spread,
                "abs_spread": s.abs_spread,
                "direction": s.direction,
                "confidence": s.confidence,
                "z_score": s.z_score,
                "expected_profit": s.expected_profit,
            })
        return pd.DataFrame(rows)

    # -------------------------------------------------------------------
    # Internal pair analysis
    # -------------------------------------------------------------------

    def _analyze_pair(
        self,
        pair: MatchedPair,
        price_store: CrossPlatformPriceStore,
        resample_freq: str,
    ) -> List[ArbitrageSignal]:
        """Analyze a single matched pair for arbitrage opportunities."""
        spread_df = price_store.build_spread_series(
            poly_id=pair.polymarket_id,
            kalshi_ticker=pair.kalshi_ticker,
            outcome="YES",
            resample_freq=resample_freq,
        )

        if spread_df.empty or len(spread_df) < self.lookback_periods + 1:
            return []

        # Compute rolling statistics
        spread_df["spread_mean"] = (
            spread_df["spread"]
            .rolling(window=self.lookback_periods, min_periods=max(self.lookback_periods // 2, 5))
            .mean()
        )
        spread_df["spread_std"] = (
            spread_df["spread"]
            .rolling(window=self.lookback_periods, min_periods=max(self.lookback_periods // 2, 5))
            .std()
        )
        spread_df["z_score"] = np.where(
            spread_df["spread_std"] > 0,
            (spread_df["spread"] - spread_df["spread_mean"]) / spread_df["spread_std"],
            0.0,
        )

        # Drop rows without enough history
        spread_df = spread_df.dropna(subset=["spread_mean", "spread_std"])

        signals: List[ArbitrageSignal] = []
        in_position = False
        entry_direction: Optional[str] = None
        periods_held = 0

        for _, row in spread_df.iterrows():
            spread = row["spread"]
            abs_spread = row["abs_spread"]
            z_score = row["z_score"]
            ts = int(row["timestamp_unix"]) if "timestamp_unix" in row.index else 0

            if not in_position:
                # Check for entry
                if abs_spread >= self.entry_spread and abs(z_score) >= self.z_score_threshold:
                    # Estimate profit after per-fill fees and half-spreads
                    fees = self.estimate_cost_per_contract(
                        row["poly_price"], row["kalshi_price"], getattr(pair, "category", None) or None
                    )
                    expected_profit = abs_spread - fees

                    if expected_profit >= self.min_expected_profit:
                        direction = "buy_kalshi" if spread > 0 else "buy_poly"
                        confidence = min(abs(z_score) / 4.0, 0.95)

                        signals.append(ArbitrageSignal(
                            pair_id=pair.pair_id,
                            polymarket_id=pair.polymarket_id,
                            kalshi_ticker=pair.kalshi_ticker,
                            signal_type="entry",
                            timestamp=ts,
                            poly_price=row["poly_price"],
                            kalshi_price=row["kalshi_price"],
                            spread=spread,
                            abs_spread=abs_spread,
                            direction=direction,
                            confidence=confidence,
                            z_score=z_score,
                            expected_profit=expected_profit,
                            metadata={
                                "spread_mean": row["spread_mean"],
                                "spread_std": row["spread_std"],
                                "fees": fees,
                                "pair_confidence": pair.confidence,
                            },
                        ))
                        in_position = True
                        entry_direction = direction
                        periods_held = 0

            else:
                # Check for exit
                periods_held += 1
                should_exit = False
                exit_reason = ""

                # Convergence exit: spread narrowed
                if abs_spread <= self.exit_spread:
                    should_exit = True
                    exit_reason = "convergence"

                # Spread flipped direction (mean reversion overshoot)
                elif entry_direction == "buy_kalshi" and spread < 0:
                    should_exit = True
                    exit_reason = "flip"
                elif entry_direction == "buy_poly" and spread > 0:
                    should_exit = True
                    exit_reason = "flip"

                # Max holding period
                elif (
                    self.max_holding_periods is not None
                    and periods_held >= self.max_holding_periods
                ):
                    should_exit = True
                    exit_reason = "max_hold"

                if should_exit:
                    signals.append(ArbitrageSignal(
                        pair_id=pair.pair_id,
                        polymarket_id=pair.polymarket_id,
                        kalshi_ticker=pair.kalshi_ticker,
                        signal_type="exit",
                        timestamp=ts,
                        poly_price=row["poly_price"],
                        kalshi_price=row["kalshi_price"],
                        spread=spread,
                        abs_spread=abs_spread,
                        direction=entry_direction or "",
                        confidence=0.0,
                        z_score=z_score,
                        expected_profit=0.0,
                        metadata={
                            "exit_reason": exit_reason,
                            "periods_held": periods_held,
                        },
                    ))
                    in_position = False
                    entry_direction = None

        return signals

    def get_parameters(self) -> Dict[str, Any]:
        """Return strategy parameters for reproducibility."""
        return {
            "strategy_name": self.name,
            "entry_spread": self.entry_spread,
            "exit_spread": self.exit_spread,
            "min_expected_profit": self.min_expected_profit,
            "lookback_periods": self.lookback_periods,
            "z_score_threshold": self.z_score_threshold,
            "max_holding_periods": self.max_holding_periods,
            "fee_model": self.fee_model.describe(),
            "kalshi_half_spread": self.kalshi_half_spread,
            "poly_half_spread": self.poly_half_spread,
            "category": self.category,
        }

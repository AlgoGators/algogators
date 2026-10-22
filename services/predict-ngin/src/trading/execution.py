"""
Trade-based execution engine for realistic backtest fills.

Walks the historical trade tape to simulate order execution:
- VWAP fill price from consumed trades
- Partial fills when liquidity is insufficient
- Spread and slippage adjustments from data-driven models
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from src.data_quality.polymarket_tokens import ensure_yes_trades
except ImportError:  # src/ itself on sys.path
    from data_quality.polymarket_tokens import ensure_yes_trades

from .data_modules.costs import (
    LiquidityFilter,
    LiquidityFilterConfig,
    SlippageModel,
    SpreadEstimator,
)
try:
    from src.timeconv import to_epoch, to_epoch_seconds
except ImportError:  # src/ itself on sys.path
    from timeconv import to_epoch, to_epoch_seconds


def _to_unix_seconds(series: pd.Series) -> pd.Series:
    """Convert a timestamp series to integer unix seconds."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return to_epoch_seconds(series)
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.max() > 1_000_000_000_000:
        return (numeric // 1000).astype("int64")
    return numeric.astype("int64")


@dataclass
class FillResult:
    """Result of an execution attempt against the trade tape."""

    filled: bool
    fill_price: float  # VWAP of consumed trades (0 if not filled)
    fill_size: float  # Actual filled amount (may be < requested)
    fill_timestamp: int  # Timestamp of last consumed trade
    num_trades_consumed: int
    slippage: float  # fill_price - arrival_price
    spread_adjustment: float  # Half-spread applied
    partial: bool  # True if fill_size < requested size


class TradeBasedExecutionEngine:
    """Walk the trade tape to simulate realistic fills.

    For a BUY of size $X:
      1. Start from signal_time + latency
      2. Consume trades on the SELL side (taker_direction == SELL)
      3. Accumulate volume until $X is reached
      4. Fill price = VWAP of consumed trades + spread/2 + slippage

    For a SELL, consume BUY-side trades and subtract spread/2.

    Supports partial fills when tape volume is insufficient.
    """

    def __init__(
        self,
        trades_df: pd.DataFrame,
        spread_estimator: Optional[SpreadEstimator] = None,
        slippage_model: Optional[SlippageModel] = None,
        liquidity_filter: Optional[LiquidityFilter] = None,
        fill_window_seconds: int = 3600,
        min_fill_fraction: float = 0.10,
    ):
        """
        Args:
            trades_df: Raw trade data with market_id, outcome, timestamp,
                       price, usd_amount, taker_direction columns.
            spread_estimator: Optional data-driven spread estimator.
            slippage_model: Optional data-driven slippage model.
            liquidity_filter: Optional volume-based fill gate.
            fill_window_seconds: Max seconds to look forward for fills.
            min_fill_fraction: Minimum fraction of order that must fill
                               (below this, treat as no-fill).
        """
        self.spread_estimator = spread_estimator
        self.slippage_model = slippage_model
        self.liquidity_filter = liquidity_filter
        self.fill_window_seconds = fill_window_seconds
        self.min_fill_fraction = min_fill_fraction

        # Pre-index trades for fast lookups
        self._index: Dict[str, pd.DataFrame] = {}

        if trades_df.empty:
            return

        # pmfix: YES-normalise so (market, "YES") groups never interleave both tokens
        df = ensure_yes_trades(trades_df, where="TradeBasedExecutionEngine").copy()

        # Ensure required columns
        if "price" not in df.columns:
            if "usd_amount" in df.columns and "token_amount" in df.columns:
                df["price"] = df["usd_amount"] / df["token_amount"]
            else:
                return

        if "usd_amount" not in df.columns:
            if "token_amount" in df.columns:
                df["usd_amount"] = df["price"] * df["token_amount"]
            else:
                df["usd_amount"] = 0.0

        # Normalise taker direction
        if "taker_direction" in df.columns:
            df["_taker"] = df["taker_direction"].astype(str).str.upper()
        elif "maker_direction" in df.columns:
            mapping = {"BUY": "SELL", "SELL": "BUY"}
            df["_taker"] = (
                df["maker_direction"].astype(str).str.upper().map(mapping).fillna("BUY")
            )
        else:
            df["_taker"] = "BUY"

        df["_ts"] = _to_unix_seconds(df["timestamp"])

        if "outcome" not in df.columns:
            df["outcome"] = "YES"
        df["outcome"] = df["outcome"].astype(str).str.upper()

        cols = ["market_id", "outcome", "_ts", "price", "usd_amount", "_taker"]
        df = df[cols].dropna().sort_values(["market_id", "outcome", "_ts"]).reset_index(drop=True)

        for (mid, out), grp in df.groupby(["market_id", "outcome"]):
            self._index[f"{mid}|{out}"] = grp.reset_index(drop=True)

    def execute_order(
        self,
        market_id: str,
        outcome: str,
        side: str,
        size: float,
        signal_ts: int,
        latency_seconds: int = 60,
    ) -> FillResult:
        """Execute an order by walking the trade tape.

        Args:
            market_id: Market identifier.
            outcome: YES or NO.
            side: BUY or SELL.
            size: Order size in USD.
            signal_ts: Signal timestamp (unix seconds).
            latency_seconds: Execution delay from signal.

        Returns:
            FillResult with VWAP fill price and fill details.
        """
        no_fill = FillResult(
            filled=False,
            fill_price=0.0,
            fill_size=0.0,
            fill_timestamp=signal_ts,
            num_trades_consumed=0,
            slippage=0.0,
            spread_adjustment=0.0,
            partial=False,
        )

        # Liquidity gate
        if self.liquidity_filter is not None:
            allowed, _prob = self.liquidity_filter.should_fill(
                market_id, outcome, signal_ts, size
            )
            if not allowed:
                return no_fill

        key = f"{market_id}|{outcome.upper()}"
        tape = self._index.get(key)
        if tape is None or tape.empty:
            return no_fill

        entry_ts = signal_ts + latency_seconds
        end_ts = entry_ts + self.fill_window_seconds

        # Filter tape to [entry_ts, end_ts]
        mask = (tape["_ts"] >= entry_ts) & (tape["_ts"] <= end_ts)
        window = tape.loc[mask]
        if window.empty:
            return no_fill

        # BUY consumes SELL-side trades (and vice versa)
        counter_side = "SELL" if side.upper() == "BUY" else "BUY"
        matching = window[window["_taker"] == counter_side]

        # If no counter-side trades, use all trades as fallback
        if matching.empty:
            matching = window

        # Walk forward consuming trades
        filled_usd = 0.0
        weighted_price_sum = 0.0
        last_ts = entry_ts
        n_consumed = 0

        for _, trade in matching.iterrows():
            trade_usd = float(trade["usd_amount"])
            trade_price = float(trade["price"])

            if trade_usd <= 0 or trade_price <= 0 or trade_price >= 1:
                continue

            remaining = size - filled_usd
            consume = min(trade_usd, remaining)

            weighted_price_sum += trade_price * consume
            filled_usd += consume
            last_ts = int(trade["_ts"])
            n_consumed += 1

            if filled_usd >= size:
                break

        if filled_usd <= 0:
            return no_fill

        fill_fraction = filled_usd / size
        if fill_fraction < self.min_fill_fraction:
            return no_fill

        vwap = weighted_price_sum / filled_usd

        # Arrival price: first trade price in window
        arrival_price = float(matching.iloc[0]["price"])

        # Spread adjustment
        spread_adj = 0.0
        if self.spread_estimator is not None:
            half_spread = self.spread_estimator.estimate_spread(
                market_id, outcome, signal_ts
            ) / 2.0
            if side.upper() == "BUY":
                spread_adj = half_spread
            else:
                spread_adj = -half_spread

        # Slippage from model
        slip = 0.0
        if self.slippage_model is not None:
            if self.liquidity_filter is not None:
                vol = self.liquidity_filter.recent_volume(
                    market_id, outcome, signal_ts
                )
            else:
                vol = float(window["usd_amount"].sum())
            slip = self.slippage_model.estimate_slippage(size, vol)
            if side.upper() == "SELL":
                slip = -slip

        fill_price = vwap + spread_adj + slip
        # Clamp to valid range for prediction markets
        fill_price = max(0.001, min(0.999, fill_price))

        raw_slippage = fill_price - arrival_price
        if side.upper() == "SELL":
            raw_slippage = -raw_slippage

        return FillResult(
            filled=True,
            fill_price=fill_price,
            fill_size=filled_usd if fill_fraction < 1.0 else size,
            fill_timestamp=last_ts,
            num_trades_consumed=n_consumed,
            slippage=raw_slippage,
            spread_adjustment=abs(spread_adj),
            partial=fill_fraction < 1.0,
        )


class LOBExecutionEngine:
    """Stub for limit-order-book based execution.

    Placeholder for when Polymarket order book snapshot data becomes
    available. Falls back to TradeBasedExecutionEngine when no LOB data
    is present.
    """

    def __init__(
        self,
        orderbook_data: Optional[pd.DataFrame] = None,
        fallback: Optional[TradeBasedExecutionEngine] = None,
    ):
        self.orderbook_data = orderbook_data
        self.fallback = fallback

    def execute_with_depth(
        self,
        market_id: str,
        outcome: str,
        side: str,
        size: float,
        signal_ts: int,
        latency_seconds: int = 60,
    ) -> FillResult:
        """Execute using LOB depth if available, else fall back to tape."""
        if self.orderbook_data is not None and not self.orderbook_data.empty:
            # TODO: implement depth-walking when LOB data is available
            #   For buys: take liquidity from ask side
            #   For sells: take liquidity from bid side
            #   Model partial fills when depth is insufficient
            pass

        if self.fallback is not None:
            return self.fallback.execute_order(
                market_id, outcome, side, size, signal_ts, latency_seconds
            )

        return FillResult(
            filled=False,
            fill_price=0.0,
            fill_size=0.0,
            fill_timestamp=signal_ts,
            num_trades_consumed=0,
            slippage=0.0,
            spread_adjustment=0.0,
            partial=False,
        )

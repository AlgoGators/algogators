"""
Prediction market cost model with market impact.

Implements square-root market impact model (Almgren-Chriss style)
with explicit assumptions for different position sizes.

Also provides data-driven cost components:
- SpreadEstimator: bid-ask spread estimated from trade taker_direction
- SlippageModel: size-dependent slippage calibrated from trade data
- LiquidityFilter: volume-based fill gating
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
try:
    from src.timeconv import to_epoch, to_epoch_seconds
except ImportError:  # src/ itself on sys.path
    from timeconv import to_epoch, to_epoch_seconds


@dataclass
class CostAssumptions:
    """Cost model assumptions for a position size category."""

    name: str
    base_spread: float  # Bid-ask spread
    base_slippage: float  # Base slippage
    impact_coefficient: float  # Market impact multiplier
    liquidity_threshold: float  # Minimum liquidity to trade
    description: str


COST_ASSUMPTIONS = {
    "small": CostAssumptions(
        name="Small Retail ($100-500)",
        base_spread=0.02,
        base_slippage=0.005,
        impact_coefficient=0.5,
        liquidity_threshold=500,
        description="Retail-sized orders with minimal market footprint",
    ),
    "medium": CostAssumptions(
        name="Medium ($1,000-5,000)",
        base_spread=0.025,
        base_slippage=0.01,
        impact_coefficient=1.0,
        liquidity_threshold=5000,
        description="Mid-sized orders requiring moderate liquidity",
    ),
    "large": CostAssumptions(
        name="Large ($10,000-20,000)",
        base_spread=0.03,
        base_slippage=0.02,
        impact_coefficient=2.0,
        liquidity_threshold=25000,
        description="Large orders with significant market footprint",
    ),
}


class CostModel:
    """
    Trading cost model for prediction markets.

    Uses square-root market impact model:
        Total Cost = Base Spread/2 + Base Slippage + Size Impact

    Where:
        Size Impact = impact_coef * base_slippage * sqrt(trade_size / liquidity)
    """

    def __init__(
        self,
        base_spread: float = 0.02,
        base_slippage: float = 0.005,
        impact_coefficient: float = 0.5,
        max_impact: float = 0.15,
    ):
        """
        Initialize cost model.

        Args:
            base_spread: Bid-ask spread (default 2%)
            base_slippage: Base slippage (default 0.5%)
            impact_coefficient: Market impact multiplier
            max_impact: Maximum allowed impact (default 15%)
        """
        self.base_spread = base_spread
        self.base_slippage = base_slippage
        self.impact_coefficient = impact_coefficient
        self.max_impact = max_impact

    @classmethod
    def from_assumptions(cls, category: str) -> "CostModel":
        """Create model from predefined assumptions."""
        if category not in COST_ASSUMPTIONS:
            raise ValueError(f"Unknown category: {category}")

        assumptions = COST_ASSUMPTIONS[category]
        return cls(
            base_spread=assumptions.base_spread,
            base_slippage=assumptions.base_slippage,
            impact_coefficient=assumptions.impact_coefficient,
        )

    def calculate_one_way_cost(
        self,
        trade_size: float,
        liquidity: float,
    ) -> float:
        """
        Calculate one-way trading cost.

        Args:
            trade_size: Size of the trade in dollars
            liquidity: Market liquidity in dollars

        Returns:
            One-way cost as decimal (e.g., 0.02 = 2%)
        """
        if liquidity <= 0:
            liquidity = 1000  # Default

        # Square-root impact model
        size_impact = (
            self.impact_coefficient
            * self.base_slippage
            * np.sqrt(trade_size / liquidity)
        )
        size_impact = min(size_impact, self.max_impact)

        return self.base_spread / 2 + self.base_slippage + size_impact

    def calculate_round_trip_cost(
        self,
        trade_size: float,
        liquidity: float,
    ) -> float:
        """Calculate round-trip (entry + exit) cost."""
        return 2 * self.calculate_one_way_cost(trade_size, liquidity)

    def calculate_entry_price(
        self,
        base_price: float,
        trade_size: float,
        liquidity: float,
    ) -> float:
        """Calculate effective entry price after costs."""
        cost = self.calculate_one_way_cost(trade_size, liquidity)
        return base_price * (1 + cost)

    def calculate_exit_price(
        self,
        base_price: float,
        trade_size: float,
        liquidity: float,
    ) -> float:
        """Calculate effective exit price after costs."""
        cost = self.calculate_one_way_cost(trade_size, liquidity)
        return base_price * (1 - cost)

    def calculate_break_even_win_rate(
        self,
        trade_size: float,
        liquidity: float,
    ) -> float:
        """
        Calculate break-even win rate for binary markets.

        For binary markets with symmetric payoffs:
            break_even = 0.5 + round_trip_cost / 2
        """
        round_trip = self.calculate_round_trip_cost(trade_size, liquidity)
        return 0.5 + round_trip / 2


# Default cost model (small retail)
DEFAULT_COST_MODEL = CostModel()

# Polymarket: no transaction fees (spread=0), 1 cent slippage, no impact
POLYMARKET_ZERO_COST_MODEL = CostModel(
    base_spread=0.0,
    base_slippage=0.01,  # 1 cent slippage
    impact_coefficient=0.0,
    max_impact=0.0,
)


# ---------------------------------------------------------------------------
# Data-driven cost components
# ---------------------------------------------------------------------------


def _to_unix_seconds(series: pd.Series) -> pd.Series:
    """Convert a timestamp series to integer unix seconds."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return to_epoch_seconds(series)
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.max() > 1_000_000_000_000:
        return (numeric // 1000).astype("int64")
    return numeric.astype("int64")


class SpreadEstimator:
    """Estimate bid-ask spread from trade data using taker_direction.

    In a rolling window, separates BUY vs SELL taker trades and computes
    avg_buy_price - avg_sell_price as a spread proxy.
    """

    def __init__(
        self,
        trades_df: pd.DataFrame,
        window_seconds: int = 3600,
        default_spread: float = 0.02,
    ):
        self.window_seconds = window_seconds
        self.default_spread = default_spread
        self._spread_cache: Dict[str, pd.DataFrame] = {}

        if trades_df.empty:
            self._trades = pd.DataFrame()
            return

        df = trades_df.copy()

        # Normalise taker direction
        if "taker_direction" in df.columns:
            df["_side"] = df["taker_direction"].astype(str).str.upper()
        elif "maker_direction" in df.columns:
            # Flip maker to get taker side
            mapping = {"BUY": "SELL", "SELL": "BUY"}
            df["_side"] = (
                df["maker_direction"].astype(str).str.upper().map(mapping).fillna("BUY")
            )
        else:
            df["_side"] = "BUY"

        if "price" not in df.columns:
            if "usd_amount" in df.columns and "token_amount" in df.columns:
                df["price"] = df["usd_amount"] / df["token_amount"]
            else:
                self._trades = pd.DataFrame()
                return

        df["_ts"] = _to_unix_seconds(df["timestamp"])

        if "outcome" not in df.columns:
            df["outcome"] = "YES"
        df["outcome"] = df["outcome"].astype(str).str.upper()

        self._trades = (
            df[["market_id", "outcome", "_ts", "price", "_side"]]
            .dropna()
            .sort_values(["market_id", "outcome", "_ts"])
            .reset_index(drop=True)
        )

    def estimate_spread(
        self, market_id: str, outcome: str, timestamp: int
    ) -> float:
        """Return estimated half-spread at *timestamp*."""
        key = f"{market_id}|{outcome.upper()}"
        if key not in self._spread_cache:
            self._spread_cache[key] = self._build_spread_series(
                market_id, outcome.upper()
            )

        series = self._spread_cache[key]
        if series.empty:
            return self.default_spread

        # Find the latest spread estimate at or before timestamp
        mask = series["_ts"] <= timestamp
        if not mask.any():
            return series.iloc[0]["spread"]
        return float(series.loc[mask].iloc[-1]["spread"])

    def _build_spread_series(
        self, market_id: str, outcome: str
    ) -> pd.DataFrame:
        if self._trades.empty:
            return pd.DataFrame()

        mkt = self._trades[
            (self._trades["market_id"] == str(market_id))
            & (self._trades["outcome"] == outcome)
        ]
        if mkt.empty:
            return pd.DataFrame()

        buys = mkt[mkt["_side"] == "BUY"]
        sells = mkt[mkt["_side"] == "SELL"]
        if buys.empty or sells.empty:
            return pd.DataFrame()

        # Build spread at regular intervals
        ts_min, ts_max = int(mkt["_ts"].min()), int(mkt["_ts"].max())
        step = max(self.window_seconds, 1)
        records = []
        for t in range(ts_min + step, ts_max + 1, step):
            w_start = t - self.window_seconds
            b = buys[(buys["_ts"] >= w_start) & (buys["_ts"] < t)]
            s = sells[(sells["_ts"] >= w_start) & (sells["_ts"] < t)]
            if b.empty or s.empty:
                continue
            spread = float(b["price"].mean() - s["price"].mean())
            spread = max(abs(spread), 0.001)  # floor at 0.1%
            records.append({"_ts": t, "spread": spread})

        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records)


class SlippageModel:
    """Size-dependent slippage calibrated from trade data.

    Model: slippage = alpha * sqrt(order_size / recent_volume) + beta
    Calibrated by bucketing historical trades by size/volume ratio and
    measuring price deviation.
    """

    def __init__(
        self,
        trades_df: Optional[pd.DataFrame] = None,
        alpha: float = 0.05,
        beta: float = 0.001,
        max_slippage: float = 0.10,
        calibrate: bool = True,
    ):
        self.alpha = alpha
        self.beta = beta
        self.max_slippage = max_slippage

        if calibrate and trades_df is not None and not trades_df.empty:
            self._calibrate(trades_df)

    def _calibrate(self, trades_df: pd.DataFrame) -> None:
        """Calibrate alpha/beta from trade data."""
        df = trades_df.copy()

        if "usd_amount" not in df.columns:
            return
        if "price" not in df.columns:
            if "token_amount" in df.columns:
                df["price"] = df["usd_amount"] / df["token_amount"]
            else:
                return

        df["_ts"] = _to_unix_seconds(df["timestamp"])
        df = df.dropna(subset=["usd_amount", "price", "_ts"])
        if len(df) < 100:
            return

        df = df.sort_values("_ts").reset_index(drop=True)

        # Rolling 24h volume per market
        hour_vol = (
            df.groupby("market_id")
            .apply(
                lambda g: g.set_index("_ts")["usd_amount"]
                .rolling(window=86400, min_periods=1)
                .sum()
                .reset_index(),
                include_groups=False,
            )
        )
        if isinstance(hour_vol, pd.DataFrame) and not hour_vol.empty:
            df["_rolling_vol"] = hour_vol["usd_amount"].values
        else:
            df["_rolling_vol"] = df["usd_amount"].rolling(100, min_periods=1).sum()

        df["_ratio"] = np.sqrt(df["usd_amount"] / df["_rolling_vol"].clip(lower=1))

        # Price deviation: |price - rolling_median| / rolling_median
        df["_median_price"] = (
            df.groupby("market_id")["price"]
            .transform(lambda s: s.rolling(50, min_periods=5).median())
        )
        df = df.dropna(subset=["_median_price"])
        if df.empty:
            return
        df["_deviation"] = (
            (df["price"] - df["_median_price"]).abs() / df["_median_price"].clip(lower=0.01)
        )

        # Bucket by ratio quintiles
        try:
            df["_bucket"] = pd.qcut(df["_ratio"], 5, labels=False, duplicates="drop")
        except ValueError:
            return

        bucket_stats = df.groupby("_bucket").agg(
            mean_ratio=("_ratio", "mean"),
            mean_dev=("_deviation", "mean"),
        )
        if len(bucket_stats) < 2:
            return

        # Simple linear fit: deviation = alpha * ratio + beta
        x = bucket_stats["mean_ratio"].values
        y = bucket_stats["mean_dev"].values
        if x.std() == 0:
            return
        slope = np.polyfit(x, y, 1)
        self.alpha = max(float(slope[0]), 0.001)
        self.beta = max(float(slope[1]), 0.0)

    def estimate_slippage(self, order_size: float, recent_volume: float) -> float:
        """Return slippage fraction for given order size vs recent volume."""
        if recent_volume <= 0:
            return self.max_slippage
        ratio = np.sqrt(order_size / recent_volume)
        slip = self.alpha * ratio + self.beta
        return float(min(slip, self.max_slippage))


@dataclass
class LiquidityFilterConfig:
    """Configuration for liquidity-based fill filtering."""

    min_volume_window_hours: int = 24
    min_volume_usd: float = 500.0
    max_size_to_volume_ratio: float = 0.10
    fill_probability_model: bool = True


class LiquidityFilter:
    """Volume-based fill gating — avoid unrealistic fills in thin markets.

    Pre-computes rolling volume per (market_id, outcome) for fast lookups.
    """

    def __init__(
        self,
        trades_df: pd.DataFrame,
        config: Optional[LiquidityFilterConfig] = None,
    ):
        self.config = config or LiquidityFilterConfig()
        self._volume_index: Dict[str, pd.DataFrame] = {}

        if trades_df.empty:
            return

        df = trades_df.copy()
        if "usd_amount" not in df.columns:
            return

        df["_ts"] = _to_unix_seconds(df["timestamp"])
        if "outcome" not in df.columns:
            df["outcome"] = "YES"
        df["outcome"] = df["outcome"].astype(str).str.upper()

        for (mid, out), grp in df.groupby(["market_id", "outcome"]):
            sorted_grp = grp[["_ts", "usd_amount"]].sort_values("_ts").reset_index(drop=True)
            self._volume_index[f"{mid}|{out}"] = sorted_grp

    def recent_volume(
        self, market_id: str, outcome: str, timestamp: int, window_hours: Optional[int] = None
    ) -> float:
        """Rolling USD volume in the window before *timestamp*."""
        hours = window_hours or self.config.min_volume_window_hours
        key = f"{market_id}|{outcome.upper()}"
        vi = self._volume_index.get(key)
        if vi is None or vi.empty:
            return 0.0

        window_start = timestamp - hours * 3600
        mask = (vi["_ts"] >= window_start) & (vi["_ts"] < timestamp)
        return float(vi.loc[mask, "usd_amount"].sum())

    def should_fill(
        self, market_id: str, outcome: str, timestamp: int, order_size: float
    ) -> Tuple[bool, float]:
        """Return (allowed, fill_probability).

        Rejected when recent volume is below threshold or order is too large
        relative to volume.
        """
        vol = self.recent_volume(market_id, outcome, timestamp)

        if vol < self.config.min_volume_usd:
            return False, 0.0

        ratio = order_size / vol if vol > 0 else float("inf")
        if ratio > self.config.max_size_to_volume_ratio:
            return False, 0.0

        if self.config.fill_probability_model:
            # Fill probability decays as order_size approaches volume
            prob = max(0.0, min(1.0, 1.0 - ratio / self.config.max_size_to_volume_ratio))
            return True, prob

        return True, 1.0

"""
Execution model for whale-following backtests.

Replaces the original "fill at the whale's own print, zero latency" assumption with:

- Entry at the NEXT trade print in the same market that is strictly later than the
  signal print and at least ``latency_s`` seconds after it (YES-normalised prices).
- A half-spread cost added to the token price paid (subtracted on market exits).
- Polymarket taker fee per share = rate * p * (1 - p), rate by category.

Also provides point-in-time sizing (trailing traded volume up to decision time,
never lifetime ``volumeClob``) and position-level aggregation of partial exits.

All prices passed in/out of the tape are YES-token prices. ``Fill.token_price`` is
the price of the token actually bought/sold: YES for BUY, NO (= 1 - YES) for SELL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from predict_ngin.costs.polymarket import polymarket_fee_per_share

# Polymarket taker fee rates by category (fee/share = rate * p * (1 - p)).
DEFAULT_FEE_RATES: Dict[str, float] = {
    "Politics": 0.04,
    "Finance": 0.04,
    "Tech": 0.04,
    "Economy": 0.05,
    "Sports": 0.05,
    "Geopolitics": 0.0,
}
DEFAULT_OTHER_FEE_RATE = 0.05

# Highest price a marketable order can pay on Polymarket (tick 0.01 .. 0.99).
MAX_TOKEN_PRICE = 0.99


def taker_fee_per_share(price: float, rate: float) -> float:
    """Polymarket taker fee per share: rate * p * (1 - p)."""
    return polymarket_fee_per_share(float(price), float(rate))


@dataclass
class FillConfig:
    """How entries/exits are filled. ``mode='original'`` reproduces the legacy model."""

    mode: str = "realistic"            # "realistic" | "original"
    latency_s: float = 5.0
    half_spread: float = 0.01
    fee_rates: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FEE_RATES))
    default_fee_rate: float = DEFAULT_OTHER_FEE_RATE
    # Legacy cost haircut applied to gross PnL in original mode (net = gross * 0.97)
    original_cost_haircut: float = 0.03

    @property
    def realistic(self) -> bool:
        return self.mode == "realistic"

    def fee_rate_for(self, category: str) -> float:
        return float(self.fee_rates.get(category, self.default_fee_rate))

    @classmethod
    def original(cls) -> "FillConfig":
        return cls(mode="original", latency_s=0.0, half_spread=0.0,
                   fee_rates={}, default_fee_rate=0.0)


@dataclass
class Fill:
    token_price: float          # price paid/received for the token traded (YES for BUY, NO for SELL)
    yes_ref_price: float        # YES print the fill was referenced to
    fee_per_share: float
    ts: pd.Timestamp


class TradeTape:
    """Per-market sorted trade prints (YES-normalised) for fills and trailing volume."""

    def __init__(self, trades_df: pd.DataFrame):
        self._ts: Dict[str, np.ndarray] = {}
        self._px: Dict[str, np.ndarray] = {}
        self._cumvol: Dict[str, np.ndarray] = {}
        if trades_df is None or trades_df.empty:
            return
        df = trades_df[["market_id", "datetime", "price", "usd_amount"]].copy()
        df["market_id"] = df["market_id"].astype(str).str.strip()
        df["_ns"] = pd.to_datetime(df["datetime"]).astype("datetime64[ns]").astype("int64")
        df = df.dropna(subset=["price"]).sort_values(["market_id", "_ns"], kind="mergesort")
        for mid, g in df.groupby("market_id", sort=False):
            ns = g["_ns"].to_numpy(dtype="int64")
            self._ts[mid] = ns
            self._px[mid] = g["price"].to_numpy(dtype="float64")
            self._cumvol[mid] = np.cumsum(g["usd_amount"].fillna(0).to_numpy(dtype="float64"))

    @staticmethod
    def _ns(ts) -> int:
        return int(pd.Timestamp(ts).value)

    def has_market(self, mid: str) -> bool:
        return str(mid) in self._ts

    def next_print(self, mid: str, after_ts, latency_s: float = 0.0) -> Optional[Tuple[pd.Timestamp, float]]:
        """First print with timestamp > after_ts and >= after_ts + latency_s."""
        mid = str(mid)
        ts = self._ts.get(mid)
        if ts is None:
            return None
        t = self._ns(after_ts)
        t_min = t + int(round(latency_s * 1e9))
        if latency_s > 0:
            idx = int(np.searchsorted(ts, t_min, side="left"))
        else:
            idx = int(np.searchsorted(ts, t, side="right"))
        # Enforce strictly later than the signal print
        while idx < len(ts) and ts[idx] <= t:
            idx += 1
        if idx >= len(ts):
            return None
        return pd.Timestamp(int(ts[idx])), float(self._px[mid][idx])

    def price_at_or_before(self, mid: str, ts, strict: bool = False) -> Optional[float]:
        mid = str(mid)
        arr = self._ts.get(mid)
        if arr is None:
            return None
        side = "left" if strict else "right"
        idx = int(np.searchsorted(arr, self._ns(ts), side=side)) - 1
        if idx < 0:
            return None
        return float(self._px[mid][idx])

    def last_price(self, mid: str) -> Optional[float]:
        px = self._px.get(str(mid))
        return None if px is None or len(px) == 0 else float(px[-1])

    def trailing_volume(self, mid: str, ts, window_s: float = 86_400.0) -> float:
        """USD volume of prints with ts - window < t <= ts (point-in-time)."""
        mid = str(mid)
        arr = self._ts.get(mid)
        if arr is None:
            return 0.0
        t = self._ns(ts)
        hi = int(np.searchsorted(arr, t, side="right"))
        lo = int(np.searchsorted(arr, t - int(window_s * 1e9), side="right"))
        if hi <= 0 or hi <= lo:
            return 0.0
        cv = self._cumvol[mid]
        return float(cv[hi - 1] - (cv[lo - 1] if lo > 0 else 0.0))


def _token_from_yes(side: str, yes_price: float) -> float:
    return float(yes_price) if side.upper() == "BUY" else 1.0 - float(yes_price)


def entry_fill(
    side: str,
    signal_yes_price: float,
    signal_ts,
    market_id: str,
    category: str,
    tape: Optional[TradeTape],
    cfg: FillConfig,
) -> Optional[Fill]:
    """Fill for opening (buying YES for BUY, NO for SELL). None if not fillable."""
    if not cfg.realistic:
        return Fill(_token_from_yes(side, signal_yes_price), float(signal_yes_price), 0.0,
                    pd.Timestamp(signal_ts))
    if tape is None:
        return None
    nxt = tape.next_print(market_id, signal_ts, cfg.latency_s)
    if nxt is None:
        return None
    fill_ts, yes_px = nxt
    token = _token_from_yes(side, yes_px) + cfg.half_spread
    if token > MAX_TOKEN_PRICE + 1e-9 or token <= 0:
        return None
    fee = taker_fee_per_share(token, cfg.fee_rate_for(category))
    return Fill(token, yes_px, fee, fill_ts)


def market_exit_fill(side: str, yes_mid: float, category: str, cfg: FillConfig, ts) -> Fill:
    """Fill for selling the held token at market (not a resolution payout)."""
    token = _token_from_yes(side, yes_mid)
    if cfg.realistic:
        token = max(token - cfg.half_spread, 0.0)
        fee = taker_fee_per_share(token, cfg.fee_rate_for(category))
    else:
        fee = 0.0
    return Fill(token, float(yes_mid), fee, pd.Timestamp(ts))


@dataclass
class SizingConfig:
    """Position sizing. ``point_in_time`` uses trailing traded volume up to decision time."""

    mode: str = "point_in_time"            # "point_in_time" | "lifetime_volume" (legacy, look-ahead)
    volume_window_s: float = 86_400.0
    volume_cap_pct: float = 0.05           # max position as fraction of trailing-window volume
    max_position_usd: Optional[float] = None
    min_position_usd: float = 1_000.0
    use_kelly: bool = True                 # False -> flat: min(max_position, volume cap, available)
    capital: Optional[float] = None        # informational preset (small account)

    @classmethod
    def small_account(cls, **kw) -> "SizingConfig":
        base = dict(mode="point_in_time", max_position_usd=250.0, min_position_usd=10.0,
                    use_kelly=False, capital=5_000.0)
        base.update(kw)
        return cls(**base)

    @classmethod
    def legacy(cls) -> "SizingConfig":
        return cls(mode="lifetime_volume", volume_cap_pct=0.03, max_position_usd=None,
                   min_position_usd=1_000.0, use_kelly=True)


def size_position_pit(trailing_volume_usd: float, available_usd: float, sizing: SizingConfig) -> Optional[float]:
    """Flat point-in-time size: min(max_position, cap% * trailing volume, available)."""
    caps = [sizing.volume_cap_pct * max(float(trailing_volume_usd), 0.0), max(float(available_usd), 0.0)]
    if sizing.max_position_usd is not None:
        caps.append(float(sizing.max_position_usd))
    size = min(caps)
    if size < sizing.min_position_usd:
        return None
    return size


def aggregate_partial_exits(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse fills (partial exits + final close) into one row per position."""
    if df is None or df.empty or "position_id" not in df.columns:
        return df
    df = df.sort_values(["position_id", "exit_date"], kind="mergesort")
    sums = {c: "sum" for c in ("gross_pnl", "net_pnl", "fees_usd", "position_size") if c in df.columns}
    firsts = {c: "first" for c in df.columns if c not in sums and c != "position_id"}
    lasts = {c: "last" for c in ("exit_date", "exit_price", "reason") if c in df.columns}
    agg = {**firsts, **lasts, **sums}
    out = df.groupby("position_id", sort=False).agg(agg)
    out["n_fills"] = df.groupby("position_id", sort=False).size()
    return out.reset_index()

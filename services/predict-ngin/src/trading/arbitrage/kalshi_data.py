"""
Kalshi data normalisation helpers shared by the fetchers, the HF downloader
and the repair scripts.

Bugs these helpers exist to prevent:

* ``created_time`` strings carry UTC offsets (``-05``/``-04``) or ``Z``.
  Casting with ``::timestamp`` (DuckDB) or naive parsing drops the offset and
  shifts ~half of all trades 4-5 hours early. Always parse offset-aware.
* Deriving a daily epoch with ``pd.to_datetime(date).astype('int64') // 10**9``
  silently yields ``1`` under pandas >= 2 (datetime64[s] resolution), which is
  why most of ``data/kalshi/prices`` had ``timestamp == 1``.
* Empty API candles (no trades in the period) have null prices; they must be
  stored as NaN, not 0.
* Candles are stamped at the period END (``end_period_ts``) because the close
  is only known then; derived candles follow the same convention.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

DAY = 86400


def parse_created_time_to_unix(created_time: Any) -> Optional[int]:
    """Parse a Kalshi ``created_time`` (ISO-8601 with offset or Z) to unix seconds."""
    if created_time is None:
        return None
    s = str(created_time).strip()
    if not s:
        return None
    try:
        ts = pd.Timestamp(s)
    except (ValueError, TypeError):
        return None
    if ts is pd.NaT:
        return None
    if ts.tzinfo is None:
        # No offset: Kalshi API times are UTC.
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def recompute_timestamp_unix(df: pd.DataFrame, col: str = "created_time") -> pd.Series:
    """Vectorised offset-aware recomputation of ``timestamp_unix`` from ``created_time``."""
    parsed = pd.to_datetime(df[col].astype(str), utc=True, format="ISO8601", errors="coerce")
    bad = parsed.isna()
    if bad.any():
        # Slow path for odd formats
        parsed.loc[bad] = pd.to_datetime(
            df.loc[bad, col].map(lambda x: pd.NaT if parse_created_time_to_unix(x) is None
                                 else pd.Timestamp(parse_created_time_to_unix(x), unit="s", tz="UTC")),
            utc=True,
        )
    secs = (parsed - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
    return secs.astype("Int64")


def _to_epoch(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, np.integer)):
        return int(v) if v > 1 else None
    if isinstance(v, float):
        return int(v) if math.isfinite(v) and v > 1 else None
    s = str(v).strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        return n if n > 1 else None
    return parse_created_time_to_unix(s)


def _price_field(d: Dict[str, Any], name: str) -> float:
    """Read a price field from a candle sub-dict: cents int, or ``<name>_dollars`` str."""
    if not d:
        return float("nan")
    dollars = d.get(f"{name}_dollars")
    if dollars not in (None, ""):
        try:
            return float(dollars)
        except (TypeError, ValueError):
            pass
    cents = d.get(name)
    if cents is None or cents == "":
        return float("nan")
    try:
        return float(cents) / 100.0
    except (TypeError, ValueError):
        return float("nan")


def candle_to_row(candle: Dict[str, Any], ticker: str, event_ticker: str = "") -> Optional[Dict[str, Any]]:
    """Convert a Kalshi candlestick API payload into a price row.

    Returns None when the candle has no usable ``end_period_ts`` (never stores ts=0/1).
    """
    end_ts = _to_epoch(candle.get("end_period_ts"))
    if end_ts is None:
        return None
    price = candle.get("price") or {}
    bid = candle.get("yes_bid") or {}
    ask = candle.get("yes_ask") or {}
    vol = candle.get("volume")
    oi = candle.get("open_interest")
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "timestamp": int(end_ts),
        "open": _price_field(price, "open"),
        "high": _price_field(price, "high"),
        "low": _price_field(price, "low"),
        "close": _price_field(price, "close"),
        "mean": _price_field(price, "mean"),
        "yes_bid_close": _price_field(bid, "close"),
        "yes_ask_close": _price_field(ask, "close"),
        "volume": int(vol) if isinstance(vol, (int, float, np.integer)) and not pd.isna(vol) else 0,
        "open_interest": int(oi) if isinstance(oi, (int, float, np.integer)) and not pd.isna(oi) else 0,
    }


def derive_daily_candles(trades: pd.DataFrame) -> pd.DataFrame:
    """Aggregate trades into UTC-daily OHLCV candles stamped at period END.

    Requires columns: ticker, timestamp_unix (correct, offset-aware), yes_price, count.
    """
    if trades.empty:
        return pd.DataFrame(
            columns=["ticker", "timestamp", "period_start_ts", "open", "high", "low", "close",
                     "mean", "volume", "open_interest"]
        )
    df = trades[["ticker", "timestamp_unix", "yes_price", "count"]].copy()
    df = df[pd.to_numeric(df["timestamp_unix"], errors="coerce") > 1]
    df["timestamp_unix"] = df["timestamp_unix"].astype("int64")
    df = df.sort_values(["ticker", "timestamp_unix"], kind="mergesort")
    df["period_start_ts"] = (df["timestamp_unix"] // DAY) * DAY
    agg = (
        df.groupby(["ticker", "period_start_ts"], sort=True)
        .agg(
            open=("yes_price", "first"),
            high=("yes_price", "max"),
            low=("yes_price", "min"),
            close=("yes_price", "last"),
            volume=("count", "sum"),
        )
        .reset_index()
    )
    agg["mean"] = (agg["open"] + agg["high"] + agg["low"] + agg["close"]) / 4
    agg["open_interest"] = 0
    agg["timestamp"] = (agg["period_start_ts"] + DAY).astype("int64")
    agg["period_start_ts"] = agg["period_start_ts"].astype("int64")
    return agg[["ticker", "timestamp", "period_start_ts", "open", "high", "low", "close",
                "mean", "volume", "open_interest"]]

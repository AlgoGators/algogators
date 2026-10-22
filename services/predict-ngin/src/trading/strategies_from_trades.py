"""
Generate signals for mean reversion, smart money, and breakout from trade data.

All strategies produce DataFrames compatible with run_polymarket_backtest.
"""

from typing import Optional

import pandas as pd

from .momentum_signals_from_trades import trades_to_price_history
try:
    from src.timeconv import to_epoch, to_epoch_seconds
except ImportError:  # src/ itself on sys.path
    from timeconv import to_epoch, to_epoch_seconds


def generate_mean_reversion_signals_from_trades(
    trades_df: pd.DataFrame,
    shock_threshold: float = 0.08,
    stabilize_threshold: float = 0.02,
    eval_freq_hours: int = 1,
    outcome: str = "YES",
    position_size: Optional[float] = None,
    max_markets: Optional[int] = 1000,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Generate mean reversion signals from trades.

    Looks for sharp 1h moves that stabilize over 6h; BUY on dips, SELL on spikes.
    Uses hourly eval so we can compute return_1h (shift 1) and return_6h (shift 6).
    """
    if trades_df.empty:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])

    price_history = trades_to_price_history(trades_df, outcome=outcome)
    if price_history.empty:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])

    if not pd.api.types.is_datetime64_any_dtype(price_history["timestamp"]):
        price_history["timestamp"] = pd.to_datetime(price_history["timestamp"], errors="coerce")
    price_history = price_history.dropna(subset=["timestamp", "price"])
    price_history["_ts_s"] = to_epoch_seconds(price_history["timestamp"])

    eval_interval = eval_freq_hours * 3600
    daily = (
        price_history.assign(eval_ts=(price_history["_ts_s"] // eval_interval) * eval_interval)
        .groupby(["market_id", "eval_ts"])
        .agg({"price": "last", "_ts_s": "max"})
        .reset_index()
        .sort_values(["market_id", "eval_ts"])
    )

    daily["price_1h"] = daily.groupby("market_id")["price"].shift(1)
    daily["price_6h"] = daily.groupby("market_id")["price"].shift(6)
    daily = daily.dropna(subset=["price_1h", "price_6h"])

    daily["ret_1h"] = (daily["price"] - daily["price_1h"]) / daily["price_1h"]
    daily["ret_6h"] = (daily["price"] - daily["price_6h"]) / daily["price_6h"]

    mask = (
        (daily["ret_1h"].abs() >= shock_threshold) &
        (daily["ret_6h"].abs() <= stabilize_threshold)
    )
    sig = daily[mask].copy()
    sig["side"] = sig["ret_1h"].apply(lambda x: "BUY" if x < 0 else "SELL")
    sig["outcome"] = outcome
    sig["size"] = position_size

    out = sig[["market_id", "eval_ts", "outcome", "side", "size"]].rename(columns={"eval_ts": "signal_ts"})
    if max_markets:
        top = out["market_id"].value_counts().head(max_markets).index.tolist()
        out = out[out["market_id"].isin(top)]
    out["signal_time"] = pd.to_datetime(out["signal_ts"], unit="s", utc=True)

    if start_date:
        start_ts = int(pd.Timestamp(start_date).timestamp())
        out = out[out["signal_ts"] >= start_ts]
    if end_date:
        end_ts = int(pd.Timestamp(end_date).timestamp()) + 86400
        out = out[out["signal_ts"] < end_ts]

    return out.sort_values("signal_ts").reset_index(drop=True)


def generate_smart_money_signals_from_trades(
    trades_df: pd.DataFrame,
    min_trade_size: float = 1000.0,
    aggregation_window_hours: int = 4,
    imbalance_threshold: float = 0.6,
    min_volume: float = 10000.0,
    min_trades: int = 3,
    eval_freq_hours: int = 4,
    outcome: str = "YES",
    position_size: Optional[float] = None,
    max_markets: Optional[int] = 1000,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Generate smart money flow signals from trades.

    Tracks large trades and signals when buy/sell imbalance exceeds threshold.
    """
    from .strategies.smart_money import SmartMoneyStrategy

    if trades_df.empty:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])

    df = trades_df.copy()
    if "datetime" not in df.columns and "timestamp" in df.columns:
        df["datetime"] = pd.to_datetime(df["timestamp"], errors="coerce")
    elif "timestamp" not in df.columns and "datetime" in df.columns:
        df["timestamp"] = df["datetime"]
    df = df.dropna(subset=["datetime", "market_id", "usd_amount"])
    if "taker_direction" not in df.columns and "maker_direction" in df.columns:
        df["taker_direction"] = df["maker_direction"].map({"BUY": "SELL", "SELL": "BUY"})
    if "taker_direction" not in df.columns:
        df["taker_direction"] = "BUY"  # fallback

    strategy = SmartMoneyStrategy(
        min_trade_size=min_trade_size,
        aggregation_window_hours=aggregation_window_hours,
        imbalance_threshold=imbalance_threshold,
        min_volume=min_volume,
        min_trades=min_trades,
    )

    start = df["datetime"].min()
    end = df["datetime"].max()
    timestamps = pd.date_range(start, end, freq=f"{eval_freq_hours}h").tolist()
    raw_signals = strategy.generate_signals(df, timestamps=timestamps)

    if not raw_signals:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])

    rows = []
    for s in raw_signals:
        side = "BUY" if s.direction == 1 else "SELL"
        rows.append({
            "market_id": s.market_id,
            "signal_time": s.timestamp,
            "signal_ts": int(pd.Timestamp(s.timestamp).timestamp()),
            "outcome": outcome,
            "side": side,
            "size": position_size,
        })

    out = pd.DataFrame(rows)
    if max_markets:
        top = out["market_id"].value_counts().head(max_markets).index.tolist()
        out = out[out["market_id"].isin(top)]
    if start_date:
        start_ts = int(pd.Timestamp(start_date).timestamp())
        out = out[out["signal_ts"] >= start_ts]
    if end_date:
        end_ts = int(pd.Timestamp(end_date).timestamp()) + 86400
        out = out[out["signal_ts"] < end_ts]

    return out.sort_values("signal_ts").reset_index(drop=True)


def generate_breakout_signals_from_trades(
    trades_df: pd.DataFrame,
    lookback_hours: int = 24,
    breakout_threshold: float = 0.05,
    min_range_width: float = 0.02,
    max_range_width: float = 0.20,
    outcome: str = "YES",
    position_size: Optional[float] = None,
    max_markets: Optional[int] = 1000,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Generate breakout signals from trades.

    Detects consolidation ranges and signals when price breaks out.
    """
    from .strategies.breakout import BreakoutStrategy

    if trades_df.empty:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])

    price_history = trades_to_price_history(trades_df, outcome=outcome)
    if price_history.empty:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])

    if not pd.api.types.is_datetime64_any_dtype(price_history["timestamp"]):
        price_history["timestamp"] = pd.to_datetime(price_history["timestamp"], errors="coerce")
    prices_df = price_history.rename(columns={"timestamp": "datetime"})[["market_id", "datetime", "price"]]
    prices_df = prices_df.dropna(subset=["datetime", "price"])

    strategy = BreakoutStrategy(
        lookback_hours=lookback_hours,
        breakout_threshold=breakout_threshold,
        min_range_width=min_range_width,
        max_range_width=max_range_width,
    )
    raw_signals = strategy.generate_signals(prices_df)

    if not raw_signals:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])

    rows = []
    for s in raw_signals:
        side = "BUY" if s.direction == 1 else "SELL"
        rows.append({
            "market_id": s.market_id,
            "signal_time": s.timestamp,
            "signal_ts": int(pd.Timestamp(s.timestamp).timestamp()),
            "outcome": outcome,
            "side": side,
            "size": position_size,
        })

    out = pd.DataFrame(rows)
    if max_markets:
        top = out["market_id"].value_counts().head(max_markets).index.tolist()
        out = out[out["market_id"].isin(top)]
    if start_date:
        start_ts = int(pd.Timestamp(start_date).timestamp())
        out = out[out["signal_ts"] >= start_ts]
    if end_date:
        end_ts = int(pd.Timestamp(end_date).timestamp()) + 86400
        out = out[out["signal_ts"] < end_ts]

    return out.sort_values("signal_ts").reset_index(drop=True)


__all__ = [
    "generate_mean_reversion_signals_from_trades",
    "generate_smart_money_signals_from_trades",
    "generate_breakout_signals_from_trades",
]

"""
Generate momentum signals from trade data instead of price files.

Extracts prices from trades, creates price history, then calculates momentum.
"""

from pathlib import Path
from typing import Optional, List

import pandas as pd
import numpy as np

try:
    from src.data_quality.polymarket_tokens import ensure_yes_trades
except ImportError:  # src/ itself on sys.path
    from data_quality.polymarket_tokens import ensure_yes_trades

try:
    import polars as pl
except ImportError:
    pl = None

from .data_modules.parquet_store import TradeStore
from .data_modules.trade_price_store import TradeBasedPriceStore
try:
    from src.timeconv import to_epoch, to_epoch_seconds
except ImportError:  # src/ itself on sys.path
    from timeconv import to_epoch, to_epoch_seconds


def trades_to_price_history(
    trades_df: pd.DataFrame,
    outcome: str = "YES",
) -> pd.DataFrame:
    """
    Convert trades DataFrame to price history format.
    
    Aggregates trades by market/outcome/timestamp to create price series.
    Uses last trade price at each timestamp (or can use weighted average).
    
    Args:
        trades_df: DataFrame with columns: market_id, outcome, timestamp, price
                  (or usd_amount + token_amount)
        outcome: Filter to specific outcome (YES/NO)
        
    Returns:
        DataFrame with columns: market_id, outcome, timestamp, price
    """
    if trades_df.empty:
        return pd.DataFrame(columns=["market_id", "outcome", "timestamp", "price"])
    
    # pmfix: express every print in YES terms (no-op if TradeStore already did it; warns if impossible)
    df = ensure_yes_trades(trades_df, where="trades_to_price_history").copy()

    # Calculate price if needed
    if "price" not in df.columns:
        if "usd_amount" in df.columns and "token_amount" in df.columns:
            df["price"] = df["usd_amount"] / df["token_amount"]
            # Remove invalid prices
            df = df[(df["price"] > 0) & (df["price"] < 1) & (df["token_amount"] > 0)]
        else:
            raise ValueError("Trades must have 'price' or ('usd_amount' + 'token_amount')")
    
    # Add outcome column if missing
    if "outcome" not in df.columns:
        # Try to infer from maker_direction or taker_direction
        if "maker_direction" in df.columns:
            df["outcome"] = df["maker_direction"].str.upper()
            # Normalize: if it's buy/sell or other values, default to YES
            df.loc[~df["outcome"].isin(["YES", "NO"]), "outcome"] = "YES"
        elif "taker_direction" in df.columns:
            df["outcome"] = df["taker_direction"].str.upper()
            df.loc[~df["outcome"].isin(["YES", "NO"]), "outcome"] = "YES"
        else:
            # No direction info, default to YES
            df["outcome"] = "YES"
    
    # Filter by outcome if specified
    if "outcome" in df.columns:
        df = df[df["outcome"].str.upper() == outcome.upper()]
    
    # Ensure timestamp is datetime
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    
    # Drop rows missing required columns (outcome is now guaranteed to exist)
    df = df.dropna(subset=["market_id", "outcome", "timestamp", "price"])
    
    if df.empty:
        return pd.DataFrame(columns=["market_id", "outcome", "timestamp", "price"])
    
    # Group by market, outcome, timestamp and take last price
    # (or could use weighted average: sum(usd_amount) / sum(token_amount))
    price_history = (
        df.groupby(["market_id", "outcome", "timestamp"])["price"]
        .last()
        .reset_index()
    )
    
    return price_history.sort_values(["market_id", "outcome", "timestamp"]).reset_index(drop=True)


def generate_momentum_signals_from_trades(
    trades_df: pd.DataFrame,
    threshold: float = 0.05,
    eval_freq_hours: int = 24,
    outcome: str = "YES",
    position_size: Optional[float] = None,
    max_markets: Optional[int] = 1000,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Generate momentum signals from trade data.
    
    Extracts prices from trades, calculates 24h returns, and generates signals
    when returns exceed threshold.
    
    Args:
        trades_df: DataFrame with trades (must have market_id, outcome, timestamp, price)
        threshold: Minimum absolute return to generate signal (default: 0.05 = 5%)
        eval_freq_hours: Evaluation frequency in hours (default: 24 = daily)
        outcome: Outcome to trade (YES/NO)
        position_size: Optional position size for signals
        max_markets: Limit on number of markets (default: 1000)
        start_date: Optional start date filter (YYYY-MM-DD)
        end_date: Optional end date filter (YYYY-MM-DD)
        
    Returns:
        DataFrame with columns: market_id, signal_time, signal_ts, outcome, side, size
    """
    if trades_df.empty:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])
    
    # Filter to top markets by volume if max_markets is specified
    if max_markets is not None and max_markets > 0:
        # Calculate volume per market
        if "usd_amount" in trades_df.columns:
            market_volumes = trades_df.groupby("market_id")["usd_amount"].sum().sort_values(ascending=False)
        elif "price" in trades_df.columns and "token_amount" in trades_df.columns:
            # Calculate volume from price * token_amount
            trades_df_copy = trades_df.copy()
            trades_df_copy["volume"] = trades_df_copy["price"] * trades_df_copy["token_amount"]
            market_volumes = trades_df_copy.groupby("market_id")["volume"].sum().sort_values(ascending=False)
        else:
            # Fallback: use trade count
            market_volumes = trades_df.groupby("market_id").size().sort_values(ascending=False)
        
        # Get top N markets
        top_markets = market_volumes.head(max_markets).index.tolist()
        
        original_count = len(trades_df)
        original_markets = trades_df["market_id"].nunique()
        
        # Filter trades to only top markets
        trades_df = trades_df[trades_df["market_id"].isin(top_markets)].copy()
        
        if trades_df.empty:
            return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])
        
        # Log filtering result
        print(f"  Filtered to top {len(top_markets)} markets by volume")
        print(f"    Trades: {original_count:,} -> {len(trades_df):,}")
        print(f"    Markets: {original_markets:,} -> {trades_df['market_id'].nunique():,}")
    
    # Convert trades to price history
    price_history = trades_to_price_history(trades_df, outcome=outcome)
    
    if price_history.empty:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])
    
    # Use Polars if available for faster processing
    # Note: max_markets already applied above, pass None to internal functions
    if pl is not None:
        return _generate_signals_polars(
            price_history,
            threshold,
            eval_freq_hours,
            outcome,
            position_size,
            None,  # Already filtered markets above
            start_date,
            end_date,
        )
    else:
        return _generate_signals_pandas(
            price_history,
            threshold,
            eval_freq_hours,
            outcome,
            position_size,
            None,  # Already filtered markets above
            start_date,
            end_date,
        )


def _generate_signals_polars(
    price_history: pd.DataFrame,
    threshold: float,
    eval_freq_hours: int,
    outcome: str,
    position_size: Optional[float],
    max_markets: Optional[int],
    start_date: Optional[str],
    end_date: Optional[str],
) -> pd.DataFrame:
    """Generate signals using Polars (faster)."""
    df = pl.from_pandas(price_history)
    
    # Convert timestamp to unix seconds
    if df["timestamp"].dtype == pl.Datetime:
        df = df.with_columns(pl.col("timestamp").dt.epoch("s").alias("_ts_s"))
    else:
        df = df.with_columns(
            pl.to_datetime(pl.col("timestamp"), strict=False)
            .dt.epoch("s")
            .alias("_ts_s")
        )
    
    df = df.filter(pl.col("_ts_s").is_not_null() & pl.col("price").is_not_null())
    
    if df.is_empty():
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])
    
    eval_interval_seconds = eval_freq_hours * 3600
    
    # Truncate to evaluation interval
    df = df.with_columns(
        (pl.col("_ts_s") // eval_interval_seconds * eval_interval_seconds).alias("eval_ts")
    )
    
    # Last price per (market_id, eval_ts)
    daily = (
        df.group_by(["market_id", "eval_ts"])
        .agg(pl.col("price").last().alias("price"), pl.col("_ts_s").max().alias("_ts_s"))
        .sort(["market_id", "eval_ts"])
    )
    
    # Previous period price (24h ago)
    daily = daily.with_columns(
        pl.col("eval_ts").shift(1).over("market_id").alias("eval_ts_prev"),
        pl.col("price").shift(1).over("market_id").alias("price_24h_ago"),
    )
    
    daily = daily.filter(pl.col("price_24h_ago").is_not_null())
    
    # Calculate return_24h
    daily = daily.with_columns(
        ((pl.col("price") - pl.col("price_24h_ago")) / pl.col("price_24h_ago")).alias("return_24h")
    )
    
    # Filter by threshold
    daily = daily.filter(
        (pl.col("return_24h") >= threshold) | (pl.col("return_24h") <= -threshold)
    )
    
    # Date range filter
    if start_date:
        start_ts = int(pd.Timestamp(start_date).timestamp())
        daily = daily.filter(pl.col("eval_ts") >= start_ts)
    if end_date:
        end_ts = int(pd.Timestamp(end_date).timestamp()) + 86400
        daily = daily.filter(pl.col("eval_ts") < end_ts)
    
    if daily.is_empty():
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])
    
    # Generate signals
    signals = daily.with_columns(
        pl.when(pl.col("return_24h") > 0)
        .then(pl.lit("BUY"))
        .otherwise(pl.lit("SELL"))
        .alias("side"),
        pl.lit(outcome).alias("outcome"),
    ).select(
        pl.col("market_id"),
        pl.col("eval_ts").alias("signal_ts"),
        pl.col("outcome"),
        pl.col("side"),
    )
    
    if position_size is not None:
        signals = signals.with_columns(pl.lit(position_size).alias("size"))
    
    # Limit markets if requested
    if max_markets is not None:
        market_order = signals.select("market_id").unique().head(max_markets)
        signals = signals.join(market_order, on="market_id", how="inner")
    
    signals = signals.sort("signal_ts")
    
    out = signals.to_pandas()
    out["signal_time"] = pd.to_datetime(out["signal_ts"], unit="s", utc=True)
    
    if "size" not in out.columns and position_size is not None:
        out["size"] = position_size
    
    return out


def _generate_signals_pandas(
    price_history: pd.DataFrame,
    threshold: float,
    eval_freq_hours: int,
    outcome: str,
    position_size: Optional[float],
    max_markets: Optional[int],
    start_date: Optional[str],
    end_date: Optional[str],
) -> pd.DataFrame:
    """Generate signals using pandas (fallback)."""
    df = price_history.copy()
    
    # Ensure timestamp is datetime
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    
    df = df.dropna(subset=["timestamp", "price"])
    
    if df.empty:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])
    
    # Convert to unix seconds
    df["_ts_s"] = to_epoch_seconds(df["timestamp"])
    
    eval_interval_seconds = eval_freq_hours * 3600
    
    # Truncate to evaluation interval
    df["eval_ts"] = (df["_ts_s"] // eval_interval_seconds) * eval_interval_seconds
    
    # Last price per (market_id, eval_ts)
    daily = (
        df.groupby(["market_id", "eval_ts"])
        .agg({"price": "last", "_ts_s": "max"})
        .reset_index()
        .sort_values(["market_id", "eval_ts"])
    )
    
    # Calculate previous period price
    daily["price_24h_ago"] = daily.groupby("market_id")["price"].shift(1)
    daily = daily.dropna(subset=["price_24h_ago"])
    
    # Calculate return_24h
    daily["return_24h"] = (daily["price"] - daily["price_24h_ago"]) / daily["price_24h_ago"]
    
    # Filter by threshold
    daily = daily[
        (daily["return_24h"] >= threshold) | (daily["return_24h"] <= -threshold)
    ]
    
    # Date range filter
    if start_date:
        start_ts = int(pd.Timestamp(start_date).timestamp())
        daily = daily[daily["eval_ts"] >= start_ts]
    if end_date:
        end_ts = int(pd.Timestamp(end_date).timestamp()) + 86400
        daily = daily[daily["eval_ts"] < end_ts]
    
    if daily.empty:
        return pd.DataFrame(columns=["market_id", "signal_time", "signal_ts", "outcome", "side"])
    
    # Generate signals
    daily["side"] = daily["return_24h"].apply(lambda x: "BUY" if x > 0 else "SELL")
    daily["outcome"] = outcome
    
    signals = daily[["market_id", "eval_ts", "outcome", "side"]].copy()
    signals = signals.rename(columns={"eval_ts": "signal_ts"})
    
    if position_size is not None:
        signals["size"] = position_size
    
    # Limit markets if requested
    if max_markets is not None:
        unique_markets = signals["market_id"].unique()[:max_markets]
        signals = signals[signals["market_id"].isin(unique_markets)]
    
    signals = signals.sort_values("signal_ts").reset_index(drop=True)
    signals["signal_time"] = pd.to_datetime(signals["signal_ts"], unit="s", utc=True)
    
    return signals


__all__ = [
    "trades_to_price_history",
    "generate_momentum_signals_from_trades",
]

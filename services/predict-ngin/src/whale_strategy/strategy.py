"""
Whale identification methods for prediction markets.

Supports multiple methods:
- volume_top10: Top 10 traders by total volume
- volume_pct95: Traders above 95th percentile volume
- large_trades: Traders with average trade size > $1000
- win_rate_60pct: Traders with 60%+ win rate (best performer)
- combo_vol_win: High volume AND good win rate
"""

import pandas as pd
from typing import Set, Dict, Any


WHALE_METHODS = {
    "volume_top10": "Top 10 by total volume",
    "volume_pct95": "95th percentile by volume",
    "large_trades": "Average trade size > $1000",
    "win_rate_60pct": "Win rate >= 60%",
    "combo_vol_win": "90th pct volume AND 55%+ win rate",
    "large_accurate": "Avg trade > $500 AND 55%+ win rate",
    "profit_proxy": "Top 50 by correct prediction count",
}


def _calculate_user_stats(
    df: pd.DataFrame,
    resolution_data: Dict[str, Dict[str, Any]],
) -> pd.DataFrame:
    """Calculate user statistics from bet data."""
    # Filter to resolved markets
    resolved_df = df[df["contractId"].isin(resolution_data.keys())].copy()

    if len(resolved_df) == 0:
        return pd.DataFrame()

    # Add resolution
    resolved_df["resolution"] = resolved_df["contractId"].map(
        lambda x: resolution_data.get(x, {}).get("resolution", None)
    )
    resolved_df = resolved_df.dropna(subset=["resolution"])

    # Calculate if bet was correct
    def bet_correct(row):
        if row["outcome"] == "YES":
            return row["resolution"] == 1.0
        if row["outcome"] == "NO":
            return row["resolution"] == 0.0
        return False

    resolved_df["correct"] = resolved_df.apply(bet_correct, axis=1)

    # Aggregate by user
    user_stats = resolved_df.groupby("userId").agg({
        "amount_abs": ["sum", "mean", "count"],
        "correct": ["sum", "mean"],
    }).reset_index()
    user_stats.columns = [
        "userId", "total_volume", "avg_trade", "trade_count", "correct_count", "win_rate"
    ]

    return user_stats


def identify_whales(
    train_df: pd.DataFrame,
    resolution_data: Dict[str, Dict[str, Any]],
    method: str = "win_rate_60pct",
    min_trades: int = 20,
) -> Set[str]:
    """
    Identify whale traders using the specified method.

    Args:
        train_df: Training period bets DataFrame
        resolution_data: Market resolution mapping
        method: Whale identification method
        min_trades: Minimum trades required

    Returns:
        Set of whale user IDs
    """
    # Calculate volume-based stats (no resolution needed)
    volume_stats = train_df.groupby("userId").agg({
        "amount_abs": ["sum", "mean", "count"]
    }).reset_index()
    volume_stats.columns = ["userId", "total_volume", "avg_trade", "trade_count"]

    # For win-rate methods, need resolution data
    if method in ["win_rate_60pct", "combo_vol_win", "large_accurate", "profit_proxy"]:
        user_stats = _calculate_user_stats(train_df, resolution_data)
        if len(user_stats) == 0:
            return set()
        user_stats = user_stats[user_stats["trade_count"] >= min_trades]
    else:
        user_stats = volume_stats

    if len(user_stats) == 0:
        return set()

    # Apply method
    if method == "volume_top10":
        return set(user_stats.nlargest(10, "total_volume")["userId"])

    if method == "volume_pct95":
        threshold = user_stats["total_volume"].quantile(0.95)
        return set(user_stats[user_stats["total_volume"] >= threshold]["userId"])

    if method == "large_trades":
        return set(user_stats[user_stats["avg_trade"] >= 1000]["userId"])

    if method == "win_rate_60pct":
        return set(user_stats[user_stats["win_rate"] >= 0.60]["userId"])

    if method == "combo_vol_win":
        vol_threshold = user_stats["total_volume"].quantile(0.90)
        return set(user_stats[
            (user_stats["total_volume"] >= vol_threshold) &
            (user_stats["win_rate"] >= 0.55)
        ]["userId"])

    if method == "large_accurate":
        return set(user_stats[
            (user_stats["avg_trade"] >= 500) &
            (user_stats["win_rate"] >= 0.55)
        ]["userId"])

    if method == "profit_proxy":
        return set(user_stats.nlargest(50, "correct_count")["userId"])

    raise ValueError(f"Unknown method: {method}. Available: {list(WHALE_METHODS.keys())}")


def identify_whales_rolling(
    df: pd.DataFrame,
    resolution_data: Dict[str, Dict[str, Any]],
    method: str = "volume_pct95",
    lookback_months: int = 3,
) -> Dict[str, Set[str]]:
    """
    Identify whales on a rolling monthly basis.

    Args:
        df: Full bets DataFrame
        resolution_data: Market resolution mapping
        method: Whale identification method
        lookback_months: Months of history to use

    Returns:
        Dictionary mapping month to whale set
    """
    months = sorted(df["month"].unique())
    rolling_whales: Dict[str, Set[str]] = {}

    for i, month in enumerate(months):
        if i < lookback_months:
            continue

        # Training data: previous N months
        train_months = months[i - lookback_months:i]
        train_df = df[df["month"].isin(train_months)]

        if len(train_df) < 1000:
            continue

        whales = identify_whales(train_df, resolution_data, method)
        rolling_whales[str(month)] = whales

    return rolling_whales


__all__ = [
    "WHALE_METHODS",
    "identify_whales",
    "identify_whales_rolling",
]

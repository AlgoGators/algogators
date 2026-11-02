"""
Whale correlation analysis for prediction markets.

Analyzes whether whale traders trade independently or herd together.
Important for:
1. Position sizing - correlated whales = concentrated risk
2. Signal diversification - independent whales = diverse signals
3. Risk management - herding increases drawdown risk

Usage:
    python -m src.whale_strategy.correlation
"""

import sqlite3
import pandas as pd
try:
    from src.data_quality.polymarket_tokens import warn_legacy_reader as _pmfix_warn_legacy
except ImportError:  # src/ itself on sys.path
    from data_quality.polymarket_tokens import warn_legacy_reader as _pmfix_warn_legacy
import numpy as np
from typing import Set, Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from collections import defaultdict

DEFAULT_DB_PATH = "data/prediction_markets.db"
DEFAULT_PARQUET_DIR = "data/polymarket/trades"


def load_whale_trades(
    db_path: str,
    whale_addresses: Set[str],
    min_usd: float = 100,
    use_parquet: Optional[bool] = None,
) -> pd.DataFrame:
    """
    Load trades for whale addresses.

    Auto-detects parquet files and uses them if available.

    Args:
        db_path: Database path (fallback)
        whale_addresses: Set of whale addresses
        min_usd: Minimum trade size
        use_parquet: Force parquet (True), force SQLite (False), or auto-detect (None)

    Returns:
        DataFrame with whale trades
    """
    _pmfix_warn_legacy("load_whale_trades (src/whale_strategy/correlation.py)", "Selects trade prices without nonusdc_side/outcomeIndex, so YES and NO prints are mixed.")
    if not whale_addresses:
        return pd.DataFrame()

    # Auto-detect parquet
    from pathlib import Path
    parquet_dir = Path(DEFAULT_PARQUET_DIR)
    if use_parquet is None:
        use_parquet = parquet_dir.exists() and any(parquet_dir.glob("trades_*.parquet"))

    if use_parquet:
        return _load_whale_trades_parquet(parquet_dir, whale_addresses, min_usd)

    return _load_whale_trades_sqlite(db_path, whale_addresses, min_usd)


def _load_whale_trades_parquet(
    parquet_dir,
    whale_addresses: Set[str],
    min_usd: float,
) -> pd.DataFrame:
    """Load whale trades from parquet files."""
    from trading.data_modules.parquet_store import TradeStore

    store = TradeStore(str(parquet_dir))
    df = store.load_trades(min_usd=min_usd)

    if df.empty:
        return df

    # Filter to whale addresses (maker or taker)
    mask = pd.Series(False, index=df.index)
    if "maker" in df.columns:
        mask |= df["maker"].isin(whale_addresses)
    if "taker" in df.columns:
        mask |= df["taker"].isin(whale_addresses)
    df = df[mask]

    df["datetime"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["datetime"])

    return df


def _load_whale_trades_sqlite(
    db_path: str,
    whale_addresses: Set[str],
    min_usd: float,
) -> pd.DataFrame:
    """Load whale trades from SQLite database."""
    conn = sqlite3.connect(db_path)

    # Build address list for SQL
    addr_list = ",".join(f"'{a}'" for a in whale_addresses)

    query = f"""
        SELECT
            timestamp,
            market_id,
            maker,
            taker,
            maker_direction,
            taker_direction,
            price,
            usd_amount
        FROM polymarket_trades
        WHERE usd_amount >= {min_usd}
          AND (maker IN ({addr_list}) OR taker IN ({addr_list}))
        ORDER BY timestamp
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    df["datetime"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["datetime"])

    return df


def calculate_position_vectors(
    trades_df: pd.DataFrame,
    whale_addresses: Set[str],
    time_bucket: str = "1D",  # Daily positions
) -> pd.DataFrame:
    """
    Calculate position vectors for each whale over time.

    Creates a matrix of whale positions per market per time period.

    Args:
        trades_df: DataFrame of whale trades
        whale_addresses: Set of whale addresses
        time_bucket: Time bucketing (1H, 1D, 1W)

    Returns:
        DataFrame with position vectors
    """
    if trades_df.empty:
        return pd.DataFrame()

    # Identify whale role and direction in each trade
    records = []
    for _, row in trades_df.iterrows():
        for role in ["maker", "taker"]:
            addr = row[role]
            if addr in whale_addresses:
                direction = row[f"{role}_direction"]
                # Convert to position: BUY = +1, SELL = -1
                position = 1 if direction and direction.lower() == "buy" else -1
                records.append({
                    "datetime": row["datetime"],
                    "whale": addr,
                    "market_id": row["market_id"],
                    "position": position,
                    "usd_amount": row["usd_amount"],
                })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["period"] = df["datetime"].dt.to_period(time_bucket[1])

    # Aggregate: net position per whale per market per period
    positions = df.groupby(["period", "whale", "market_id"]).agg({
        "position": "sum",  # Net direction
        "usd_amount": "sum",
    }).reset_index()

    # Pivot to get whale x market matrix per period
    pivoted = positions.pivot_table(
        index=["period", "market_id"],
        columns="whale",
        values="position",
        fill_value=0,
    )

    return pivoted


def calculate_whale_correlations(
    position_matrix: pd.DataFrame,
    min_overlapping_markets: int = 10,
) -> pd.DataFrame:
    """
    Calculate pairwise correlations between whale traders.

    Args:
        position_matrix: Whale x market position matrix
        min_overlapping_markets: Minimum markets traded by both whales

    Returns:
        DataFrame with correlation matrix
    """
    if position_matrix.empty:
        return pd.DataFrame()

    whales = position_matrix.columns.tolist()

    # Calculate correlation matrix
    correlations = []
    for i, whale1 in enumerate(whales):
        for j, whale2 in enumerate(whales):
            if i >= j:
                continue

            # Get positions for both whales
            pos1 = position_matrix[whale1]
            pos2 = position_matrix[whale2]

            # Only consider markets where both traded
            mask = (pos1 != 0) & (pos2 != 0)
            overlapping = mask.sum()

            if overlapping >= min_overlapping_markets:
                corr = np.corrcoef(pos1[mask], pos2[mask])[0, 1]
                correlations.append({
                    "whale1": whale1,
                    "whale2": whale2,
                    "correlation": corr,
                    "overlapping_markets": overlapping,
                })

    return pd.DataFrame(correlations)


def calculate_herding_score(
    trades_df: pd.DataFrame,
    whale_addresses: Set[str],
    time_window_minutes: int = 60,
) -> Dict[str, Any]:
    """
    Calculate herding score based on temporal clustering.

    Measures how often whales trade the same direction in the same
    market within a short time window.

    Args:
        trades_df: DataFrame of trades
        whale_addresses: Set of whale addresses
        time_window_minutes: Window for "same time" trades

    Returns:
        Dict with herding metrics
    """
    if trades_df.empty:
        return {"herding_score": 0, "sample_size": 0}

    # Get whale trades with normalized direction
    whale_trades = []
    for _, row in trades_df.iterrows():
        for role in ["maker", "taker"]:
            addr = row[role]
            if addr in whale_addresses:
                direction = row[f"{role}_direction"]
                whale_trades.append({
                    "datetime": row["datetime"],
                    "whale": addr,
                    "market_id": row["market_id"],
                    "direction": 1 if direction and direction.lower() == "buy" else -1,
                })

    if not whale_trades:
        return {"herding_score": 0, "sample_size": 0}

    wt_df = pd.DataFrame(whale_trades).sort_values("datetime")

    # For each trade, count same-direction trades in same market within window
    window = timedelta(minutes=time_window_minutes)
    same_direction = 0
    opposite_direction = 0
    total_pairs = 0

    # Group by market for efficiency
    for market_id, group in wt_df.groupby("market_id"):
        group = group.sort_values("datetime")
        trades = group.to_dict("records")

        for i, trade1 in enumerate(trades):
            for j, trade2 in enumerate(trades):
                if i >= j:
                    continue
                if trade1["whale"] == trade2["whale"]:
                    continue

                time_diff = abs((trade1["datetime"] - trade2["datetime"]).total_seconds())
                if time_diff <= time_window_minutes * 60:
                    total_pairs += 1
                    if trade1["direction"] == trade2["direction"]:
                        same_direction += 1
                    else:
                        opposite_direction += 1

    if total_pairs == 0:
        return {"herding_score": 0, "sample_size": 0}

    # Herding score: (same - opposite) / total
    # Range: -1 (perfect anti-correlation) to +1 (perfect herding)
    herding_score = (same_direction - opposite_direction) / total_pairs

    return {
        "herding_score": herding_score,
        "same_direction_pct": same_direction / total_pairs,
        "opposite_direction_pct": opposite_direction / total_pairs,
        "total_pairs": total_pairs,
        "sample_size": len(whale_trades),
    }


def analyze_signal_concentration(
    trades_df: pd.DataFrame,
    whale_addresses: Set[str],
) -> Dict[str, Any]:
    """
    Analyze signal concentration across whales.

    Measures how diversified signals are across whales.

    Args:
        trades_df: DataFrame of trades
        whale_addresses: Set of whale addresses

    Returns:
        Dict with concentration metrics
    """
    if trades_df.empty:
        return {}

    # Count trades per whale
    whale_trades = defaultdict(int)
    whale_volume = defaultdict(float)

    for _, row in trades_df.iterrows():
        for role in ["maker", "taker"]:
            addr = row[role]
            if addr in whale_addresses:
                whale_trades[addr] += 1
                whale_volume[addr] += row["usd_amount"]

    if not whale_trades:
        return {}

    # Calculate Herfindahl-Hirschman Index (HHI) for concentration
    total_trades = sum(whale_trades.values())
    total_volume = sum(whale_volume.values())

    trade_shares = [n / total_trades for n in whale_trades.values()]
    volume_shares = [v / total_volume for v in whale_volume.values()]

    hhi_trades = sum(s ** 2 for s in trade_shares)
    hhi_volume = sum(s ** 2 for s in volume_shares)

    # Gini coefficient for inequality
    def gini(values):
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        cumulative = np.cumsum(sorted_vals)
        return (n + 1 - 2 * sum(cumulative) / cumulative[-1]) / n

    gini_trades = gini(list(whale_trades.values()))
    gini_volume = gini(list(whale_volume.values()))

    return {
        "num_whales": len(whale_trades),
        "total_trades": total_trades,
        "total_volume": total_volume,
        "hhi_trades": hhi_trades,  # 0 = diverse, 1 = concentrated
        "hhi_volume": hhi_volume,
        "gini_trades": gini_trades,  # 0 = equal, 1 = unequal
        "gini_volume": gini_volume,
        "top_whale_trade_share": max(trade_shares),
        "top_whale_volume_share": max(volume_shares),
    }


def run_correlation_analysis(
    db_path: str = DEFAULT_DB_PATH,
    whale_method: str = "win_rate_60pct",
    min_trades: int = 20,
    min_volume: float = 1000,
) -> Dict[str, Any]:
    """
    Full whale correlation analysis.

    Args:
        db_path: Database path
        whale_method: Method for identifying whales
        min_trades: Minimum trades for whale qualification
        min_volume: Minimum volume for whale qualification

    Returns:
        Dict with all analysis results
    """
    from src.whale_strategy.polymarket_whales import (
        load_polymarket_trades,
        identify_polymarket_whales,
        build_price_snapshot,
    )

    print("=" * 60)
    print("WHALE CORRELATION ANALYSIS")
    print("=" * 60)

    # Load trades and identify whales
    print("\n[1] Loading trades...")
    all_trades = load_polymarket_trades(db_path)
    print(f"  Loaded {len(all_trades):,} trades")

    print(f"\n[2] Identifying whales ({whale_method})...")
    price_snapshot = build_price_snapshot(all_trades)
    whales = identify_polymarket_whales(
        all_trades,
        method=whale_method,
        min_trades=min_trades,
        min_volume=min_volume,
        price_snapshot=price_snapshot,
    )
    print(f"  Found {len(whales)} whales")

    if len(whales) < 2:
        print("  Not enough whales for correlation analysis")
        return {"error": "Not enough whales"}

    # Load whale trades
    print("\n[3] Loading whale trades...")
    whale_trades = load_whale_trades(db_path, whales)
    print(f"  Loaded {len(whale_trades):,} whale trades")

    if whale_trades.empty:
        print("  No whale trades found")
        return {"error": "No whale trades"}

    # Calculate position matrix
    print("\n[4] Calculating position vectors...")
    positions = calculate_position_vectors(whale_trades, whales)
    print(f"  Position matrix: {positions.shape}")

    # Calculate correlations
    print("\n[5] Calculating pairwise correlations...")
    correlations = calculate_whale_correlations(positions)

    if not correlations.empty:
        avg_corr = correlations["correlation"].mean()
        max_corr = correlations["correlation"].max()
        min_corr = correlations["correlation"].min()
        print(f"  Avg correlation: {avg_corr:.3f}")
        print(f"  Max correlation: {max_corr:.3f}")
        print(f"  Min correlation: {min_corr:.3f}")
    else:
        avg_corr = 0
        print("  Not enough overlapping markets for correlation")

    # Calculate herding score
    print("\n[6] Calculating herding score...")
    herding = calculate_herding_score(whale_trades, whales)
    print(f"  Herding score: {herding.get('herding_score', 0):.3f}")
    print(f"  Same direction: {herding.get('same_direction_pct', 0)*100:.1f}%")

    # Signal concentration
    print("\n[7] Analyzing signal concentration...")
    concentration = analyze_signal_concentration(whale_trades, whales)
    print(f"  HHI (trades): {concentration.get('hhi_trades', 0):.3f}")
    print(f"  Gini (volume): {concentration.get('gini_volume', 0):.3f}")
    print(f"  Top whale share: {concentration.get('top_whale_volume_share', 0)*100:.1f}%")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    independence_score = 1 - abs(herding.get("herding_score", 0))
    diversification_score = 1 - concentration.get("hhi_volume", 0)

    print(f"\n  Independence score: {independence_score:.2f} (1 = fully independent)")
    print(f"  Diversification score: {diversification_score:.2f} (1 = fully diversified)")

    if independence_score > 0.7 and diversification_score > 0.7:
        print("\n  Recommendation: GOOD - Whales trade independently")
        print("  Position sizing: Can follow multiple whales without concentration risk")
    elif independence_score < 0.3:
        print("\n  Recommendation: CAUTION - Strong herding behavior")
        print("  Position sizing: Following multiple whales = concentrated bet")
    else:
        print("\n  Recommendation: MODERATE - Some correlation observed")
        print("  Position sizing: Consider diversification across whale types")

    return {
        "num_whales": len(whales),
        "num_trades": len(whale_trades),
        "correlations": correlations.to_dict() if not correlations.empty else {},
        "avg_correlation": avg_corr,
        "herding": herding,
        "concentration": concentration,
        "independence_score": independence_score,
        "diversification_score": diversification_score,
    }


if __name__ == "__main__":
    results = run_correlation_analysis()

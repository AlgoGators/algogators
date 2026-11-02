"""
Polymarket whale identification and signal generation.

Identifies profitable traders from Polymarket trade history
and generates signals when they trade.
"""

import os
import sqlite3
import pandas as pd
try:
    from src.data_quality.polymarket_tokens import warn_legacy_reader as _pmfix_warn_legacy
except ImportError:  # src/ itself on sys.path
    from data_quality.polymarket_tokens import warn_legacy_reader as _pmfix_warn_legacy
import numpy as np
from typing import Set, Dict, Any, Optional
from pathlib import Path
try:
    from src.timeconv import to_epoch, to_epoch_seconds
except ImportError:  # src/ itself on sys.path
    from timeconv import to_epoch, to_epoch_seconds
try:
    import polars as pl
except ImportError:  # pragma: no cover - optional dependency
    pl = None

def _require_polars() -> bool:
    return os.getenv("PREDICT_NGIN_FORCE_POLARS", "").strip().lower() in {
        "1", "true", "yes", "on"
    }

DEFAULT_DB_PATH = "data/prediction_markets.db"
DEFAULT_PARQUET_DIR = "data/polymarket/trades"

POLY_WHALE_METHODS = {
    "volume_top10": "Top 10 by total volume",
    "volume_pct95": "Top 5% by total volume",
    "large_trades": "Average trade size >= $1,000",
    "win_rate_60pct": "Win rate >= 60% vs current price",
    "combo_vol_win": "Top volume + win rate filter",
    "profit_proxy": "Highest count of winning trades vs current price",
    "mid_price_accuracy": "Accuracy on mid-range prices using resolution data",
    "top_returns": "Top % by N-month mark-to-market return",
    "return_3m_top10pct": "Deprecated: use top_returns",
}


def load_aligned_trades(
    db_path: str = DEFAULT_DB_PATH,
    min_usd: float = 10.0,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    """
    Load aligned Polymarket trades that can be joined to prices.

    These trades have been joined to markets via slug, giving them
    market_ids that match the prices table.

    Args:
        db_path: Path to SQLite database
        min_usd: Minimum USD trade size
        limit: Maximum number of trades to load

    Returns:
        DataFrame with trades in backtest-compatible format
    """
    conn = sqlite3.connect(db_path)

    limit_sql = f" LIMIT {limit}" if limit else ""

    df = pd.read_sql_query(f'''
        SELECT
            original_id as trade_id,
            timestamp,
            market_id,
            proxy_wallet as maker,
            proxy_wallet as taker,
            side as maker_direction,
            CASE WHEN side = "BUY" THEN "SELL" ELSE "BUY" END as taker_direction,
            price,
            size * price as usd_amount,
            size as token_amount,
            transaction_hash,
            slug,
            question
        FROM polymarket_trades_aligned
        WHERE size * price >= {min_usd}
        ORDER BY timestamp
        {limit_sql}
    ''', conn)

    conn.close()

    # Parse timestamps (Unix timestamp stored as text in this table)
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce")
    df = df.dropna(subset=["datetime"])
    df["date"] = df["datetime"].dt.date
    df["month"] = df["datetime"].dt.to_period("M")
    df["timestamp_unix"] = df["timestamp"].astype(int)

    # Normalize directions to lowercase
    df["maker_direction"] = df["maker_direction"].str.lower()
    df["taker_direction"] = df["taker_direction"].str.lower()

    return df


def load_polymarket_trades(
    db_path: str = DEFAULT_DB_PATH,
    min_usd: float = 10.0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = None,
    use_parquet: Optional[bool] = None,
    columns: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Load Polymarket trades from parquet (preferred) or SQLite database.

    Auto-detects parquet files at data/parquet/trades/ and uses them
    if available, falling back to SQLite.

    Args:
        db_path: Path to SQLite database (fallback)
        min_usd: Minimum USD trade size
        start_date: Filter trades after this date (YYYY-MM-DD or ISO format)
        end_date: Filter trades before this date (YYYY-MM-DD or ISO format)
        limit: Maximum number of trades to load
        use_parquet: Force parquet (True), force SQLite (False), or auto-detect (None)

    Returns:
        DataFrame with trades
    """
    # Auto-detect parquet
    parquet_dir = Path(DEFAULT_PARQUET_DIR)
    if use_parquet is None:
        use_parquet = parquet_dir.exists() and any(parquet_dir.glob("trades_*.parquet"))

    if use_parquet:
        return _load_trades_from_parquet(
            parquet_dir, min_usd, start_date, end_date, limit, columns
        )

    return _load_trades_from_sqlite(db_path, min_usd, start_date, end_date, limit, columns)


def build_price_snapshot(
    trades_df: pd.DataFrame,
    as_of: Optional[pd.Timestamp] = None,
) -> Dict[str, float]:
    """
    Build a market_id -> current price snapshot using the latest trade price.

    Args:
        trades_df: DataFrame of trades with datetime and price columns
        as_of: Optional cutoff time (only include trades <= as_of)

    Returns:
        Dict mapping market_id (str) -> last known price
    """
    if trades_df is None:
        return {}

    if pl is not None and isinstance(trades_df, pl.DataFrame):
        df = trades_df
        if "datetime" not in df.columns:
            return {}
        df = df.filter(
            pl.col("datetime").is_not_null() & pl.col("price").is_not_null()
        )
        if as_of is not None:
            df = df.filter(pl.col("datetime") <= as_of)
        if df.height == 0:
            return {}
        df = df.with_columns(
            pl.col("market_id")
            .cast(pl.Utf8, strict=False)
            .str.replace(r"\.0$", "")
            .alias("market_id_str")
        )
        latest = (
            df.sort("datetime")
            .group_by("market_id_str")
            .agg(pl.col("price").last().alias("price"))
        )
        return dict(zip(latest["market_id_str"].to_list(), latest["price"].to_list()))

    df = trades_df.copy()
    if "datetime" not in df.columns:
        return {}
    df = df.dropna(subset=["datetime", "price"])
    if as_of is not None:
        df = df[df["datetime"] <= as_of]
    if df.empty:
        return {}
    df["market_id_str"] = df["market_id"].astype(str).str.replace(".0", "", regex=False)
    latest = df.sort_values("datetime").groupby("market_id_str")["price"].last()
    return latest.to_dict()


def _load_trades_from_parquet(
    parquet_dir: Path,
    min_usd: float,
    start_date: Optional[str],
    end_date: Optional[str],
    limit: Optional[int],
    columns: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Load trades from partitioned parquet files."""
    from trading.data_modules.parquet_store import TradeStore

    store = TradeStore(str(parquet_dir))
    df = store.load_trades(
        min_usd=min_usd,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        columns=columns,
    )

    if df.empty:
        return df

    # Normalize market_id to string (CSV stores as numeric float like 240380.0)
    if "market_id" in df.columns:
        df["market_id"] = df["market_id"].apply(
            lambda x: str(int(x)) if pd.notna(x) and isinstance(x, float) and x == int(x) else str(x)
        )

    # Parse timestamps
    df["datetime"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["datetime"])
    df["date"] = df["datetime"].dt.date
    df["month"] = df["datetime"].dt.to_period("M")
    df["timestamp_unix"] = to_epoch_seconds(df["datetime"])

    # Normalize directions to lowercase
    if "maker_direction" in df.columns:
        df["maker_direction"] = df["maker_direction"].str.lower()
    if "taker_direction" in df.columns:
        df["taker_direction"] = df["taker_direction"].str.lower()

    return df


def _load_trades_from_sqlite(
    db_path: str,
    min_usd: float,
    start_date: Optional[str],
    end_date: Optional[str],
    limit: Optional[int],
    columns: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Load trades from SQLite database (fallback)."""
    _pmfix_warn_legacy("_load_trades_from_sqlite (src/whale_strategy/polymarket_whales.py)", "Selects trade prices without nonusdc_side/outcomeIndex, so YES and NO prints are mixed.")
    conn = sqlite3.connect(db_path)

    where_clauses = [f"usd_amount >= {min_usd}"]
    if start_date:
        where_clauses.append(f"timestamp >= '{start_date}'")
    if end_date:
        where_clauses.append(f"timestamp <= '{end_date}'")

    where_sql = " AND ".join(where_clauses)
    limit_sql = f" LIMIT {limit}" if limit else ""

    base_columns = [
        "trade_id",
        "timestamp",
        "market_id",
        "maker",
        "taker",
        "maker_direction",
        "taker_direction",
        "price",
        "usd_amount",
        "token_amount",
        "transaction_hash",
        "market_cum_liquidity_usd",
    ]
    if columns:
        # Restrict to requested columns, but only those that exist in the table.
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(polymarket_trades)")
            available = {row[1] for row in cur.fetchall()}
        except Exception:
            available = set(base_columns)

        select_cols = [c for c in base_columns if c in columns and c in available]
        if not select_cols:
            select_cols = [c for c in base_columns if c in available]
    else:
        select_cols = base_columns

    df = pd.read_sql_query(
        f"""
        SELECT
            {", ".join(select_cols)}
        FROM polymarket_trades
        WHERE {where_sql}
        ORDER BY timestamp
        {limit_sql}
        """,
        conn,
    )

    conn.close()

    # Parse timestamps
    df["datetime"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["datetime"])
    df["date"] = df["datetime"].dt.date
    df["month"] = df["datetime"].dt.to_period("M")
    df["timestamp_unix"] = to_epoch_seconds(df["datetime"])

    # Normalize directions to lowercase
    df["maker_direction"] = df["maker_direction"].str.lower()
    df["taker_direction"] = df["taker_direction"].str.lower()

    return df


def _calculate_trader_stats_pandas(
    trades_df: pd.DataFrame,
    role: str = "maker",
    price_snapshot: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """Pandas implementation of trader stats (fallback)."""
    df = trades_df.copy()

    trader_col = role
    direction_col = f"{role}_direction"

    df[direction_col] = df[direction_col].str.lower()

    stats = df.groupby(trader_col).agg({
        "usd_amount": ["sum", "mean", "count"],
        "price": ["mean", "std"],
    }).reset_index()
    stats.columns = [
        "address", "total_volume", "avg_trade", "trade_count",
        "avg_price", "price_std"
    ]

    if price_snapshot:
        df["market_id_str"] = df["market_id"].astype(str).str.replace(".0", "", regex=False)
        df["current_price"] = df["market_id_str"].map(price_snapshot)
        priced_df = df[df["current_price"].notna()].copy()

        if not priced_df.empty:
            direction = priced_df[direction_col].str.lower()
            entry_price = priced_df["price"]
            current_price = priced_df["current_price"]
            priced_df["is_win"] = (
                ((direction == "buy") & (current_price > entry_price)) |
                ((direction == "sell") & (current_price < entry_price))
            )

            win_stats = priced_df.groupby(trader_col).agg({
                "is_win": ["sum", "count", "mean"]
            }).reset_index()
            win_stats.columns = ["address", "winning_trades", "priced_trades", "win_rate"]
            stats = stats.merge(win_stats, on="address", how="left")
            stats["win_rate"] = stats["win_rate"].fillna(0.0)
            stats["priced_trades"] = stats["priced_trades"].fillna(0)
            stats["winning_trades"] = stats["winning_trades"].fillna(0)
        else:
            stats["win_rate"] = 0.0
            stats["priced_trades"] = 0
            stats["winning_trades"] = 0
    else:
        stats["win_rate"] = 0.0
        stats["priced_trades"] = 0
        stats["winning_trades"] = 0

    return stats


def _calculate_trader_stats_polars(
    trades_df: pd.DataFrame,
    role: str = "maker",
    price_snapshot: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """Polars implementation of trader stats (faster)."""
    trader_col = role
    direction_col = f"{role}_direction"

    if isinstance(trades_df, pl.DataFrame):
        df = trades_df
    else:
        df = pl.from_pandas(trades_df, include_index=False)

    df = df.with_columns(
        pl.col(direction_col)
        .cast(pl.Utf8, strict=False)
        .str.to_lowercase()
        .alias(direction_col),
        pl.col("market_id")
        .cast(pl.Utf8, strict=False)
        .str.replace(r"\.0$", "")
        .alias("market_id_str"),
    )

    stats = df.group_by(trader_col).agg(
        pl.col("usd_amount").sum().alias("total_volume"),
        pl.col("usd_amount").mean().alias("avg_trade"),
        pl.col("usd_amount").count().alias("trade_count"),
        pl.col("price").mean().alias("avg_price"),
        pl.col("price").std().alias("price_std"),
    ).rename({trader_col: "address"})

    if price_snapshot:
        prices_df = pl.DataFrame({
            "market_id_str": list(price_snapshot.keys()),
            "current_price": list(price_snapshot.values()),
        })
        priced_df = df.join(prices_df, on="market_id_str", how="left").filter(
            pl.col("current_price").is_not_null()
        )

        if priced_df.height > 0:
            is_win = (
                ((pl.col(direction_col) == "buy") & (pl.col("current_price") > pl.col("price"))) |
                ((pl.col(direction_col) == "sell") & (pl.col("current_price") < pl.col("price")))
            ).alias("is_win")
            priced_df = priced_df.with_columns(is_win)

            win_stats = priced_df.group_by(trader_col).agg(
                pl.col("is_win").sum().alias("winning_trades"),
                pl.col("is_win").count().alias("priced_trades"),
                pl.col("is_win").mean().alias("win_rate"),
            ).rename({trader_col: "address"})
            stats = stats.join(win_stats, on="address", how="left")
            stats = stats.with_columns(
                pl.col("win_rate").fill_null(0.0),
                pl.col("priced_trades").fill_null(0),
                pl.col("winning_trades").fill_null(0),
            )
        else:
            stats = stats.with_columns(
                pl.lit(0.0).alias("win_rate"),
                pl.lit(0).alias("priced_trades"),
                pl.lit(0).alias("winning_trades"),
            )
    else:
        stats = stats.with_columns(
            pl.lit(0.0).alias("win_rate"),
            pl.lit(0).alias("priced_trades"),
            pl.lit(0).alias("winning_trades"),
        )

    return stats.to_pandas()


def calculate_trader_stats(
    trades_df: pd.DataFrame,
    role: str = "maker",  # "maker" or "taker"
    price_snapshot: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """
    Calculate statistics for each trader.

    Win rate is computed from mark-to-market wins using current prices.
    """
    if pl is None:
        if _require_polars():
            raise RuntimeError("Polars is required but not installed.")
        return _calculate_trader_stats_pandas(trades_df, role, price_snapshot)
    try:
        return _calculate_trader_stats_polars(trades_df, role, price_snapshot)
    except Exception:
        if _require_polars():
            raise
        return _calculate_trader_stats_pandas(trades_df, role, price_snapshot)


def _calculate_trader_returns_pandas(
    trades_df: pd.DataFrame,
    role: str = "maker",
    price_snapshot: Optional[Dict[str, float]] = None,
    lookback_months: int = 3,
    as_of: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Pandas implementation of trader mark-to-market returns."""
    if trades_df is None or len(trades_df) == 0 or not price_snapshot:
        return pd.DataFrame()

    df = trades_df.copy()
    if "datetime" not in df.columns:
        return pd.DataFrame()

    df = df.dropna(subset=["datetime", "price"])
    if df.empty:
        return pd.DataFrame()

    if as_of is None:
        as_of = df["datetime"].max()

    cutoff = pd.Timestamp(as_of) - pd.DateOffset(months=lookback_months)
    df = df[(df["datetime"] >= cutoff) & (df["datetime"] <= as_of)]
    if df.empty:
        return pd.DataFrame()

    trader_col = role
    direction_col = f"{role}_direction"
    if direction_col not in df.columns:
        return pd.DataFrame()

    if "token_amount" not in df.columns:
        if "size" in df.columns:
            df["token_amount"] = df["size"]
        elif "usd_amount" in df.columns:
            df = df[df["price"] > 0]
            if df.empty:
                return pd.DataFrame()
            df["token_amount"] = df["usd_amount"] / df["price"]
        else:
            return pd.DataFrame()

    df["token_amount"] = pd.to_numeric(df["token_amount"], errors="coerce")
    df = df.dropna(subset=["token_amount"])
    if df.empty:
        return pd.DataFrame()

    df["market_id_str"] = df["market_id"].astype(str).str.replace(".0", "", regex=False)
    df["current_price"] = df["market_id_str"].map(price_snapshot)
    df = df.dropna(subset=["current_price"])
    if df.empty:
        return pd.DataFrame()

    df[direction_col] = df[direction_col].str.lower()
    df = df[df[direction_col].isin(["buy", "sell"])]
    if df.empty:
        return pd.DataFrame()

    entry_price = df["price"].astype(float)
    current_price = df["current_price"].astype(float)
    tokens = df["token_amount"].astype(float)
    is_buy = df[direction_col] == "buy"

    df["entry_cost"] = np.where(
        is_buy,
        entry_price * tokens,
        (1.0 - entry_price) * tokens,
    )
    df["current_value"] = np.where(
        is_buy,
        current_price * tokens,
        (1.0 - current_price) * tokens,
    )
    df = df[df["entry_cost"] > 0]
    if df.empty:
        return pd.DataFrame()

    df["pnl"] = df["current_value"] - df["entry_cost"]

    stats = df.groupby(trader_col).agg(
        total_capital=("entry_cost", "sum"),
        current_value=("current_value", "sum"),
        pnl=("pnl", "sum"),
        priced_trades=("entry_cost", "count"),
    ).reset_index()

    stats["return_pct"] = stats["pnl"] / stats["total_capital"]

    if "usd_amount" in df.columns:
        volume = df.groupby(trader_col)["usd_amount"].sum().rename("total_volume")
        stats = stats.merge(volume, on=trader_col, how="left")
    else:
        stats["total_volume"] = stats["total_capital"]

    stats = stats.rename(columns={trader_col: "address"})
    return stats


def _calculate_trader_returns_polars(
    trades_df: pd.DataFrame,
    role: str = "maker",
    price_snapshot: Optional[Dict[str, float]] = None,
    lookback_months: int = 3,
    as_of: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Polars implementation of trader mark-to-market returns."""
    if not price_snapshot:
        return pd.DataFrame()

    if isinstance(trades_df, pl.DataFrame):
        df = trades_df
    else:
        df = pl.from_pandas(trades_df, include_index=False)

    if "datetime" not in df.columns:
        return pd.DataFrame()

    df = df.filter(
        pl.col("datetime").is_not_null() & pl.col("price").is_not_null()
    )
    if df.height == 0:
        return pd.DataFrame()

    if as_of is None:
        as_of = df.select(pl.col("datetime").max()).item()

    cutoff = pd.Timestamp(as_of) - pd.DateOffset(months=lookback_months)
    df = df.filter(
        (pl.col("datetime") >= cutoff) & (pl.col("datetime") <= pd.Timestamp(as_of))
    )
    if df.height == 0:
        return pd.DataFrame()

    trader_col = role
    direction_col = f"{role}_direction"
    if direction_col not in df.columns:
        return pd.DataFrame()

    if "token_amount" not in df.columns:
        if "size" in df.columns:
            df = df.with_columns(pl.col("size").alias("token_amount"))
        elif "usd_amount" in df.columns:
            df = df.filter(pl.col("price") > 0)
            if df.height == 0:
                return pd.DataFrame()
            df = df.with_columns((pl.col("usd_amount") / pl.col("price")).alias("token_amount"))
        else:
            return pd.DataFrame()

    df = df.with_columns(
        pl.col(direction_col)
        .cast(pl.Utf8, strict=False)
        .str.to_lowercase()
        .alias(direction_col),
        pl.col("market_id")
        .cast(pl.Utf8, strict=False)
        .str.replace(r"\.0$", "")
        .alias("market_id_str"),
    )

    prices_df = pl.DataFrame({
        "market_id_str": list(price_snapshot.keys()),
        "current_price": list(price_snapshot.values()),
    })
    df = df.join(prices_df, on="market_id_str", how="left").filter(
        pl.col("current_price").is_not_null()
    )
    if df.height == 0:
        return pd.DataFrame()

    df = df.filter(pl.col(direction_col).is_in(["buy", "sell"]))
    if df.height == 0:
        return pd.DataFrame()

    df = df.with_columns(
        pl.when(pl.col(direction_col) == "buy")
        .then(pl.col("price") * pl.col("token_amount"))
        .otherwise((1.0 - pl.col("price")) * pl.col("token_amount"))
        .alias("entry_cost"),
        pl.when(pl.col(direction_col) == "buy")
        .then(pl.col("current_price") * pl.col("token_amount"))
        .otherwise((1.0 - pl.col("current_price")) * pl.col("token_amount"))
        .alias("current_value"),
    )
    df = df.filter(pl.col("entry_cost") > 0)
    if df.height == 0:
        return pd.DataFrame()

    df = df.with_columns((pl.col("current_value") - pl.col("entry_cost")).alias("pnl"))

    agg_exprs = [
        pl.col("entry_cost").sum().alias("total_capital"),
        pl.col("current_value").sum().alias("current_value"),
        pl.col("pnl").sum().alias("pnl"),
        pl.col("entry_cost").count().alias("priced_trades"),
    ]
    if "usd_amount" in df.columns:
        agg_exprs.append(pl.col("usd_amount").sum().alias("total_volume"))
    else:
        agg_exprs.append(pl.col("entry_cost").sum().alias("total_volume"))

    stats = df.group_by(trader_col).agg(*agg_exprs).rename({trader_col: "address"})
    stats = stats.with_columns(
        (pl.col("pnl") / pl.col("total_capital")).alias("return_pct")
    )

    return stats.to_pandas()


def calculate_trader_returns(
    trades_df: pd.DataFrame,
    role: str = "maker",
    price_snapshot: Optional[Dict[str, float]] = None,
    lookback_months: int = 3,
    as_of: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """
    Calculate mark-to-market returns for each trader over a recent window.

    Returns are computed as:
        return_pct = (current_value - total_capital) / total_capital
    where total_capital is the capital at risk for BUY/SELL positions.
    """
    if pl is None:
        if _require_polars():
            raise RuntimeError("Polars is required but not installed.")
        return _calculate_trader_returns_pandas(
            trades_df, role, price_snapshot, lookback_months, as_of
        )
    try:
        return _calculate_trader_returns_polars(
            trades_df, role, price_snapshot, lookback_months, as_of
        )
    except Exception:
        if _require_polars():
            raise
        return _calculate_trader_returns_pandas(
            trades_df, role, price_snapshot, lookback_months, as_of
        )


def load_resolution_winners(
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, str]:
    """
    Load resolution winners from the database.

    Returns:
        Dict mapping market_id (str) -> winner ("YES" or "NO")
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT market_id, winner
        FROM polymarket_resolutions
        WHERE winner IS NOT NULL
    """)
    winners = {}
    for row in cur.fetchall():
        market_id_str = str(row[0])
        winners[market_id_str] = row[1]
    conn.close()
    return winners


def calculate_trader_accuracy(
    trades_df: pd.DataFrame,
    resolution_winners: Dict[str, str],
    role: str = "maker",
    min_price: float = 0.20,
    max_price: float = 0.80,
    min_trades: int = 10,
    min_accuracy: float = 0.55,
    min_unique_markets: int = 5,
) -> Set[str]:
    """
    Identify accurate traders using actual resolution data on mid-range prices.

    Only considers trades where price is between min_price and max_price,
    so the outcome was NOT obvious at trade time. Correctness is determined
    by joining to actual resolution data.

    Args:
        trades_df: DataFrame of trades
        resolution_winners: Dict mapping market_id -> "YES"/"NO"
        role: "maker" or "taker"
        min_price: Lower bound for mid-range filter
        max_price: Upper bound for mid-range filter
        min_trades: Minimum qualifying trades per trader
        min_accuracy: Minimum accuracy threshold
        min_unique_markets: Minimum unique markets traded

    Returns:
        Set of trader addresses meeting accuracy criteria
    """
    trader_col = role
    direction_col = f"{role}_direction"

    df = trades_df.copy()

    # Normalize market_id for lookup
    df["market_id_str"] = df["market_id"].astype(str).str.replace(".0", "", regex=False)

    # Filter to mid-range prices
    df = df[(df["price"] >= min_price) & (df["price"] <= max_price)]

    # Join to resolution data
    df["winner"] = df["market_id_str"].map(resolution_winners)
    df = df.dropna(subset=["winner"])

    if df.empty:
        return set()

    # Determine correctness: BUY + YES winner = correct, SELL + NO winner = correct
    direction = df[direction_col].str.lower()
    winner = df["winner"].str.upper()
    df["correct"] = (
        ((direction == "buy") & (winner == "YES")) |
        ((direction == "sell") & (winner == "NO"))
    )

    # Aggregate per trader
    trader_stats = df.groupby(trader_col).agg(
        accuracy=("correct", "mean"),
        trade_count=("correct", "count"),
        volume=("usd_amount", "sum"),
        unique_markets=("market_id_str", "nunique"),
    ).reset_index()

    # Filter by criteria
    qualified = trader_stats[
        (trader_stats["accuracy"] >= min_accuracy) &
        (trader_stats["trade_count"] >= min_trades) &
        (trader_stats["unique_markets"] >= min_unique_markets)
    ]

    return set(qualified[trader_col])


def identify_polymarket_whales(
    trades_df: pd.DataFrame,
    method: str = "win_rate_60pct",
    role: str = "maker",
    min_trades: int = 20,
    min_volume: float = 1000,
    resolution_winners: Optional[Dict[str, str]] = None,
    price_snapshot: Optional[Dict[str, float]] = None,
    return_lookback_months: int = 3,
    return_top_pct: float = 10.0,
) -> Set[str]:
    """
    Identify whale traders from Polymarket trade data.

    Args:
        trades_df: DataFrame of trades
        method: Identification method
        role: "maker" or "taker"
        min_trades: Minimum trades required
        min_volume: Minimum total volume
        resolution_winners: Dict mapping market_id -> "YES"/"NO" (required for mid_price_accuracy)
        price_snapshot: Dict mapping market_id -> current price (required for win-rate methods)
        return_lookback_months: Months of history for top_returns window
        return_top_pct: Percent of traders to keep for top_returns (0-100)

    Returns:
        Set of whale addresses
    """
    needs_price = method in {
        "win_rate_60pct",
        "combo_vol_win",
        "profit_proxy",
        "top_returns",
        "return_3m_top10pct",
    }
    if needs_price and price_snapshot is None:
        raise ValueError(f"{method} requires price_snapshot (current market prices).")
    if method == "mid_price_accuracy" and resolution_winners is None:
        raise ValueError("mid_price_accuracy requires resolution_winners parameter")

    stats = calculate_trader_stats(trades_df, role, price_snapshot)

    # Filter by minimum activity
    stats = stats[
        (stats["trade_count"] >= min_trades) &
        (stats["total_volume"] >= min_volume)
    ]

    if len(stats) == 0:
        return set()

    if method == "volume_top10":
        return set(stats.nlargest(10, "total_volume")["address"])

    elif method == "volume_pct95":
        threshold = stats["total_volume"].quantile(0.95)
        return set(stats[stats["total_volume"] >= threshold]["address"])

    elif method == "large_trades":
        return set(stats[stats["avg_trade"] >= 1000]["address"])

    elif method == "win_rate_60pct":
        qualified = stats[stats["priced_trades"] >= min_trades]
        return set(qualified[qualified["win_rate"] >= 0.60]["address"])

    elif method == "combo_vol_win":
        vol_threshold = stats["total_volume"].quantile(0.90)
        qualified = stats[stats["priced_trades"] >= min_trades]
        return set(qualified[
            (qualified["total_volume"] >= vol_threshold) &
            (qualified["win_rate"] >= 0.55)
        ]["address"])

    elif method == "profit_proxy":
        qualified = stats[stats["priced_trades"] >= min_trades]
        return set(qualified.nlargest(50, "winning_trades")["address"])

    elif method in {"top_returns", "return_3m_top10pct"}:
        if return_top_pct <= 0 or return_top_pct >= 100:
            raise ValueError("return_top_pct must be between 0 and 100 (exclusive).")
        if return_lookback_months <= 0:
            raise ValueError("return_lookback_months must be positive.")
        return_stats = calculate_trader_returns(
            trades_df,
            role=role,
            price_snapshot=price_snapshot,
            lookback_months=return_lookback_months,
        )
        if len(return_stats) == 0:
            return set()
        return_stats = return_stats[
            (return_stats["priced_trades"] >= min_trades) &
            (return_stats["total_capital"] >= min_volume)
        ]
        if len(return_stats) == 0:
            return set()
        threshold = return_stats["return_pct"].quantile(1.0 - (return_top_pct / 100.0))
        return set(return_stats[return_stats["return_pct"] >= threshold]["address"])

    elif method == "mid_price_accuracy":
        return calculate_trader_accuracy(
            trades_df,
            resolution_winners,
            role=role,
            min_trades=min_trades,
        )

    else:
        raise ValueError(f"Unknown method: {method}")


def generate_whale_signals(
    trades_df: pd.DataFrame,
    whale_set: Set[str],
    role: str = "maker",
    min_usd: float = 100,
) -> pd.DataFrame:
    """
    Generate trading signals when whales trade.

    Args:
        trades_df: DataFrame of all trades
        whale_set: Set of whale addresses
        role: "maker" or "taker"
        min_usd: Minimum trade size to trigger signal

    Returns:
        DataFrame of signals for backtest
    """
    trader_col = role
    direction_col = f"{role}_direction"

    # Filter to whale trades
    whale_trades = trades_df[
        (trades_df[trader_col].isin(whale_set)) &
        (trades_df["usd_amount"] >= min_usd)
    ].copy()

    if len(whale_trades) == 0:
        return pd.DataFrame()

    # Convert to signals
    signals = pd.DataFrame({
        "market_id": whale_trades["market_id"],
        "signal_time": whale_trades["datetime"],
        "timestamp": whale_trades["timestamp_unix"],
        "outcome": "YES",  # Always YES, direction determines BUY/SELL
        "side": whale_trades[direction_col].str.upper(),
        "size": whale_trades["usd_amount"],
        "whale_address": whale_trades[trader_col],
        "price_at_signal": whale_trades["price"],
    })

    # Normalize side
    signals["side"] = signals["side"].map({"BUY": "BUY", "SELL": "SELL"})
    signals = signals.dropna(subset=["side"])

    return signals.sort_values("timestamp").reset_index(drop=True)


def run_polymarket_whale_analysis(
    db_path: str = DEFAULT_DB_PATH,
    method: str = "win_rate_60pct",
    train_ratio: float = 0.3,
    min_trades: int = 20,
    min_volume: float = 1000,
    return_lookback_months: int = 3,
    return_top_pct: float = 10.0,
) -> Dict[str, Any]:
    """
    Full whale analysis on Polymarket trade data.

    Args:
        db_path: Path to database
        method: Whale identification method
        train_ratio: Fraction of data for training
        min_trades: Minimum trades for whale qualification
        min_volume: Minimum volume for whale qualification

    Returns:
        Dictionary with analysis results
    """
    print("Loading Polymarket trades...")
    trades = load_polymarket_trades(db_path)
    print(f"  Loaded {len(trades):,} trades")
    print(f"  Date range: {trades['datetime'].min()} to {trades['datetime'].max()}")

    # Train/test split
    split_date = trades["datetime"].quantile(train_ratio)
    train_df = trades[trades["datetime"] <= split_date]
    test_df = trades[trades["datetime"] > split_date]

    print(f"\nTrain/test split at {split_date}")
    print(f"  Training: {len(train_df):,} trades ({train_ratio*100:.0f}%)")
    print(f"  Testing:  {len(test_df):,} trades ({(1-train_ratio)*100:.0f}%)")

    # Identify whales from training data
    print(f"\nIdentifying whales using method: {method}")
    resolution_winners = None
    if method == "mid_price_accuracy":
        resolution_winners = load_resolution_winners(db_path)
    price_snapshot = build_price_snapshot(train_df)
    whales = identify_polymarket_whales(
        train_df,
        method=method,
        min_trades=min_trades,
        min_volume=min_volume,
        resolution_winners=resolution_winners,
        price_snapshot=price_snapshot,
        return_lookback_months=return_lookback_months,
        return_top_pct=return_top_pct,
    )
    print(f"  Found {len(whales)} whales")

    # Calculate whale stats
    all_stats = calculate_trader_stats(train_df, price_snapshot=price_snapshot)
    whale_stats = all_stats[all_stats["address"].isin(whales)]

    print("\nWhale Statistics (Training Period):")
    print(f"  Total volume: ${whale_stats['total_volume'].sum():,.0f}")
    print(f"  Avg trade size: ${whale_stats['avg_trade'].mean():,.0f}")
    print(f"  Avg win rate: {whale_stats['win_rate'].mean()*100:.1f}%")

    # Generate signals from test data
    print("\nGenerating signals from test period whale trades...")
    signals = generate_whale_signals(test_df, whales, min_usd=100)
    print(f"  Generated {len(signals):,} signals")

    # Analyze signal performance (without price data backtest)
    if len(signals) > 0:
        print("\nSignal Analysis (Test Period):")
        print(f"  Unique markets: {signals['market_id'].nunique():,}")
        print(f"  Buy signals: {(signals['side'] == 'BUY').sum():,}")
        print(f"  Sell signals: {(signals['side'] == 'SELL').sum():,}")
        print(f"  Avg signal size: ${signals['size'].mean():,.0f}")
        print(f"  Avg price at signal: {signals['price_at_signal'].mean():.3f}")

    return {
        "trades": trades,
        "train_df": train_df,
        "test_df": test_df,
        "whales": whales,
        "whale_stats": whale_stats,
        "signals": signals,
        "method": method,
    }


if __name__ == "__main__":
    results = run_polymarket_whale_analysis(
        method="win_rate_60pct",
        min_trades=20,
        min_volume=1000,
    )

    print("\n" + "="*60)
    print("TOP 10 WHALES BY VOLUME")
    print("="*60)
    whale_stats = results["whale_stats"].nlargest(10, "total_volume")
    for i, row in whale_stats.iterrows():
        addr = row["address"][:10] + "..." + row["address"][-6:]
        print(f"  {addr}  Vol: ${row['total_volume']:>12,.0f}  "
              f"Trades: {row['trade_count']:>5,}  "
              f"WR: {row['win_rate']*100:>5.1f}%")

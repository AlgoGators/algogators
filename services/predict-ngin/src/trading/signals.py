"""
Signal/strategy research layer for Polymarket alpha hypotheses.

This module is intentionally separate from backtest/execution. It provides:
- Polymarket data loading helpers
- Feature engineering from market metadata + CLOB price history
- Filters and signal rules

Replace the example rule with your actual alpha hypothesis when ready.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

import numpy as np
import pandas as pd

# Default database path
DEFAULT_DB_PATH = "data/prediction_markets.db"


@dataclass(frozen=True)
class Signal:
    market_id: str
    slug: str
    question: str
    side: str  # "YES" or "NO"
    score: float
    as_of: pd.Timestamp
    features: Dict[str, Any]
    rule: str


@dataclass(frozen=True)
class SignalConfig:
    min_volume24hr: float = 10000.0
    min_liquidity: float = 5000.0
    min_history_points: int = 120
    min_price: float = 0.05
    max_price: float = 0.95
    shock_return_1h: float = 0.08
    stabilize_return_6h: float = 0.02
    feature_window_hours: int = 24


@dataclass(frozen=True)
class SignalContext:
    market_info: Dict[str, Any]
    history: Optional[pd.DataFrame]
    as_of: pd.Timestamp


def _parse_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return []


def load_polymarket_markets(
    data_dir: str = "data/polymarket",
    db_path: str = DEFAULT_DB_PATH,
    use_db: bool = True,
) -> pd.DataFrame:
    """
    Load Polymarket markets.

    Args:
        data_dir: Directory with markets_*.json files (fallback)
        db_path: Path to SQLite database (preferred)
        use_db: If True, try loading from database first

    Returns:
        DataFrame with all Polymarket markets
    """
    # Try loading from database first
    if use_db and Path(db_path).exists():
        df = _load_polymarket_markets_from_db(db_path)
        if len(df) > 0:
            return df

    # Fallback to JSON files
    data_path = Path(data_dir)
    files = sorted(data_path.glob("markets_*.json"))
    markets: List[Dict[str, Any]] = []
    for filepath in files:
        try:
            with open(filepath, encoding="utf-8") as f:
                markets.extend(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return pd.DataFrame(markets)


def _load_polymarket_markets_from_db(db_path: str) -> pd.DataFrame:
    """Load Polymarket markets from SQLite database."""
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query("""
            SELECT
                id, slug, question, volume, volume_24hr as volume24hr,
                liquidity, end_date as endDate, outcomes, outcome_prices as outcomePrices,
                token_id_yes, token_id_no, created_at
            FROM polymarket_markets
            ORDER BY volume_24hr DESC
        """, conn)

        # Parse JSON columns
        if len(df) > 0:
            df["outcomes"] = df["outcomes"].apply(_parse_json_list)
            df["outcomePrices"] = df["outcomePrices"].apply(_parse_json_list)

        return df
    finally:
        conn.close()


def _history_to_frame(history: List[Dict[str, Any]]) -> pd.DataFrame:
    if not history:
        return pd.DataFrame(columns=["timestamp", "price"])
    df = pd.DataFrame(history)
    if "t" in df.columns:
        df = df.rename(columns={"t": "timestamp"})
    if "p" in df.columns:
        df = df.rename(columns={"p": "price"})
    df = df.dropna(subset=["timestamp", "price"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def iter_polymarket_clob_markets(
    data_dir: str = "data/polymarket_clob",
    db_path: str = DEFAULT_DB_PATH,
    outcome: str = "YES",
    use_db: bool = True,
) -> Iterator[SignalContext]:
    """
    Stream Polymarket CLOB market data as SignalContext objects.

    Args:
        data_dir: Directory with market_*.json files (fallback)
        db_path: Path to SQLite database (preferred)
        outcome: "YES" or "NO" for price history
        use_db: If True, try loading from database first

    Yields:
        SignalContext objects with market info and price history
    """
    # Try loading from database first
    if use_db and Path(db_path).exists():
        yield from _iter_clob_markets_from_db(db_path, outcome)
        return

    # Fallback to JSON files
    data_path = Path(data_dir)
    for filepath in sorted(data_path.glob("market_*.json")):
        if "index" in filepath.name:
            continue
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        market_info = data.get("market_info", {})
        price_history = data.get("price_history", {})
        history = price_history.get(outcome, {}).get("history", [])
        history_df = _history_to_frame(history)

        if history_df.empty:
            as_of = pd.Timestamp.utcnow()
        else:
            as_of = pd.to_datetime(history_df["timestamp"].iloc[-1], unit="s", utc=True)

        yield SignalContext(
            market_info=market_info,
            history=history_df,
            as_of=as_of,
        )


def _iter_clob_markets_from_db(db_path: str, outcome: str) -> Iterator[SignalContext]:
    """Stream CLOB market data from SQLite database."""
    conn = sqlite3.connect(db_path)
    try:
        # Get all markets with price history
        markets_df = pd.read_sql_query("""
            SELECT DISTINCT m.*
            FROM polymarket_markets m
            INNER JOIN polymarket_prices p ON m.id = p.market_id
            ORDER BY m.volume_24hr DESC
        """, conn)

        for _, market in markets_df.iterrows():
            market_id = market["id"]

            # Get price history for this market
            prices_df = pd.read_sql_query("""
                SELECT timestamp, price
                FROM polymarket_prices
                WHERE market_id = ? AND outcome = ?
                ORDER BY timestamp
            """, conn, params=(market_id, outcome))

            market_info = {
                "id": market["id"],
                "slug": market["slug"],
                "question": market["question"],
                "volume": market["volume"],
                "volume24hr": market["volume_24hr"],
                "liquidity": market["liquidity"],
                "endDate": market["end_date"],
                "outcomes": _parse_json_list(market["outcomes"]),
                "outcomePrices": _parse_json_list(market["outcome_prices"]),
            }

            if prices_df.empty:
                as_of = pd.Timestamp.utcnow()
            else:
                as_of = pd.to_datetime(prices_df["timestamp"].iloc[-1], unit="s", utc=True)

            yield SignalContext(
                market_info=market_info,
                history=prices_df,
                as_of=as_of,
            )
    finally:
        conn.close()


def _price_at_or_before(history_df: pd.DataFrame, timestamp: int) -> Optional[float]:
    if history_df.empty:
        return None
    subset = history_df[history_df["timestamp"] <= timestamp]
    if subset.empty:
        return None
    return float(subset["price"].iloc[-1])


def build_features(context: SignalContext, config: SignalConfig) -> Dict[str, Any]:
    """
    Build feature dict from market metadata and price history.
    """
    features: Dict[str, Any] = {}
    info = context.market_info

    volume24hr = _parse_float(info.get("volume24hr", info.get("volume24Hours")))
    liquidity = _parse_float(info.get("liquidity", info.get("totalLiquidity")))
    volume = _parse_float(info.get("volume"))

    end_date_raw = info.get("endDate")
    end_date = pd.to_datetime(end_date_raw, utc=True, errors="coerce")

    features.update({
        "volume24hr": volume24hr,
        "volume": volume,
        "liquidity": liquidity,
        "end_date": end_date,
    })

    if not pd.isna(end_date):
        features["days_to_close"] = (end_date - context.as_of).total_seconds() / 86400
    else:
        features["days_to_close"] = None

    history_df = context.history
    if history_df is None or history_df.empty:
        return features

    last_ts = int(history_df["timestamp"].iloc[-1])
    last_price = float(history_df["price"].iloc[-1])
    features["price"] = last_price
    features["history_points"] = int(len(history_df))

    window_seconds = config.feature_window_hours * 3600
    window_start = last_ts - window_seconds
    window_df = history_df[history_df["timestamp"] >= window_start]

    price_1h = _price_at_or_before(history_df, last_ts - 3600)
    price_6h = _price_at_or_before(history_df, last_ts - 6 * 3600)
    price_24h = _price_at_or_before(history_df, last_ts - 24 * 3600)

    def _ret(now: float, past: Optional[float]) -> Optional[float]:
        if past in (None, 0):
            return None
        return (now - past) / past

    features["return_1h"] = _ret(last_price, price_1h)
    features["return_6h"] = _ret(last_price, price_6h)
    features["return_24h"] = _ret(last_price, price_24h)

    if len(window_df) > 2:
        pct = window_df["price"].pct_change().dropna()
        features["vol_24h"] = float(pct.std()) if not pct.empty else None
        features["range_24h"] = float(window_df["price"].max() - window_df["price"].min())
    else:
        features["vol_24h"] = None
        features["range_24h"] = None

    return features


def passes_base_filters(features: Dict[str, Any], config: SignalConfig) -> bool:
    if features.get("volume24hr", 0.0) < config.min_volume24hr:
        return False
    if features.get("liquidity", 0.0) < config.min_liquidity:
        return False
    if features.get("history_points", 0) < config.min_history_points:
        return False

    price = features.get("price")
    if price is None:
        return False
    if price < config.min_price or price > config.max_price:
        return False
    return True


def mean_reversion_rule(
    context: SignalContext,
    features: Dict[str, Any],
    config: SignalConfig,
) -> Optional[Signal]:
    """
    Example alpha hypothesis:
    - Require a sharp 1h move (shock_return_1h)
    - Require 6h return to be relatively stable (stabilize_return_6h)
    - Trade mean reversion (buy YES after sharp drop, buy NO after sharp rise)
    """
    ret_1h = features.get("return_1h")
    ret_6h = features.get("return_6h")

    if ret_1h is None or ret_6h is None:
        return None

    if abs(ret_1h) < config.shock_return_1h:
        return None
    if abs(ret_6h) > config.stabilize_return_6h:
        return None

    if ret_1h < 0:
        side = "YES"
    else:
        side = "NO"

    info = context.market_info
    return Signal(
        market_id=str(info.get("id", "")),
        slug=str(info.get("slug", "")),
        question=str(info.get("question", "")),
        side=side,
        score=abs(ret_1h),
        as_of=context.as_of,
        features=features,
        rule="mean_reversion_after_shock",
    )


class SignalEngine:
    def __init__(
        self,
        config: Optional[SignalConfig] = None,
    ) -> None:
        self.config = config or SignalConfig()

    def evaluate(self, context: SignalContext) -> List[Signal]:
        features = build_features(context, self.config)
        if not passes_base_filters(features, self.config):
            return []

        signal = mean_reversion_rule(context, features, self.config)
        return [signal] if signal else []


def generate_signals(
    data_dir: str = "data/polymarket_clob",
    outcome: str = "YES",
    config: Optional[SignalConfig] = None,
) -> List[Signal]:
    """
    Generate signals from local Polymarket CLOB market files.
    """
    engine = SignalEngine(config=config)
    signals: List[Signal] = []
    for context in iter_polymarket_clob_markets(data_dir=data_dir, outcome=outcome):
        signals.extend(engine.evaluate(context))
    return signals


def signals_to_dataframe(signals: Iterable[Signal]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for signal in signals:
        row = {
            "market_id": signal.market_id,
            "slug": signal.slug,
            "question": signal.question,
            "side": signal.side,
            "score": signal.score,
            "as_of": signal.as_of,
            "rule": signal.rule,
        }
        for key, value in signal.features.items():
            row[f"feat_{key}"] = value
        rows.append(row)
    return pd.DataFrame(rows)

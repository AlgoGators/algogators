"""
Momentum signal generation from price data at scale.

Produces a signals DataFrame (market_id, signal_time, outcome, side, size)
suitable for run_polymarket_backtest, using vectorized parquet passes or
batched SQLite reads. Optimized for large datasets (~100GB) via
partition-by-partition processing and evaluation at discrete times (e.g. daily).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, List

import pandas as pd
try:
    from src.timeconv import to_epoch, to_epoch_seconds
except ImportError:  # src/ itself on sys.path
    from timeconv import to_epoch, to_epoch_seconds

try:
    import polars as pl
except ImportError:
    pl = None

# Default parquet layout
DEFAULT_PARQUET_PRICES_DIR = "data/polymarket/prices"
SECONDS_PER_DAY = 86400


def _normalize_timestamp_seconds(ts: "pl.Expr") -> "pl.Expr":
    """Ensure timestamp is in seconds (int). Handles ms or datetime."""
    # If values are > 1e12 assume milliseconds
    return pl.when(ts > 1_000_000_000_000).then(ts // 1000).otherwise(ts)


def _select_price_files(
    base_dir: Path,
    start_date: Optional[str],
    end_date: Optional[str],
) -> List[Path]:
    """Select parquet files by optional date range (filename like prices_YYYY-MM.parquet)."""
    files = sorted(base_dir.glob("prices_*.parquet"))
    if not files:
        return []
    if not start_date and not end_date:
        return files
    out = []
    for f in files:
        stem = f.stem.replace("prices_", "")
        if len(stem) >= 7 and stem[4] == "-":  # YYYY-MM
            if start_date and stem < start_date[:7]:
                continue
            if end_date and stem > end_date[:7]:
                continue
        out.append(f)
    return out


def generate_momentum_signals_parquet(
    parquet_dir: str = DEFAULT_PARQUET_PRICES_DIR,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    threshold: float = 0.05,
    eval_freq_hours: int = 24,
    outcome: str = "YES",
    position_size: Optional[float] = None,
    max_markets: Optional[int] = 1000,
) -> pd.DataFrame:
    """
    Generate momentum signals from parquet price files using Polars.

    Processes file-by-file (or partition-by-partition) to avoid loading
    full dataset. Evaluation is at discrete times (e.g. daily) to limit rows.
    Emits BUY when return_24h > threshold, SELL when return_24h < -threshold.

    Returns:
        DataFrame with columns: market_id, signal_time, outcome, side, size (optional).
    """
    if pl is None:
        raise RuntimeError("Polars is required for parquet signal generation. pip install polars")

    base_dir = Path(parquet_dir)
    if not base_dir.exists():
        return pd.DataFrame()

    files = _select_price_files(base_dir, start_date, end_date)
    if not files:
        return pd.DataFrame()

    eval_interval_seconds = eval_freq_hours * 3600
    all_signals: List[pl.DataFrame] = []

    for path in files:
        try:
            df = pl.read_parquet(path)
        except Exception:
            continue

        # Require columns
        if not all(c in df.columns for c in ("market_id", "timestamp", "price")):
            continue
        if "outcome" in df.columns:
            df = df.filter(pl.col("outcome").cast(pl.Utf8).str.to_uppercase() == outcome.upper())
        else:
            df = df  # assume YES

        if df.is_empty():
            continue

        # Normalize types
        df = df.with_columns(
            pl.col("market_id").cast(pl.Utf8, strict=False),
            pl.col("price").cast(pl.Float64, strict=False),
        )
        # Timestamp: may be int (s or ms) or datetime
        ts_col = pl.col("timestamp")
        if df["timestamp"].dtype in (pl.Datetime, pl.Datetime("us"), pl.Datetime("ns")):
            ts_seconds = ts_col.dt.epoch("s")
        else:
            ts_seconds = _normalize_timestamp_seconds(ts_col)
        df = df.with_columns(ts_seconds.alias("_ts_s"))
        df = df.filter(pl.col("_ts_s").is_not_null() & pl.col("price").is_not_null())

        if df.is_empty():
            continue

        # Truncate to evaluation interval (e.g. day)
        df = df.with_columns(
            (pl.col("_ts_s") // eval_interval_seconds * eval_interval_seconds).alias("eval_ts")
        )
        # Last price per (market_id, eval_ts)
        daily = (
            df.group_by(["market_id", "eval_ts"])
            .agg(pl.col("price").last().alias("price"), pl.col("_ts_s").max().alias("_ts_s"))
            .sort(["market_id", "eval_ts"])
        )
        # Previous period price (24h ago = previous eval_ts for daily)
        daily = daily.with_columns(
            pl.col("eval_ts").shift(1).over("market_id").alias("eval_ts_prev"),
            pl.col("price").shift(1).over("market_id").alias("price_24h_ago"),
        )
        daily = daily.filter(pl.col("price_24h_ago").is_not_null())
        # return_24h
        daily = daily.with_columns(
            ((pl.col("price") - pl.col("price_24h_ago")) / pl.col("price_24h_ago")).alias(
                "return_24h"
            )
        )
        daily = daily.filter(
            (pl.col("return_24h") >= threshold) | (pl.col("return_24h") <= -threshold)
        )
        # Optional date range filter on eval_ts
        if start_date:
            start_ts = int(pd.Timestamp(start_date).timestamp())
            daily = daily.filter(pl.col("eval_ts") >= start_ts)
        if end_date:
            end_ts = int(pd.Timestamp(end_date).timestamp()) + SECONDS_PER_DAY
            daily = daily.filter(pl.col("eval_ts") < end_ts)
        if daily.is_empty():
            continue

        # Signal columns: side = BUY if return_24h > 0 else SELL
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
        all_signals.append(signals)

    if not all_signals:
        return _empty_signals_df()

    combined = pl.concat(all_signals, how="vertical").unique()
    if max_markets is not None:
        # Keep first max_markets by market_id (arbitrary but deterministic)
        market_order = combined.select("market_id").unique().head(max_markets)
        combined = combined.join(market_order, on="market_id", how="inner")
    combined = combined.sort("signal_ts")

    out = combined.to_pandas()
    out["signal_time"] = pd.to_datetime(out["signal_ts"], unit="s", utc=True)
    if "size" not in out.columns and position_size is not None:
        out["size"] = position_size
    return out


def generate_momentum_signals_sqlite(
    db_path: str = "data/prediction_markets.db",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    threshold: float = 0.05,
    eval_freq_hours: int = 24,
    outcome: str = "YES",
    position_size: Optional[float] = None,
    max_markets: Optional[int] = 1000,
) -> pd.DataFrame:
    """
    Generate momentum signals from SQLite polymarket_prices.

    Fetches market list (optionally limited), then for each market loads
    price history and computes return_24h at evaluation times. Batched
    for memory efficiency.
    """
    from .data_modules.database import PredictionMarketDB

    db = PredictionMarketDB(db_path)
    try:
        # Market list: distinct market_id from prices (or from polymarket_markets)
        markets_df = db.query(
            "SELECT DISTINCT market_id FROM polymarket_prices WHERE outcome = ? ORDER BY market_id",
            (outcome,),
        )
        if markets_df.empty:
            return _empty_signals_df()
        market_ids = markets_df["market_id"].astype(str).tolist()
        if max_markets is not None:
            market_ids = market_ids[: max_markets]
    finally:
        db.close()

    start_ts = int(pd.Timestamp(start_date).timestamp()) if start_date else None
    end_ts = int(pd.Timestamp(end_date).timestamp()) + SECONDS_PER_DAY if end_date else None
    eval_interval_seconds = eval_freq_hours * 3600
    rows: List[dict] = []

    db = PredictionMarketDB(db_path)
    try:
        for market_id in market_ids:
            hist = db.get_price_history(str(market_id), outcome)
            if hist is None or len(hist) < 2:
                continue
            hist = hist.copy()
            hist["timestamp"] = pd.to_numeric(hist["timestamp"], errors="coerce")
            hist["price"] = pd.to_numeric(hist["price"], errors="coerce")
            hist = hist.dropna(subset=["timestamp", "price"]).sort_values("timestamp")
            if hist.empty:
                continue
            ts = hist["timestamp"].values
            pr = hist["price"].values
            # Eval grid from min to max timestamp
            t_min, t_max = int(ts.min()), int(ts.max())
            eval_times = range(
                (t_min // eval_interval_seconds) * eval_interval_seconds,
                t_max + 1,
                eval_interval_seconds,
            )
            for et in eval_times:
                if start_ts is not None and et < start_ts:
                    continue
                if end_ts is not None and et >= end_ts:
                    continue
                # Price at or before et
                cand = (ts <= et).nonzero()[0]
                if len(cand) == 0:
                    continue
                idx = int(cand[-1])
                if ts[idx] > et:
                    continue
                price_now = float(pr[idx])
                # Price at or before et - 24h
                et_24h = et - eval_interval_seconds
                idx_24h = (ts <= et_24h).nonzero()[0]
                if len(idx_24h) == 0:
                    continue
                idx_24h = idx_24h[-1]
                price_24h = float(pr[idx_24h])
                if price_24h <= 0:
                    continue
                ret_24h = (price_now - price_24h) / price_24h
                if abs(ret_24h) < threshold:
                    continue
                side = "BUY" if ret_24h > 0 else "SELL"
                row = {
                    "market_id": str(market_id),
                    "signal_ts": et,
                    "outcome": outcome,
                    "side": side,
                }
                if position_size is not None:
                    row["size"] = position_size
                rows.append(row)
    finally:
        db.close()

    if not rows:
        return _empty_signals_df()
    out = pd.DataFrame(rows)
    out["signal_time"] = pd.to_datetime(out["signal_ts"], unit="s", utc=True)
    return out.sort_values("signal_ts").reset_index(drop=True)


def _empty_signals_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["market_id", "signal_time", "signal_ts", "outcome", "side", "size"]
    )


def generate_momentum_signals(
    parquet_dir: Optional[str] = None,
    db_path: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    threshold: float = 0.05,
    eval_freq_hours: int = 24,
    outcome: str = "YES",
    position_size: Optional[float] = None,
    max_markets: Optional[int] = 1000,
    source: str = "auto",
) -> pd.DataFrame:
    """
    Generate momentum signals from parquet or SQLite.

    Args:
        parquet_dir: Path to parquet prices (e.g. data/parquet/prices).
        db_path: Path to SQLite DB (e.g. data/prediction_markets.db).
        start_date: Optional start date (YYYY-MM-DD).
        end_date: Optional end date (YYYY-MM-DD).
        threshold: Minimum |return_24h| to emit a signal (default 0.05).
        eval_freq_hours: Evaluation interval in hours (default 24 = daily).
        outcome: Outcome to trade (default YES).
        position_size: Optional size per signal for backtest.
        max_markets: Cap on number of markets (default: 1000).
        source: "auto" (parquet if available else sqlite), "parquet", or "sqlite".

    Returns:
        DataFrame with market_id, signal_time, outcome, side, optional size.
    """
    parquet_dir = parquet_dir or DEFAULT_PARQUET_PRICES_DIR
    db_path = db_path or "data/prediction_markets.db"
    use_parquet = False
    if source == "parquet":
        use_parquet = True
    elif source == "sqlite":
        use_parquet = False
    else:
        if Path(parquet_dir).exists() and any(Path(parquet_dir).glob("prices_*.parquet")):
            use_parquet = True

    if use_parquet and pl is not None:
        return generate_momentum_signals_parquet(
            parquet_dir=parquet_dir,
            start_date=start_date,
            end_date=end_date,
            threshold=threshold,
            eval_freq_hours=eval_freq_hours,
            outcome=outcome,
            position_size=position_size,
            max_markets=max_markets,
        )
    return generate_momentum_signals_sqlite(
        db_path=db_path,
        start_date=start_date,
        end_date=end_date,
        threshold=threshold,
        eval_freq_hours=eval_freq_hours,
        outcome=outcome,
        position_size=position_size,
        max_markets=max_markets,
    )


def signals_dataframe_to_backtest_format(signals: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure signals DataFrame has columns expected by run_polymarket_backtest.

    Expected: market_id, signal_time, outcome, side, optional size.
    """
    if signals is None or signals.empty:
        return _empty_signals_df()
    required = {"market_id", "signal_time", "outcome", "side"}
    out = signals.copy()
    if "signal_ts" not in out.columns and "signal_time" in out.columns:
        out["signal_ts"] = to_epoch_seconds(pd.to_datetime(out["signal_time"], utc=True))
    for col in required:
        if col not in out.columns:
            raise ValueError(f"Signals missing required column: {col}")
    return out

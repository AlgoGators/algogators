"""
Realistic Polymarket backtest with proper capital constraints and position tracking.

Fixes from naive backtest:
1. P&L counted on EXIT date (resolution), not entry date
2. Capital constraint - can only open positions with available capital
3. Holding period tracking - capital is locked until position closes
4. No look-ahead bias - only uses actual resolution data
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set, Dict, Any, Optional, List, Iterable
from scipy import stats

try:
    import polars as pl
except ImportError:  # pragma: no cover - optional dependency
    pl = None

from trading.data_modules.costs import CostModel, DEFAULT_COST_MODEL
from trading.polymarket_backtest import ClobPriceStore


class _ClobPriceCache:
    """Cache for CLOB price history lookups (YES outcome)."""

    def __init__(self, price_store: ClobPriceStore, outcome: str = "YES"):
        self.price_store = price_store
        self.outcome = outcome
        self._cache: Dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def price_at_or_before(self, market_id: str, ts: pd.Timestamp) -> Optional[float]:
        if market_id not in self._cache:
            df = self.price_store.get_price_history(market_id, self.outcome)
            if df is None or df.empty:
                self._cache[market_id] = (np.array([], dtype="int64"), np.array([], dtype="float64"))
            else:
                t = pd.to_numeric(df["timestamp"], errors="coerce").dropna().astype("int64").to_numpy()
                p = pd.to_numeric(df["price"], errors="coerce").dropna().astype("float64").to_numpy()
                self._cache[market_id] = (t, p)

        timestamps, prices = self._cache[market_id]
        if timestamps.size == 0:
            return None

        ts_end = int(pd.Timestamp(ts).replace(hour=23, minute=59, second=59).timestamp())
        idx = int(np.searchsorted(timestamps, ts_end, side="right")) - 1
        if idx < 0:
            return None
        return float(prices[idx])


@dataclass
class Position:
    """Track an open position."""
    market_id: str
    entry_date: pd.Timestamp
    entry_price: float
    direction: str  # 'buy' or 'sell'
    size: float
    whale_address: str
    expected_exit_date: Optional[pd.Timestamp] = None
    resolution: Optional[float] = None


@dataclass
class RealisticBacktestResult:
    """Results from realistic backtest."""
    strategy_name: str
    total_trades: int
    unique_markets: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_gross_pnl: float
    total_net_pnl: float
    total_costs: float
    avg_pnl_per_trade: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    roi_pct: float
    position_size: float
    starting_capital: float
    max_concurrent_positions: int
    avg_concurrent_positions: float
    avg_holding_days: float
    trades_df: pd.DataFrame = field(repr=False)
    daily_equity: pd.Series = field(repr=False)
    peak_capital_deployed: float = 0.0
    signals_skipped_no_capital: int = 0


def load_resolutions_by_slug(
    db_path: str = "data/prediction_markets.db",
) -> Dict[str, Dict[str, Any]]:
    """
    Load resolution data indexed by slug for joining with trades.

    Returns:
        Dict mapping slug -> resolution data
    """
    import sqlite3

    resolutions = {}
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT slug, winner, closed_time, resolution_price_yes, resolution_price_no
            FROM polymarket_resolutions
            WHERE winner IS NOT NULL AND slug IS NOT NULL
        """)
        for row in cur.fetchall():
            slug = row[0]
            winner = row[1]
            closed_time = row[2]
            resolutions[slug] = {
                "winner": winner,
                "resolution": 1.0 if winner == "YES" else 0.0,
                "closed_time": closed_time,
                "resolution_price_yes": row[3],
                "resolution_price_no": row[4],
            }
        conn.close()
        print(f"    Loaded {len(resolutions):,} resolutions by slug")
    except Exception as e:
        print(f"    Warning: Could not load resolutions by slug: {e}")

    return resolutions


def _to_datetime_safe(value: Any) -> Optional[pd.Timestamp]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:  # ms
            ts = ts / 1000.0
        return pd.to_datetime(ts, unit="s", errors="coerce")
    return pd.to_datetime(value, errors="coerce")


def _load_market_close_dates(
    market_ids: Iterable[Any],
    parquet_dir: str = "data/polymarket",
    db_path: str = "data/prediction_markets.db",
) -> Dict[str, pd.Timestamp]:
    """
    Load Polymarket market end dates for the given market IDs.

    Prefers parquet (data/polymarket) and falls back to SQLite.
    Returns mapping of market_id -> close_date (pd.Timestamp).
    """
    ids = {str(mid).replace(".0", "") for mid in market_ids if mid is not None}
    if not ids:
        return {}

    close_dates: Dict[str, pd.Timestamp] = {}

    # Prefer parquet markets if available
    pq_path = Path(parquet_dir)
    single = pq_path / "markets.parquet"
    pq_files = [single] if single.exists() else (sorted(pq_path.glob("markets_*.parquet")) if pq_path.exists() else [])
    if pq_files and pl is not None:
        try:
            lf = pl.scan_parquet([str(f) for f in pq_files])
            schema = lf.collect_schema()
            # Support both polymarket (end_date) and parquet (endDate, endDateIso, closedTime) schemas
            cols = [pl.col("id").cast(pl.Utf8).alias("market_id")]
            for c in ["endDateIso", "endDate", "closedTime", "end_date"]:
                if c in schema.names():
                    cols.append(pl.col(c))
            lf = lf.select(cols).with_columns(
                pl.col("market_id").str.replace(r"\.0$", "")
            ).filter(pl.col("market_id").is_in(list(ids)))
            df = lf.collect().to_pandas()
            if not df.empty:
                for _, row in df.iterrows():
                    mid = str(row.get("market_id", "")).replace(".0", "")
                    if not mid:
                        continue
                    end_val = row.get("endDateIso") or row.get("endDate") or row.get("closedTime") or row.get("end_date")
                    if end_val is None or (isinstance(end_val, float) and np.isnan(end_val)):
                        continue
                    dt = _to_datetime_safe(end_val)
                    if dt is not None and not pd.isna(dt):
                        close_dates[mid] = dt
            return close_dates
        except Exception:
            # Fall back to SQLite
            close_dates = {}

    # SQLite fallback
    try:
        import sqlite3

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        chunk = 999  # SQLite parameter limit
        id_list = list(ids)
        for i in range(0, len(id_list), chunk):
            subset = id_list[i:i + chunk]
            placeholders = ",".join(["?"] * len(subset))
            cur.execute(
                f"SELECT id, end_date FROM polymarket_markets WHERE id IN ({placeholders})",
                subset,
            )
            for market_id, end_date in cur.fetchall():
                mid = str(market_id).replace(".0", "")
                dt = _to_datetime_safe(end_date)
                if dt is not None and not pd.isna(dt):
                    close_dates[mid] = dt
        conn.close()
    except Exception:
        pass

    return close_dates


def build_market_resolution_dates(
    trades_df: pd.DataFrame,
    db_path: str = "data/prediction_markets.db",
    market_close_dates: Optional[Dict[str, pd.Timestamp]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Build resolution data for each market including resolution DATE.

    IMPORTANT: To avoid look-ahead bias, only uses actual resolution data
    from the polymarket_resolutions table.

    Supports two join methods:
    1. If trades have 'slug' column: join via slug (preferred, higher match rate)
    2. Otherwise: join via market_id (legacy)

    Args:
        trades_df: DataFrame of trades (used only for market IDs and last trade dates)
        db_path: Path to database
        market_close_dates: Optional mapping of market_id -> scheduled end date
    """
    import sqlite3

    resolutions = {}
    use_slug_join = "slug" in trades_df.columns

    # Load actual resolution data from database
    db_resolutions_by_id = {}
    db_resolutions_by_slug = {}

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT market_id, slug, winner, closed_time
            FROM polymarket_resolutions
            WHERE winner IS NOT NULL
        """)
        for row in cur.fetchall():
            market_id = str(row[0])
            slug = row[1]
            winner = row[2]
            closed_time = row[3]

            db_resolutions_by_id[market_id] = {
                "winner": winner,
                "closed_time": closed_time,
            }
            if slug:
                db_resolutions_by_slug[slug] = {
                    "winner": winner,
                    "closed_time": closed_time,
                }
        conn.close()
        print(f"    Loaded {len(db_resolutions_by_id):,} resolutions by ID")
        print(f"    Loaded {len(db_resolutions_by_slug):,} resolutions by slug")
        if use_slug_join:
            print(f"    Using slug-based join (trades have slug column)")
    except Exception as e:
        print(f"    Warning: Could not load resolution table: {e}")

    for market_id, market_trades in trades_df.groupby("market_id"):
        market_trades = market_trades.sort_values("datetime")
        final_price = market_trades["price"].iloc[-1]
        last_trade_date = market_trades["datetime"].max()

        # Normalize market_id for lookup
        market_id_str = str(market_id).replace(".0", "")

        # Get slug if available (for slug-based join)
        market_slug = None
        if use_slug_join and "slug" in market_trades.columns:
            market_slug = market_trades["slug"].iloc[0]

        # Market end date (from market metadata)
        market_close_date = None
        if market_close_dates:
            market_close_date = market_close_dates.get(market_id_str)

        # Check if we have actual resolution data (NO LOOK-AHEAD BIAS)
        # Try slug-based lookup first (higher match rate), then ID-based
        resolution_data = None
        if market_slug and market_slug in db_resolutions_by_slug:
            resolution_data = db_resolutions_by_slug[market_slug]
        elif market_id_str in db_resolutions_by_id:
            resolution_data = db_resolutions_by_id[market_id_str]

        if resolution_data:
            winner = resolution_data["winner"]
            resolution = 1.0 if winner == "YES" else 0.0
            is_resolved = True
            closed_time = resolution_data["closed_time"]
            if closed_time:
                try:
                    resolution_date = pd.to_datetime(closed_time)
                except:
                    resolution_date = last_trade_date
            else:
                resolution_date = last_trade_date

            resolutions[str(market_id)] = {
                "resolution": resolution,
                "is_resolved": is_resolved,
                "final_price": final_price,
                "resolution_date": resolution_date,
                "market_close_date": market_close_date or resolution_date,
                "has_actual_resolution": True,
            }
        else:
            # No resolution data available
            # Mark as unresolved - will be skipped if resolved_only=True
            resolutions[str(market_id)] = {
                "resolution": None,
                "is_resolved": False,
                "final_price": final_price,
                "resolution_date": None,
                "market_close_date": market_close_date,
                "has_actual_resolution": False,
            }

    # Stats
    actual_count = sum(1 for r in resolutions.values() if r.get("has_actual_resolution"))
    resolved_count = sum(1 for r in resolutions.values() if r.get("is_resolved"))
    print(f"    Markets with actual resolution: {actual_count:,} / {len(resolutions):,}")
    print(f"    Total resolved markets: {resolved_count:,}")

    return resolutions


def run_realistic_backtest(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    whale_set: Set[str],
    role: str = "maker",
    strategy_name: str = "Polymarket Whales (Realistic)",
    cost_model: Optional[CostModel] = None,
    position_size: float = 250,
    starting_capital: float = 10000,
    min_usd: float = 100,
    resolved_only: bool = False,
    max_capital: Optional[float] = None,
    max_position_liquidity_pct: Optional[float] = None,
) -> Optional[RealisticBacktestResult]:
    """
    Run realistic backtest with optional capital constraints.

    Key differences from naive backtest:
    - Tracks open positions and their holding periods
    - P&L is realized on exit date, not entry date
    - Builds equity curve based on actual capital deployment
    - NO LOOK-AHEAD BIAS: Only uses actual resolution data
    - Tracks peak capital deployed for honest reporting

    Args:
        train_df: Training data for whale identification
        test_df: Test data for signal generation
        whale_set: Set of whale addresses
        role: "maker" or "taker"
        strategy_name: Name for reporting
        cost_model: Cost model for slippage/fees
        position_size: Size of each position in USD
        starting_capital: Starting capital in USD
        min_usd: Minimum trade size to generate signal
        resolved_only: If True, only trade markets with known resolution
        max_capital: Maximum total deployed capital (None = unlimited)
        max_position_liquidity_pct: Cap position size by % of market cumulative liquidity
    """
    if cost_model is None:
        cost_model = DEFAULT_COST_MODEL

    if max_position_liquidity_pct is not None:
        if "market_cum_liquidity_usd" not in test_df.columns:
            raise ValueError(
                "market_cum_liquidity_usd not found in trades. "
                "Run scripts/add_market_cum_volume_to_parquet.py to add it."
            )

    trader_col = role
    direction_col = f"{role}_direction"

    # Build resolution map - only use actual resolution data to avoid look-ahead bias
    all_trades = pd.concat([train_df, test_df]).sort_values("datetime")
    market_close_dates = _load_market_close_dates(all_trades["market_id"])
    resolutions = build_market_resolution_dates(
        all_trades,
        market_close_dates=market_close_dates,
    )

    # Filter test trades to whale trades only
    whale_trades = test_df[
        (test_df[trader_col].isin(whale_set)) &
        (test_df["usd_amount"] >= min_usd)
    ].copy()
    whale_trades = whale_trades.sort_values("datetime")

    if whale_trades.empty:
        return None

    if "_date" not in whale_trades.columns:
        whale_trades["_date"] = whale_trades["datetime"].dt.normalize()
    whale_trades_by_date = whale_trades.groupby("_date", sort=False).indices

    price_store = ClobPriceStore()
    clob_cache = _ClobPriceCache(price_store, outcome="YES")

    # State tracking
    open_positions: Dict[str, Position] = {}  # market_id -> Position
    closed_trades: List[Dict[str, Any]] = []
    capital_deployed = 0.0
    peak_capital_deployed = 0.0
    cumulative_realized_pnl = 0.0
    signals_skipped = 0

    # Daily tracking for equity curve
    daily_data: Dict[pd.Timestamp, Dict] = {}

    # Get all unique dates in test period
    test_dates = sorted(whale_trades["_date"].unique())

    try:
        # Process each day
        for current_date in test_dates:
            # 1. Close positions that resolve or end today
            positions_to_close = []
            for market_id, pos in open_positions.items():
                res_data = resolutions.get(market_id, {})
                res_date = res_data.get("resolution_date")

                if res_date is not None and pd.to_datetime(res_date).date() <= current_date.date():
                    positions_to_close.append((market_id, "resolution", res_date))
                    continue

                market_close_date = res_data.get("market_close_date")
                if market_close_date is not None and pd.to_datetime(market_close_date).date() <= current_date.date():
                    positions_to_close.append((market_id, "market_close", market_close_date))

            # Process closures
            for market_id, close_reason, close_date in positions_to_close:
                pos = open_positions.pop(market_id)
                res_data = resolutions.get(market_id, {})

                resolution = res_data.get("resolution")
                exit_date = close_date

                if close_reason == "resolution":
                    exit_price = resolution
                else:
                    market_key = str(market_id).replace(".0", "")
                    clob_price = clob_cache.price_at_or_before(market_key, close_date)
                    if clob_price is None:
                        raise ValueError(f"No CLOB price history for market {market_key} at {close_date}.")
                    exit_price = clob_price

                # Calculate P&L
                if pos.direction == "buy":
                    gross_pnl = (exit_price - pos.entry_price) * pos.size
                else:
                    gross_pnl = (pos.entry_price - exit_price) * pos.size

                # Apply costs
                entry_cost = cost_model.calculate_entry_price(
                    pos.entry_price, pos.size, liquidity=10000
                )
                exit_proceeds = cost_model.calculate_exit_price(
                    exit_price, pos.size, liquidity=10000
                )

                if pos.direction == "buy":
                    net_pnl = (exit_proceeds - entry_cost) * pos.size
                else:
                    net_pnl = (entry_cost - exit_proceeds) * pos.size

                # Return capital
                capital_deployed -= pos.size
                cumulative_realized_pnl += net_pnl

                # Ensure both dates are tz-naive for subtraction
                exit_dt = pd.to_datetime(exit_date)
                entry_dt = pos.entry_date
                if hasattr(exit_dt, 'tz') and exit_dt.tz is not None:
                    exit_dt = exit_dt.tz_localize(None)
                if hasattr(entry_dt, 'tz') and entry_dt.tz is not None:
                    entry_dt = entry_dt.tz_localize(None)
                holding_days = (exit_dt - entry_dt).days

                closed_trades.append({
                    "market_id": market_id,
                    "entry_date": pos.entry_date,
                    "exit_date": exit_date,
                    "direction": pos.direction,
                    "entry_price": pos.entry_price,
                    "exit_price": exit_price,
                    "resolution": resolution,
                    "position_size": pos.size,
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "holding_days": holding_days,
                    "whale_address": pos.whale_address,
                })

        # 2. Process new signals for today
        today_idx = whale_trades_by_date.get(current_date, [])
        today_signals = whale_trades.iloc[today_idx] if len(today_idx) > 0 else whale_trades.iloc[0:0]

        for _, signal in today_signals.iterrows():
            market_id = str(signal["market_id"])
            market_key = market_id.replace(".0", "")

            # Skip if already have position in this market
            if market_id in open_positions:
                continue

            # Skip if market already resolved
            res_data = resolutions.get(market_id)
            if res_data is None:
                continue

            if resolved_only and not res_data.get("is_resolved"):
                continue

            # Skip if no resolution data available (only when resolved_only)
            if resolved_only and res_data.get("resolution") is None:
                continue

            # Check if resolution date is after entry (no look-ahead on resolution)
            res_date = res_data.get("resolution_date")
            if res_date is not None and pd.to_datetime(res_date).date() <= current_date.date():
                continue  # Market already resolved, can't enter

            # Skip if market already past scheduled close date
            market_close_date = res_data.get("market_close_date")
            if market_close_date is not None and pd.to_datetime(market_close_date).date() <= current_date.date():
                continue

            direction = str(signal[direction_col]).lower()
            entry_price = signal["price"]

            # Skip extreme prices
            if entry_price < 0.02 or entry_price > 0.98:
                continue

            # Require CLOB price history at or before entry date (strict CLOB-only)
            if clob_cache.price_at_or_before(market_key, current_date) is None:
                continue

            # Liquidity-based position cap (uses cumulative liquidity proxy)
            position_size_eff = position_size
            if max_position_liquidity_pct is not None:
                liq_val = signal.get("market_cum_liquidity_usd", None)
                if pd.notna(liq_val):
                    try:
                        cap = float(liq_val) * float(max_position_liquidity_pct)
                    except Exception:
                        cap = None
                    if cap is not None:
                        if cap <= 0:
                            continue
                        position_size_eff = min(position_size_eff, cap)

            # Check capital constraint
            if max_capital is not None and capital_deployed + position_size_eff > max_capital:
                signals_skipped += 1
                continue

            # Open position
            capital_deployed += position_size_eff
            peak_capital_deployed = max(peak_capital_deployed, capital_deployed)

            open_positions[market_id] = Position(
                market_id=market_id,
                entry_date=pd.to_datetime(current_date),
                entry_price=entry_price,
                direction=direction,
                size=position_size_eff,
                whale_address=signal[trader_col],
                expected_exit_date=pd.to_datetime(res_date) if res_date else None,
                resolution=res_data["resolution"],
            )

            # 3. Record daily state (mark-to-market using CLOB prices only)
            unrealized_pnl = 0.0
            for market_id, pos in open_positions.items():
                market_key = str(market_id).replace(".0", "")
                clob_price = clob_cache.price_at_or_before(market_key, current_date)
                if clob_price is None:
                    raise ValueError(f"No CLOB price history for market {market_key} at {current_date}.")
                current_price = clob_price
                if pos.direction == "buy":
                    unrealized_pnl += (current_price - pos.entry_price) * pos.size
                else:
                    unrealized_pnl += (pos.entry_price - current_price) * pos.size

            equity = starting_capital + cumulative_realized_pnl + unrealized_pnl

            daily_data[current_date] = {
                "equity": equity,
                "open_positions": len(open_positions),
                "capital_deployed": capital_deployed,
                "realized_pnl": cumulative_realized_pnl,
            }

        # Close any remaining open positions at final prices (mark-to-market, CLOB only)
        for market_id, pos in list(open_positions.items()):
            res_data = resolutions.get(market_id, {})
            market_key = str(market_id).replace(".0", "")
            clob_price = clob_cache.price_at_or_before(market_key, test_dates[-1])
            if clob_price is None:
                raise ValueError(f"No CLOB price history for market {market_key} at {test_dates[-1]}.")
            exit_price = clob_price

            if pos.direction == "buy":
                gross_pnl = (exit_price - pos.entry_price) * pos.size
            else:
                gross_pnl = (pos.entry_price - exit_price) * pos.size

            # Simplified cost for open positions
            net_pnl = gross_pnl * 0.97  # Rough cost estimate

            closed_trades.append({
                "market_id": market_id,
                "entry_date": pos.entry_date,
                "exit_date": test_dates[-1],
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "resolution": exit_price,
                "position_size": pos.size,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "holding_days": (test_dates[-1] - pos.entry_date).days,
                "whale_address": pos.whale_address,
                "status": "open",
            })
    finally:
        price_store.close()

    if not closed_trades:
        price_store.close()
        return None

    # Build results
    trades_df = pd.DataFrame(closed_trades)
    trades_df["status"] = trades_df.get("status", "closed")
    trades_df.loc[trades_df["status"].isna(), "status"] = "closed"

    # Only count closed trades for metrics
    closed_only = trades_df[trades_df["status"] == "closed"]

    total_trades = len(closed_only)
    unique_markets = closed_only["market_id"].nunique()
    winning_trades = len(closed_only[closed_only["net_pnl"] > 0])
    losing_trades = len(closed_only[closed_only["net_pnl"] <= 0])
    win_rate = winning_trades / total_trades if total_trades > 0 else 0

    total_gross_pnl = closed_only["gross_pnl"].sum()
    total_net_pnl = closed_only["net_pnl"].sum()
    total_costs = total_gross_pnl - total_net_pnl
    avg_pnl_per_trade = total_net_pnl / total_trades if total_trades > 0 else 0
    avg_holding_days = closed_only["holding_days"].mean()

    # Build equity curve
    daily_equity = pd.Series({k: v["equity"] for k, v in daily_data.items()}).sort_index()
    daily_positions = pd.Series({k: v["open_positions"] for k, v in daily_data.items()})

    max_concurrent = daily_positions.max()
    avg_concurrent = daily_positions.mean()

    # Daily returns from equity curve
    daily_returns = daily_equity.pct_change().dropna()

    # Sharpe ratio
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
    else:
        sharpe_ratio = 0

    # Max drawdown
    rolling_max = daily_equity.cummax()
    drawdown = rolling_max - daily_equity
    max_drawdown = drawdown.max()
    max_drawdown_pct = (drawdown / rolling_max).max() * 100

    roi_pct = (total_net_pnl / starting_capital) * 100

    return RealisticBacktestResult(
        strategy_name=strategy_name,
        total_trades=total_trades,
        unique_markets=unique_markets,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        total_gross_pnl=total_gross_pnl,
        total_net_pnl=total_net_pnl,
        total_costs=total_costs,
        avg_pnl_per_trade=avg_pnl_per_trade,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        roi_pct=roi_pct,
        position_size=position_size,
        starting_capital=starting_capital,
        max_concurrent_positions=int(max_concurrent),
        avg_concurrent_positions=avg_concurrent,
        avg_holding_days=avg_holding_days,
        peak_capital_deployed=peak_capital_deployed,
        trades_df=trades_df,
        daily_equity=daily_equity,
        signals_skipped_no_capital=signals_skipped,
    )


def run_rolling_realistic_backtest(
    trades_df: pd.DataFrame,
    method: str = "mid_price_accuracy",
    role: str = "maker",
    cost_model: Optional[CostModel] = None,
    position_size: float = 250,
    starting_capital: float = 10000,
    min_usd: float = 100,
    resolved_only: bool = False,
    max_capital: Optional[float] = None,
    max_position_liquidity_pct: Optional[float] = None,
    lookback_months: int = 3,
    resolution_winners: Optional[Dict[str, str]] = None,
    min_trades: int = 20,
    min_volume: float = 1000,
    return_lookback_months: int = 3,
    return_top_pct: float = 10.0,
    test_start: Optional[str] = None,
    test_end: Optional[str] = None,
) -> Optional[RealisticBacktestResult]:
    """
    Rolling realistic backtest with daily whale re-identification.

    Each day:
    1. Look back N months from the current date to build a training window
    2. Re-identify whales from that window
    3. Filter that day's trades to the current whale set
    4. Process signals and resolve positions (same logic as run_realistic_backtest)

    Positions carry over across days — a position opened on day D can close
    on any later day when its market resolves.

    A monthly summary table is printed for readability, but whales are
    recalculated every day.

    Args:
        trades_df: All trades DataFrame
        method: Whale identification method
        role: "maker" or "taker"
        cost_model: Cost model for slippage/fees
        position_size: Size of each position in USD
        starting_capital: Starting capital in USD
        min_usd: Minimum trade size to generate signal
        resolved_only: If True, only trade markets with known resolution
        max_capital: Maximum total deployed capital (None = unlimited)
        max_position_liquidity_pct: Cap position size by % of market cumulative liquidity
        lookback_months: Months of lookback for whale identification
        resolution_winners: Dict mapping slug -> "YES"/"NO" (for mid_price_accuracy)
        min_trades: Minimum trades for whale qualification
        min_volume: Minimum total volume for whale qualification
        return_lookback_months: Months of history for top_returns window
        return_top_pct: Percent of traders to keep for top_returns (0-100)
        test_start: Start date for test period (YYYY-MM-DD). If None, starts after lookback.
        test_end: End date for test period (YYYY-MM-DD). If None, runs to end of data.
    """
    from .polymarket_whales import identify_polymarket_whales

    if cost_model is None:
        cost_model = DEFAULT_COST_MODEL

    if max_position_liquidity_pct is not None:
        if "market_cum_liquidity_usd" not in trades_df.columns:
            raise ValueError(
                "market_cum_liquidity_usd not found in trades. "
                "Run scripts/add_market_cum_volume_to_parquet.py to add it."
            )

    trader_col = role
    direction_col = f"{role}_direction"

    # Build resolution map once at the start
    market_close_dates = _load_market_close_dates(trades_df["market_id"])
    resolutions = build_market_resolution_dates(
        trades_df,
        market_close_dates=market_close_dates,
    )

    # Ensure datetime column is proper datetime
    trades_df = trades_df.copy()
    trades_df["datetime"] = pd.to_datetime(trades_df["datetime"])
    trades_df["_date"] = trades_df["datetime"].dt.normalize()

    trades_sorted = trades_df.sort_values("datetime")
    trade_dates = trades_sorted["_date"].to_numpy()
    trade_market_ids = (
        trades_sorted["market_id"]
        .astype(str)
        .str.replace(".0", "", regex=False)
        .tolist()
    )
    trade_prices = trades_sorted["price"].to_numpy()
    price_snapshot: Dict[str, float] = {}
    trade_idx = 0
    trades_by_date = trades_df.groupby("_date", sort=False).indices

    # Get all unique dates sorted
    all_dates = sorted(trades_df["_date"].unique())

    # Determine test date bounds
    earliest_trade_date = all_dates[0]
    first_valid_date = earliest_trade_date + pd.DateOffset(months=lookback_months)

    # Apply user-specified test window (test_start/test_end)
    period_start = pd.Timestamp(test_start) if test_start else first_valid_date
    # Ensure we have enough lookback even if user requests an early start
    period_start = max(period_start, first_valid_date)
    period_end = pd.Timestamp(test_end) if test_end else all_dates[-1]

    test_dates = [d for d in all_dates if period_start <= d <= period_end]
    if not test_dates:
        print(f"    Not enough history for {lookback_months}-month lookback. Need data before {first_test_date}.")
        return None

    print(f"    Test period: {test_dates[0].date()} to {test_dates[-1].date()} ({len(test_dates)} days)")
    print(f"    Re-identifying whales daily with {lookback_months}-month lookback")

    price_store = ClobPriceStore()
    clob_cache = _ClobPriceCache(price_store, outcome="YES")

    # State that carries across days
    open_positions: Dict[str, Position] = {}  # market_id -> Position
    closed_trades: List[Dict[str, Any]] = []
    capital_deployed = 0.0
    peak_capital_deployed = 0.0
    cumulative_realized_pnl = 0.0
    signals_skipped = 0

    # Daily tracking for equity curve
    daily_data: Dict[pd.Timestamp, Dict] = {}

    # Monthly summary tracking
    current_month = None
    month_signals = 0
    month_closed_start = 0
    month_whale_counts: List[int] = []  # whale counts per day, for avg

    # Monthly summary table header
    print(f"\nRolling Realistic Backtest ({lookback_months}-month lookback, daily re-id)")
    print("-" * 95)
    print(
        f"{'Month':<10} | {'Avg Whales':>10} | {'Signals':>7} | {'Closed':>6} | "
        f"{'Win%':>6} | {'Net PnL':>10} | {'Capital':>10}"
    )
    print("-" * 95)

    for current_date in test_dates:
        # Check if we've crossed into a new month — print summary for the previous one
        date_month = current_date.to_period("M")
        if current_month is not None and date_month != current_month:
            # Print summary row for the month that just ended
            month_closed = closed_trades[month_closed_start:]
            month_closed_count = len(month_closed)
            month_wins = sum(1 for t in month_closed if t["net_pnl"] > 0)
            month_win_rate = (month_wins / month_closed_count * 100) if month_closed_count > 0 else 0
            month_net_pnl = sum(t["net_pnl"] for t in month_closed)
            avg_whales = np.mean(month_whale_counts) if month_whale_counts else 0

            print(
                f"{current_month!s:<10} | {avg_whales:>10.0f} | {month_signals:>7} | "
                f"{month_closed_count:>6} | {month_win_rate:>5.1f}% | "
                f"${month_net_pnl:>9,.0f} | ${capital_deployed:>9,.0f}"
            )

            # Reset monthly accumulators
            month_signals = 0
            month_closed_start = len(closed_trades)
            month_whale_counts = []

        current_month = date_month

        # Update price snapshot with all trades up to current_date
        while trade_idx < len(trade_dates) and trade_dates[trade_idx] <= current_date:
            price_snapshot[trade_market_ids[trade_idx]] = float(trade_prices[trade_idx])
            trade_idx += 1

        # --- Daily whale re-identification ---
        lookback_start = current_date - pd.DateOffset(months=lookback_months)
        train_mask = (trades_df["_date"] >= lookback_start) & (trades_df["_date"] < current_date)
        train_df = trades_df[train_mask]

        if len(train_df) < 1000:
            # Not enough training data for this day
            month_whale_counts.append(0)
            # Still need to close positions that resolve or end today
            positions_to_close = []
            for market_id, pos in open_positions.items():
                res_data = resolutions.get(market_id, {})
                res_date = res_data.get("resolution_date")
                if res_date is not None and pd.to_datetime(res_date).date() <= current_date.date():
                    positions_to_close.append((market_id, "resolution", res_date))
                    continue

                market_close_date = res_data.get("market_close_date")
                if market_close_date is not None and pd.to_datetime(market_close_date).date() <= current_date.date():
                    positions_to_close.append((market_id, "market_close", market_close_date))

            for market_id, close_reason, close_date in positions_to_close:
                pos = open_positions.pop(market_id)
                res_data = resolutions.get(market_id, {})
                resolution = res_data.get("resolution")
                exit_date = close_date
                if close_reason == "resolution":
                    exit_price = resolution
                else:
                    market_key = str(market_id).replace(".0", "")
                    clob_price = clob_cache.price_at_or_before(market_key, close_date)
                    if clob_price is None:
                        raise ValueError(f"No CLOB price history for market {market_key} at {close_date}.")
                    exit_price = clob_price
                if pos.direction == "buy":
                    gross_pnl = (exit_price - pos.entry_price) * pos.size
                else:
                    gross_pnl = (pos.entry_price - exit_price) * pos.size
                entry_cost = cost_model.calculate_entry_price(pos.entry_price, pos.size, liquidity=10000)
                exit_proceeds = cost_model.calculate_exit_price(exit_price, pos.size, liquidity=10000)
                if pos.direction == "buy":
                    net_pnl = (exit_proceeds - entry_cost) * pos.size
                else:
                    net_pnl = (entry_cost - exit_proceeds) * pos.size
                capital_deployed -= pos.size
                cumulative_realized_pnl += net_pnl
                exit_dt = pd.to_datetime(exit_date)
                entry_dt = pos.entry_date
                if hasattr(exit_dt, 'tz') and exit_dt.tz is not None:
                    exit_dt = exit_dt.tz_localize(None)
                if hasattr(entry_dt, 'tz') and entry_dt.tz is not None:
                    entry_dt = entry_dt.tz_localize(None)
                holding_days = (exit_dt - entry_dt).days
                closed_trades.append({
                    "market_id": market_id, "entry_date": pos.entry_date,
                    "exit_date": exit_date, "direction": pos.direction,
                    "entry_price": pos.entry_price, "exit_price": exit_price,
                    "resolution": resolution, "position_size": pos.size,
                    "gross_pnl": gross_pnl, "net_pnl": net_pnl,
                    "holding_days": holding_days, "whale_address": pos.whale_address,
                })
            unrealized_pnl = 0.0
            for market_id, pos in open_positions.items():
                market_key = str(market_id).replace(".0", "")
                clob_price = clob_cache.price_at_or_before(market_key, current_date)
                if clob_price is None:
                    raise ValueError(f"No CLOB price history for market {market_key} at {current_date}.")
                current_price = clob_price
                if pos.direction == "buy":
                    unrealized_pnl += (current_price - pos.entry_price) * pos.size
                else:
                    unrealized_pnl += (pos.entry_price - current_price) * pos.size
            equity = starting_capital + cumulative_realized_pnl + unrealized_pnl
            daily_data[current_date] = {
                "equity": equity, "open_positions": len(open_positions),
                "capital_deployed": capital_deployed, "realized_pnl": cumulative_realized_pnl,
            }
            continue

        whales = identify_polymarket_whales(
            train_df,
            method=method,
            role=role,
            min_trades=min_trades,
            min_volume=min_volume,
            resolution_winners=resolution_winners,
            price_snapshot=price_snapshot,
            return_lookback_months=return_lookback_months,
            return_top_pct=return_top_pct,
        )
        month_whale_counts.append(len(whales))

        # 1. Close positions that resolve or end today
        positions_to_close = []
        for market_id, pos in open_positions.items():
            res_data = resolutions.get(market_id, {})
            res_date = res_data.get("resolution_date")

            if res_date is not None and pd.to_datetime(res_date).date() <= current_date.date():
                positions_to_close.append((market_id, "resolution", res_date))
                continue

            market_close_date = res_data.get("market_close_date")
            if market_close_date is not None and pd.to_datetime(market_close_date).date() <= current_date.date():
                positions_to_close.append((market_id, "market_close", market_close_date))

        for market_id, close_reason, close_date in positions_to_close:
            pos = open_positions.pop(market_id)
            res_data = resolutions.get(market_id, {})

            resolution = res_data.get("resolution")
            exit_date = close_date

            if close_reason == "resolution":
                exit_price = resolution
            else:
                market_key = str(market_id).replace(".0", "")
                clob_price = clob_cache.price_at_or_before(market_key, close_date)
                if clob_price is None:
                    raise ValueError(f"No CLOB price history for market {market_key} at {close_date}.")
                exit_price = clob_price

            if pos.direction == "buy":
                gross_pnl = (exit_price - pos.entry_price) * pos.size
            else:
                gross_pnl = (pos.entry_price - exit_price) * pos.size

            entry_cost = cost_model.calculate_entry_price(
                pos.entry_price, pos.size, liquidity=10000
            )
            exit_proceeds = cost_model.calculate_exit_price(
                exit_price, pos.size, liquidity=10000
            )

            if pos.direction == "buy":
                net_pnl = (exit_proceeds - entry_cost) * pos.size
            else:
                net_pnl = (entry_cost - exit_proceeds) * pos.size

            capital_deployed -= pos.size
            cumulative_realized_pnl += net_pnl

            exit_dt = pd.to_datetime(exit_date)
            entry_dt = pos.entry_date
            if hasattr(exit_dt, 'tz') and exit_dt.tz is not None:
                exit_dt = exit_dt.tz_localize(None)
            if hasattr(entry_dt, 'tz') and entry_dt.tz is not None:
                entry_dt = entry_dt.tz_localize(None)
            holding_days = (exit_dt - entry_dt).days

            closed_trades.append({
                "market_id": market_id,
                "entry_date": pos.entry_date,
                "exit_date": exit_date,
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "resolution": resolution,
                "position_size": pos.size,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "holding_days": holding_days,
                "whale_address": pos.whale_address,
            })

        # 2. Process new signals for today using today's whale set
        if len(whales) > 0:
            day_idx = trades_by_date.get(current_date, [])
            if len(day_idx) > 0:
                day_trades = trades_df.iloc[day_idx]
                today_trades = day_trades[
                    (day_trades[trader_col].isin(whales)) &
                    (day_trades["usd_amount"] >= min_usd)
                ]
            else:
                today_trades = trades_df.iloc[0:0]

            for _, signal in today_trades.iterrows():
                market_id = str(signal["market_id"])
                market_key = market_id.replace(".0", "")

                if market_id in open_positions:
                    continue

                res_data = resolutions.get(market_id)
                if res_data is None:
                    continue

                if resolved_only and not res_data.get("is_resolved"):
                    continue

                if resolved_only and res_data.get("resolution") is None:
                    continue

                res_date = res_data.get("resolution_date")
                if res_date is not None and pd.to_datetime(res_date).date() <= current_date.date():
                    continue

                market_close_date = res_data.get("market_close_date")
                if market_close_date is not None and pd.to_datetime(market_close_date).date() <= current_date.date():
                    continue

                direction = str(signal[direction_col]).lower()
                entry_price = signal["price"]

                if entry_price < 0.02 or entry_price > 0.98:
                    continue

                # Require CLOB price history at or before entry date (strict CLOB-only)
                if clob_cache.price_at_or_before(market_key, current_date) is None:
                    continue

                # Liquidity-based position cap (uses cumulative liquidity proxy)
                position_size_eff = position_size
                if max_position_liquidity_pct is not None:
                    liq_val = signal.get("market_cum_liquidity_usd", None)
                    if pd.notna(liq_val):
                        try:
                            cap = float(liq_val) * float(max_position_liquidity_pct)
                        except Exception:
                            cap = None
                        if cap is not None:
                            if cap <= 0:
                                continue
                            position_size_eff = min(position_size_eff, cap)

                if max_capital is not None and capital_deployed + position_size_eff > max_capital:
                    signals_skipped += 1
                    continue

                capital_deployed += position_size_eff
                peak_capital_deployed = max(peak_capital_deployed, capital_deployed)
                month_signals += 1

                open_positions[market_id] = Position(
                    market_id=market_id,
                    entry_date=pd.to_datetime(current_date),
                    entry_price=entry_price,
                    direction=direction,
                    size=position_size_eff,
                    whale_address=signal[trader_col],
                    expected_exit_date=pd.to_datetime(res_date) if res_date else None,
                    resolution=res_data["resolution"],
                )

        # 3. Record daily state
        unrealized_pnl = 0.0
        for market_id, pos in open_positions.items():
            market_key = str(market_id).replace(".0", "")
            clob_price = clob_cache.price_at_or_before(market_key, current_date)
            if clob_price is None:
                raise ValueError(f"No CLOB price history for market {market_key} at {current_date}.")
            current_price = clob_price
            if pos.direction == "buy":
                unrealized_pnl += (current_price - pos.entry_price) * pos.size
            else:
                unrealized_pnl += (pos.entry_price - current_price) * pos.size
        equity = starting_capital + cumulative_realized_pnl + unrealized_pnl
        daily_data[current_date] = {
            "equity": equity,
            "open_positions": len(open_positions),
            "capital_deployed": capital_deployed,
            "realized_pnl": cumulative_realized_pnl,
        }

    # Print final month summary
    if current_month is not None:
        month_closed = closed_trades[month_closed_start:]
        month_closed_count = len(month_closed)
        month_wins = sum(1 for t in month_closed if t["net_pnl"] > 0)
        month_win_rate = (month_wins / month_closed_count * 100) if month_closed_count > 0 else 0
        month_net_pnl = sum(t["net_pnl"] for t in month_closed)
        avg_whales = np.mean(month_whale_counts) if month_whale_counts else 0

        print(
            f"{current_month!s:<10} | {avg_whales:>10.0f} | {month_signals:>7} | "
            f"{month_closed_count:>6} | {month_win_rate:>5.1f}% | "
            f"${month_net_pnl:>9,.0f} | ${capital_deployed:>9,.0f}"
        )

    # Close any remaining open positions at final prices (mark-to-market)
    final_date = test_dates[-1] if test_dates else pd.Timestamp.now()

    for market_id, pos in list(open_positions.items()):
        res_data = resolutions.get(market_id, {})
        market_key = str(market_id).replace(".0", "")
        clob_price = clob_cache.price_at_or_before(market_key, final_date)
        if clob_price is None:
            raise ValueError(f"No CLOB price history for market {market_key} at {final_date}.")
        exit_price = clob_price

        if pos.direction == "buy":
            gross_pnl = (exit_price - pos.entry_price) * pos.size
        else:
            gross_pnl = (pos.entry_price - exit_price) * pos.size

        net_pnl = gross_pnl * 0.97  # Rough cost estimate

        closed_trades.append({
            "market_id": market_id,
            "entry_date": pos.entry_date,
            "exit_date": final_date,
            "direction": pos.direction,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "resolution": exit_price,
            "position_size": pos.size,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "holding_days": (final_date - pos.entry_date).days,
            "whale_address": pos.whale_address,
            "status": "open",
        })

    if not closed_trades:
        price_store.close()
        return None

    # Print summary footer
    print("-" * 95)
    print(f"Peak Capital Deployed: ${peak_capital_deployed:,.0f}")
    print(f"Signals Skipped (no capital): {signals_skipped:,}")
    print(f"Positions still open: {len(open_positions):,}")

    # Build results
    trades_result_df = pd.DataFrame(closed_trades)
    trades_result_df["status"] = trades_result_df.get("status", "closed")
    trades_result_df.loc[trades_result_df["status"].isna(), "status"] = "closed"

    closed_only = trades_result_df[trades_result_df["status"] == "closed"]

    total_trades = len(closed_only)
    unique_markets = closed_only["market_id"].nunique()
    winning_trades = len(closed_only[closed_only["net_pnl"] > 0])
    losing_trades = len(closed_only[closed_only["net_pnl"] <= 0])
    win_rate = winning_trades / total_trades if total_trades > 0 else 0

    total_gross_pnl = closed_only["gross_pnl"].sum()
    total_net_pnl = closed_only["net_pnl"].sum()
    total_costs = total_gross_pnl - total_net_pnl
    avg_pnl_per_trade = total_net_pnl / total_trades if total_trades > 0 else 0
    avg_holding_days = closed_only["holding_days"].mean() if total_trades > 0 else 0

    daily_equity = pd.Series({k: v["equity"] for k, v in daily_data.items()}).sort_index()
    daily_positions = pd.Series({k: v["open_positions"] for k, v in daily_data.items()})

    max_concurrent = daily_positions.max() if len(daily_positions) > 0 else 0
    avg_concurrent = daily_positions.mean() if len(daily_positions) > 0 else 0

    daily_returns = daily_equity.pct_change().dropna()

    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
    else:
        sharpe_ratio = 0

    if len(daily_equity) > 0:
        rolling_max = daily_equity.cummax()
        drawdown = rolling_max - daily_equity
        max_drawdown = drawdown.max()
        max_drawdown_pct = (drawdown / rolling_max).max() * 100
    else:
        max_drawdown = 0
        max_drawdown_pct = 0

    roi_pct = (total_net_pnl / starting_capital) * 100

    result = RealisticBacktestResult(
        strategy_name=f"Polymarket Whales Rolling Realistic ({method}, {lookback_months}m lookback, daily re-id)",
        total_trades=total_trades,
        unique_markets=unique_markets,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        total_gross_pnl=total_gross_pnl,
        total_net_pnl=total_net_pnl,
        total_costs=total_costs,
        avg_pnl_per_trade=avg_pnl_per_trade,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        roi_pct=roi_pct,
        position_size=position_size,
        starting_capital=starting_capital,
        max_concurrent_positions=int(max_concurrent),
        avg_concurrent_positions=avg_concurrent,
        avg_holding_days=avg_holding_days,
        peak_capital_deployed=peak_capital_deployed,
        trades_df=trades_result_df,
        daily_equity=daily_equity,
        signals_skipped_no_capital=signals_skipped,
    )
    price_store.close()
    return result


def print_realistic_result(result: RealisticBacktestResult) -> None:
    """Print formatted backtest results."""
    trades_df = result.trades_df if isinstance(result.trades_df, pd.DataFrame) else None
    realized_pnl = result.total_net_pnl
    unrealized_pnl = 0.0
    open_positions = 0
    if trades_df is not None and not trades_df.empty and "net_pnl" in trades_df.columns:
        status_col = trades_df.get("status")
        if status_col is not None:
            open_mask = status_col == "open"
            open_positions = int(open_mask.sum())
            closed_mask = status_col == "closed"
            realized_pnl = float(trades_df.loc[closed_mask, "net_pnl"].sum())
            unrealized_pnl = float(trades_df.loc[open_mask, "net_pnl"].sum())

    print(f"\n{'='*65}")
    print(f"STRATEGY: {result.strategy_name}")
    print(f"{'='*65}")
    print(f"Total Trades (closed):    {result.total_trades:,}")
    print(f"Unique Markets:           {result.unique_markets:,}")
    print(f"Win Rate:                 {result.win_rate*100:.1f}%")
    print()
    print(f"Gross P&L:                ${result.total_gross_pnl:>12,.2f}")
    print(f"Trading Costs:            ${result.total_costs:>12,.2f}")
    print(f"Net P&L:                  ${result.total_net_pnl:>12,.2f}")
    print(f"Realized P&L:             ${realized_pnl:>12,.2f}")
    print(f"Unrealized P&L:           ${unrealized_pnl:>12,.2f}")
    print(f"Open Positions:           {open_positions:>12,}")
    print(f"ROI:                      {result.roi_pct:>12.2f}%")
    print()
    print(f"Avg P&L per Trade:        ${result.avg_pnl_per_trade:>12,.2f}")
    print(f"Avg Holding Period:       {result.avg_holding_days:>12.1f} days")
    print()
    print(f"Sharpe Ratio:             {result.sharpe_ratio:>12.2f}")
    print(f"Max Drawdown:             ${result.max_drawdown:>12,.2f}")
    print(f"Max Drawdown %:           {result.max_drawdown_pct:>12.2f}%")
    print()
    print("--- Capital Utilization ---")
    print(f"Starting Capital:         ${result.starting_capital:>12,.0f}")
    print(f"Position Size:            ${result.position_size:>12,.0f}")
    print(f"Peak Capital Deployed:    ${result.peak_capital_deployed:>12,.0f}")
    print(f"Max Concurrent Positions: {result.max_concurrent_positions:>12,}")
    print(f"Avg Concurrent Positions: {result.avg_concurrent_positions:>12.1f}")
    print(f"Signals Skipped (no $):   {result.signals_skipped_no_capital:>12,}")

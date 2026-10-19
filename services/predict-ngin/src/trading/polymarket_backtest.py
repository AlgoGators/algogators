"""
Polymarket CLOB backtest engine.

Converts external signals into simulated trades using CLOB price history and
the shared cost model. Signal generation is intentionally separate.
"""

from dataclasses import dataclass, replace
from typing import Optional, Dict, Any, List

import numpy as np
import pandas as pd

from .costs import CostModel, DEFAULT_COST_MODEL
from .portfolio import PortfolioConstraints, PositionSizer, PortfolioState
from .data_modules.database import PredictionMarketDB
from .reporting import (
    compute_daily_returns,
    build_run_summary_from_trades,
    RunSummary,
)


@dataclass
class PolymarketBacktestConfig:
    """Configuration for Polymarket CLOB backtests."""

    strategy_name: str = "Polymarket Strategy"
    position_size: float = 100.0
    starting_capital: float = 10000.0
    fill_latency_seconds: int = 60
    max_holding_seconds: Optional[int] = None
    include_open: bool = True
    enforce_one_position_per_market: bool = True
    min_liquidity: float = 0.0
    min_volume: float = 0.0
    # Resolution-aware exit settings
    resolution_aware: bool = True
    forced_exit_hours_before_resolution: int = 24
    near_resolution_slippage_multiplier: float = 2.0


@dataclass
class PolymarketBacktestResult:
    """Result bundle for a Polymarket backtest."""

    strategy_name: str
    trades_df: pd.DataFrame
    daily_returns: pd.Series
    summary: RunSummary


class ClobPriceStore:
    """Thin cache over Polymarket CLOB price history (parquet or SQLite)."""

    def __init__(
        self,
        db: Optional[PredictionMarketDB] = None,
        db_path: str = "data/prediction_markets.db",
        use_parquet: Optional[bool] = None,
        parquet_dir: str = "data/polymarket/prices",
    ):
        self._own_db = db is None
        self.db = db or PredictionMarketDB(db_path)
        self._market_cache: Dict[str, Dict[str, Any]] = {}
        self._price_cache: Dict[tuple[str, str], pd.DataFrame] = {}

        # Auto-detect parquet price store
        from pathlib import Path
        pq_path = Path(parquet_dir)
        if use_parquet is None:
            use_parquet = False
            if str(db_path) == "data/prediction_markets.db":
                use_parquet = pq_path.exists() and any(pq_path.glob("prices_*.parquet"))
        self._use_parquet = use_parquet
        self._parquet_store = None
        if self._use_parquet:
            from .data_modules.parquet_store import PriceStore
            self._parquet_store = PriceStore(parquet_dir)

    def close(self) -> None:
        if self._own_db and self.db is not None:
            self.db.close()

    def get_market_metadata(self, market_id: str) -> Dict[str, Any]:
        if market_id in self._market_cache:
            return self._market_cache[market_id]

        row = self.db.query(
            "SELECT * FROM polymarket_markets WHERE id = ?",
            (str(market_id),),
        )
        if row.empty:
            meta: Dict[str, Any] = {}
        else:
            meta = row.iloc[0].to_dict()

        self._market_cache[market_id] = meta
        return meta

    def get_price_history(self, market_id: str, outcome: str) -> pd.DataFrame:
        cache_key = (str(market_id), outcome)
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]

        if self._use_parquet and self._parquet_store is not None:
            df = self._parquet_store.get_price_history(str(market_id), outcome)
        else:
            df = self.db.get_price_history(str(market_id), outcome)
            if not df.empty:
                df = df.copy()
                df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
                df["price"] = pd.to_numeric(df["price"], errors="coerce")
                df = df.dropna(subset=["timestamp", "price"]).sort_values("timestamp")

        self._price_cache[cache_key] = df
        return df

    def price_at_or_after(
        self,
        market_id: str,
        outcome: str,
        timestamp: int,
    ) -> Optional[Dict[str, Any]]:
        if self._use_parquet and self._parquet_store is not None:
            return self._parquet_store.price_at_or_after(market_id, outcome, timestamp)

        df = self.get_price_history(market_id, outcome)
        if df.empty:
            return None

        timestamps = df["timestamp"].to_numpy()
        idx = int(np.searchsorted(timestamps, timestamp, side="left"))
        if idx >= len(df):
            return None

        row = df.iloc[idx]
        return {
            "timestamp": int(row["timestamp"]),
            "price": float(row["price"]),
        }

    def last_price(self, market_id: str, outcome: str) -> Optional[Dict[str, Any]]:
        if self._use_parquet and self._parquet_store is not None:
            return self._parquet_store.last_price(market_id, outcome)

        df = self.get_price_history(market_id, outcome)
        if df.empty:
            return None
        row = df.iloc[-1]
        return {
            "timestamp": int(row["timestamp"]),
            "price": float(row["price"]),
        }


def _to_timestamp_seconds(value: Any) -> Optional[int]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            num = int(text)
            if num > 10_000_000_000:  # ms
                return int(num / 1000)
            return num
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:  # ms
            return int(value / 1000)
        return int(value)
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return int(ts.timestamp())


def _normalize_text(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip().upper()
    if text in ("", "NAN", "NONE"):
        return None
    return text


def _safe_float(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _infer_resolution_from_prices(
    yes_price: Optional[float],
    no_price: Optional[float],
) -> Optional[str]:
    if yes_price is None or no_price is None:
        return None
    if yes_price >= 0.95 and no_price <= 0.05:
        return "YES"
    if no_price >= 0.95 and yes_price <= 0.05:
        return "NO"
    return None


def _resolution_from_meta(meta: Dict[str, Any]) -> tuple[Optional[float], Optional[int], Optional[str]]:
    if not meta:
        return None, None, None

    outcome = _normalize_text(meta.get("resolution_outcome"))
    if outcome not in ("YES", "NO"):
        yes_price = _safe_float(meta.get("resolution_price_yes"))
        no_price = _safe_float(meta.get("resolution_price_no"))
        outcome = _infer_resolution_from_prices(yes_price, no_price)

    if outcome not in ("YES", "NO"):
        return None, None, None

    resolution = 1.0 if outcome == "YES" else 0.0
    resolution_ts = _to_timestamp_seconds(meta.get("closed_time"))
    if resolution_ts is None:
        resolution_ts = _to_timestamp_seconds(meta.get("end_date") or meta.get("endDate"))

    return resolution, resolution_ts, outcome


def _normalize_outcome_side(row: pd.Series) -> tuple[str, str]:
    outcome = _normalize_text(row.get("outcome"))
    side = _normalize_text(row.get("side"))

    if outcome in ("BUY", "SELL") and side in ("YES", "NO"):
        outcome, side = side, outcome

    if outcome not in ("YES", "NO"):
        if side in ("YES", "NO"):
            outcome = side
            side = "BUY"
        else:
            outcome = "YES"

    if side not in ("BUY", "SELL"):
        side = "BUY"

    return outcome, side


def _normalize_signals(signals: pd.DataFrame) -> pd.DataFrame:
    if signals is None or signals.empty:
        return pd.DataFrame()

    df = signals.copy()

    if "market_id" not in df.columns:
        for alt in ("market", "marketId", "id"):
            if alt in df.columns:
                df["market_id"] = df[alt]
                break

    if "signal_time" not in df.columns:
        for alt in ("timestamp", "datetime", "time", "as_of"):
            if alt in df.columns:
                df["signal_time"] = df[alt]
                break

    if "outcome" not in df.columns:
        df["outcome"] = None

    if "side" not in df.columns:
        df["side"] = None

    if "size" not in df.columns:
        df["size"] = np.nan

    required = ["market_id", "signal_time", "outcome", "side"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Signals missing required columns: {missing}")

    df[["outcome", "side"]] = df.apply(
        _normalize_outcome_side,
        axis=1,
        result_type="expand",
    )

    df = df[df["outcome"].isin(["YES", "NO"])]
    df = df[df["side"].isin(["BUY", "SELL"])]

    df["signal_ts"] = df["signal_time"].apply(_to_timestamp_seconds)
    df = df.dropna(subset=["signal_ts"])
    df = df.sort_values("signal_ts")

    return df


def _effective_entry_price(
    cost_model: CostModel,
    base_price: float,
    trade_size: float,
    liquidity: float,
    side: str,
) -> float:
    if side == "BUY":
        return cost_model.calculate_entry_price(base_price, trade_size, liquidity)
    return cost_model.calculate_exit_price(base_price, trade_size, liquidity)


def _effective_exit_price(
    cost_model: CostModel,
    base_price: float,
    trade_size: float,
    liquidity: float,
    side: str,
) -> float:
    if side == "BUY":
        return cost_model.calculate_exit_price(base_price, trade_size, liquidity)
    return cost_model.calculate_entry_price(base_price, trade_size, liquidity)


def run_polymarket_backtest(
    signals: pd.DataFrame,
    price_store: Optional[ClobPriceStore] = None,
    config: Optional[PolymarketBacktestConfig] = None,
    cost_model: Optional[CostModel] = None,
    portfolio_constraints: Optional[PortfolioConstraints] = None,
    position_sizer: Optional[PositionSizer] = None,
    execution_engine=None,
) -> PolymarketBacktestResult:
    """
    Convert signals into simulated Polymarket trades using CLOB price history.

    Signals must include:
      - market_id
      - signal_time (timestamp/datetime)
      - outcome (YES/NO)
    Optional:
      - side (BUY/SELL) default BUY
      - size (position size)
      - exit_time (timestamp/datetime)
      - hold_seconds (override max_holding_seconds)
      - execution_engine: TradeBasedExecutionEngine for realistic tape-walking fills.
        When provided, entry/exit prices come from the trade tape (VWAP) instead of
        price_store lookups + flat cost model.
    """
    if config is None:
        config = PolymarketBacktestConfig()
    if cost_model is None:
        cost_model = DEFAULT_COST_MODEL
    if price_store is None:
        price_store = ClobPriceStore()

    use_tape = execution_engine is not None

    signals_df = _normalize_signals(signals)
    if signals_df.empty:
        empty_df = pd.DataFrame()
        summary = build_run_summary_from_trades(
            strategy_name=config.strategy_name,
            trades_df=empty_df,
            run_type="polymarket_backtest",
            starting_capital=config.starting_capital,
        )
        return PolymarketBacktestResult(
            strategy_name=config.strategy_name,
            trades_df=empty_df,
            daily_returns=pd.Series(dtype=float),
            summary=summary,
        )

    if portfolio_constraints is None:
        portfolio_constraints = PortfolioConstraints(
            min_liquidity=config.min_liquidity,
            min_volume=config.min_volume,
        )

    if position_sizer is None:
        position_sizer = PositionSizer(base_position_size=config.position_size)

    portfolio = PortfolioState(
        constraints=portfolio_constraints,
        total_capital=config.starting_capital,
    )

    traded_markets: set[str] = set()
    trades: List[Dict[str, Any]] = []

    for _, signal in signals_df.iterrows():
        market_id = str(signal["market_id"])
        outcome = signal["outcome"]
        side = signal["side"]

        signal_ts = int(signal["signal_ts"])
        entry_ts = signal_ts + int(config.fill_latency_seconds)
        signal_time = pd.to_datetime(signal_ts, unit="s")

        if config.enforce_one_position_per_market and market_id in traded_markets:
            continue

        meta = price_store.get_market_metadata(market_id)
        liquidity = float(meta.get("liquidity", 0) or 0)
        volume = float(meta.get("volume_24hr", 0) or meta.get("volume", 0) or 0)
        end_date = meta.get("end_date", None)
        resolution_value, resolution_ts, resolution_outcome = _resolution_from_meta(meta)

        if not portfolio_constraints.market_allowed("", liquidity, volume):
            continue

        portfolio.close_positions_through(pd.to_datetime(signal_ts, unit="s"))

        signal_size = signal.get("size", np.nan)
        if pd.isna(signal_size):
            sizer = position_sizer
        else:
            sizer = replace(position_sizer, base_position_size=float(signal_size))

        effective_size = sizer.size_for_market(
            liquidity=liquidity,
            capital_available=portfolio.available_capital(),
            cap_per_market=portfolio_constraints.max_capital_per_market,
        )
        if effective_size <= 0:
            continue

        if not portfolio.can_open("", effective_size):
            continue

        # ---- Entry execution ----
        if use_tape:
            fill = execution_engine.execute_order(
                market_id, outcome, side, effective_size,
                signal_ts, config.fill_latency_seconds,
            )
            if not fill.filled:
                continue
            entry_price = fill.fill_price
            entry_fill_ts = fill.fill_timestamp
            effective_size = fill.fill_size  # may be partial
            entry_cost_price = entry_price   # costs already baked into VWAP + spread + slippage
        else:
            price_point = price_store.price_at_or_after(market_id, outcome, entry_ts)
            if price_point is None:
                continue
            entry_fill_ts = int(price_point["timestamp"])
            entry_price = price_point["price"]
            if entry_price <= 0 or entry_price >= 1:
                continue
            entry_cost_price = _effective_entry_price(
                cost_model, entry_price, effective_size, liquidity, side,
            )

        entry_time = pd.to_datetime(entry_fill_ts, unit="s")

        if resolution_ts is not None and entry_fill_ts >= resolution_ts:
            continue

        # ---- Determine exit timing ----
        exit_ts_source = None
        exit_ts = _to_timestamp_seconds(signal.get("exit_time", None))
        if exit_ts is not None:
            exit_ts_source = "signal"
        if exit_ts is None:
            hold_seconds = signal.get("hold_seconds", None)
            if pd.isna(hold_seconds):
                hold_seconds = None
            if hold_seconds is not None:
                exit_ts = entry_fill_ts + int(hold_seconds)
                exit_ts_source = "hold"
            elif config.max_holding_seconds is not None:
                exit_ts = entry_fill_ts + int(config.max_holding_seconds)
                exit_ts_source = "max_holding"
            elif end_date:
                exit_ts = _to_timestamp_seconds(end_date)
                exit_ts_source = "end_date"

        status = "closed"
        exit_price = None
        exit_time = None
        exit_fill_ts: Optional[int] = None
        exit_reason = exit_ts_source or "exit_time"

        # ---- Resolution-aware exit logic ----
        use_resolution = False
        if resolution_value is not None:
            if exit_ts_source in (None, "end_date"):
                use_resolution = True
            elif resolution_ts is not None:
                use_resolution = exit_ts >= resolution_ts

        # Forced pre-resolution exit: if near resolution and config says so
        forced_pre_resolution = False
        if (
            config.resolution_aware
            and resolution_ts is not None
            and not use_resolution
            and exit_ts is not None
        ):
            hours_before = (resolution_ts - exit_ts) / 3600.0
            if 0 < hours_before <= config.forced_exit_hours_before_resolution:
                forced_pre_resolution = True

        if use_resolution:
            exit_reason = "resolution"
            exit_price = resolution_value if outcome == "YES" else 1 - resolution_value
            if resolution_ts is not None:
                exit_fill_ts = int(resolution_ts)
                exit_time = pd.to_datetime(exit_fill_ts, unit="s")
            else:
                last_point = price_store.last_price(market_id, outcome)
                if last_point and int(last_point["timestamp"]) >= entry_fill_ts:
                    exit_fill_ts = int(last_point["timestamp"])
                    exit_time = pd.to_datetime(exit_fill_ts, unit="s")
                else:
                    exit_fill_ts = entry_fill_ts
                    exit_time = entry_time
        else:
            # ---- Exit execution ----
            if use_tape and exit_ts is not None:
                exit_fill = execution_engine.execute_order(
                    market_id, outcome,
                    "SELL" if side == "BUY" else "BUY",
                    effective_size, exit_ts, 0,
                )
                if exit_fill.filled:
                    exit_fill_ts = exit_fill.fill_timestamp
                    exit_price = exit_fill.fill_price
                    exit_time = pd.to_datetime(exit_fill_ts, unit="s")
                else:
                    # Fall back to price_store
                    exit_point = price_store.price_at_or_after(market_id, outcome, exit_ts)
                    if exit_point is None:
                        last_point = price_store.last_price(market_id, outcome)
                        if last_point is None:
                            continue
                        if not config.include_open:
                            continue
                        exit_fill_ts = int(last_point["timestamp"])
                        exit_price = last_point["price"]
                        exit_time = pd.to_datetime(exit_fill_ts, unit="s")
                        status = "open"
                        exit_reason = "open"
                    else:
                        exit_fill_ts = int(exit_point["timestamp"])
                        exit_price = exit_point["price"]
                        exit_time = pd.to_datetime(exit_fill_ts, unit="s")
            else:
                if exit_ts is not None:
                    exit_point = price_store.price_at_or_after(market_id, outcome, exit_ts)
                else:
                    exit_point = None

                if exit_point is None:
                    last_point = price_store.last_price(market_id, outcome)
                    if last_point is None:
                        continue
                    if not config.include_open:
                        continue
                    exit_fill_ts = int(last_point["timestamp"])
                    exit_price = last_point["price"]
                    exit_time = pd.to_datetime(exit_fill_ts, unit="s")
                    status = "open"
                    exit_reason = "open"
                else:
                    exit_fill_ts = int(exit_point["timestamp"])
                    exit_price = exit_point["price"]
                    exit_time = pd.to_datetime(exit_fill_ts, unit="s")

        if forced_pre_resolution:
            exit_reason = "forced_pre_resolution"

        direction = 1 if side == "BUY" else -1

        exit_cost_price = None
        if status == "closed":
            if exit_reason == "resolution":
                exit_cost_price = exit_price  # binary payoff, no cost
            elif use_tape:
                exit_cost_price = exit_price  # costs baked into tape VWAP
                # Apply extra slippage near resolution
                if forced_pre_resolution:
                    extra = abs(exit_price) * (config.near_resolution_slippage_multiplier - 1) * 0.01
                    if side == "BUY":
                        exit_cost_price = exit_price - extra
                    else:
                        exit_cost_price = exit_price + extra
            else:
                exit_cost_price = _effective_exit_price(
                    cost_model, exit_price, effective_size, liquidity, side,
                )
                # Apply extra slippage near resolution
                if forced_pre_resolution:
                    extra = abs(exit_price) * (config.near_resolution_slippage_multiplier - 1) * 0.01
                    if side == "BUY":
                        exit_cost_price -= extra
                    else:
                        exit_cost_price += extra
            net_pnl = direction * (exit_cost_price - entry_cost_price) * effective_size
        else:
            net_pnl = direction * (exit_price - entry_cost_price) * effective_size

        gross_pnl = direction * (exit_price - entry_price) * effective_size

        portfolio.open_position(
            contract_id=market_id,
            category="",
            size=effective_size,
            exit_time=exit_time,
        )
        traded_markets.add(market_id)

        trades.append({
            "market_id": market_id,
            "contract_id": market_id,
            "outcome": outcome,
            "side": side,
            "status": status,
            "exit_reason": exit_reason,
            "resolution_outcome": resolution_outcome,
            "resolution_ts": resolution_ts,
            "signal_time": signal_time,
            "signal_ts": signal_ts,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "datetime": entry_time,
            "date": entry_time.date(),
            "entry_fill_ts": entry_fill_ts,
            "exit_fill_ts": exit_fill_ts,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_price_effective": entry_cost_price,
            "exit_price_effective": exit_cost_price,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "position_size": effective_size,
            "liquidity": liquidity,
            "volume_24hr": volume,
        })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        summary = build_run_summary_from_trades(
            strategy_name=config.strategy_name,
            trades_df=trades_df,
            run_type="polymarket_backtest",
            starting_capital=config.starting_capital,
        )
        return PolymarketBacktestResult(
            strategy_name=config.strategy_name,
            trades_df=trades_df,
            daily_returns=pd.Series(dtype=float),
            summary=summary,
        )

    trades_df = trades_df.sort_values("entry_time")
    trades_df["cumulative_pnl"] = trades_df["net_pnl"].cumsum()

    daily_returns = compute_daily_returns(
        trades_df=trades_df,
        starting_capital=config.starting_capital,
    )
    summary = build_run_summary_from_trades(
        strategy_name=config.strategy_name,
        trades_df=trades_df,
        run_type="polymarket_backtest",
        starting_capital=config.starting_capital,
        position_size=config.position_size,
    )

    return PolymarketBacktestResult(
        strategy_name=config.strategy_name,
        trades_df=trades_df,
        daily_returns=daily_returns,
        summary=summary,
    )


def print_polymarket_result(result: PolymarketBacktestResult) -> None:
    """Print a concise summary of Polymarket backtest results."""
    metrics = result.summary.metrics
    print(f"\n{'='*60}")
    print(f"STRATEGY: {result.strategy_name}")
    print(f"{'='*60}")
    print(f"Unique Markets Traded:  {metrics.unique_markets:,}")
    print(f"Total Trades:           {metrics.total_trades:,}")
    print(f"Win Rate:               {metrics.win_rate*100:.1f}%")
    print()
    print(f"Gross P&L:              ${metrics.total_gross_pnl:>12,.2f}")
    print(f"Trading Costs:          ${metrics.total_costs:>12,.2f}")
    print(f"Net P&L:                ${metrics.total_net_pnl:>12,.2f}")
    print(f"ROI:                    {metrics.roi_pct:>12.2f}%")
    print()
    print(f"Sharpe Ratio:           {metrics.sharpe_ratio:>12.2f}")
    print(f"Max Drawdown:           ${metrics.max_drawdown:>12,.2f}")
    print()
    print("--- Capital Allocation ---")
    print(f"Avg Position Size:      ${metrics.avg_position_size:>12,.0f}")
    print(f"Max Position Size:      ${metrics.max_position_size:>12,.0f}")
    print(f"Max Concurrent Pos:      {metrics.max_concurrent_positions:>12,}")
    print(f"Avg Concurrent Pos:      {metrics.avg_concurrent_positions:>12.1f}")
    if metrics.max_concurrent_capital > 0:
        print(f"Peak Capital Required:  ${metrics.max_concurrent_capital:>12,.0f}")
    if metrics.open_positions > 0:
        print()
        print("--- Open Positions ---")
        print(f"Open Positions:         {metrics.open_positions:>12,}")
        print(f"Capital in Open:        ${metrics.open_capital:>12,.0f}")
        print(f"Unrealized P&L:         ${metrics.open_unrealized_pnl:>12,.2f}")

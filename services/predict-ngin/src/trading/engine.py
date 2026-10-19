"""
Trading Engine

Event-driven trading engine that processes signals from strategies,
applies risk management, and tracks execution and performance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Iterator, Iterable, Union, Type
from enum import Enum
import heapq
import pandas as pd
import numpy as np

from .strategy import Strategy, StrategyManager, Signal, SignalType
from .risk import RiskLimits, RiskModule
from .costs import CostModel, DEFAULT_COST_MODEL


class EventType(Enum):
    """Types of events in the engine."""

    PRICE_UPDATE = "price_update"
    TRADE = "trade"
    SIGNAL = "signal"
    FILL = "fill"
    RESOLUTION = "resolution"
    TIMER = "timer"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


@dataclass(order=True)
class Event:
    """An event in the trading engine."""

    timestamp: int  # Unix timestamp for ordering
    event_type: EventType = field(compare=False)
    data: Dict[str, Any] = field(compare=False, default_factory=dict)


@dataclass
class TradeRecord:
    """Record of an executed trade."""

    trade_id: str
    market_id: str
    strategy: str
    side: str
    outcome: str
    entry_time: datetime
    entry_price: float
    size: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    fees: float = 0.0
    status: str = "open"  # open, closed, stopped

    @property
    def net_pnl(self) -> float:
        return self.pnl - self.fees


@dataclass
class EngineConfig:
    """Configuration for the trading engine."""

    starting_capital: float = 10000.0
    fill_latency_ms: int = 100  # Simulated fill latency
    enable_stop_loss: bool = True
    stop_loss_pct: float = 0.20  # 20% stop loss
    enable_take_profit: bool = False
    take_profit_pct: float = 0.50  # 50% take profit
    max_holding_seconds: Optional[int] = None
    record_all_events: bool = False


class TradingEngine:
    """
    Event-driven trading engine.

    Processes market data, generates signals via strategies,
    applies risk management, and simulates trade execution.

    Usage:
        engine = TradingEngine(config=EngineConfig(starting_capital=10000))
        engine.add_strategy(MyStrategy())
        engine.run_backtest(price_data, resolution_data)
    """

    def __init__(
        self,
        config: Optional[EngineConfig] = None,
        risk_limits: Optional[RiskLimits] = None,
        risk_modules: Optional[Iterable[Union[RiskModule, str, Type[RiskModule]]]] = None,
        risk_profile: Optional[str] = None,
        cost_model: Optional[CostModel] = None,
    ):
        self.config = config or EngineConfig()
        self.cost_model = cost_model or DEFAULT_COST_MODEL
        self.strategy_manager = StrategyManager(
            capital=self.config.starting_capital,
            risk_limits=risk_limits,
            risk_modules=risk_modules,
            risk_profile=risk_profile,
        )

        # Event queue
        self._events: List[Event] = []
        self._current_time: int = 0

        # State
        self._trades: Dict[str, TradeRecord] = {}
        self._closed_trades: List[TradeRecord] = []
        self._prices: Dict[str, float] = {}  # market_id_outcome -> price
        self._market_metadata: Dict[str, Dict[str, Any]] = {}

        # Metrics
        self._event_log: List[Event] = []
        self._signal_count = 0
        self._trade_count = 0

    def add_strategy(self, strategy: Strategy):
        """Add a strategy to the engine."""
        self.strategy_manager.add_strategy(strategy)

    def push_event(self, event: Event):
        """Add an event to the queue."""
        heapq.heappush(self._events, event)

    def pop_event(self) -> Optional[Event]:
        """Get the next event from the queue."""
        if not self._events:
            return None
        return heapq.heappop(self._events)

    def process_event(self, event: Event):
        """Process a single event."""
        self._current_time = event.timestamp

        if self.config.record_all_events:
            self._event_log.append(event)

        if event.event_type == EventType.PRICE_UPDATE:
            self._handle_price_update(event)
        elif event.event_type == EventType.TRADE:
            self._handle_external_trade(event)
        elif event.event_type == EventType.SIGNAL:
            self._handle_signal(event)
        elif event.event_type == EventType.FILL:
            self._handle_fill(event)
        elif event.event_type == EventType.RESOLUTION:
            self._handle_resolution(event)
        elif event.event_type == EventType.STOP_LOSS:
            self._handle_stop_loss(event)
        elif event.event_type == EventType.TAKE_PROFIT:
            self._handle_take_profit(event)

    def _handle_price_update(self, event: Event):
        """Handle a price update event."""
        market_id = event.data.get("market_id")
        outcome = event.data.get("outcome", "YES")
        price = event.data.get("price")

        if not market_id or price is None:
            return

        key = f"{market_id}_{outcome}"
        old_price = self._prices.get(key)
        self._prices[key] = price

        # Update market metadata
        if market_id not in self._market_metadata:
            self._market_metadata[market_id] = {}
        self._market_metadata[market_id].update({
            "price": price,
            "liquidity": event.data.get("liquidity", 0),
            "volume_24h": event.data.get("volume_24h", 0),
        })

        # Calculate returns if we have history
        if old_price and old_price > 0:
            ret = (price - old_price) / old_price
            self._market_metadata[market_id]["last_return"] = ret

        # Update position prices
        self.strategy_manager.update_prices(self._prices)

        # Check stop loss / take profit
        self._check_exit_triggers(market_id, outcome, price)

        # Generate signals based on new data
        self._generate_signals_from_price(market_id, price)

    def _handle_external_trade(self, event: Event):
        """Handle an external trade event (e.g., on-chain or CLOB trade)."""
        # Build market data for strategy
        market_data = {
            "recent_trades": [event.data],
            "markets": list(self._market_metadata.values()),
        }

        timestamp = datetime.fromtimestamp(self._current_time)
        signals = self.strategy_manager.generate_signals(market_data, timestamp)

        # Queue signals with latency
        for signal in signals:
            if signal.risk_check_passed:
                self.push_event(Event(
                    timestamp=self._current_time + self.config.fill_latency_ms // 1000,
                    event_type=EventType.SIGNAL,
                    data={"signal": signal},
                ))
                self._signal_count += 1

    def _handle_signal(self, event: Event):
        """Handle a signal event - attempt to execute trade."""
        signal: Signal = event.data.get("signal")
        if not signal:
            return

        # Get current price
        key = f"{signal.market_id}_{signal.outcome}"
        current_price = self._prices.get(key)

        if current_price is None:
            return  # No price available

        # Calculate execution price with costs
        liquidity = signal.liquidity or self._market_metadata.get(
            signal.market_id, {}
        ).get("liquidity", 1000)

        if signal.signal_type == SignalType.BUY:
            exec_price = self.cost_model.calculate_entry_price(
                current_price, signal.effective_size, liquidity
            )
        else:
            exec_price = self.cost_model.calculate_exit_price(
                current_price, signal.effective_size, liquidity
            )

        # Create fill event
        self.push_event(Event(
            timestamp=self._current_time + 1,
            event_type=EventType.FILL,
            data={
                "signal": signal,
                "price": exec_price,
                "base_price": current_price,
            },
        ))

    def _handle_fill(self, event: Event):
        """Handle a fill event - record the trade."""
        signal: Signal = event.data.get("signal")
        price = event.data.get("price")

        if not signal or price is None:
            return

        trade_id = f"{signal.market_id}_{signal.outcome}_{self._current_time}"

        # Calculate fees
        fees = self.cost_model.calculate_one_way_cost(
            signal.effective_size,
            signal.liquidity or 1000,
        ) * signal.effective_size

        trade = TradeRecord(
            trade_id=trade_id,
            market_id=signal.market_id,
            strategy=signal.strategy_name,
            side=signal.side,
            outcome=signal.outcome,
            entry_time=datetime.fromtimestamp(self._current_time),
            entry_price=price,
            size=signal.effective_size,
            fees=fees,
            status="open",
        )

        self._trades[trade_id] = trade
        self._trade_count += 1

        # Record in risk manager
        self.strategy_manager.record_trade(signal, price)

        # Schedule max holding exit if configured
        if self.config.max_holding_seconds:
            self.push_event(Event(
                timestamp=self._current_time + self.config.max_holding_seconds,
                event_type=EventType.STOP_LOSS,  # Reuse stop loss handler
                data={
                    "trade_id": trade_id,
                    "reason": "max_holding_time",
                },
            ))

    def _handle_resolution(self, event: Event):
        """Handle a market resolution event."""
        market_id = event.data.get("market_id")
        resolution = event.data.get("resolution")  # 1.0 for YES, 0.0 for NO

        if market_id is None or resolution is None:
            return

        # Close all positions in this market
        to_close = [
            tid for tid, trade in self._trades.items()
            if trade.market_id == market_id and trade.status == "open"
        ]

        for trade_id in to_close:
            trade = self._trades[trade_id]

            # Exit price based on resolution
            if trade.outcome == "YES":
                exit_price = resolution
            else:
                exit_price = 1 - resolution

            self._close_trade(trade_id, exit_price, "resolved")

    def _handle_stop_loss(self, event: Event):
        """Handle stop loss trigger."""
        trade_id = event.data.get("trade_id")
        if not trade_id or trade_id not in self._trades:
            return

        trade = self._trades[trade_id]
        if trade.status != "open":
            return

        # Get current price
        key = f"{trade.market_id}_{trade.outcome}"
        price = self._prices.get(key, trade.entry_price)

        self._close_trade(trade_id, price, event.data.get("reason", "stop_loss"))

    def _handle_take_profit(self, event: Event):
        """Handle take profit trigger."""
        trade_id = event.data.get("trade_id")
        if not trade_id or trade_id not in self._trades:
            return

        trade = self._trades[trade_id]
        if trade.status != "open":
            return

        key = f"{trade.market_id}_{trade.outcome}"
        price = self._prices.get(key, trade.entry_price)

        self._close_trade(trade_id, price, "take_profit")

    def _check_exit_triggers(self, market_id: str, outcome: str, price: float):
        """Check stop loss and take profit triggers."""
        for trade_id, trade in self._trades.items():
            if trade.market_id != market_id or trade.outcome != outcome:
                continue
            if trade.status != "open":
                continue

            # Calculate P&L percentage
            direction = 1 if trade.side == "BUY" else -1
            pnl_pct = direction * (price - trade.entry_price) / trade.entry_price

            # Check stop loss
            if self.config.enable_stop_loss:
                if pnl_pct <= -self.config.stop_loss_pct:
                    self.push_event(Event(
                        timestamp=self._current_time + 1,
                        event_type=EventType.STOP_LOSS,
                        data={"trade_id": trade_id, "reason": "stop_loss"},
                    ))

            # Check take profit
            if self.config.enable_take_profit:
                if pnl_pct >= self.config.take_profit_pct:
                    self.push_event(Event(
                        timestamp=self._current_time + 1,
                        event_type=EventType.TAKE_PROFIT,
                        data={"trade_id": trade_id},
                    ))

    def _close_trade(self, trade_id: str, exit_price: float, reason: str):
        """Close a trade and calculate P&L."""
        if trade_id not in self._trades:
            return

        trade = self._trades.pop(trade_id)
        trade.exit_time = datetime.fromtimestamp(self._current_time)
        trade.exit_price = exit_price
        trade.status = reason

        # Calculate P&L
        direction = 1 if trade.side == "BUY" else -1
        trade.pnl = direction * (exit_price - trade.entry_price) * trade.size

        self._closed_trades.append(trade)

        # Update risk manager
        self.strategy_manager.close_position(
            trade_id, exit_price, trade.exit_time
        )

    def _generate_signals_from_price(self, market_id: str, price: float):
        """Generate signals based on price update."""
        # Build market data for strategies
        market = self._market_metadata.get(market_id, {})
        market["id"] = market_id
        market["price"] = price

        market_data = {
            "markets": [market],
            "recent_trades": [],
        }

        timestamp = datetime.fromtimestamp(self._current_time)
        signals = self.strategy_manager.generate_signals(market_data, timestamp)

        for signal in signals:
            if signal.risk_check_passed:
                self.push_event(Event(
                    timestamp=self._current_time + self.config.fill_latency_ms // 1000,
                    event_type=EventType.SIGNAL,
                    data={"signal": signal},
                ))
                self._signal_count += 1

    def run_backtest(
        self,
        price_events: Iterator[Dict[str, Any]],
        resolution_events: Optional[Iterator[Dict[str, Any]]] = None,
        trade_events: Optional[Iterator[Dict[str, Any]]] = None,
    ) -> "BacktestResult":
        """
        Run a backtest over historical data.

        Args:
            price_events: Iterator of price updates
            resolution_events: Iterator of market resolutions
            trade_events: Iterator of external trades (e.g., whale trades)

        Returns:
            BacktestResult with performance metrics
        """
        # Load all events into queue
        for pe in price_events:
            self.push_event(Event(
                timestamp=pe.get("timestamp", 0),
                event_type=EventType.PRICE_UPDATE,
                data=pe,
            ))

        if resolution_events:
            for re in resolution_events:
                self.push_event(Event(
                    timestamp=re.get("timestamp", 0),
                    event_type=EventType.RESOLUTION,
                    data=re,
                ))

        if trade_events:
            for te in trade_events:
                self.push_event(Event(
                    timestamp=te.get("timestamp", 0),
                    event_type=EventType.TRADE,
                    data=te,
                ))

        # Process all events
        while self._events:
            event = self.pop_event()
            self.process_event(event)

        return self._build_result()

    def _build_result(self) -> "BacktestResult":
        """Build backtest result from engine state."""
        trades_df = pd.DataFrame([
            {
                "trade_id": t.trade_id,
                "market_id": t.market_id,
                "strategy": t.strategy,
                "side": t.side,
                "outcome": t.outcome,
                "entry_time": t.entry_time,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time,
                "exit_price": t.exit_price,
                "size": t.size,
                "pnl": t.pnl,
                "fees": t.fees,
                "net_pnl": t.net_pnl,
                "status": t.status,
            }
            for t in self._closed_trades
        ])

        # Include open trades
        for trade in self._trades.values():
            key = f"{trade.market_id}_{trade.outcome}"
            current_price = self._prices.get(key, trade.entry_price)
            direction = 1 if trade.side == "BUY" else -1
            unrealized = direction * (current_price - trade.entry_price) * trade.size

            trades_df = pd.concat([trades_df, pd.DataFrame([{
                "trade_id": trade.trade_id,
                "market_id": trade.market_id,
                "strategy": trade.strategy,
                "side": trade.side,
                "outcome": trade.outcome,
                "entry_time": trade.entry_time,
                "entry_price": trade.entry_price,
                "exit_time": None,
                "exit_price": current_price,
                "size": trade.size,
                "pnl": unrealized,
                "fees": trade.fees,
                "net_pnl": unrealized - trade.fees,
                "status": "open",
            }])], ignore_index=True)

        return BacktestResult(
            trades_df=trades_df,
            config=self.config,
            risk_report=self.strategy_manager.get_risk_report(),
            signal_count=self._signal_count,
            trade_count=self._trade_count,
        )

    def reset(self):
        """Reset the engine state."""
        self._events.clear()
        self._trades.clear()
        self._closed_trades.clear()
        self._prices.clear()
        self._market_metadata.clear()
        self._event_log.clear()
        self._signal_count = 0
        self._trade_count = 0
        self.strategy_manager.reset()


@dataclass
class BacktestResult:
    """Result of a backtest run."""

    trades_df: pd.DataFrame
    config: EngineConfig
    risk_report: Dict[str, Any]
    signal_count: int
    trade_count: int

    @property
    def total_trades(self) -> int:
        return len(self.trades_df)

    @property
    def closed_trades(self) -> int:
        return len(self.trades_df[self.trades_df["status"] != "open"])

    @property
    def open_positions(self) -> int:
        return len(self.trades_df[self.trades_df["status"] == "open"])

    @property
    def win_rate(self) -> float:
        closed = self.trades_df[self.trades_df["status"] != "open"]
        if len(closed) == 0:
            return 0.0
        wins = (closed["net_pnl"] > 0).sum()
        return wins / len(closed)

    @property
    def total_pnl(self) -> float:
        return self.trades_df["net_pnl"].sum()

    @property
    def gross_pnl(self) -> float:
        return self.trades_df["pnl"].sum()

    @property
    def total_fees(self) -> float:
        return self.trades_df["fees"].sum()

    @property
    def sharpe_ratio(self) -> float:
        if len(self.trades_df) < 2:
            return 0.0
        returns = self.trades_df["net_pnl"]
        if returns.std() == 0:
            return 0.0
        return returns.mean() / returns.std() * np.sqrt(252)

    def summary(self) -> str:
        """Get a summary string."""
        return f"""
{'='*60}
BACKTEST RESULTS
{'='*60}
Signals Generated:     {self.signal_count:,}
Trades Executed:       {self.trade_count:,}
Open Positions:        {self.open_positions:,}

Win Rate:              {self.win_rate*100:.1f}%
Gross P&L:             ${self.gross_pnl:,.2f}
Fees:                  ${self.total_fees:,.2f}
Net P&L:               ${self.total_pnl:,.2f}
Sharpe Ratio:          {self.sharpe_ratio:.2f}

Risk Report:
  Drawdown:            ${self.risk_report.get('drawdown', 0):,.2f}
  Drawdown %:          {self.risk_report.get('drawdown_pct', 0)*100:.1f}%
  Max Exposure:        ${self.risk_report.get('total_exposure', 0):,.0f}
{'='*60}
"""

    def by_strategy(self) -> pd.DataFrame:
        """Get performance breakdown by strategy."""
        return self.trades_df.groupby("strategy").agg({
            "trade_id": "count",
            "pnl": "sum",
            "fees": "sum",
            "net_pnl": ["sum", "mean"],
        }).round(2)

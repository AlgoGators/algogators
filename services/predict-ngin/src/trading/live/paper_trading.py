"""
Paper Trading Module

Simulates live execution without real capital.
Uses real-time market data but virtual positions.

Features:
- Real-time price feeds via WebSocket
- Virtual position tracking
- P/L calculation with realistic costs
- Signal integration (whale, ensemble)
- Performance logging

Usage:
    python -m src.trading.live.paper_trading --start
    python -m src.trading.live.paper_trading --status
    python -m src.trading.live.paper_trading --stop
"""

import json
import time
import sqlite3
import asyncio
import threading
import websockets
import requests
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from enum import Enum
import queue

# Constants
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

DEFAULT_DB_PATH = "data/prediction_markets.db"
PAPER_STATE_PATH = "data/paper_trading_state.json"
PAPER_LOG_PATH = "data/paper_trading_log.jsonl"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class PaperOrder:
    """A simulated order."""
    order_id: str
    market_id: str
    token_id: str
    side: OrderSide
    size_usd: float
    limit_price: Optional[float]
    created_at: datetime
    status: OrderStatus = OrderStatus.PENDING
    filled_at: Optional[datetime] = None
    filled_price: Optional[float] = None
    filled_size: float = 0
    slippage: float = 0
    fees: float = 0
    signal_source: str = ""
    notes: str = ""


@dataclass
class PaperPosition:
    """A virtual position."""
    position_id: str
    market_id: str
    token_id: str
    outcome: str  # YES or NO
    side: OrderSide  # BUY or SELL
    entry_price: float
    entry_size_usd: float
    entry_time: datetime
    current_price: float = 0
    unrealized_pnl: float = 0
    realized_pnl: float = 0
    status: PositionStatus = PositionStatus.OPEN
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    entry_order_id: str = ""
    exit_order_id: str = ""


@dataclass
class PaperAccount:
    """Virtual trading account state."""
    initial_capital: float = 10000.0
    cash: float = 10000.0
    positions: Dict[str, PaperPosition] = field(default_factory=dict)
    orders: Dict[str, PaperOrder] = field(default_factory=dict)
    total_pnl: float = 0
    total_fees: float = 0
    trade_count: int = 0
    win_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def equity(self) -> float:
        """Total account value."""
        unrealized = sum(p.unrealized_pnl for p in self.positions.values() if p.status == PositionStatus.OPEN)
        return self.cash + unrealized

    @property
    def win_rate(self) -> float:
        """Win rate on closed trades."""
        if self.trade_count == 0:
            return 0
        return self.win_count / self.trade_count

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "positions": {k: asdict(v) for k, v in self.positions.items()},
            "orders": {k: asdict(v) for k, v in self.orders.items()},
            "total_pnl": self.total_pnl,
            "total_fees": self.total_fees,
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperAccount":
        """Deserialize from dict."""
        def _parse_enum(enum_cls, value):
            if isinstance(value, enum_cls):
                return value
            if isinstance(value, str):
                if value in enum_cls._value2member_map_:
                    return enum_cls(value)
                if "." in value:
                    value = value.split(".")[-1]
                if value.upper() in enum_cls.__members__:
                    return enum_cls.__members__[value.upper()]
                if value.lower() in enum_cls._value2member_map_:
                    return enum_cls(value.lower())
            return list(enum_cls)[0]

        account = cls(
            initial_capital=data.get("initial_capital", 10000),
            cash=data.get("cash", 10000),
            total_pnl=data.get("total_pnl", 0),
            total_fees=data.get("total_fees", 0),
            trade_count=data.get("trade_count", 0),
            win_count=data.get("win_count", 0),
        )

        if "created_at" in data:
            account.created_at = datetime.fromisoformat(data["created_at"])

        # Reconstruct positions
        for k, v in data.get("positions", {}).items():
            v["side"] = _parse_enum(OrderSide, v.get("side"))
            v["status"] = _parse_enum(PositionStatus, v.get("status"))
            v["entry_time"] = datetime.fromisoformat(v["entry_time"]) if isinstance(v["entry_time"], str) else v["entry_time"]
            if v.get("exit_time"):
                v["exit_time"] = datetime.fromisoformat(v["exit_time"]) if isinstance(v["exit_time"], str) else v["exit_time"]
            account.positions[k] = PaperPosition(**v)

        return account


class CostModel:
    """Realistic cost model for paper trading."""

    def __init__(
        self,
        base_spread: float = 0.02,
        slippage_bps: float = 50,
        impact_coef: float = 0.5,
        fee_rate: float = 0.001,
    ):
        self.base_spread = base_spread
        self.slippage_bps = slippage_bps
        self.impact_coef = impact_coef
        self.fee_rate = fee_rate

    def calculate_execution_price(
        self,
        side: OrderSide,
        mid_price: float,
        size_usd: float,
        liquidity: float = 10000,
    ) -> tuple[float, float, float]:
        """
        Calculate realistic execution price with slippage.

        Returns:
            (execution_price, slippage, fees)
        """
        # Base spread cost
        spread_cost = self.base_spread / 2

        # Size-dependent slippage
        slippage = self.slippage_bps / 10000

        # Market impact (scaled by base slippage)
        if liquidity > 0:
            impact = self.impact_coef * slippage * (size_usd / liquidity) ** 0.5
        else:
            impact = 0.05  # Assume high impact for unknown liquidity

        total_slippage = spread_cost + slippage + impact

        # Apply to price
        if side == OrderSide.BUY:
            exec_price = mid_price * (1 + total_slippage)
        else:
            exec_price = mid_price * (1 - total_slippage)

        # Fees
        fees = size_usd * self.fee_rate

        return exec_price, total_slippage, fees


class PriceTracker:
    """Tracks real-time prices for markets."""

    def __init__(self):
        self.prices: Dict[str, float] = {}
        self.last_update: Dict[str, datetime] = {}
        self._lock = threading.Lock()

    def update(self, market_id: str, price: float):
        """Update price for a market."""
        with self._lock:
            self.prices[market_id] = price
            self.last_update[market_id] = datetime.now()

    def get(self, market_id: str) -> Optional[float]:
        """Get current price."""
        with self._lock:
            return self.prices.get(market_id)

    def get_all(self) -> Dict[str, float]:
        """Get all prices."""
        with self._lock:
            return dict(self.prices)


class SignalQueue:
    """Queue for incoming trading signals."""

    def __init__(self):
        self._queue = queue.Queue()

    def put(self, signal: Dict[str, Any]):
        """Add signal to queue."""
        signal["received_at"] = datetime.now().isoformat()
        self._queue.put(signal)

    def get(self, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        """Get next signal (non-blocking)."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def empty(self) -> bool:
        return self._queue.empty()


class PaperTrader:
    """
    Paper trading engine.

    Simulates live trading with virtual capital using real market data.
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        db_path: str = DEFAULT_DB_PATH,
        state_path: str = PAPER_STATE_PATH,
        log_path: str = PAPER_LOG_PATH,
        max_position_size: float = 1000.0,
        max_positions: int = 20,
    ):
        self.db_path = db_path
        self.state_path = Path(state_path)
        self.log_path = Path(log_path)
        self.max_position_size = max_position_size
        self.max_positions = max_positions

        # Load or create account
        self.account = self._load_state() or PaperAccount(
            initial_capital=initial_capital,
            cash=initial_capital,
        )

        self.cost_model = CostModel()
        self.price_tracker = PriceTracker()
        self.signal_queue = SignalQueue()

        self._running = False
        self._order_counter = 0
        self._position_counter = 0

        # Whale addresses for signal generation
        self.whale_addresses: Set[str] = set()

        # Callbacks
        self.on_fill: Optional[Callable] = None
        self.on_position_update: Optional[Callable] = None

    def _load_state(self) -> Optional[PaperAccount]:
        """Load saved state."""
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    data = json.load(f)
                return PaperAccount.from_dict(data)
            except Exception as e:
                print(f"Failed to load state: {e}")
        return None

    def _save_state(self):
        """Save current state."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(self.account.to_dict(), f, indent=2, default=str)

    def _log_event(self, event_type: str, data: Dict[str, Any]):
        """Log event to JSONL file."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            **data,
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def _generate_order_id(self) -> str:
        self._order_counter += 1
        return f"PAPER-{datetime.now().strftime('%Y%m%d')}-{self._order_counter:06d}"

    def _generate_position_id(self) -> str:
        self._position_counter += 1
        return f"POS-{datetime.now().strftime('%Y%m%d')}-{self._position_counter:06d}"

    def load_whale_addresses(self):
        """Load whale addresses from database."""
        try:
            from src.whale_strategy.polymarket_whales import (
                load_polymarket_trades, identify_polymarket_whales, build_price_snapshot
            )
            trades = load_polymarket_trades(self.db_path)
            price_snapshot = build_price_snapshot(trades)
            self.whale_addresses = identify_polymarket_whales(
                trades,
                method="win_rate_60pct",
                price_snapshot=price_snapshot,
            )
            print(f"Loaded {len(self.whale_addresses)} whale addresses")
        except Exception as e:
            print(f"Failed to load whales: {e}")

    def get_market_price(self, market_id: str) -> Optional[float]:
        """Get current market price."""
        # First check tracker
        price = self.price_tracker.get(market_id)
        if price:
            return price

        # Fall back to API
        try:
            response = requests.get(
                f"{GAMMA_API}/markets/{market_id}",
                timeout=5,
            )
            if response.status_code == 200:
                data = response.json()
                prices = data.get("outcomePrices", [])
                if prices:
                    price = float(prices[0])
                    self.price_tracker.update(market_id, price)
                    return price
        except:
            pass

        return None

    def get_market_liquidity(self, token_id: str) -> float:
        """Get current market liquidity."""
        try:
            response = requests.get(
                f"{CLOB_API}/book",
                params={"token_id": token_id},
                timeout=5,
            )
            if response.status_code == 200:
                book = response.json()
                bids = book.get("bids", [])
                asks = book.get("asks", [])

                total_liq = 0
                for level in bids[:5] + asks[:5]:
                    total_liq += float(level.get("size", 0))

                return total_liq
        except:
            pass

        return 1000  # Default assumption

    def submit_order(
        self,
        market_id: str,
        token_id: str,
        side: OrderSide,
        size_usd: float,
        signal_source: str = "",
        notes: str = "",
    ) -> Optional[PaperOrder]:
        """
        Submit a paper order.

        Immediately simulates fill with realistic slippage.
        """
        # Validate
        if size_usd > self.max_position_size:
            print(f"Order size ${size_usd} exceeds max ${self.max_position_size}")
            return None

        if side == OrderSide.BUY and size_usd > self.account.cash:
            print(f"Insufficient cash: ${self.account.cash:.2f} < ${size_usd:.2f}")
            return None

        open_positions = sum(
            1 for p in self.account.positions.values()
            if p.status == PositionStatus.OPEN
        )
        if open_positions >= self.max_positions:
            print(f"Max positions reached: {open_positions}")
            return None

        # Get current price
        mid_price = self.get_market_price(market_id)
        if mid_price is None:
            print(f"Cannot get price for market {market_id}")
            return None

        # Get liquidity for impact calculation
        liquidity = self.get_market_liquidity(token_id)

        # Calculate execution
        exec_price, slippage, fees = self.cost_model.calculate_execution_price(
            side, mid_price, size_usd, liquidity
        )

        # Create order
        order = PaperOrder(
            order_id=self._generate_order_id(),
            market_id=market_id,
            token_id=token_id,
            side=side,
            size_usd=size_usd,
            limit_price=None,
            created_at=datetime.now(),
            status=OrderStatus.FILLED,
            filled_at=datetime.now(),
            filled_price=exec_price,
            filled_size=size_usd,
            slippage=slippage,
            fees=fees,
            signal_source=signal_source,
            notes=notes,
        )

        self.account.orders[order.order_id] = order

        # Update account
        if side == OrderSide.BUY:
            self.account.cash -= (size_usd + fees)

            # Create position
            position = PaperPosition(
                position_id=self._generate_position_id(),
                market_id=market_id,
                token_id=token_id,
                outcome="YES",
                side=side,
                entry_price=exec_price,
                entry_size_usd=size_usd,
                entry_time=datetime.now(),
                current_price=exec_price,
                entry_order_id=order.order_id,
            )
            self.account.positions[position.position_id] = position
        else:
            # For sells, close existing position or short
            self.account.cash += (size_usd - fees)

        self.account.total_fees += fees

        # Log
        self._log_event("order_filled", asdict(order))
        self._save_state()

        if self.on_fill:
            self.on_fill(order)

        return order

    def close_position(
        self,
        position_id: str,
        notes: str = "",
    ) -> Optional[PaperOrder]:
        """Close an open position."""
        if position_id not in self.account.positions:
            print(f"Position not found: {position_id}")
            return None

        position = self.account.positions[position_id]
        if position.status != PositionStatus.OPEN:
            print(f"Position already closed: {position_id}")
            return None

        # Get current price
        current_price = self.get_market_price(position.market_id)
        if current_price is None:
            print(f"Cannot get price for position close")
            return None

        # Calculate exit
        exit_side = OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY
        liquidity = self.get_market_liquidity(position.token_id)

        exec_price, slippage, fees = self.cost_model.calculate_execution_price(
            exit_side, current_price, position.entry_size_usd, liquidity
        )

        # Create exit order
        order = PaperOrder(
            order_id=self._generate_order_id(),
            market_id=position.market_id,
            token_id=position.token_id,
            side=exit_side,
            size_usd=position.entry_size_usd,
            limit_price=None,
            created_at=datetime.now(),
            status=OrderStatus.FILLED,
            filled_at=datetime.now(),
            filled_price=exec_price,
            filled_size=position.entry_size_usd,
            slippage=slippage,
            fees=fees,
            notes=notes,
        )

        self.account.orders[order.order_id] = order

        # Calculate P/L
        if position.side == OrderSide.BUY:
            pnl = (exec_price - position.entry_price) * position.entry_size_usd - fees
        else:
            pnl = (position.entry_price - exec_price) * position.entry_size_usd - fees

        # Update position
        position.status = PositionStatus.CLOSED
        position.exit_price = exec_price
        position.exit_time = datetime.now()
        position.exit_order_id = order.order_id
        position.realized_pnl = pnl

        # Update account
        self.account.cash += position.entry_size_usd + pnl
        self.account.total_pnl += pnl
        self.account.total_fees += fees
        self.account.trade_count += 1
        if pnl > 0:
            self.account.win_count += 1

        # Log
        self._log_event("position_closed", {
            "position_id": position_id,
            "pnl": pnl,
            "entry_price": position.entry_price,
            "exit_price": exec_price,
        })
        self._save_state()

        return order

    def update_positions(self):
        """Update unrealized P/L for all open positions."""
        for position in self.account.positions.values():
            if position.status != PositionStatus.OPEN:
                continue

            current_price = self.get_market_price(position.market_id)
            if current_price is None:
                continue

            position.current_price = current_price

            if position.side == OrderSide.BUY:
                position.unrealized_pnl = (
                    (current_price - position.entry_price) * position.entry_size_usd
                )
            else:
                position.unrealized_pnl = (
                    (position.entry_price - current_price) * position.entry_size_usd
                )

        if self.on_position_update:
            self.on_position_update(self.account)

    def process_signal(self, signal: Dict[str, Any]):
        """Process an incoming trading signal."""
        market_id = signal.get("market_id")
        direction = signal.get("direction", "buy").lower()
        size = signal.get("size_usd", 100)
        source = signal.get("source", "unknown")

        if not market_id:
            return

        side = OrderSide.BUY if direction == "buy" else OrderSide.SELL

        # Check if we already have a position in this market
        existing = [
            p for p in self.account.positions.values()
            if p.market_id == market_id and p.status == PositionStatus.OPEN
        ]

        if existing:
            print(f"Already have position in {market_id}, skipping signal")
            return

        # Get token ID (need to look up)
        token_id = signal.get("token_id", "")
        if not token_id:
            # Try to get from API
            try:
                response = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    tokens = data.get("clobTokenIds", [])
                    if tokens:
                        token_id = tokens[0]
            except:
                pass

        if not token_id:
            print(f"Cannot get token ID for {market_id}")
            return

        # Submit order
        order = self.submit_order(
            market_id=market_id,
            token_id=token_id,
            side=side,
            size_usd=min(size, self.max_position_size),
            signal_source=source,
        )

        if order:
            print(f"Executed {side.value} ${size:.0f} on {market_id[:20]}... @ {order.filled_price:.3f}")

    def get_status(self) -> Dict[str, Any]:
        """Get current trading status."""
        self.update_positions()

        open_positions = [
            p for p in self.account.positions.values()
            if p.status == PositionStatus.OPEN
        ]

        return {
            "running": self._running,
            "equity": self.account.equity,
            "cash": self.account.cash,
            "total_pnl": self.account.total_pnl,
            "unrealized_pnl": sum(p.unrealized_pnl for p in open_positions),
            "open_positions": len(open_positions),
            "total_trades": self.account.trade_count,
            "win_rate": self.account.win_rate,
            "total_fees": self.account.total_fees,
            "return_pct": (self.account.equity / self.account.initial_capital - 1) * 100,
        }

    def print_status(self):
        """Print formatted status."""
        status = self.get_status()

        print("\n" + "=" * 50)
        print("PAPER TRADING STATUS")
        print("=" * 50)
        print(f"Running: {status['running']}")
        print(f"Equity: ${status['equity']:,.2f}")
        print(f"Cash: ${status['cash']:,.2f}")
        print(f"Return: {status['return_pct']:+.2f}%")
        print(f"Total P/L: ${status['total_pnl']:+,.2f}")
        print(f"Unrealized P/L: ${status['unrealized_pnl']:+,.2f}")
        print(f"Open Positions: {status['open_positions']}")
        print(f"Total Trades: {status['total_trades']}")
        print(f"Win Rate: {status['win_rate']:.1%}")
        print(f"Total Fees: ${status['total_fees']:,.2f}")
        print("=" * 50)

        # Show open positions
        open_positions = [
            p for p in self.account.positions.values()
            if p.status == PositionStatus.OPEN
        ]

        if open_positions:
            print("\nOPEN POSITIONS:")
            print("-" * 50)
            for p in open_positions:
                print(f"  {p.market_id[:30]}...")
                print(f"    Entry: ${p.entry_price:.3f} | Current: ${p.current_price:.3f}")
                print(f"    Size: ${p.entry_size_usd:.0f} | P/L: ${p.unrealized_pnl:+.2f}")

    def run_loop(self, check_interval: float = 1.0):
        """Main trading loop."""
        self._running = True
        print("Paper trading started. Press Ctrl+C to stop.")

        try:
            while self._running:
                # Process any queued signals
                while not self.signal_queue.empty():
                    signal = self.signal_queue.get()
                    if signal:
                        self.process_signal(signal)

                # Update positions
                self.update_positions()

                # Check for position exits (e.g., time-based)
                self._check_exit_conditions()

                time.sleep(check_interval)

        except KeyboardInterrupt:
            print("\nStopping paper trading...")
        finally:
            self._running = False
            self._save_state()

    def _check_exit_conditions(self):
        """Check if any positions should be exited."""
        max_holding_hours = 48

        for pos_id, position in list(self.account.positions.items()):
            if position.status != PositionStatus.OPEN:
                continue

            # Time-based exit
            holding_time = datetime.now() - position.entry_time
            if holding_time > timedelta(hours=max_holding_hours):
                print(f"Closing position {pos_id} - max holding time reached")
                self.close_position(pos_id, notes="max_holding_time")
                continue

            # Stop loss at -20%
            if position.unrealized_pnl < -0.20 * position.entry_size_usd:
                print(f"Closing position {pos_id} - stop loss triggered")
                self.close_position(pos_id, notes="stop_loss")
                continue

            # Take profit at +30%
            if position.unrealized_pnl > 0.30 * position.entry_size_usd:
                print(f"Closing position {pos_id} - take profit triggered")
                self.close_position(pos_id, notes="take_profit")
                continue

    def stop(self):
        """Stop the trading loop."""
        self._running = False
        self._save_state()

    def reset(self, initial_capital: float = 10000.0):
        """Reset account to initial state."""
        self.account = PaperAccount(
            initial_capital=initial_capital,
            cash=initial_capital,
        )
        self._order_counter = 0
        self._position_counter = 0
        self._save_state()
        print(f"Account reset with ${initial_capital:,.0f}")


def main():
    """Main entry point."""
    import sys

    trader = PaperTrader()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m src.trading.live.paper_trading --start")
        print("  python -m src.trading.live.paper_trading --status")
        print("  python -m src.trading.live.paper_trading --reset")
        return

    command = sys.argv[1]

    if command == "--start":
        trader.load_whale_addresses()
        trader.print_status()
        trader.run_loop()

    elif command == "--status":
        trader.print_status()

    elif command == "--reset":
        capital = float(sys.argv[2]) if len(sys.argv) > 2 else 10000
        trader.reset(capital)

    elif command == "--test":
        # Test order execution
        trader.load_whale_addresses()
        print("Submitting test order...")

        # Get an active market
        response = requests.get(f"{GAMMA_API}/markets?limit=1&closed=false")
        if response.status_code == 200:
            markets = response.json()
            if markets:
                market = markets[0]
                market_id = market["id"]
                tokens = market.get("clobTokenIds", [])
                if tokens:
                    token_id = tokens[0]
                    order = trader.submit_order(
                        market_id=market_id,
                        token_id=token_id,
                        side=OrderSide.BUY,
                        size_usd=100,
                        signal_source="test",
                    )
                    if order:
                        print(f"Test order executed: {order.order_id}")
                        trader.print_status()


if __name__ == "__main__":
    main()

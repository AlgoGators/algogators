#!/usr/bin/env python3
"""
Real-time whale signal pipeline for Polymarket.

Connects to Polymarket WebSocket for live trade data, monitors for whale
activity, and generates trading signals in real-time.

Usage:
    python -m src.trading.live.realtime_signals
    python -m src.trading.live.realtime_signals --threshold 500 --window 100

Requires:
    pip install websockets
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    List,
    Optional,
    Set,
)

try:
    import websockets
    from websockets.asyncio.client import ClientConnection
except ImportError:
    try:
        # Fallback for older websockets versions
        import websockets
        from websockets.client import ClientConnection as ClientConnection
    except ImportError:
        print("Error: websockets library not found. Install with: pip install websockets")
        sys.exit(1)


# Polymarket WebSocket endpoints
WS_TRADES_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class SignalType(Enum):
    """Type of whale signal."""

    WHALE_BUY = "WHALE_BUY"
    WHALE_SELL = "WHALE_SELL"


@dataclass
class Trade:
    """Represents a single trade from the WebSocket feed."""

    trade_id: str
    timestamp: datetime
    market_id: str
    token_id: str
    maker: str
    taker: str
    side: str  # "buy" or "sell"
    price: float
    size: float  # Token amount
    usd_amount: float

    @classmethod
    def from_ws_message(cls, data: Dict[str, Any]) -> Optional["Trade"]:
        """Parse a trade from WebSocket message data."""
        try:
            # Handle different message formats
            trade_id = data.get("id", data.get("trade_id", ""))
            timestamp_raw = data.get("timestamp", data.get("t"))

            if isinstance(timestamp_raw, (int, float)):
                # Unix timestamp in seconds or milliseconds
                if timestamp_raw > 1e12:
                    timestamp_raw = timestamp_raw / 1000
                timestamp = datetime.fromtimestamp(timestamp_raw, tz=timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)

            market_id = data.get("market", data.get("market_id", data.get("asset_id", "")))
            token_id = data.get("asset_id", data.get("token_id", market_id))

            maker = data.get("maker", data.get("maker_address", ""))
            taker = data.get("taker", data.get("taker_address", ""))

            side = data.get("side", data.get("taker_side", "buy")).lower()

            price = float(data.get("price", 0))
            size = float(data.get("size", data.get("amount", data.get("token_amount", 0))))

            # Calculate USD amount if not provided
            usd_amount = float(data.get("usd_amount", 0))
            if usd_amount == 0 and price > 0 and size > 0:
                usd_amount = price * size

            if not market_id or price <= 0:
                return None

            return cls(
                trade_id=str(trade_id),
                timestamp=timestamp,
                market_id=str(market_id),
                token_id=str(token_id),
                maker=str(maker),
                taker=str(taker),
                side=side,
                price=price,
                size=size,
                usd_amount=usd_amount,
            )
        except (ValueError, TypeError, KeyError):
            return None


@dataclass
class WhaleSignal:
    """Signal generated when a whale trades."""

    signal_type: SignalType
    timestamp: datetime
    market_id: str
    token_id: str
    whale_address: str
    trade_side: str
    price: float
    usd_amount: float
    confidence: float = 0.7
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert signal to dictionary."""
        return {
            "signal_type": self.signal_type.value,
            "timestamp": self.timestamp.isoformat(),
            "market_id": self.market_id,
            "token_id": self.token_id,
            "whale_address": self.whale_address,
            "trade_side": self.trade_side,
            "price": self.price,
            "usd_amount": self.usd_amount,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    def __str__(self) -> str:
        addr = self.whale_address[:8] + "..." + self.whale_address[-6:]
        return (
            f"[{self.signal_type.value}] {addr} "
            f"{self.trade_side.upper()} ${self.usd_amount:,.0f} @ {self.price:.4f}"
        )


class WhaleSignalGenerator:
    """
    Generates trading signals when known whale addresses trade.

    Monitors incoming trades and emits signals when whales trade
    above a configured USD threshold.
    """

    def __init__(
        self,
        whale_addresses: Set[str],
        min_usd_threshold: float = 100.0,
        confidence_by_size: bool = True,
        on_signal: Optional[Callable[[WhaleSignal], None]] = None,
    ):
        """
        Initialize the whale signal generator.

        Args:
            whale_addresses: Set of known whale wallet addresses
            min_usd_threshold: Minimum USD trade size to generate signal
            confidence_by_size: If True, scale confidence by trade size
            on_signal: Callback function for generated signals
        """
        self.whale_addresses = {addr.lower() for addr in whale_addresses}
        self.min_usd_threshold = min_usd_threshold
        self.confidence_by_size = confidence_by_size
        self.on_signal = on_signal or self._default_signal_handler

        # Statistics
        self.trades_processed = 0
        self.signals_generated = 0
        self.whale_trades_seen = 0

    def _default_signal_handler(self, signal: WhaleSignal):
        """Default handler - print signal to console."""
        print(f"[SIGNAL] {signal}")

    def _calculate_confidence(self, usd_amount: float) -> float:
        """Calculate signal confidence based on trade size."""
        if not self.confidence_by_size:
            return 0.7

        # Scale confidence from 0.5 to 0.95 based on trade size
        # $100 = 0.5, $10,000+ = 0.95
        if usd_amount <= self.min_usd_threshold:
            return 0.5

        log_size = min((usd_amount / self.min_usd_threshold), 100)
        confidence = 0.5 + 0.45 * (log_size / 100)
        return min(confidence, 0.95)

    def process_trade(self, trade: Trade) -> Optional[WhaleSignal]:
        """
        Process a single trade and generate a signal if whale is detected.

        Args:
            trade: Trade to process

        Returns:
            WhaleSignal if whale detected and above threshold, else None
        """
        self.trades_processed += 1

        # Check if maker or taker is a whale
        whale_address = None
        whale_role = None

        if trade.maker.lower() in self.whale_addresses:
            whale_address = trade.maker
            whale_role = "maker"
        elif trade.taker.lower() in self.whale_addresses:
            whale_address = trade.taker
            whale_role = "taker"

        if not whale_address:
            return None

        self.whale_trades_seen += 1

        # Check USD threshold
        if trade.usd_amount < self.min_usd_threshold:
            return None

        # Determine signal type based on trade side
        signal_type = SignalType.WHALE_BUY if trade.side == "buy" else SignalType.WHALE_SELL

        # Generate signal
        signal = WhaleSignal(
            signal_type=signal_type,
            timestamp=trade.timestamp,
            market_id=trade.market_id,
            token_id=trade.token_id,
            whale_address=whale_address,
            trade_side=trade.side,
            price=trade.price,
            usd_amount=trade.usd_amount,
            confidence=self._calculate_confidence(trade.usd_amount),
            metadata={
                "trade_id": trade.trade_id,
                "whale_role": whale_role,
                "token_size": trade.size,
            },
        )

        self.signals_generated += 1
        self.on_signal(signal)

        return signal

    def get_stats(self) -> Dict[str, Any]:
        """Get generator statistics."""
        return {
            "trades_processed": self.trades_processed,
            "whale_trades_seen": self.whale_trades_seen,
            "signals_generated": self.signals_generated,
            "whale_count": len(self.whale_addresses),
            "min_threshold": self.min_usd_threshold,
        }


class RealTimeTradeStream:
    """
    Real-time trade stream from Polymarket WebSocket.

    Maintains a connection to the Polymarket WebSocket feed and
    streams trades to registered handlers.
    """

    def __init__(
        self,
        token_ids: Optional[Set[str]] = None,
        max_window_size: int = 1000,
        on_trade: Optional[Callable[[Trade], None]] = None,
        reconnect_delay: float = 5.0,
    ):
        """
        Initialize the trade stream.

        Args:
            token_ids: Set of token IDs to subscribe to (None = all)
            max_window_size: Maximum trades to keep in rolling window
            on_trade: Callback for each trade received
            reconnect_delay: Seconds to wait before reconnecting
        """
        self.token_ids = token_ids or set()
        self.max_window_size = max_window_size
        self.on_trade = on_trade
        self.reconnect_delay = reconnect_delay

        # Rolling window of recent trades
        self.trades: Deque[Trade] = deque(maxlen=max_window_size)

        # Connection state
        self._ws: Optional[ClientConnection] = None
        self._running = False
        self._connected = False

        # Statistics
        self.messages_received = 0
        self.trades_received = 0
        self.connection_errors = 0

    async def _subscribe(self, ws: ClientConnection):
        """Subscribe to market/trade channels."""
        if self.token_ids:
            # Subscribe to specific tokens
            for token_id in self.token_ids:
                subscribe_msg = {
                    "type": "subscribe",
                    "channel": "market",
                    "market": token_id,
                }
                await ws.send(json.dumps(subscribe_msg))
        else:
            # Subscribe to all trades channel if available
            subscribe_msg = {
                "type": "subscribe",
                "channel": "trades",
            }
            await ws.send(json.dumps(subscribe_msg))

    async def _handle_message(self, message: str):
        """Process incoming WebSocket message."""
        self.messages_received += 1

        try:
            data = json.loads(message)

            # Handle different message types
            msg_type = data.get("type", data.get("event_type", ""))

            if msg_type in ("trade", "last_trade_price", "tick"):
                trade = Trade.from_ws_message(data)
                if trade:
                    self._process_trade(trade)

            elif msg_type == "book":
                # Book updates may contain last trade info
                last_trade = data.get("last_trade")
                if last_trade:
                    trade = Trade.from_ws_message(last_trade)
                    if trade:
                        self._process_trade(trade)

            elif msg_type == "price_change":
                # Price changes indicate a trade occurred
                # Create a synthetic trade record
                trade_data = {
                    "market": data.get("market", data.get("asset_id")),
                    "price": data.get("price"),
                    "side": data.get("side", "buy"),
                    "size": data.get("size", 0),
                    "maker": data.get("maker", ""),
                    "taker": data.get("taker", ""),
                }
                trade = Trade.from_ws_message(trade_data)
                if trade and trade.size > 0:
                    self._process_trade(trade)

        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    def _process_trade(self, trade: Trade):
        """Add trade to window and notify handlers."""
        self.trades_received += 1
        self.trades.append(trade)

        if self.on_trade:
            self.on_trade(trade)

    def get_recent_trades(self, n: Optional[int] = None) -> List[Trade]:
        """Get recent trades from the rolling window."""
        if n is None:
            return list(self.trades)
        return list(self.trades)[-n:]

    def get_trades_by_market(self, market_id: str) -> List[Trade]:
        """Get trades for a specific market."""
        return [t for t in self.trades if t.market_id == market_id]

    async def connect(self):
        """Establish WebSocket connection."""
        print(f"Connecting to {WS_TRADES_URL}...")

        try:
            self._ws = await websockets.connect(
                WS_TRADES_URL,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            )
            self._connected = True
            print("Connected to Polymarket WebSocket")

            await self._subscribe(self._ws)
            print(f"Subscribed to {len(self.token_ids) or 'all'} tokens")

        except Exception as e:
            self.connection_errors += 1
            self._connected = False
            raise ConnectionError(f"Failed to connect: {e}")

    async def disconnect(self):
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connected = False
        print("Disconnected from Polymarket WebSocket")

    async def stream(self):
        """
        Start streaming trades.

        This is the main event loop that receives and processes trades.
        Automatically reconnects on connection loss.
        """
        self._running = True

        while self._running:
            try:
                if not self._connected:
                    await self.connect()

                if not self._ws:
                    raise ConnectionError("WebSocket not connected")

                async for message in self._ws:
                    if not self._running:
                        break
                    await self._handle_message(message)

            except websockets.ConnectionClosed:
                self._connected = False
                if self._running:
                    print(f"Connection lost. Reconnecting in {self.reconnect_delay}s...")
                    await asyncio.sleep(self.reconnect_delay)

            except Exception as e:
                self._connected = False
                self.connection_errors += 1
                if self._running:
                    print(f"Error: {e}. Reconnecting in {self.reconnect_delay}s...")
                    await asyncio.sleep(self.reconnect_delay)

    def get_stats(self) -> Dict[str, Any]:
        """Get stream statistics."""
        return {
            "connected": self._connected,
            "running": self._running,
            "messages_received": self.messages_received,
            "trades_received": self.trades_received,
            "trades_in_window": len(self.trades),
            "connection_errors": self.connection_errors,
            "subscribed_tokens": len(self.token_ids),
        }


class RealTimeSignalPipeline:
    """
    Complete real-time signal pipeline.

    Connects the trade stream to the whale signal generator and
    manages the overall lifecycle.
    """

    def __init__(
        self,
        whale_addresses: Set[str],
        token_ids: Optional[Set[str]] = None,
        min_usd_threshold: float = 100.0,
        max_window_size: int = 1000,
        signal_callback: Optional[Callable[[WhaleSignal], None]] = None,
    ):
        """
        Initialize the signal pipeline.

        Args:
            whale_addresses: Set of known whale addresses
            token_ids: Token IDs to monitor (None = all)
            min_usd_threshold: Minimum USD for signal generation
            max_window_size: Size of rolling trade window
            signal_callback: Callback for generated signals
        """
        self.signal_queue: asyncio.Queue[WhaleSignal] = asyncio.Queue()

        # Set up signal handler
        def handle_signal(sig: WhaleSignal):
            # Put in queue (non-blocking)
            try:
                self.signal_queue.put_nowait(sig)
            except asyncio.QueueFull:
                pass

            # Also call user callback if provided
            if signal_callback:
                signal_callback(sig)

        # Create components
        self.signal_generator = WhaleSignalGenerator(
            whale_addresses=whale_addresses,
            min_usd_threshold=min_usd_threshold,
            on_signal=handle_signal,
        )

        self.trade_stream = RealTimeTradeStream(
            token_ids=token_ids,
            max_window_size=max_window_size,
            on_trade=self.signal_generator.process_trade,
        )

        self._running = False

    async def start(self):
        """Start the signal pipeline."""
        self._running = True
        print("\n" + "=" * 60)
        print("Starting Real-Time Whale Signal Pipeline")
        print("=" * 60)
        print(f"Monitoring {len(self.signal_generator.whale_addresses)} whale addresses")
        print(f"Minimum trade threshold: ${self.signal_generator.min_usd_threshold:,.0f}")
        print(f"Rolling window size: {self.trade_stream.max_window_size} trades")
        print("=" * 60 + "\n")

        await self.trade_stream.stream()

    async def stop(self):
        """Stop the signal pipeline."""
        self._running = False
        await self.trade_stream.disconnect()

    async def get_signal(self, timeout: Optional[float] = None) -> Optional[WhaleSignal]:
        """
        Get the next signal from the queue.

        Args:
            timeout: Maximum seconds to wait (None = wait forever)

        Returns:
            WhaleSignal or None if timeout
        """
        try:
            if timeout:
                return await asyncio.wait_for(
                    self.signal_queue.get(),
                    timeout=timeout,
                )
            return await self.signal_queue.get()
        except asyncio.TimeoutError:
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline statistics."""
        return {
            "running": self._running,
            "stream": self.trade_stream.get_stats(),
            "generator": self.signal_generator.get_stats(),
            "queue_size": self.signal_queue.qsize(),
        }


def load_whale_addresses_from_db(
    db_path: str = "data/prediction_markets.db",
    method: str = "win_rate_60pct",
    min_trades: int = 20,
    min_volume: float = 1000,
) -> Set[str]:
    """
    Load whale addresses from the database using polymarket_whales module.

    Falls back to an empty set if database not available.
    """
    try:
        from ...whale_strategy.polymarket_whales import (
            load_polymarket_trades,
            identify_polymarket_whales,
            build_price_snapshot,
        )

        trades = load_polymarket_trades(db_path)
        if len(trades) == 0:
            return set()

        price_snapshot = build_price_snapshot(trades)
        whales = identify_polymarket_whales(
            trades,
            method=method,
            min_trades=min_trades,
            min_volume=min_volume,
            price_snapshot=price_snapshot,
        )
        return whales

    except Exception as e:
        print(f"Warning: Could not load whales from database: {e}")
        return set()


def get_active_token_ids(min_volume: float = 10000, limit: int = 100) -> Set[str]:
    """Fetch token IDs for active markets from Polymarket API."""
    try:
        import requests

        print(f"Fetching active markets with volume >= ${min_volume:,.0f}...")
        token_ids = set()

        response = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": limit, "closed": "false"},
            timeout=30,
        )
        response.raise_for_status()

        for market in response.json():
            vol = float(market.get("volume24hr", 0) or 0)
            if vol >= min_volume:
                tokens = market.get("clobTokenIds", "[]")
                if isinstance(tokens, str):
                    try:
                        tokens = json.loads(tokens)
                    except json.JSONDecodeError:
                        tokens = []
                token_ids.update(tokens)

        print(f"Found {len(token_ids)} tokens from {limit} markets")
        return token_ids

    except Exception as e:
        print(f"Warning: Could not fetch token IDs: {e}")
        return set()


async def main_async(
    whale_addresses: Optional[Set[str]] = None,
    min_usd_threshold: float = 100.0,
    max_window_size: int = 1000,
    token_ids: Optional[Set[str]] = None,
    fetch_active_tokens: bool = True,
    load_whales_from_db: bool = True,
):
    """
    Main async entry point for the real-time signal pipeline.
    """
    # Load whale addresses if not provided
    if whale_addresses is None:
        whale_addresses = set()

        if load_whales_from_db:
            print("Loading whale addresses from database...")
            whale_addresses = load_whale_addresses_from_db()
            print(f"Loaded {len(whale_addresses)} whale addresses")

        if not whale_addresses:
            # Use demo addresses if none loaded
            print("No whale addresses found. Using demo mode with sample addresses.")
            whale_addresses = {
                "0x0000000000000000000000000000000000000001",
                "0x0000000000000000000000000000000000000002",
            }

    # Fetch active token IDs if requested
    if token_ids is None and fetch_active_tokens:
        token_ids = get_active_token_ids(min_volume=10000, limit=50)

    # Create and start pipeline
    pipeline = RealTimeSignalPipeline(
        whale_addresses=whale_addresses,
        token_ids=token_ids,
        min_usd_threshold=min_usd_threshold,
        max_window_size=max_window_size,
    )

    # Set up graceful shutdown
    shutdown_event = asyncio.Event()

    def handle_shutdown(signum, frame):
        print("\n\nShutdown signal received. Stopping gracefully...")
        shutdown_event.set()

    # Register signal handlers (Unix-style)
    if sys.platform != "win32":
        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

    # Start pipeline with shutdown monitoring
    try:
        # Create tasks
        stream_task = asyncio.create_task(pipeline.start())

        # Monitor for shutdown
        async def monitor_shutdown():
            while not shutdown_event.is_set():
                await asyncio.sleep(0.5)
            await pipeline.stop()
            stream_task.cancel()

        if sys.platform == "win32":
            # On Windows, handle Ctrl+C differently
            try:
                await stream_task
            except KeyboardInterrupt:
                print("\n\nShutdown signal received. Stopping gracefully...")
                await pipeline.stop()
        else:
            await asyncio.gather(
                stream_task,
                monitor_shutdown(),
                return_exceptions=True,
            )

    except asyncio.CancelledError:
        pass

    finally:
        # Print final stats
        stats = pipeline.get_stats()
        print("\n" + "=" * 60)
        print("Final Statistics")
        print("=" * 60)
        print(f"Messages received: {stats['stream']['messages_received']}")
        print(f"Trades processed: {stats['generator']['trades_processed']}")
        print(f"Whale trades seen: {stats['generator']['whale_trades_seen']}")
        print(f"Signals generated: {stats['generator']['signals_generated']}")
        print("=" * 60)


def main():
    """Main entry point with argument parsing."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Real-time whale signal pipeline for Polymarket",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=100.0,
        help="Minimum USD trade size to generate signal (default: 100)",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=1000,
        help="Rolling window size for recent trades (default: 1000)",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Don't load whale addresses from database",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Don't fetch active token IDs from API",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            main_async(
                min_usd_threshold=args.threshold,
                max_window_size=args.window,
                load_whales_from_db=not args.no_db,
                fetch_active_tokens=not args.no_fetch,
            )
        )
    except KeyboardInterrupt:
        print("\nShutdown complete.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Real-time Polymarket price streaming via WebSocket.

This is MUCH faster than polling the REST API for live data.

Usage:
    python realtime_prices.py

Requires:
    pip install websockets
"""

import asyncio
import json
from datetime import datetime
from typing import Set, Dict, Any, Optional

try:
    import websockets
except ImportError:
    print("Install websockets: pip install websockets")
    exit(1)

# Polymarket WebSocket endpoint
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PolymarketPriceStream:
    """Stream real-time prices from Polymarket WebSocket."""

    def __init__(self, token_ids: Set[str], on_price: Optional[callable] = None):
        """
        Args:
            token_ids: Set of CLOB token IDs to subscribe to
            on_price: Callback function(token_id, price, timestamp)
        """
        self.token_ids = token_ids
        self.on_price = on_price or self._default_handler
        self.prices: Dict[str, float] = {}
        self._ws = None

    def _default_handler(self, token_id: str, price: float, timestamp: datetime):
        """Default price handler - just print."""
        print(f"[{timestamp.strftime('%H:%M:%S')}] {token_id[:16]}... = {price:.4f}")

    async def _subscribe(self, ws):
        """Subscribe to price updates for tokens."""
        for token_id in self.token_ids:
            subscribe_msg = {
                "type": "subscribe",
                "channel": "market",
                "market": token_id,
            }
            await ws.send(json.dumps(subscribe_msg))

    async def _handle_message(self, message: str):
        """Process incoming WebSocket message."""
        try:
            data = json.loads(message)

            # Handle price update
            if data.get("type") == "price_change":
                token_id = data.get("market", data.get("asset_id"))
                price = float(data.get("price", 0))
                timestamp = datetime.utcnow()

                if token_id and price > 0:
                    self.prices[token_id] = price
                    self.on_price(token_id, price, timestamp)

            # Handle book update (can extract best bid/ask)
            elif data.get("type") == "book":
                token_id = data.get("market", data.get("asset_id"))
                bids = data.get("bids", [])
                asks = data.get("asks", [])

                if bids and asks:
                    best_bid = float(bids[0]["price"]) if bids else 0
                    best_ask = float(asks[0]["price"]) if asks else 1
                    mid_price = (best_bid + best_ask) / 2
                    timestamp = datetime.utcnow()

                    if token_id:
                        self.prices[token_id] = mid_price
                        self.on_price(token_id, mid_price, timestamp)

        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    async def stream(self, duration_seconds: Optional[int] = None):
        """
        Start streaming prices.

        Args:
            duration_seconds: Stop after this many seconds (None = forever)
        """
        print(f"Connecting to {WS_URL}...")
        print(f"Subscribing to {len(self.token_ids)} tokens...")

        start_time = asyncio.get_event_loop().time()

        async with websockets.connect(WS_URL) as ws:
            self._ws = ws
            await self._subscribe(ws)
            print("Connected! Streaming prices...\n")

            while True:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=30)
                    await self._handle_message(message)

                    if duration_seconds:
                        elapsed = asyncio.get_event_loop().time() - start_time
                        if elapsed >= duration_seconds:
                            print(f"\nStopped after {duration_seconds}s")
                            break

                except asyncio.TimeoutError:
                    # Send ping to keep alive
                    await ws.ping()

                except websockets.ConnectionClosed:
                    print("Connection closed, reconnecting...")
                    await asyncio.sleep(1)
                    break

    def run(self, duration_seconds: Optional[int] = None):
        """Run the stream (blocking)."""
        asyncio.get_event_loop().run_until_complete(self.stream(duration_seconds))


def get_active_token_ids(min_volume: float = 10000, limit: int = 100) -> Set[str]:
    """Fetch token IDs for active markets."""
    import requests

    print(f"Fetching active markets with volume >= ${min_volume:,.0f}...")
    token_ids = set()

    response = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={"limit": limit, "closed": "false"}
    )

    for market in response.json():
        vol = float(market.get("volume24hr", 0) or 0)
        if vol >= min_volume:
            tokens = market.get("clobTokenIds", "[]")
            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens)
                except:
                    tokens = []
            token_ids.update(tokens)

    print(f"Found {len(token_ids)} tokens from {limit} markets")
    return token_ids


# Example: Track whale addresses in real-time
class WhaleTracker:
    """Track prices and alert when whales might trade."""

    def __init__(self, whale_addresses: Set[str]):
        self.whale_addresses = whale_addresses
        self.price_history: Dict[str, list] = {}

    def on_price(self, token_id: str, price: float, timestamp: datetime):
        """Called on each price update."""
        if token_id not in self.price_history:
            self.price_history[token_id] = []

        self.price_history[token_id].append((timestamp, price))

        # Keep last 100 prices
        if len(self.price_history[token_id]) > 100:
            self.price_history[token_id] = self.price_history[token_id][-100:]

        # Check for significant move (potential whale activity)
        history = self.price_history[token_id]
        if len(history) >= 10:
            recent_prices = [p for _, p in history[-10:]]
            price_change = max(recent_prices) - min(recent_prices)

            if price_change > 0.05:  # 5% move
                print(f"[ALERT] {token_id[:16]}... moved {price_change:.1%} in last 10 updates!")


if __name__ == "__main__":
    # Get active token IDs
    token_ids = get_active_token_ids(min_volume=10000, limit=50)

    if not token_ids:
        print("No tokens found")
        exit(1)

    # Stream prices for 60 seconds
    stream = PolymarketPriceStream(token_ids)
    stream.run(duration_seconds=60)

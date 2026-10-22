#!/usr/bin/env python3
"""
Real-time Polymarket whale trade notifier.

Uses the Polymarket market WebSocket and flags large last-trade events.
Note: the market channel does not include trader addresses. "Whale" here
means large notional size based on price * size.
"""

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import requests

try:
    import websockets
except ImportError as exc:
    raise SystemExit("Install websockets: pip install websockets") from exc

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_URL = "https://gamma-api.polymarket.com/markets"

DEFAULT_MIN_USD = 2500.0
DEFAULT_MIN_VOLUME = 10000.0
DEFAULT_MARKET_LIMIT = 50
DEFAULT_COOLDOWN_SECONDS = 10


@dataclass
class TradeEvent:
    asset_id: str
    market: Optional[str]
    side: Optional[str]
    price: float
    size: float
    notional_usd: float
    ts: datetime


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def chunked(values: Iterable[str], size: int) -> List[List[str]]:
    chunk: List[str] = []
    chunks: List[List[str]] = []
    for value in values:
        chunk.append(value)
        if len(chunk) >= size:
            chunks.append(chunk)
            chunk = []
    if chunk:
        chunks.append(chunk)
    return chunks


def get_active_token_ids(min_volume: float, limit: int) -> Set[str]:
    token_ids: Set[str] = set()
    response = requests.get(
        GAMMA_URL,
        params={"limit": limit, "closed": "false"},
        timeout=30,
    )
    response.raise_for_status()
    for market in response.json():
        vol = float(market.get("volume24hr", 0) or 0)
        if vol < min_volume:
            continue
        tokens = market.get("clobTokenIds", "[]")
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except json.JSONDecodeError:
                tokens = []
        token_ids.update(tokens)
    return token_ids


class DiscordNotifier:
    def __init__(self, token: Optional[str], channel_id: Optional[str]):
        self.token = token
        self.channel_id = channel_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.channel_id)

    def send_message(self, message: str) -> bool:
        if not self.enabled:
            return False
        url = f"https://discord.com/api/v10/channels/{self.channel_id}/messages"
        headers = {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }
        payload = {"content": message}
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        return response.status_code in (200, 201)


class PolymarketMarketStream:
    def __init__(
        self,
        token_ids: Set[str],
        min_usd: float,
        on_whale_trade: Optional[callable] = None,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ):
        self.token_ids = token_ids
        self.min_usd = min_usd
        self.on_whale_trade = on_whale_trade or (lambda event: None)
        self.cooldown_seconds = cooldown_seconds
        self._last_alert_at: Dict[str, float] = {}

    def _parse_trade_event(self, data: dict) -> Optional[TradeEvent]:
        event_type = str(data.get("event_type") or data.get("type") or "").lower()
        if event_type not in ("last_trade_price", "trade"):
            return None
        asset_id = data.get("asset_id") or data.get("market") or data.get("token_id")
        if not asset_id:
            return None
        price = float(data.get("price") or 0)
        size = float(data.get("size") or 0)
        if price <= 0 or size <= 0:
            return None
        notional = price * size
        if notional < self.min_usd:
            return None
        ts = datetime.now(timezone.utc)
        return TradeEvent(
            asset_id=asset_id,
            market=data.get("market"),
            side=data.get("side"),
            price=price,
            size=size,
            notional_usd=notional,
            ts=ts,
        )

    def _should_alert(self, event: TradeEvent) -> bool:
        now = event.ts.timestamp()
        last = self._last_alert_at.get(event.asset_id)
        if last and (now - last) < self.cooldown_seconds:
            return False
        self._last_alert_at[event.asset_id] = now
        return True

    async def _subscribe(self, ws) -> None:
        chunks = chunked(self.token_ids, 100)
        if not chunks:
            return
        first = {"type": "MARKET", "assets_ids": chunks[0]}
        await ws.send(json.dumps(first))
        for chunk in chunks[1:]:
            msg = {"operation": "subscribe", "assets_ids": chunk}
            await ws.send(json.dumps(msg))

    async def _handle_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if not isinstance(message, dict):
                continue
            event = self._parse_trade_event(message)
            if event and self._should_alert(event):
                self.on_whale_trade(event)

    async def stream(self, duration_seconds: Optional[int] = None) -> None:
        print(f"Connecting to {WS_URL}...")
        start_time = asyncio.get_event_loop().time()
        async with websockets.connect(WS_URL) as ws:
            await self._subscribe(ws)
            print(f"Subscribed to {len(self.token_ids)} assets")
            while True:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=30)
                    await self._handle_message(message)
                    if duration_seconds:
                        elapsed = asyncio.get_event_loop().time() - start_time
                        if elapsed >= duration_seconds:
                            print(f"Stopped after {duration_seconds}s")
                            break
                except asyncio.TimeoutError:
                    await ws.ping()
                except websockets.ConnectionClosed:
                    print("Connection closed.")
                    break


def format_discord_message(event: TradeEvent) -> str:
    ts = event.ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    side = (event.side or "UNKNOWN").upper()
    return (
        "Predict-ngin whale trade\n"
        f"- Asset: {event.asset_id}\n"
        f"- Side: {side}\n"
        f"- Price: {event.price:.4f}\n"
        f"- Size: {event.size:,.2f}\n"
        f"- Notional: ${event.notional_usd:,.2f}\n"
        f"- Time: {ts}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Polymarket whale trade notifier (market websocket)."
    )
    parser.add_argument("--min-usd", type=float, default=DEFAULT_MIN_USD)
    parser.add_argument("--min-volume", type=float, default=DEFAULT_MIN_VOLUME)
    parser.add_argument("--limit", type=int, default=DEFAULT_MARKET_LIMIT)
    parser.add_argument("--token-ids", type=str, default="")
    parser.add_argument("--duration", type=int, default=None)
    parser.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_SECONDS)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--no-discord", action="store_true")
    return parser.parse_args()


def main() -> None:
    load_env_file(Path(".env"))
    args = parse_args()

    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel_id = os.environ.get("DISCORD_CHANNEL_ID")
    notifier = DiscordNotifier(token, channel_id)

    if args.test:
        ok = notifier.send_message("Predict-ngin test message")
        if ok:
            print("Test message sent.")
            return
        raise SystemExit("Failed to send test message. Check token/channel ID.")

    token_ids: Set[str]
    if args.token_ids:
        token_ids = set(t.strip() for t in args.token_ids.split(",") if t.strip())
    else:
        token_ids = get_active_token_ids(args.min_volume, args.limit)

    if not token_ids:
        raise SystemExit("No token IDs available to subscribe.")

    if args.no_discord or not notifier.enabled:
        print("Discord notifications disabled (missing config or --no-discord).")

    def on_whale_trade(event: TradeEvent) -> None:
        msg = format_discord_message(event)
        print(msg)
        if not args.no_discord and notifier.enabled:
            notifier.send_message(msg)

    stream = PolymarketMarketStream(
        token_ids=token_ids,
        min_usd=args.min_usd,
        on_whale_trade=on_whale_trade,
        cooldown_seconds=args.cooldown,
    )
    asyncio.get_event_loop().run_until_complete(stream.stream(args.duration))


if __name__ == "__main__":
    main()

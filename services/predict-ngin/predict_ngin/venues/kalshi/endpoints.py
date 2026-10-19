"""Kalshi URLs, defined once."""

from __future__ import annotations

__all__ = ["HOST", "API_PREFIX", "BASE_URL", "WS_URL", "WS_SIGN_PATH"]

HOST = "https://api.elections.kalshi.com"
API_PREFIX = "/trade-api/v2"
BASE_URL = HOST + API_PREFIX
WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
# The WebSocket handshake signs this path (timestamp + "GET" + path).
WS_SIGN_PATH = "/trade-api/ws/v2"

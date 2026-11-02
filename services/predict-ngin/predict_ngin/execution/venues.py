"""Trading clients for Kalshi (trade-api v2, Create Order V2) and Polymarket US (retail API v1).

Reads (orders, positions, balance, fills) are always allowed. Every state-changing call goes
through ``_mutate``, which asks the :class:`~predict_ngin.execution.gate.LiveGate` at call time;
``_send`` itself refuses any non-GET request unless ``_mutate`` opened a one-shot permit.
Round-trip times of every request are returned so the latency test can report them.

Kalshi (docs.kalshi.com, checked 2026-10-15 for the LIP pilot):
  POST /portfolio/events/orders
       {ticker, client_order_id, side: bid|ask (YES leg), count, price (YES, dollars),
        time_in_force, post_only, self_trade_prevention_type, cancel_order_on_pause}
  DELETE /portfolio/events/orders/{id}?market_ticker=...
  GET /portfolio/orders|positions|fills|balance
  A NO bid at q is side=ask at YES price 1-q (both outcomes match at the same price).
Polymarket US (docs.polymarket.us, checked 2026-10-17; unverified until an API key exists):
  auth headers X-PM-Access-Key, X-PM-Timestamp (ms),
       X-PM-Signature = base64 Ed25519(timestamp + method + path)
  POST /v1/orders {marketSlug, type, price {value, currency}, quantity, tif,
       participateDontInitiate, outcomeSide, action, manualOrderIndicator}
  POST /v1/order/{id}/cancel {marketSlug}
  GET /v1/orders/open, /v1/portfolio/positions, /v1/account/balances; 25 req/s per IP.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from ..venues.kalshi.endpoints import API_PREFIX as KALSHI_PREFIX
from ..venues.kalshi.endpoints import BASE_URL as KALSHI_BASE
from .gate import LiveGate, MutationBlocked
from .orders import OrderIntent

PMUS_BASE = "https://api.polymarket.us"


class VenueError(RuntimeError):
    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status, self.body = status, body


@dataclass
class Reply:
    status: int
    body: Any
    ms: float


class _Base:
    venue = ""
    base = ""

    def __init__(self, gate: Optional[LiveGate] = None, timeout_s: float = 10.0):
        self.gate = gate or LiveGate(self.venue)
        self.timeout_s = timeout_s
        self._permit = threading.local()
        import requests

        self._http = requests.Session()

    def _headers(self, method: str, path: str) -> Dict[str, str]:
        raise NotImplementedError

    def _send(self, method: str, path: str, *, params=None, json=None, auth=True) -> Reply:
        if method != "GET" and not getattr(self._permit, "open", False):
            raise MutationBlocked(f"{method} {path} refused: not inside a gated mutation")
        headers = self._headers(method, path) if auth else {}
        t0 = time.perf_counter()
        r = self._http.request(
            method,
            self.base + path,
            params=params,
            json=json,
            headers=headers,
            timeout=self.timeout_s,
        )
        ms = (time.perf_counter() - t0) * 1000
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if r.status_code >= 400:
            msg = body.get("error", body) if isinstance(body, dict) else body
            raise VenueError(r.status_code, str(msg)[:300], body)
        return Reply(r.status_code, body, ms)

    def _mutate(
        self, method: str, path: str, *, live: bool, new_order: bool, json=None, params=None
    ) -> Reply:
        self.gate.require(live, new_order=new_order)
        self._permit.open = True
        try:
            return self._send(method, path, json=json, params=params)
        finally:
            self._permit.open = False


class KalshiTrader(_Base):
    venue = "kalshi"
    base = KALSHI_BASE

    def __init__(
        self, signer: Any = None, gate: Optional[LiveGate] = None, timeout_s: float = 10.0
    ):
        super().__init__(gate, timeout_s)
        if signer is None:
            from ..venues.kalshi.auth import KalshiSigner

            signer = KalshiSigner()
        self.signer = signer

    def _headers(self, method: str, path: str) -> Dict[str, str]:
        return self.signer.headers(
            method, KALSHI_PREFIX + path
        )  # signed path excludes the query string

    # reads
    def balance(self) -> Reply:
        return self._send("GET", "/portfolio/balance")

    def open_orders(self, market: Optional[str] = None) -> Reply:
        p = {"status": "resting", "limit": 1000, **({"ticker": market} if market else {})}
        return self._send("GET", "/portfolio/orders", params=p)

    def positions(self) -> Reply:
        return self._send("GET", "/portfolio/positions", params={"limit": 1000})

    def orderbook(self, market: str, depth: int = 10) -> Reply:
        return self._send(
            "GET", f"/markets/{market}/orderbook", params={"depth": depth}, auth=False
        )

    # mutations
    def place(self, o: OrderIntent, *, live: bool) -> Tuple[Reply, Dict[str, Any]]:
        o.validate()
        # YES leg terms: buy YES @p = bid p; buy NO @q = ask (1-q);
        # sell YES @p = ask p; sell NO @q = bid (1-q)
        yes_side = (o.outcome == "yes") == (o.action == "buy")
        yes_px = o.price if o.outcome == "yes" else 1.0 - o.price
        body: Dict[str, Any] = {
            "ticker": o.market,
            "client_order_id": o.client_order_id,
            "side": "bid" if yes_side else "ask",
            "count": f"{o.quantity:g}",
            "price": f"{round(yes_px, 4):.4f}",
            "time_in_force": o.time_in_force,
            "post_only": bool(o.post_only),
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }
        if o.expiration_ts:
            body["expiration_time"] = int(o.expiration_ts)
        return self._mutate(
            "POST", "/portfolio/events/orders", live=live, new_order=True, json=body
        ), body

    def cancel(self, order_id: str, market: Optional[str] = None, *, live: bool) -> Reply:
        params = {"market_ticker": market} if market else None
        return self._mutate(
            "DELETE",
            f"/portfolio/events/orders/{order_id}",
            live=live,
            new_order=False,
            params=params,
        )


class PolymarketUSTrader(_Base):
    """Needs POLYMARKET_US_KEY_ID and POLYMARKET_US_SECRET_KEY (environment or .env).

    Create them at polymarket.us/developer.
    """

    venue = "polymarket_us"
    base = PMUS_BASE

    def __init__(
        self,
        key_id: Optional[str] = None,
        secret: Optional[str] = None,
        gate: Optional[LiveGate] = None,
        timeout_s: float = 10.0,
    ):
        super().__init__(gate, timeout_s)
        from ..shared.paths import repo_root
        from ..venues.kalshi.auth import read_dotenv

        env = {**read_dotenv(repo_root() / ".env"), **os.environ}
        self.key_id: str = key_id or env.get("POLYMARKET_US_KEY_ID") or ""
        secret = secret or env.get("POLYMARKET_US_SECRET_KEY")
        if not self.key_id or not secret:
            raise RuntimeError(
                "Polymarket US credentials not configured (POLYMARKET_US_KEY_ID / _SECRET_KEY)"
            )
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self._key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(secret)[:32])

    def __repr__(self) -> str:
        return "PolymarketUSTrader(<redacted>)"

    def _headers(self, method: str, path: str) -> Dict[str, str]:
        ts = str(int(time.time() * 1000))
        sig = base64.b64encode(self._key.sign(f"{ts}{method}{path}".encode())).decode()
        return {
            "X-PM-Access-Key": self.key_id,
            "X-PM-Timestamp": ts,
            "X-PM-Signature": sig,
            "Content-Type": "application/json",
        }

    def balance(self) -> Reply:
        return self._send("GET", "/v1/account/balances")

    def open_orders(self, market: Optional[str] = None) -> Reply:
        return self._send("GET", "/v1/orders/open", params={"slugs": market} if market else None)

    def positions(self) -> Reply:
        return self._send("GET", "/v1/portfolio/positions")

    def place(self, o: OrderIntent, *, live: bool) -> Tuple[Reply, Dict[str, Any]]:
        o.validate()
        body = {
            "marketSlug": o.market,
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": f"{o.price:.4f}".rstrip("0").rstrip("."), "currency": "USD"},
            "quantity": float(o.quantity),
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": bool(o.post_only),
            "outcomeSide": "OUTCOME_SIDE_YES" if o.outcome == "yes" else "OUTCOME_SIDE_NO",
            "action": "ORDER_ACTION_BUY" if o.action == "buy" else "ORDER_ACTION_SELL",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        }
        return self._mutate("POST", "/v1/orders", live=live, new_order=True, json=body), body

    def cancel(self, order_id: str, market: Optional[str] = None, *, live: bool) -> Reply:
        return self._mutate(
            "POST",
            f"/v1/order/{order_id}/cancel",
            live=live,
            new_order=False,
            json={"marketSlug": market} if market else {},
        )


def make_trader(venue: str, gate: Optional[LiveGate] = None):
    if venue == "kalshi":
        return KalshiTrader(gate=gate)
    if venue == "polymarket_us":
        return PolymarketUSTrader(gate=gate)
    raise ValueError(f"unknown venue {venue!r} (kalshi | polymarket_us)")

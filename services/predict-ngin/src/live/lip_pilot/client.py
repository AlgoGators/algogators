"""Kalshi trade-api v2 client for the LIP pilot.

Two defences keep a dry run from ever changing exchange state:

1. Every mutating method (create / cancel / batch-cancel) goes through ``_mutate``. That checks the
   three-gate ``LiveGate`` *at call time* and raises ``MutationBlocked`` unless it is armed.
2. ``_send`` refuses any non-GET HTTP method unless ``_mutate`` has opened a one-shot permit.

There is deliberately no amend endpoint and no way to send an order without ``post_only=True``.

Endpoints (docs.kalshi.com, checked 2026-10-15):
- ``POST   /portfolio/events/orders``          Create Order V2. Body: ticker, client_order_id, side (bid|ask,
  YES leg), count (fixed-point string), price (fixed-point dollars, YES price), time_in_force,
  post_only (bool), self_trade_prevention_type, cancel_order_on_pause, expiration_time.
  A NO bid at q is sent as side=ask at YES price 1-q (order_direction.md: bid == yes, ask == no, and
  orders on both outcomes match at the same price p).
- ``DELETE /portfolio/events/orders/{id}``      Cancel Order V2 (``market_ticker`` query for routing).
- ``DELETE /portfolio/events/orders/batched``   Batch cancel V2 (2 write tokens per cancel).
- ``GET    /portfolio/orders|positions|fills|balance|settlements`` (authenticated, read-only).
- ``GET    /incentive_programs``, ``/markets``, ``/markets/{t}``, ``/markets/{t}/orderbook``,
  ``/exchange/status`` (public).
"""
from __future__ import annotations

import email.utils
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from .config import LiveGate
from .ratelimit import TokenBucket

ORDER_WRITE_COST = 10.0
CANCEL_WRITE_COST = 10.0
BATCH_CANCEL_COST_PER_ORDER = 2.0
READ_METHODS = frozenset({"GET"})


class MutationBlocked(RuntimeError):
    """A state-changing call was attempted without all three live gates."""


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.body = body


@dataclass
class Response:
    status: int
    body: Any
    headers: Dict[str, str] = field(default_factory=dict)


class Transport:
    """HTTP layer. Tests replace it with a fake that records calls."""

    def request(self, method: str, url: str, *, params=None, json=None, headers=None,
                timeout: float = 15.0) -> Response:  # pragma: no cover - real network
        import requests
        r = requests.request(method, url, params=params, json=json, headers=headers, timeout=timeout)
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return Response(r.status_code, body, dict(r.headers))


def _fmt_price(p: float) -> str:
    return f"{p:.4f}"


class KalshiClient:
    def __init__(self, base_url: str, gate: LiveGate, transport: Optional[Transport] = None,
                 signer: Any = None, timeout_s: float = 15.0,
                 read_bucket: Optional[TokenBucket] = None, write_bucket: Optional[TokenBucket] = None,
                 sleep: Callable[[float], None] = time.sleep, wall_clock: Callable[[], float] = time.time):
        self.base_url = base_url.rstrip("/")
        self._path_prefix = urlparse(self.base_url).path
        self.gate = gate
        self.transport = transport or Transport()
        self.signer = signer
        self.timeout_s = timeout_s
        self.read_bucket = read_bucket or TokenBucket(5.0, 5.0)
        self.write_bucket = write_bucket or TokenBucket(20.0, 60.0)
        self._sleep = sleep
        self._wall = wall_clock
        self._permit = threading.local()
        self.last_clock_skew_s: Optional[float] = None
        self.calls: List[Tuple[str, str]] = []  # (method, path) audit trail

    # ------------------------------------------------------------------ low level
    def _send(self, method: str, path: str, *, params=None, json=None, auth: bool = False,
              retries: int = 3) -> Any:
        method = method.upper()
        if method not in READ_METHODS and not getattr(self._permit, "open", False):
            raise MutationBlocked(f"{method} {path} refused: not inside a gated mutation")
        url = self.base_url + path
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            headers = {}
            if auth:
                if self.signer is None:
                    raise MutationBlocked(f"{method} {path}: authenticated call without a signer")
                headers = self.signer.headers(method, self._path_prefix + path)
            self.calls.append((method, path))
            try:
                resp = self.transport.request(method, url, params=params, json=json, headers=headers,
                                              timeout=self.timeout_s)
            except Exception as exc:  # network error: retry (writes are idempotent via client_order_id)
                last_exc = exc
                self._sleep(min(8.0, 0.5 * 2 ** attempt))
                continue
            self._note_clock(resp.headers)
            if resp.status == 429 or resp.status >= 500:
                last_exc = ApiError(resp.status, "retryable", resp.body)
                self._sleep(min(8.0, 0.5 * 2 ** attempt))
                continue
            if resp.status >= 400:
                msg = resp.body.get("error", resp.body) if isinstance(resp.body, dict) else resp.body
                raise ApiError(resp.status, str(msg)[:300], resp.body)
            return resp.body
        if isinstance(last_exc, ApiError):
            raise last_exc
        raise ApiError(0, f"network error: {type(last_exc).__name__}")

    def _note_clock(self, headers: Dict[str, str]) -> None:
        date = None
        for k, v in (headers or {}).items():
            if k.lower() == "date":
                date = v
        if not date:
            return
        try:
            server = email.utils.parsedate_to_datetime(date).timestamp()
        except (TypeError, ValueError):
            return
        self.last_clock_skew_s = self._wall() - server

    def _get(self, path: str, params=None, auth: bool = False) -> Any:
        self.read_bucket.acquire(1.0)
        return self._send("GET", path, params=params, auth=auth)

    def _mutate(self, method: str, path: str, cost: float, json=None, params=None) -> Any:
        if not self.gate.armed:
            raise MutationBlocked(f"{method} {path} refused: live gates not armed ({'; '.join(self.gate.reasons())})")
        if self.signer is None:
            raise MutationBlocked("live mutation requires a signer")
        self.write_bucket.acquire(cost)
        self._permit.open = True
        try:
            return self._send(method, path, params=params, json=json, auth=True, retries=2)
        finally:
            self._permit.open = False

    # ------------------------------------------------------------------ public reads
    def exchange_status(self) -> Dict[str, Any]:
        return self._get("/exchange/status")

    def incentive_programs(self, status: str = "active", type_: str = "liquidity",
                           max_pages: int = 20) -> List[Dict[str, Any]]:
        out, cursor = [], None
        for _ in range(max_pages):
            params = {"status": status, "type": type_, "limit": 10000}
            if cursor:
                params["cursor"] = cursor
            d = self._get("/incentive_programs", params)
            out += d.get("incentive_programs", []) or []
            cursor = d.get("next_cursor")
            if not cursor:
                break
        return out

    def markets(self, series_ticker: str, status: str = "open") -> List[Dict[str, Any]]:
        out, cursor = [], None
        for _ in range(20):
            params = {"series_ticker": series_ticker, "status": status, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            d = self._get("/markets", params)
            out += d.get("markets", []) or []
            cursor = d.get("cursor")
            if not cursor:
                break
        return out

    def market(self, ticker: str) -> Dict[str, Any]:
        return self._get(f"/markets/{ticker}").get("market", {})

    def orderbook(self, ticker: str, depth: int = 0) -> Dict[str, Any]:
        return self._get(f"/markets/{ticker}/orderbook", {"depth": depth})

    # ------------------------------------------------------------------ authenticated reads
    def _paged(self, path: str, key: str, params: Dict[str, Any], cursor_key: str = "cursor") -> List[Dict]:
        out, cursor = [], None
        for _ in range(50):
            p = dict(params)
            if cursor:
                p["cursor"] = cursor
            d = self._get(path, p, auth=True)
            out += d.get(key, []) or []
            cursor = d.get(cursor_key)
            if not cursor:
                break
        return out

    def get_orders(self, status: str = "resting", ticker: Optional[str] = None) -> List[Dict[str, Any]]:
        p: Dict[str, Any] = {"status": status, "limit": 1000}
        if ticker:
            p["ticker"] = ticker
        return self._paged("/portfolio/orders", "orders", p)

    def get_positions(self) -> List[Dict[str, Any]]:
        return self._paged("/portfolio/positions", "market_positions", {"limit": 1000})

    def get_fills(self, min_ts: Optional[int] = None) -> List[Dict[str, Any]]:
        p: Dict[str, Any] = {"limit": 1000}
        if min_ts:
            p["min_ts"] = int(min_ts)
        return self._paged("/portfolio/fills", "fills", p)

    def get_balance(self) -> Dict[str, Any]:
        return self._get("/portfolio/balance", auth=True)

    def get_settlements(self, min_ts: Optional[int] = None) -> List[Dict[str, Any]]:
        p: Dict[str, Any] = {"limit": 1000}
        if min_ts:
            p["min_ts"] = int(min_ts)
        return self._paged("/portfolio/settlements", "settlements", p)

    # ------------------------------------------------------------------ mutations (gated)
    def create_order(self, *, ticker: str, client_order_id: str, outcome: str, count: int,
                     price: float, expiration_ts: Optional[int] = None) -> Dict[str, Any]:
        """Post-only GTC limit BUY of ``outcome`` ('yes'|'no') at ``price`` (that outcome's price)."""
        if outcome not in ("yes", "no"):
            raise ValueError("outcome must be yes|no")
        if not (isinstance(count, int) and count > 0):
            raise ValueError("count must be a positive int")
        if not (0.0 < price < 1.0):
            raise ValueError("price out of range")
        side, yes_price = ("bid", price) if outcome == "yes" else ("ask", 1.0 - price)
        body = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": side,
            "count": str(int(count)),
            "price": _fmt_price(round(yes_price, 4)),
            "time_in_force": "good_till_canceled",
            "post_only": True,
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
        }
        if expiration_ts:
            body["expiration_time"] = int(expiration_ts)
        assert body["post_only"] is True
        return self._mutate("POST", "/portfolio/events/orders", ORDER_WRITE_COST, json=body)

    def cancel_order(self, order_id: str, ticker: Optional[str] = None) -> Dict[str, Any]:
        params = {"market_ticker": ticker} if ticker else None
        return self._mutate("DELETE", f"/portfolio/events/orders/{order_id}", CANCEL_WRITE_COST, params=params)

    def batch_cancel(self, orders: Iterable[Tuple[str, Optional[str]]]) -> Dict[str, Any]:
        items = [{"order_id": oid, **({"market_ticker": t} if t else {})} for oid, t in orders]
        if not items:
            return {"orders": []}
        cost = max(BATCH_CANCEL_COST_PER_ORDER * len(items), 1.0)
        return self._mutate("DELETE", "/portfolio/events/orders/batched", cost, json={"orders": items})

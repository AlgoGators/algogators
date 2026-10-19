"""Paper fills against public venue data, with queue position (no keys, no orders).

An order joining the best price waits behind the size displayed there when it was placed; public
trades at its price eat that queue first, and only then fill the order. A trade through its price
fills the rest. An order priced better than the best (inside the spread) starts first in line.
This is the fill model of the mmq replay study; it never improves on the queue (cancellations
ahead do not advance us), so paper results are cautious.

Kalshi: public ``GET /markets/{t}/orderbook`` and ``GET /markets/trades?ticker=...&min_ts=``.
Polymarket US: public gateway book only (no public trade tape), so an order fills only when the
opposite best price crosses it (trade-through); queue fills are not modelled there.

Kalshi requests are batched: tops of book for many markets come from one
``GET /markets?tickers=...`` (best bid / ask with sizes) and fills from one call to the global
public trade feed per poll, whatever the number of markets and orders. A trade counts once (by
trade id) and only if it happened after the order was placed. Requests are paced to ``max_rps``;
:meth:`PaperBook.prefetch` and :meth:`PaperBook.best` share the book cache, so a strategy deciding
on the same books costs no extra request.
"""

from __future__ import annotations

import datetime as dt
import itertools
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from .orders import OrderAck, OrderIntent


@dataclass
class PaperOrder:
    intent: OrderIntent
    order_id: str
    queue_ahead: float
    remaining: float
    placed_ts: float  # unix seconds (fractional)
    filled: float = 0.0


class PaperBook:
    def __init__(self, venue: str, max_rps: float = 10.0, book_ttl_s: float = 1.0, signer=None):
        """``signer`` (a :class:`~predict_ngin.venues.kalshi.auth.KalshiSigner`) signs the Kalshi
        GETs, so they count against the account's read limit instead of the IP's."""
        if venue not in ("kalshi", "polymarket_us"):
            raise ValueError(venue)
        import requests

        self.venue = venue
        self.http = requests.Session()
        self.orders: Dict[str, PaperOrder] = {}
        self.fills: List[dict] = []
        self._ids = itertools.count(1)
        self.max_rps, self.book_ttl_s = max_rps, book_ttl_s
        self._last_req = 0.0
        self._books: Dict[str, tuple] = {}  # market -> (fetched at, best tuple)
        self._seen: Dict[str, float] = {}  # trade id -> trade time (pruned after SEEN_KEEP_S)
        self._trades_since: Optional[float] = None
        self.requests = 0
        self.signer = signer

    def _get(self, url: str, **params):
        wait = self._last_req + 1.0 / self.max_rps - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        from urllib.parse import urlparse

        for attempt in range(4):
            headers = None
            if self.signer is not None and self.venue == "kalshi":
                headers = self.signer.headers("GET", urlparse(url).path)  # fresh timestamp
            self._last_req = time.monotonic()
            self.requests += 1
            r = self.http.get(url, params=params or None, headers=headers, timeout=10)
            if r.status_code != 429 or attempt == 3:
                break
            time.sleep(0.5 * 2**attempt)  # rate limited: back off and retry
        r.raise_for_status()
        return r.json()

    def best(self, market: str, max_age_s: Optional[float] = None):
        """(best YES bid, size, best YES ask, size), cached for ``book_ttl_s``."""
        age = self.book_ttl_s if max_age_s is None else max_age_s
        hit = self._books.get(market)
        if hit and time.monotonic() - hit[0] <= age:
            return hit[1]
        if self.venue == "kalshi":
            self.prefetch([market])
            return self._books[market][1]
        b = self._best(market)
        self._books[market] = (time.monotonic(), b)
        return b

    def prefetch(self, markets: List[str]) -> None:
        """Refresh the cached tops of book of ``markets`` (Kalshi: one request per 50 markets)."""
        if self.venue != "kalshi":
            for m in markets:
                self._books[m] = (time.monotonic(), self._best(m))
            return
        from ..venues.kalshi.endpoints import BASE_URL

        markets = list(dict.fromkeys(markets))
        for i in range(0, len(markets), 50):
            chunk = markets[i : i + 50]
            body = self._get(f"{BASE_URL}/markets", tickers=",".join(chunk), limit=len(chunk))
            t = time.monotonic()
            got = {m.get("ticker"): m for m in body.get("markets") or []}
            for m in chunk:
                self._books[m] = (t, _kalshi_top(got.get(m) or {}))

    # ------------------------------------------------------------------ public data
    def _best(self, market: str):
        """Polymarket US: (best YES bid, size, best YES ask, size) from the public gateway book."""
        g = self._get(f"https://gateway.polymarket.us/v1/markets/{market}/book")
        bids, asks = g.get("bids") or [], g.get("offers") or g.get("asks") or []

        def lv(x):
            px = x.get("px")
            return float(px["value"] if isinstance(px, dict) else px), float(x.get("qty") or 0)

        b = max((lv(x) for x in bids), default=(None, 0.0))
        a = min((lv(x) for x in asks), default=(None, 0.0))
        return b[0], b[1], a[0], a[1]

    def _yes_terms(self, o: OrderIntent):
        """(+1 resting YES bid / -1 resting YES ask, YES price)."""
        bid = (o.outcome == "yes") == (o.action == "buy")
        return (1 if bid else -1), (o.price if o.outcome == "yes" else round(1 - o.price, 4))

    # ------------------------------------------------------------------ orders
    def place(self, o: OrderIntent) -> OrderAck:
        side, px = self._yes_terms(o)
        bb, bsz, ba, asz = self.best(o.market)
        if o.post_only and (
            (side > 0 and ba is not None and px >= ba) or (side < 0 and bb is not None and px <= bb)
        ):
            return OrderAck(
                self.venue,
                o.market,
                o.client_order_id,
                None,
                "rejected",
                raw={"reason": "would cross"},
            )
        same = (side > 0 and bb is not None and abs(px - bb) < 1e-9) or (
            side < 0 and ba is not None and abs(px - ba) < 1e-9
        )
        q = (bsz if side > 0 else asz) if same else 0.0
        oid = f"paper-{next(self._ids)}"
        self.orders[oid] = PaperOrder(o, oid, q, float(o.quantity), time.time())
        return OrderAck(
            self.venue, o.market, o.client_order_id, oid, "paper", 0.0, float(o.quantity)
        )

    def cancel(self, order_id: str) -> Optional[float]:
        po = self.orders.pop(order_id, None)
        return None if po is None else po.remaining

    def _trades(self, since: float, markets: Set[str]) -> Dict[str, List[dict]]:
        """Unseen Kalshi public trades on ``markets`` since ``since`` (unix s), oldest first.

        One call to the global trade feed (paged when busy) covers every market."""
        from ..venues.kalshi.endpoints import BASE_URL

        # earlier trades were already applied (orders placed since then cannot need them)
        start = since if self._trades_since is None else self._trades_since
        out: List[dict] = []
        cursor = None
        for _ in range(10):
            params = {"min_ts": int(start) - 1, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            body = self._get(f"{BASE_URL}/markets/trades", **params)
            page = body.get("trades") or []
            out += page
            cursor = body.get("cursor")
            if not cursor or len(page) < 1000:
                break
        self._trades_since = time.time() - 5  # overlap; duplicates are dropped by trade id
        by: Dict[str, List[dict]] = {}
        for t in out:
            tid = t.get("trade_id") or "|".join(
                str(t.get(k))
                for k in ("ticker", "created_time", "count_fp", "yes_price_dollars", "taker_side")
            )
            if tid in self._seen:
                continue
            ts = _unix(t.get("created_time"))
            self._seen[tid] = ts or time.time()
            if t.get("ticker") in markets:
                t["_ts"] = ts
                by.setdefault(t["ticker"], []).append(t)
        cut = time.time() - SEEN_KEEP_S
        if len(self._seen) > 20_000:
            self._seen = {k: v for k, v in self._seen.items() if v >= cut}
        return {m: sorted(v, key=lambda x: x["_ts"] or 0.0) for m, v in by.items()}

    def forget(self, market: str) -> None:
        """Drop a market's cached book (after it closes)."""
        self._books.pop(market, None)

    def poll(self) -> List[dict]:
        """Advance resting paper orders with public trades (Kalshi) and the book; new fills."""
        new: List[dict] = []
        if not self.orders:
            return new
        markets = sorted({po.intent.market for po in self.orders.values()})
        trades: Dict[str, List[dict]] = {}
        if self.venue == "kalshi":
            first = min(po.placed_ts for po in self.orders.values())
            trades = self._trades(first, set(markets))
        stale = [
            m
            for m in markets
            if m not in self._books or time.monotonic() - self._books[m][0] > self.book_ttl_s
        ]
        if stale:
            self.prefetch(stale)
        for m in markets:
            mine = [po for po in self.orders.values() if po.intent.market == m]
            bb, _, ba, _ = self._books[m][1]
            for po in mine:
                got = self._advance(po, trades.get(m, []), bb, ba)
                if got > 0:
                    po.remaining -= got
                    po.filled += got
                    fill = {
                        "order_id": po.order_id,
                        "market": m,
                        "outcome": po.intent.outcome,
                        "action": po.intent.action,
                        "price": po.intent.price,
                        "quantity": got,
                        "ts": time.time(),
                    }
                    self.fills.append(fill)
                    new.append(fill)
                if po.remaining <= 1e-9:
                    self.orders.pop(po.order_id, None)
        return new

    def _advance(self, po: PaperOrder, trades: List[dict], bb, ba) -> float:
        """Contracts of ``po`` filled by ``trades`` (those after it was placed) and the book now."""
        side, px = self._yes_terms(po.intent)
        got = 0.0
        for t in trades:
            if t["_ts"] is not None and t["_ts"] < po.placed_ts:
                continue
            tp = float(t.get("yes_price_dollars") or float(t.get("yes_price", 0)) / 100)
            q = float(t.get("count_fp") or t.get("count") or 0)
            taker_sold_yes = t.get("taker_side") == "no"
            hits = (side > 0 and taker_sold_yes and tp <= px + 1e-9) or (
                side < 0 and not taker_sold_yes and tp >= px - 1e-9
            )
            if not hits:
                continue
            if (side > 0 and tp < px - 1e-9) or (side < 0 and tp > px + 1e-9):
                return po.remaining  # traded through our price
            po.queue_ahead -= q
            if po.queue_ahead < 0:
                got += min(po.remaining - got, -po.queue_ahead)
                po.queue_ahead = 0.0
            if got >= po.remaining:
                return po.remaining
        if (side > 0 and ba is not None and ba <= px) or (side < 0 and bb is not None and bb >= px):
            return po.remaining  # the other side crossed our price: we would have traded
        return got


SEEN_KEEP_S = 900.0


def _kalshi_top(m: dict):
    """(best YES bid, size, best YES ask, size) from a ``GET /markets`` row; None where empty."""

    def f(k):
        try:
            return float(m.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0

    bb, ba = f("yes_bid_dollars"), f("yes_ask_dollars")
    bs, as_ = f("yes_bid_size_fp"), f("yes_ask_size_fp")
    return (bb if bb > 0 else None), bs, (ba if 0 < ba < 1 else None), as_


def _unix(s) -> Optional[float]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None

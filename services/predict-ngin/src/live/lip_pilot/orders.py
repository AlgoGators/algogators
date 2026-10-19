"""Order manager: one post-only resting bid per (market, side), cancel-then-replace on reprice.

Gateways:
- ``DryRunGateway`` logs what it WOULD do. It holds no client and cannot reach the network.
- ``LiveGateway`` calls the gated ``KalshiClient`` (which itself refuses unless all three gates are armed).

Client order ids are deterministic per intent: ``lip-<run_id>-<seq>``. A retry of the same intent (network
error / 5xx inside the client) re-sends the same id, so Kalshi deduplicates; a 409 conflict is resolved by
looking the id up among resting orders.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .client import ApiError, KalshiClient
from .journal import Journal

CLIENT_ID_PREFIX = "lip-"


@dataclass
class ManagedOrder:
    ticker: str
    outcome: str
    px: float
    size: int
    client_order_id: str
    order_id: Optional[str]
    placed_ts: float
    filled: float = 0.0           # contracts filled on this order (known so far)


class ClientIdFactory:
    def __init__(self, run_id: str):
        if not run_id or len(run_id) > 40:
            raise ValueError("run_id must be 1..40 chars")
        self.run_id = run_id
        self._seq = itertools.count(1)

    def next(self) -> str:
        return f"{CLIENT_ID_PREFIX}{self.run_id}-{next(self._seq):06d}"

    def owns(self, client_order_id: Optional[str]) -> bool:
        return bool(client_order_id) and str(client_order_id).startswith(f"{CLIENT_ID_PREFIX}{self.run_id}-")


def is_pilot_id(client_order_id: Optional[str]) -> bool:
    return bool(client_order_id) and str(client_order_id).startswith(CLIENT_ID_PREFIX)


class PostOnlyRejected(RuntimeError):
    pass


class DryRunGateway:
    live = False

    def __init__(self, journal: Journal):
        self.journal = journal
        self._n = itertools.count(1)

    def place(self, ticker, outcome, px, size, client_order_id, expiration_ts) -> Tuple[str, float]:
        oid = f"dry-{next(self._n)}"
        self.journal.write("would_place", ticker=ticker, side=outcome, px=px, size=size, post_only=True,
                           client_order_id=client_order_id, expiration_ts=expiration_ts, order_id=oid)
        return oid, 0.0

    def cancel(self, order: ManagedOrder, reason: str) -> Optional[float]:
        self.journal.write("would_cancel", ticker=order.ticker, side=order.outcome, order_id=order.order_id,
                           client_order_id=order.client_order_id, reason=reason)
        return float(order.size - order.filled)

    def cancel_many(self, orders: List[ManagedOrder], reason: str) -> None:
        for o in orders:
            self.cancel(o, reason)


class LiveGateway:
    live = True

    def __init__(self, client: KalshiClient, journal: Journal):
        self.client = client
        self.journal = journal

    def place(self, ticker, outcome, px, size, client_order_id, expiration_ts) -> Tuple[str, float]:
        self.journal.write("place", ticker=ticker, side=outcome, px=px, size=size, post_only=True,
                           client_order_id=client_order_id, expiration_ts=expiration_ts)
        try:
            r = self.client.create_order(ticker=ticker, client_order_id=client_order_id, outcome=outcome,
                                         count=int(size), price=px, expiration_ts=expiration_ts)
        except ApiError as e:
            if e.status == 409:  # duplicate client_order_id: the first attempt landed
                for o in self.client.get_orders(status="resting", ticker=ticker):
                    if o.get("client_order_id") == client_order_id:
                        self.journal.write("place_dedup", client_order_id=client_order_id, order_id=o.get("order_id"))
                        return o.get("order_id"), float(o.get("fill_count_fp") or 0)
            raise
        fill = float(r.get("fill_count") or 0)
        remaining = float(r.get("remaining_count") or 0)
        self.journal.write("placed", client_order_id=client_order_id, order_id=r.get("order_id"),
                           fill_count=fill, remaining_count=remaining)
        if fill > 0:
            # post-only must never take; treat as a hard failure (the runner kills on this)
            raise PostOnlyRejected(f"post-only order {client_order_id} filled {fill} on entry")
        if remaining <= 0:
            raise PostOnlyRejected(f"post-only order {client_order_id} cancelled on entry (would cross)")
        return r.get("order_id"), fill

    def cancel(self, order: ManagedOrder, reason: str) -> Optional[float]:
        self.journal.write("cancel", ticker=order.ticker, side=order.outcome, order_id=order.order_id,
                           client_order_id=order.client_order_id, reason=reason)
        try:
            r = self.client.cancel_order(order.order_id, order.ticker)
        except ApiError as e:
            if e.status == 404:   # already gone (filled / expired); reconciliation settles the fill count
                self.journal.write("cancel_missing", order_id=order.order_id)
                return None
            raise
        reduced = float(r.get("reduced_by") or 0)
        self.journal.write("cancelled", order_id=order.order_id, reduced_by=reduced)
        return reduced

    def cancel_many(self, orders: List[ManagedOrder], reason: str, chunk: int = 20) -> None:
        for i in range(0, len(orders), chunk):
            part = orders[i:i + chunk]
            self.journal.write("cancel_batch", reason=reason, order_ids=[o.order_id for o in part])
            r = self.client.batch_cancel([(o.order_id, o.ticker) for o in part])
            for item in (r or {}).get("orders", []) or []:
                if item.get("error"):
                    self.journal.write("cancel_batch_error", order_id=item.get("order_id"), error=item.get("error"))


@dataclass
class SideState:
    filled: float = 0.0           # contracts filled on this side since start (all orders)
    cost: float = 0.0
    order: Optional[ManagedOrder] = None
    last_change_ts: float = -1e18


class OrderManager:
    def __init__(self, gateway, ids: ClientIdFactory, journal: Journal, min_reprice_interval_s: float,
                 on_reprice: Optional[Callable[[str, str, float], None]] = None):
        self.gw = gateway
        self.ids = ids
        self.journal = journal
        self.min_reprice_interval_s = min_reprice_interval_s
        self.on_reprice = on_reprice
        self.sides: Dict[Tuple[str, str], SideState] = {}
        self.history: Dict[str, Tuple[SideState, ManagedOrder]] = {}
        self.fills_by_order: Dict[str, float] = {}

    def side(self, ticker: str, outcome: str) -> SideState:
        return self.sides.setdefault((ticker, outcome), SideState())

    def resting(self) -> List[ManagedOrder]:
        return [s.order for s in self.sides.values() if s.order is not None]

    def _record_fill(self, st: SideState, o: ManagedOrder, new_filled: float) -> float:
        delta = max(0.0, new_filled - o.filled)
        if delta > 0:
            o.filled += delta
            st.filled += delta
            st.cost += delta * o.px
        return delta

    def apply_fill(self, order_id: str, count: float) -> Optional[Tuple[ManagedOrder, float]]:
        """Fill reported by GET /portfolio/fills (each fill_id once). Fills are summed per order and merged
        with the cancel-derived count by max(), so a fill reported after its cancel is not double counted.
        Returns (order, newly counted contracts), or None if the order was never ours."""
        hit = self.history.get(order_id)
        if hit is None:
            return None
        st, o = hit
        self.fills_by_order[order_id] = self.fills_by_order.get(order_id, 0.0) + float(count)
        delta = self._record_fill(st, o, max(o.filled, self.fills_by_order[order_id]))
        if st.order is o and o.filled >= o.size - 1e-9:
            st.order = None
        return o, delta

    def sync(self, ticker: str, outcome: str, px: Optional[float], size: int, now: float,
             expiration_ts: Optional[int], reason: str) -> None:
        st = self.side(ticker, outcome)
        cur = st.order
        if px is None or size <= 0:
            if cur is not None:
                self._cancel(st, cur, reason)
            return
        if cur is not None:
            remaining = cur.size - cur.filled
            same_px = abs(cur.px - px) < 1e-9
            if same_px and int(round(remaining)) <= size:
                return  # keep queue position; a smaller remaining (partial fill) is fine
            if same_px is False and now - st.last_change_ts < self.min_reprice_interval_s and int(round(remaining)) <= size:
                return  # throttle price-only changes; size reductions (caps) are never throttled
            self._cancel(st, cur, "reprice" if not same_px else "resize")
            if self.on_reprice and not same_px:
                self.on_reprice(ticker, outcome, now)
        cid = self.ids.next()
        oid, fill = self.gw.place(ticker, outcome, px, int(size), cid, expiration_ts)
        st.order = ManagedOrder(ticker, outcome, px, int(size), cid, oid, now)
        st.last_change_ts = now
        if oid:
            self.history[oid] = (st, st.order)
        if fill:
            self._record_fill(st, st.order, fill)

    def _cancel(self, st: SideState, o: ManagedOrder, reason: str) -> None:
        reduced = self.gw.cancel(o, reason)
        if reduced is not None:
            # anything not cancelled was filled
            self._record_fill(st, o, max(o.filled, o.size - reduced))
        st.order = None

    def cancel_all(self, reason: str) -> int:
        orders = self.resting()
        if orders:
            self.gw.cancel_many(orders, reason)
        for st in self.sides.values():
            st.order = None
        self.journal.write("cancel_all", reason=reason, n=len(orders))
        return len(orders)

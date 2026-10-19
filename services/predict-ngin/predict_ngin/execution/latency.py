"""Measure real order latency: post-only 1-contract bids far below the market, then cancel.

Each trial: POST create (round trip), poll GET open orders until the order shows (visible), DELETE
cancel (round trip), then confirm it is gone. Default order: BUY 1 YES at the minimum price (1c on
Kalshi) in a market whose best YES bid is well above it, so it cannot fill in practice; the worst
case per trial is the price of one contract. Also times plain authenticated GETs as a baseline.
Everything is journaled. Live only (all gates + --live).
"""

from __future__ import annotations

import statistics
import time
from typing import List, Optional

from .brokers import make_broker
from .orders import OrderIntent, new_client_order_id


def _pick_kalshi_market(trader) -> str:
    """An open market with a best YES bid >= 10c (so a 1c bid sits far from the touch)."""
    import requests

    from ..venues.kalshi.endpoints import BASE_URL

    for series in ("KXINXU", "KXNASDAQ100U", "KXBTCD", "KXHIGHNY"):
        ms = (
            requests.get(
                f"{BASE_URL}/markets",
                params={"status": "open", "series_ticker": series, "limit": 200},
                timeout=20,
            )
            .json()
            .get("markets")
            or []
        )
        for m in ms:
            bid = float(m.get("yes_bid_dollars") or 0) or float(m.get("yes_bid") or 0) / 100
            if bid >= 0.10:
                return m["ticker"]
    raise RuntimeError("no suitable open market found; pass --market")


def run(
    venue: str, market: Optional[str], trials: int = 3, price: float = 0.01, live: bool = False
) -> dict:
    b = make_broker(venue, live=live)
    t = b.trader
    if market is None:
        if venue != "kalshi":
            raise ValueError("pass --market for Polymarket US")
        market = _pick_kalshi_market(t)
    get_ms = [t.balance().ms for _ in range(3)]
    res: List[dict] = []
    for i in range(trials):
        intent = OrderIntent(
            venue=venue,
            market=market,
            outcome="yes",
            price=price,
            quantity=1,
            client_order_id=new_client_order_id("lat"),
        )
        ack = b.place(intent)
        assert ack.order_id, f"order not acknowledged: {ack}"
        t_visible = None
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 10:
            body = t.open_orders(market).body
            orders = (body.get("orders") or []) if isinstance(body, dict) else []
            if any((o.get("order_id") or o.get("id")) == ack.order_id for o in orders):
                t_visible = (time.perf_counter() - t0) * 1000
                break
        c0 = time.perf_counter()
        b.cancel(ack.order_id)
        cancel_ms = (time.perf_counter() - c0) * 1000
        # the resting-orders list lags writes by a few hundred ms (measured 2026-10-17: ~300-400
        # ms on Kalshi), so poll until the order is gone instead of reading once
        t_gone, still = None, True
        while time.perf_counter() - c0 < 10:
            body = t.open_orders(market).body
            still = any(
                (o.get("order_id") or o.get("id")) == ack.order_id
                for o in ((body.get("orders") or []) if isinstance(body, dict) else [])
            )
            if not still:
                t_gone = (time.perf_counter() - c0) * 1000
                break
        res.append(
            {
                "trial": i + 1,
                "order_id": ack.order_id,
                "create_ms": ack.latency_ms,
                "visible_after_ms": t_visible,
                "cancel_ms": cancel_ms,
                "gone_after_ms": t_gone,
                "filled": ack.filled,
                "still_open_10s_after_cancel": still,
            }
        )
        b.journal.write("latency_trial", **res[-1])
        time.sleep(1.0)

    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(statistics.median(xs), 1) if xs else None

    out = {
        "venue": venue,
        "market": market,
        "trials": res,
        "median_get_ms": med(get_ms),
        "median_create_ms": med([r["create_ms"] for r in res]),
        "median_gone_ms": med([r["gone_after_ms"] for r in res]),
        "median_cancel_ms": med([r["cancel_ms"] for r in res]),
        "median_visible_ms": med([r["visible_after_ms"] for r in res]),
    }
    b.journal.write("latency_summary", **{k: v for k, v in out.items() if k != "trials"})
    return out

"""Polymarket real-time data socket (RTDS) -- public reference crypto prices, read-only.

Verified live 2026-10-13 (no key needed):

* ``wss://ws-live-data.polymarket.com``. Subscribe with
  ``{"action": "subscribe", "subscriptions": [{"topic": "crypto_prices_chainlink",
  "type": "*", "filters": ""}, {"topic": "crypto_prices", "type": "update", "filters": ""}]}``.
  Send text ``PING`` every ~5 s to keep the connection open.
* ``crypto_prices_chainlink``: Chainlink data-stream prices (the settlement source of
  Polymarket's crypto up/down markets), symbols like ``btc/usd``, about 1 update/s each.
* ``crypto_prices``: Binance prices, symbols like ``btcusdt``.
* Message: ``{"topic": .., "type": "update", "timestamp": <ms, socket>, "payload":
  {"symbol": "eth/usd", "timestamp": <ms, observation>, "value": 2505.69, ...}}``.

(Polymarket's newer documented endpoint, ``ws-live-v2``, requires CLOB API credentials,
so this module uses the public legacy socket.)

Rows go to the ``prices`` table under venue ``polymarket_rtds``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Iterable, List, Optional, Tuple

from ..util import Backoff, ms_to_ns, now_ns

log = logging.getLogger("recorder.polymarket_rtds")

VENUE = "polymarket_rtds"


def subscribe_msg(topics: Iterable[Tuple[str, Iterable[str]]]) -> dict:
    subs = []
    for topic, _symbols in topics:
        subs.append({"topic": topic, "type": "*" if topic.endswith("chainlink") else "update", "filters": ""})
    return {"action": "subscribe", "subscriptions": subs}


def parse(raw: str, recv_ns: int, wanted: Dict[str, Optional[set]]) -> List[dict]:
    """``wanted``: topic -> set of symbols (None = all). Returns price rows."""
    if not raw or raw in ("PONG", "PING"):
        return []
    try:
        m = json.loads(raw)
    except ValueError:
        return []
    out = []
    for x in m if isinstance(m, list) else [m]:
        if not isinstance(x, dict):
            continue
        topic = x.get("topic")
        if topic not in wanted:
            continue
        p = x.get("payload") or {}
        sym = p.get("symbol")
        if sym is None or p.get("value") is None:
            continue
        syms = wanted[topic]
        if syms is not None and sym not in syms:
            continue
        out.append({
            "venue": VENUE, "topic": topic, "symbol": sym,
            "ts_exchange_ns": ms_to_ns(p.get("timestamp")), "ts_msg_ns": ms_to_ns(x.get("timestamp")),
            "ts_recv_ns": recv_ns, "value": float(p["value"]),
        })
    return out


async def run(cfg, sink, stop: asyncio.Event, stats) -> None:
    """``cfg``: config.RtdsConfig."""
    import websockets

    wanted = {t: (set(s) if s else None) for t, s in cfg.topics}
    add = getattr(sink, "add_price", None)
    backoff = Backoff()
    while not stop.is_set():
        try:
            async with websockets.connect(cfg.url, ping_interval=None, max_size=2 ** 22, max_queue=4096) as ws:
                await ws.send(json.dumps(subscribe_msg(cfg.topics)))
                stats["connects"] += 1
                log.info("rtds connected: %s", ", ".join(wanted))
                loop = asyncio.get_running_loop()
                last_ping = last_msg = loop.time()
                while not stop.is_set():
                    now = loop.time()
                    if now - last_ping >= 5:
                        await ws.send("PING")
                        last_ping = now
                    if now - last_msg > 60:
                        raise RuntimeError("no data for 60 s")
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    last_msg = loop.time()
                    stats["msgs"] += 1
                    rows = parse(raw, now_ns(), wanted)
                    if add is not None:
                        for r in rows:
                            add(r)
                    stats["price_rows"] += len(rows)
                    backoff.reset()
        except Exception as e:
            stats["errors"] += 1
            log.warning("rtds error: %s", e)
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff.next())
        except asyncio.TimeoutError:
            pass

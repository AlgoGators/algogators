"""Polymarket US (CFTC-regulated DCM) adapter -- read-only REST.

Verified 2026-10-12 against https://docs.polymarket.us and live endpoints:

* Public gateway ``https://gateway.polymarket.us`` (no key): ``GET /v1/markets``,
  ``GET /v1/markets/{slug}/book`` (bids/offers with ``px.value`` and ``qty``, plus
  ``stats`` incl. last trade and ``transactTime`` with ns precision), ``/bbo``.
  Rate limit 25 req/s per IP shared across endpoints.
* WebSocket ``wss://api.polymarket.us/v1/ws/markets`` (book + trades) lives on the
  *authenticated* API and needs a KYC'd account + API key.
  TODO: add a WS feed once the user has a Polymarket US API key.

Each market slug is one instrument ("long" side); prices are in that side's terms.
There is no public trade tape, so we record ``stats.lastTrade*`` changes as
best-effort trade rows (``source='rest_last'``); trades between polls are missed.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from ..core import FeedContext, MarketMeta, trade_row
from ..util import Backoff, TokenBucket, fnum, now_ns, rfc3339_to_ns

log = logging.getLogger("recorder.polymarket_us")

VENUE = "polymarket_us"
GATEWAY = "https://gateway.polymarket.us"

Level = Tuple[float, float]


def _lv(levels) -> List[Level]:
    out = []
    for l in levels or []:
        px = l.get("px")
        px = px.get("value") if isinstance(px, dict) else px
        out.append((float(px), fnum(l.get("qty"))))
    return out


def parse_book(payload: dict) -> Tuple[List[Level], List[Level], Optional[int]]:
    md = payload.get("marketData") or payload
    return _lv(md.get("bids")), _lv(md.get("offers")), rfc3339_to_ns(md.get("transactTime"))


def parse_last_trade(payload: dict, recv_ns: int, meta: Optional[MarketMeta]) -> Optional[dict]:
    md = payload.get("marketData") or payload
    st = md.get("stats") or {}
    t = st.get("lastTradeSetTime")
    px = (st.get("lastTradePx") or {}).get("value")
    if not t or px is None:
        return None
    slug = md.get("marketSlug")
    return trade_row(
        VENUE, slug, meta, trade_id=f"{slug}@{t}", ts_exchange_ns=rfc3339_to_ns(t), ts_recv_ns=recv_ns,
        price=float(px), size=fnum(st.get("lastTradeQty")), taker_side=None, source="rest_last",
    )


async def run(ctx: FeedContext, client) -> None:
    vc = ctx.venue_cfg
    if vc.mode == "ws":
        log.warning("polymarket_us: WS needs an API key (not implemented) -> REST polling")
    bucket = TokenBucket(vc.max_requests_per_s)
    slugs = [m.market_id for m in ctx.markets]
    meta: Dict[str, MarketMeta] = ctx.meta()
    books = {s: ctx.new_book(s) for s in slugs}
    last_trade: Dict[str, str] = {}
    backoff = Backoff()
    while not ctx.stop.is_set():
        loop_t0 = now_ns()
        for s in slugs:
            if ctx.stop.is_set():
                break
            await bucket.acquire()
            try:
                r = await client.get(f"{GATEWAY}/v1/markets/{s}/book")
                if r.status_code == 404:
                    ctx.stats["not_found"] += 1
                    continue
                r.raise_for_status()
                data = r.json()
                backoff.reset()
            except Exception as e:
                ctx.stats["errors"] += 1
                log.warning("polymarket_us %s: %s", s, e)
                if await ctx.sleep(backoff.next()):
                    break
                continue
            recv = now_ns()
            bids, asks, ts = parse_book(data)
            books[s].apply_snapshot(bids, asks, ts_exchange_ns=ts, ts_recv_ns=recv)
            ctx.stats["polls"] += 1
            ctx.emit_due({s: books[s]}, recv, "rest")
            tr = parse_last_trade(data, recv, meta.get(s))
            if tr and last_trade.get(s) != tr["trade_id"]:
                if s in last_trade:  # skip the pre-existing last trade at startup
                    ctx.emit_trades([tr])
                last_trade[s] = tr["trade_id"]
        elapsed = (now_ns() - loop_t0) / 1e9
        if await ctx.sleep(vc.rest_poll_interval_s - elapsed):
            break

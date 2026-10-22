"""Shared runtime types for venue feeds."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .book import OrderBook
from .sampler import Sampler, effective_sampling

log = logging.getLogger("recorder")


@dataclass
class Events:
    """Result of processing one websocket message."""

    book_updates: List[str] = field(default_factory=list)
    trades: List[dict] = field(default_factory=list)
    bbo: List[dict] = field(default_factory=list)  # venue-published top of book
    resync: bool = False
    resync_ids: Set[str] = field(default_factory=set)


@dataclass
class MarketMeta:
    outcome: str = "yes"
    pair: Optional[str] = None
    label: Optional[str] = None
    depth: Optional[int] = None
    heartbeat_s: Optional[float] = None
    group: Optional[str] = None
    bbo: bool = True
    sample_interval_s: Optional[float] = None
    sample_windows: tuple = ()


def meta_of(m) -> MarketMeta:
    """config.MarketRef -> MarketMeta."""
    return MarketMeta(m.outcome, m.pair, m.label, getattr(m, "depth", None),
                      getattr(m, "heartbeat_s", None), getattr(m, "group", None), getattr(m, "bbo", True),
                      getattr(m, "sample_interval_s", None), tuple(getattr(m, "sample_windows", ()) or ()))


def trade_row(
    venue: str,
    market_id: str,
    meta: Optional[MarketMeta],
    *,
    trade_id: str,
    ts_exchange_ns: Optional[int],
    ts_recv_ns: int,
    price: float,
    size: float,
    taker_side: Optional[str],
    source: str,
) -> dict:
    meta = meta or MarketMeta()
    return {
        "venue": venue,
        "market_id": market_id,
        "outcome": meta.outcome,
        "pair": meta.pair,
        "trade_id": trade_id,
        "ts_exchange_ns": ts_exchange_ns,
        "ts_recv_ns": ts_recv_ns,
        "price": float(price),
        "size": float(size),
        "taker_side": taker_side,
        "source": source,
    }


def bbo_row(
    venue: str,
    market_id: str,
    meta: Optional[MarketMeta],
    *,
    ts_exchange_ns: Optional[int],
    ts_recv_ns: int,
    best_bid: Optional[float],
    best_ask: Optional[float],
    source: str,
) -> dict:
    """Venue-published best bid/ask (independent of our depth reconstruction)."""
    meta = meta or MarketMeta()
    return {
        "venue": venue,
        "market_id": market_id,
        "outcome": meta.outcome,
        "pair": meta.pair,
        "ts_exchange_ns": ts_exchange_ns,
        "ts_recv_ns": ts_recv_ns,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "source": source,
    }


@dataclass
class FeedContext:
    """Everything a venue feed needs. ``sink`` implements add_book/add_trade."""

    venue: str
    markets: list  # list[config.MarketRef]
    venue_cfg: object  # config.VenueConfig
    depth: int
    sampler: Sampler
    sink: object
    stop: asyncio.Event
    stats: Counter = field(default_factory=Counter)
    universe: object = None  # universe.Universe (dynamic markets); None = static only
    universe_kind: str = "all"  # which part of the universe this feed serves: static|dynamic|all
    meta_map: Dict[str, MarketMeta] = field(default_factory=dict)  # shared, updated on rollover

    def __post_init__(self):
        for m in self.markets:
            self.meta_map.setdefault(m.market_id, meta_of(m))

    def meta(self) -> Dict[str, MarketMeta]:
        """Shared, live metadata dict (discovery adds entries as markets roll in)."""
        return self.meta_map

    def current_markets(self) -> Dict[str, object]:
        """market_id -> MarketRef this feed should record right now."""
        if self.universe is None:
            return {m.market_id: m for m in self.markets}
        cur = self.universe.current(self.venue, self.universe_kind)
        for mid, ref in cur.items():
            old = self.meta_map.get(mid)
            # depth / heartbeat can tighten when a second group takes the market
            if old is None or old.depth != ref.depth or old.heartbeat_s != getattr(ref, "heartbeat_s", None):
                self.meta_map[mid] = meta_of(ref)
        return cur

    def forget(self, market_id: str) -> None:
        """Drop per-market state after a market rolled out."""
        self.sampler.forget(self.venue, market_id)
        self.meta_map.pop(market_id, None)

    def new_book(self, market_id: str) -> OrderBook:
        mm = {m.market_id: m for m in self.markets}.get(market_id)
        return OrderBook(
            self.venue,
            market_id,
            outcome=mm.outcome if mm else "yes",
            pair=mm.pair if mm else None,
        )

    def emit_due(self, books, now_ns: int, source: str) -> int:
        """``books``: dict of OrderBook (or any iterable of them)."""
        n = 0
        mm = self.meta_map
        for b in (books.values() if isinstance(books, dict) else books):
            m = mm.get(b.market_id)
            if m is None:
                iv = hb = None
            elif m.sample_windows:
                iv, hb = effective_sampling(now_ns, m.sample_interval_s, m.heartbeat_s, m.sample_windows)
            else:
                iv, hb = m.sample_interval_s, m.heartbeat_s
            if b.valid and self.sampler.due(b, now_ns, hb, iv):
                depth = (m.depth if m is not None and m.depth else None) or self.depth
                self.sink.add_book(self.sampler.sample(b, depth, now_ns, source))
                n += 1
        self.stats["book_rows"] += n
        return n

    def emit_trades(self, rows: List[dict]) -> None:
        for r in rows:
            self.sink.add_trade(r)
        self.stats["trade_rows"] += len(rows)

    def emit_bbo(self, rows: List[dict]) -> None:
        add = getattr(self.sink, "add_bbo", None)
        if add is None:
            return
        n = 0
        for r in rows:
            m = self.meta_map.get(r["market_id"])
            if m is not None and not m.bbo:
                continue
            add(r)
            n += 1
        self.stats["bbo_rows"] += n

    async def sleep(self, seconds: float) -> bool:
        """Sleep unless stopped. Returns True if stop was requested."""
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=max(0.0, seconds))
            return True
        except asyncio.TimeoutError:
            return False

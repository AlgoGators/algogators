"""Decides when a book state becomes a stored row, and builds that row."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from .book import OrderBook

_NS = 1_000_000_000


def _positive(px, sz):
    """Drop non-positive levels. OrderBook never holds them; this guarantees that
    best_bid/best_ask always name a level with size > 0 even if a future adapter
    writes to the dicts directly."""
    if all(s > 0 for s in sz):
        return px, sz
    keep = [(p, s) for p, s in zip(px, sz) if s > 0]
    return [p for p, _ in keep], [s for _, s in keep]


def book_row(book: OrderBook, depth: int, now_ns: int, source: str, n_updates: int = 0) -> dict:
    bp, bs, ap, asz = book.top(depth)
    bp, bs = _positive(bp, bs)
    ap, asz = _positive(ap, asz)
    last = book.ts_recv_ns
    return {
        "venue": book.venue,
        "market_id": book.market_id,
        "outcome": book.outcome,
        "pair": book.pair,
        "source": source,
        "ts_exchange_ns": book.ts_exchange_ns,
        "ts_recv_ns": now_ns,
        "seq": book.seq,
        "n_updates": int(n_updates),
        "gap": bool(book.gap_since_sample),
        "best_bid": bp[0] if bp else None,
        "best_ask": ap[0] if ap else None,
        "bid_px": bp,
        "bid_sz": bs,
        "ask_px": ap,
        "ask_sz": asz,
        # Staleness: local receive time of the last venue message (WS delta/snapshot or
        # REST snapshot/confirmation) for this market, and its age at this row.
        "last_update_ns": last,
        "age_s": (now_ns - last) / _NS if last is not None else None,
    }


def effective_sampling(now_ns: int, interval_s: Optional[float], heartbeat_s: Optional[float],
                       windows: tuple = ()) -> Tuple[Optional[float], Optional[float]]:
    """(interval_s, heartbeat_s) in force at ``now_ns`` for one market.

    ``interval_s``/``heartbeat_s`` are the market's own settings (None = sampler default). A
    SampleWindow containing ``now_ns`` overrides the interval (and the heartbeat if it sets one);
    with overlapping windows the fastest interval wins.
    """
    active = [w for w in windows if w.start_ns <= now_ns < w.end_ns]
    if not active:
        return interval_s, heartbeat_s
    w = min(active, key=lambda x: x.interval_s)
    hbs = [x.heartbeat_s for x in active if x.heartbeat_s is not None]
    return w.interval_s, (min(hbs) if hbs else heartbeat_s)


class Sampler:
    """Emit a row when the book changed and ``interval_s`` elapsed, or every ``heartbeat_s``.

    Unchanged books are thus stored at most once per heartbeat, which keeps quiet
    markets cheap while still proving the recorder was alive.
    """

    def __init__(self, interval_s: float = 1.0, heartbeat_s: float = 60.0):
        self.interval_ns = int(interval_s * _NS)
        self.heartbeat_ns = int(heartbeat_s * _NS)
        self._last: Dict[Tuple[str, str], Tuple[int, int]] = {}  # key -> (emit_ns, version)

    @staticmethod
    def _key(b: OrderBook):
        return (b.venue, b.market_id)

    def due(self, book: OrderBook, now_ns: int, heartbeat_s: Optional[float] = None,
            interval_s: Optional[float] = None) -> bool:
        last = self._last.get(self._key(book))
        if last is None:
            return True
        t, v = last
        hb = self.heartbeat_ns if heartbeat_s is None else int(heartbeat_s * _NS)
        if now_ns - t >= hb:
            return True
        iv = self.interval_ns if interval_s is None else int(interval_s * _NS)
        return book.version != v and now_ns - t >= iv

    def forget(self, venue: str, market_id: str) -> None:
        self._last.pop((venue, market_id), None)

    def mark(self, book: OrderBook, now_ns: int) -> None:
        self._last[self._key(book)] = (now_ns, book.version)

    def sample(self, book: OrderBook, depth: int, now_ns: int, source: str) -> dict:
        last = self._last.get(self._key(book))
        n_upd = book.version - last[1] if last else 0
        row = book_row(book, depth, now_ns, source, n_updates=n_upd)
        book.gap_since_sample = False
        self.mark(book, now_ns)
        return row

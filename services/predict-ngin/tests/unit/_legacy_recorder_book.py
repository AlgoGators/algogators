# Frozen copy of src/recorder/book.py replaced in step 5.7 (2026-10-15), verbatim as of commit
# 48aa1d8 (below). Do not edit; the recorder replay tests compare against it.
"""In-memory order-book state for one instrument.

All books are stored in a single price convention per instrument: ``bids`` and
``asks`` keyed by price (float, rounded to 6 dp to kill float noise) with size
in contracts/shares. Venue adapters are responsible for converting to this
convention (e.g. Kalshi NO bids -> YES asks at ``1 - p``).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

Level = Tuple[float, float]

_PX_DP = 6


def norm_px(px: float) -> float:
    return round(float(px), _PX_DP)


class OrderBook:
    __slots__ = (
        "venue", "market_id", "outcome", "pair",
        "bids", "asks", "seq", "ts_exchange_ns", "ts_recv_ns",
        "version", "valid", "gap_since_sample",
    )

    def __init__(self, venue: str, market_id: str, outcome: str = "yes", pair: Optional[str] = None):
        self.venue = venue
        self.market_id = market_id
        self.outcome = outcome
        self.pair = pair
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.seq: Optional[int] = None
        self.ts_exchange_ns: Optional[int] = None
        self.ts_recv_ns: Optional[int] = None
        self.version = 0  # bumps on every real content change
        self.valid = False  # True once a snapshot has been applied and no inconsistency seen
        self.gap_since_sample = False

    # -- mutation -----------------------------------------------------------

    def _side(self, side: str) -> Dict[float, float]:
        if side in ("bid", "buy", "BUY", "bids"):
            return self.bids
        if side in ("ask", "sell", "SELL", "asks"):
            return self.asks
        raise ValueError(f"unknown side {side!r}")

    def _stamp(self, seq, ts_exchange_ns, ts_recv_ns):
        if seq is not None:
            self.seq = seq
        if ts_exchange_ns is not None:
            self.ts_exchange_ns = ts_exchange_ns
        if ts_recv_ns is not None:
            self.ts_recv_ns = ts_recv_ns

    def apply_snapshot(
        self,
        bids: Iterable[Level],
        asks: Iterable[Level],
        *,
        seq: Optional[int] = None,
        ts_exchange_ns: Optional[int] = None,
        ts_recv_ns: Optional[int] = None,
    ) -> None:
        nb = {norm_px(p): float(s) for p, s in bids if float(s) > 0}
        na = {norm_px(p): float(s) for p, s in asks if float(s) > 0}
        if nb != self.bids or na != self.asks or not self.valid:
            self.bids, self.asks = nb, na
            self.version += 1
        self.valid = True
        self._stamp(seq, ts_exchange_ns, ts_recv_ns)

    def set_level(self, side: str, px: float, size: float, **stamp) -> None:
        """Absolute size at a price level (0 removes)."""
        book = self._side(side)
        p = norm_px(px)
        s = float(size)
        old = book.get(p)
        if s <= 0:
            if old is not None:
                del book[p]
                self.version += 1
        elif old != s:
            book[p] = s
            self.version += 1
        self._stamp(stamp.get("seq"), stamp.get("ts_exchange_ns"), stamp.get("ts_recv_ns"))

    def apply_delta(self, side: str, px: float, delta: float, **stamp) -> bool:
        """Incremental size change. Returns False (and invalidates) if size goes negative."""
        book = self._side(side)
        p = norm_px(px)
        new = round(book.get(p, 0.0) + float(delta), 8)
        ok = True
        if new < 0:
            ok = False
            self.valid = False
            new = 0.0
        if new <= 0:
            if p in book:
                del book[p]
        else:
            book[p] = new
        if delta:
            self.version += 1
        self._stamp(stamp.get("seq"), stamp.get("ts_exchange_ns"), stamp.get("ts_recv_ns"))
        return ok

    def invalidate(self) -> None:
        self.valid = False
        self.gap_since_sample = True

    # -- views --------------------------------------------------------------

    def top(self, n: int) -> Tuple[List[float], List[float], List[float], List[float]]:
        bp = sorted(self.bids, reverse=True)[:n]
        ap = sorted(self.asks)[:n]
        return bp, [self.bids[p] for p in bp], ap, [self.asks[p] for p in ap]

    @property
    def best_bid(self) -> Optional[float]:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return min(self.asks) if self.asks else None

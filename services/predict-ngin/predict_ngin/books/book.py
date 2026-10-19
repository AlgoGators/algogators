"""The order-book domain type: one in-memory L2 book per instrument, with snapshots and deltas.

Moved verbatim from ``src/recorder/book.py`` (the recorder's live book, step 5.7); that module is
now a shim. The recorder's Parquet output depends on this class, so the methods in the section
marked "recorder contract" must not change behaviour.

All books are stored in a single price convention per instrument: ``bids`` and ``asks`` keyed by
price (float, rounded to 6 dp to kill float noise) with size in contracts/shares. Venue adapters
are responsible for converting to this convention (e.g. Kalshi NO bids -> YES asks at ``1 - p``,
see :func:`to_yes_terms`).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

__all__ = [
    "Level",
    "norm_px",
    "complement",
    "to_yes_terms",
    "walk_levels",
    "OrderBook",
]

Level = Tuple[float, float]

_PX_DP = 6


def norm_px(px: float) -> float:
    return round(float(px), _PX_DP)


def complement(levels: Iterable[Level]) -> List[Level]:
    """The other side of a binary contract: a bid for NO at ``p`` is an ask for YES at ``1 - p``."""
    return [(norm_px(1.0 - p), s) for p, s in levels]


def to_yes_terms(yes_bids: Iterable[Level],
                 no_bids: Iterable[Level]) -> Tuple[List[Level], List[Level]]:
    """Kalshi books are bids only (YES bids and NO bids). Return ``(bids, asks)`` in YES terms."""
    return list(yes_bids), complement(no_bids)


def walk_levels(levels: Iterable[Level], qty: float) -> Tuple[float, float]:
    """Walk price levels (best first) for up to ``qty``. Returns ``(filled, notional)``;
    the average price is ``notional / filled`` when ``filled > 0``."""
    filled = notional = 0.0
    left = float(qty)
    for px, sz in levels:
        if left <= 0:
            break
        take = min(left, float(sz))
        if take <= 0:
            continue
        filled += take
        notional += take * px
        left -= take
    return filled, notional


class OrderBook:
    __slots__ = (
        "venue", "market_id", "outcome", "pair",
        "bids", "asks", "seq", "ts_exchange_ns", "ts_recv_ns",
        "version", "valid", "gap_since_sample",
    )

    def __init__(self, venue: str, market_id: str, outcome: str = "yes",
                 pair: Optional[str] = None):
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

    # == recorder contract (src/recorder: sampler rows, audits) ================================

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

    # == additions (step 5.7; not used by the recorder) ========================================

    @classmethod
    def from_levels(cls, venue: str, market_id: str, bids: Iterable[Level], asks: Iterable[Level],
                    *, outcome: str = "yes", pair: Optional[str] = None,
                    **stamp) -> "OrderBook":
        """A valid book from one snapshot of ``(price, size)`` levels in any order."""
        b = cls(venue, market_id, outcome, pair)
        b.apply_snapshot(bids, asks, **stamp)
        return b

    def levels(self, side: str) -> List[Level]:
        """``(price, size)`` levels of one side, best first."""
        book = self._side(side)
        prices = sorted(book, reverse=book is self.bids)
        return [(p, book[p]) for p in prices]

    @property
    def mid(self) -> Optional[float]:
        bb, ba = self.best_bid, self.best_ask
        return None if bb is None or ba is None else (bb + ba) / 2.0

    @property
    def spread(self) -> Optional[float]:
        bb, ba = self.best_bid, self.best_ask
        return None if bb is None or ba is None else ba - bb

    def is_crossed(self) -> bool:
        bb, ba = self.best_bid, self.best_ask
        return bb is not None and ba is not None and bb >= ba

    def walk(self, side: str, qty: float) -> Tuple[float, float]:
        """Depth walk for a marketable order of ``qty``: ``side="buy"`` lifts the asks,
        ``side="sell"`` hits the bids. Returns ``(filled, notional)``."""
        if side in ("buy", "BUY"):
            return walk_levels(self.levels("ask"), qty)
        if side in ("sell", "SELL"):
            return walk_levels(self.levels("bid"), qty)
        raise ValueError(f"unknown side {side!r}")

    def depth(self, side: str, within: float) -> float:
        """Total size on one side priced within ``within`` of that side's best price."""
        lv = self.levels(side)
        if not lv:
            return 0.0
        best = lv[0][0]
        return float(sum(s for p, s in lv if abs(p - best) <= within + 1e-12))

    def no_side(self) -> "OrderBook":
        """The same book in NO terms: NO bids at ``1 - YES ask``, NO asks at ``1 - YES bid``."""
        out = OrderBook(self.venue, self.market_id, "no" if self.outcome == "yes" else "yes",
                        self.pair)
        out.apply_snapshot(complement(self.asks.items()), complement(self.bids.items()),
                           seq=self.seq, ts_exchange_ns=self.ts_exchange_ns,
                           ts_recv_ns=self.ts_recv_ns)
        out.valid = self.valid
        return out

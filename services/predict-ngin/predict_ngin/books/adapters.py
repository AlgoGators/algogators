"""Thin adapters from the other book types in the repository to :class:`OrderBook`.

They read attributes only (duck typing), so the core never imports the legacy modules:

* ``src/live/lip_pilot/book.py`` ``Book`` (Kalshi, bids only: ``yes`` and ``no`` lists, best
  first) -> :func:`from_kalshi_bids`;
* ``src/backtest_v3/data/clob_book.py`` ``BookSnapshot`` (reconstructed Polymarket ladder of
  ``BookLevel(price, size_usd)``) -> :func:`from_book_snapshot`; sizes stay in USD;
* ``src/trading/live/order_router.py`` ``OrderBook`` (Polymarket CLOB snapshot, ``(price, size)``
  tuples) -> :func:`from_clob_snapshot`.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .book import Level, OrderBook, to_yes_terms

__all__ = ["from_kalshi_bids", "from_book_snapshot", "from_clob_snapshot"]

_NS = 1_000_000_000


def _sec_to_ns(t: float) -> int:
    """UNIX seconds (float) to integer nanoseconds without losing the fraction to float scaling."""
    whole = int(t // 1)
    return whole * _NS + int(round((t - whole) * _NS))


def from_kalshi_bids(market_id: str, yes: Iterable[Level], no: Iterable[Level], *,
                     ts: Optional[float] = None, venue: str = "kalshi") -> OrderBook:
    """A YES-terms book from Kalshi's bids-only form (``ts`` in UNIX seconds, optional)."""
    bids, asks = to_yes_terms(yes, no)
    recv = None if ts is None else _sec_to_ns(ts)
    return OrderBook.from_levels(venue, market_id, bids, asks, ts_recv_ns=recv)


def from_book_snapshot(snap: Any, *, venue: str = "polymarket") -> OrderBook:
    """From a backtest_v3 ``BookSnapshot``; sizes are its ``size_usd`` (USD, not shares)."""
    return OrderBook.from_levels(
        venue, str(snap.condition_id),
        [(lv.price, lv.size_usd) for lv in snap.bids],
        [(lv.price, lv.size_usd) for lv in snap.asks],
        ts_exchange_ns=int(snap.as_of_s) * _NS,
    )


def from_clob_snapshot(ob: Any, *, venue: str = "polymarket") -> OrderBook:
    """From the order router's ``OrderBook`` (``token_id``, ``timestamp`` datetime, levels)."""
    ts = getattr(ob, "timestamp", None)
    ts_ns = _sec_to_ns(ts.timestamp()) if ts is not None else None
    return OrderBook.from_levels(venue, str(ob.token_id), ob.bids, ob.asks, ts_exchange_ns=ts_ns)

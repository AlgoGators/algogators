"""In-memory order-book state for one instrument.

Shim (step 5.7): the order-book type lives in :mod:`predict_ngin.books.book`, moved there verbatim.
The recorder keeps importing ``OrderBook``, ``Level`` and ``norm_px`` from here; its Parquet output
is unchanged (tests/unit/test_golden_books.py replays delta sequences against a frozen copy).

All books are stored in a single price convention per instrument: ``bids`` and ``asks`` keyed by
price (float, rounded to 6 dp to kill float noise) with size in contracts/shares. Venue adapters
are responsible for converting to this convention (e.g. Kalshi NO bids -> YES asks at ``1 - p``).
"""

from __future__ import annotations

from predict_ngin.books.book import _PX_DP, Level, OrderBook, norm_px  # noqa: F401

__all__ = ["Level", "OrderBook", "norm_px"]

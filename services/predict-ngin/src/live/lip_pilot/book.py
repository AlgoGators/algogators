"""Orderbook parsing. Kalshi returns bids only: YES bids and NO bids (a YES bid at x == a NO ask at 1-x)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

Level = Tuple[float, float]


def _levels(raw: Any) -> List[Level]:
    out: List[Level] = []
    for lv in raw or []:
        try:
            p, s = float(lv[0]), float(lv[1])
        except (TypeError, ValueError, IndexError):
            continue
        if p > 1.0:  # legacy cents format
            p = p / 100.0
        if s > 0:
            out.append((round(p, 4), s))
    out.sort(key=lambda x: -x[0])
    return out


@dataclass
class Book:
    ticker: str
    yes: List[Level]          # best first
    no: List[Level]
    ts: float                 # local receive time (unix seconds)

    @classmethod
    def from_api(cls, ticker: str, body: Dict[str, Any], ts: float) -> "Book":
        ob = body.get("orderbook_fp") or body.get("orderbook") or {}
        yes = _levels(ob.get("yes_dollars") or ob.get("yes"))
        no = _levels(ob.get("no_dollars") or ob.get("no"))
        return cls(ticker, yes, no, ts)

    def side(self, outcome: str) -> List[Level]:
        return self.yes if outcome == "yes" else self.no

    def best_bid(self, outcome: str) -> Optional[float]:
        lv = self.side(outcome)
        return lv[0][0] if lv else None

    def others(self, outcome: str, our_px: Optional[float], our_sz: float) -> List[Level]:
        """The public book includes our resting order; remove it to get others' size."""
        out: List[Level] = []
        for p, s in self.side(outcome):
            if our_px is not None and abs(p - our_px) < 1e-9:
                s = s - our_sz
            if s > 1e-9:
                out.append((p, s))
        return out

    def is_crossed(self) -> bool:
        by, bn = self.best_bid("yes"), self.best_bid("no")
        return by is not None and bn is not None and by + bn >= 1.0 - 1e-9

    def to_order_book(self):
        """This book in YES terms as the core ``predict_ngin.books.OrderBook`` (step 5.7)."""
        from predict_ngin.books.adapters import from_kalshi_bids
        return from_kalshi_bids(self.ticker, self.yes, self.no, ts=self.ts)

    def mid_yes(self) -> Optional[float]:
        by, bn = self.best_bid("yes"), self.best_bid("no")
        if by is None or bn is None:
            return None
        return (by + (1.0 - bn)) / 2.0

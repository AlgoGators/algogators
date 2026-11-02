"""Small immutable value objects for market identity and settlement.

Kept deliberately small: they name things, they do not fetch or compute. Research code mostly works
on DataFrames; these types are for code that handles one market at a time (runners, adapters).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .tickers import event_of_market, series_of

__all__ = ["Series", "Event", "Market", "Settlement"]


@dataclass(frozen=True)
class Series:
    ticker: str
    venue: str = "kalshi"


@dataclass(frozen=True)
class Event:
    ticker: str
    series: str
    venue: str = "kalshi"

    @classmethod
    def from_ticker(cls, event_ticker: str, venue: str = "kalshi") -> "Event":
        return cls(str(event_ticker), series_of(event_ticker), venue)


@dataclass(frozen=True)
class Market:
    ticker: str
    event_ticker: str
    series: str
    venue: str = "kalshi"

    @classmethod
    def from_ticker(cls, market_ticker: str, event_ticker: Optional[str] = None,
                    venue: str = "kalshi") -> "Market":
        """Build from a market ticker; the event defaults to the ticker minus its last part."""
        ev = event_ticker if event_ticker else event_of_market(market_ticker)
        return cls(str(market_ticker), str(ev), series_of(ev), venue)


@dataclass(frozen=True)
class Settlement:
    """A settled binary market. ``win`` is 1.0 for a YES result and 0.0 for NO.

    Times are UNIX seconds; ``settle_ts`` is None when the venue gave no settlement time.
    """

    ticker: str
    result: str
    win: float
    close_ts: Optional[int] = None
    settle_ts: Optional[int] = None
    event_ticker: Optional[str] = None

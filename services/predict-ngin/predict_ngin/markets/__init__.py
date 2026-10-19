"""Market/series/event identity, contract terms, cross-venue pairing and settlement access."""

from .domain import Event, Market, Series, Settlement
from .settlement import (
    BINARY_RESULTS,
    iter_settlements,
    read_settled_markets,
    settled_frame,
    win_of,
)
from .tickers import event_of_market, series_column, series_of

__all__ = [
    "BINARY_RESULTS",
    "Event",
    "Market",
    "Series",
    "Settlement",
    "event_of_market",
    "iter_settlements",
    "read_settled_markets",
    "series_column",
    "series_of",
    "settled_frame",
    "win_of",
]

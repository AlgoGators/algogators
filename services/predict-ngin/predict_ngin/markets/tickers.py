"""Kalshi ticker parsing: series, event and market identity from ticker strings.

Kalshi tickers nest by dashes: series ``KXHIGHNY``, event ``KXHIGHNY-26OCT15``, market
``KXHIGHNY-26OCT15-T70``. The series is the part before the first dash, the event is the market
ticker without its last dash-separated part.
"""

from __future__ import annotations

import pandas as pd

__all__ = ["series_of", "series_column", "event_of_market"]


def series_of(ticker) -> str:
    """Series of an event or market ticker: the text before the first dash.

    The value is converted with ``str`` first, so ``None`` gives ``"None"`` and NaN gives ``"nan"``
    (the convention of kalshi_flb and capbook). Callers that want ``""`` for a missing ticker pass
    ``ticker or ""``.
    """
    return str(ticker).split("-", 1)[0]


def series_column(values) -> pd.Series:
    """Vectorised :func:`series_of` over a Series (index kept) or any sequence of tickers."""
    s = values if isinstance(values, pd.Series) else pd.Series(values)
    return s.astype(str).str.split("-", n=1).str[0]


def event_of_market(market_ticker) -> str:
    """Event ticker of a market ticker: everything before the last dash.

    A ticker without a dash is returned unchanged.
    """
    return str(market_ticker).rsplit("-", 1)[0]

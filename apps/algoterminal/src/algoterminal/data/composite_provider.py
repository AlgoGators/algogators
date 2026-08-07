"""Falls through a priority-ordered list of providers per symbol.

This is how new free/public data sources get added over time — each new
`DataProvider` just joins the list, in priority order, behind the existing
ones as a fallback. This tool is intentionally standalone from AlgoGators'
internal data feed and is not meant to eventually connect to it.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from algoterminal.data.provider import AssetClass, DataProvider


class CompositeProvider(DataProvider):
    """Tries each provider in order per symbol; keeps the first non-empty result."""

    name = "composite"

    def __init__(self, providers: list[DataProvider]) -> None:
        if not providers:
            raise ValueError("CompositeProvider needs at least one provider")
        self.providers = providers

    def fetch(
        self,
        symbol: str,
        asset_class: AssetClass,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        for provider in self.providers:
            try:
                df = provider.fetch(symbol, asset_class, start, end)
            except Exception:
                continue
            if not df.empty:
                return df
        return pd.DataFrame()

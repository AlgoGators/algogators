"""DataProvider backed by alternative.me's Crypto Fear & Greed Index — free,
keyless daily sentiment index (0 = Extreme Fear, 100 = Extreme Greed) derived
from crypto volatility, momentum, social media, market dominance, and
Google Trends data.

There's only one series, not a symbol vocabulary like the other alt-data
sources — `SYMBOLS` exists so the Data tab's reference panels can describe
it the same way as every other source.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import requests

from algoterminal.data import cache
from algoterminal.data.provider import AssetClass, DataProvider

_BASE_URL = "https://api.alternative.me/fng/"

SYMBOLS: dict[str, str] = {
    "CRYPTO": "Crypto Fear & Greed Index (0=Extreme Fear, 100=Extreme Greed), daily",
}


class FearGreedProvider(DataProvider):
    """Daily Crypto Fear & Greed Index from alternative.me."""

    name = "fear-greed"

    def fetch(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.ALT_DATA,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        end = end or date.today()

        cached = cache.read(self.name, symbol)
        if cached is None or cached.empty or cached.index.max().date() < end:
            fresh = self._download(symbol)
            cached = cache.merge_and_write(self.name, symbol, cached, fresh)

        if cached.empty:
            return cached
        if start is not None:
            return cached.loc[str(start) : str(end)]
        return cached.loc[: str(end)]

    @staticmethod
    def _download(symbol: str) -> pd.DataFrame:
        if symbol.upper() not in SYMBOLS:
            return pd.DataFrame()

        # limit=0 asks the API for the full available history in one shot.
        params: dict[str, str] = {"limit": "0", "format": "json"}
        try:
            response = requests.get(_BASE_URL, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return pd.DataFrame()

        records = payload.get("data", [])
        if not records:
            return pd.DataFrame()

        rows = [
            (
                pd.Timestamp.fromtimestamp(int(rec["timestamp"])).normalize(),
                float(rec["value"]),
                rec.get("value_classification", ""),
            )
            for rec in records
            if rec.get("value") is not None and rec.get("timestamp") is not None
        ]
        if not rows:
            return pd.DataFrame()

        df = (
            pd.DataFrame(rows, columns=["date", "close", "classification"])
            .set_index("date")
            .sort_index()
        )
        df.index.name = "date"
        return df

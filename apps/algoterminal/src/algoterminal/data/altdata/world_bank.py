"""DataProvider backed by the World Bank Open Data API — free, keyless
annual macroeconomic and development indicators by country.

"Symbol" here is `"COUNTRY:INDICATOR"` (see `COUNTRIES` and `INDICATORS`),
e.g. `"USA:NY.GDP.MKTP.CD"`. Data is annual, so each fetch just pulls the
whole available series rather than doing incremental range gaps.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import requests

from algoterminal.data import cache
from algoterminal.data.provider import AssetClass, DataProvider

_BASE_URL = "https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"

# ISO3 country code -> human name.
COUNTRIES: dict[str, str] = {
    "USA": "United States",
    "CHN": "China",
    "JPN": "Japan",
    "DEU": "Germany",
    "GBR": "United Kingdom",
    "IND": "India",
    "BRA": "Brazil",
    "ZAF": "South Africa",
}

# World Bank indicator code -> human description.
INDICATORS: dict[str, str] = {
    "NY.GDP.MKTP.CD": "GDP, current US$",
    "NY.GDP.MKTP.KD.ZG": "GDP growth (annual %)",
    "FP.CPI.TOTL.ZG": "Inflation, consumer prices (annual %)",
    "SL.UEM.TOTL.ZS": "Unemployment, total (% of labor force)",
    "NE.TRD.GNFS.ZS": "Trade (% of GDP)",
    "SP.POP.TOTL": "Total population",
}


class WorldBankProvider(DataProvider):
    """Annual macro/development indicators by country from World Bank Open Data."""

    name = "world-bank"

    def fetch(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.ALT_DATA,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        start = start or date(1990, 1, 1)
        end = end or date.today()

        cached = cache.read(self.name, symbol)
        if cached is None or cached.empty:
            fresh = self._download(symbol)
            cached = cache.merge_and_write(self.name, symbol, None, fresh)

        if cached.empty:
            return cached
        return cached.loc[str(start) : str(end)]

    @staticmethod
    def _download(symbol: str) -> pd.DataFrame:
        country, _, indicator = symbol.partition(":")
        if country not in COUNTRIES or indicator not in INDICATORS:
            return pd.DataFrame()

        url = _BASE_URL.format(country=country, indicator=indicator)
        params = {"format": "json", "per_page": 1000}
        try:
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return pd.DataFrame()

        if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
            return pd.DataFrame()

        rows = [
            (pd.Timestamp(year=int(rec["date"]), month=1, day=1), rec["value"])
            for rec in payload[1]
            if rec.get("value") is not None
        ]
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["date", "close"]).set_index("date").sort_index()
        return df

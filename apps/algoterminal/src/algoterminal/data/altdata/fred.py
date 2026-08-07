"""DataProvider backed by FRED (Federal Reserve Economic Data)'s public CSV
export — free, keyless macroeconomic time series published by the St. Louis
Fed.

"Symbol" here is a FRED series ID (see `SERIES`), not a ticker.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

import pandas as pd
import requests

from algoterminal.data import cache
from algoterminal.data.provider import AssetClass, DataProvider

_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# FRED series ID -> human description.
SERIES: dict[str, str] = {
    "UNRATE": "US unemployment rate (%, seasonally adjusted)",
    "CPIAUCSL": "US Consumer Price Index, all urban consumers (index, 1982-84=100)",
    "DFF": "Effective federal funds rate (%)",
    "DGS10": "10-year US Treasury constant maturity yield (%)",
    "DGS2": "2-year US Treasury constant maturity yield (%)",
    "GDP": "US nominal Gross Domestic Product ($ billions, quarterly)",
    "PAYEMS": "US total nonfarm payroll employment (thousands)",
    "INDPRO": "US industrial production index",
    "M2SL": "US M2 money stock ($ billions)",
    "UMCSENT": "University of Michigan consumer sentiment index",
    "HOUST": "US housing starts (thousands, annualized)",
    "VIXCLS": "CBOE Volatility Index, daily close",
}


class FredProvider(DataProvider):
    """US macroeconomic series from FRED's keyless CSV export."""

    name = "fred"

    def fetch(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.ALT_DATA,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        start = start or (date.today() - timedelta(days=365 * 5))
        end = end or date.today()

        cached = cache.read(self.name, symbol)
        gaps = cache.missing_ranges(cached, start, end)
        if not gaps:
            return cached.loc[str(start) : str(end)]

        fresh = self._download(symbol, gaps[0][0], gaps[-1][1])
        merged = cache.merge_and_write(self.name, symbol, cached, fresh)

        if merged.empty:
            return merged
        return merged.loc[str(start) : str(end)]

    @staticmethod
    def _download(symbol: str, start: date, end: date) -> pd.DataFrame:
        if symbol not in SERIES:
            return pd.DataFrame()

        params = {"id": symbol, "cosd": start.isoformat(), "coed": end.isoformat()}
        try:
            response = requests.get(_BASE_URL, params=params, timeout=20)
            response.raise_for_status()
        except requests.RequestException:
            return pd.DataFrame()

        try:
            raw = pd.read_csv(io.StringIO(response.text))
        except Exception:
            return pd.DataFrame()
        # FRED's CSV export names its date column "observation_date"; the
        # value column is the series ID itself.
        date_col = "observation_date" if "observation_date" in raw.columns else "DATE"
        if raw.empty or date_col not in raw.columns:
            return pd.DataFrame()

        value_col = symbol if symbol in raw.columns else raw.columns[1]
        raw["date"] = pd.to_datetime(raw[date_col])
        raw["close"] = pd.to_numeric(raw[value_col], errors="coerce")
        return raw.set_index("date")[["close"]].sort_index()

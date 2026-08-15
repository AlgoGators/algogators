"""DataProvider backed by NASA's POWER (Prediction Of Worldwide Energy
Resources) API — free, keyless satellite/reanalysis meteorological and
solar data by lat/lon point, published by NASA Langley Research Center.

Unlike the market providers, "symbol" here is a location code (see
`LOCATIONS`), not a ticker. Each fetch pulls a fixed bundle of daily
parameters for that point: temperature, solar irradiance, wind speed,
precipitation, and humidity.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests

from algoterminal.data import cache
from algoterminal.data.provider import AssetClass, DataProvider

_BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

# NASA POWER parameter code -> (output column, human description).
PARAMETERS: dict[str, tuple[str, str]] = {
    "T2M": ("close", "Temperature at 2 meters (deg C), daily mean"),
    "ALLSKY_SFC_SW_DWN": (
        "solar_irradiance",
        "All-sky surface shortwave solar irradiance (kWh/m^2/day)",
    ),
    "WS10M": ("wind_speed", "Wind speed at 10 meters (m/s)"),
    "PRECTOTCORR": ("precipitation", "Bias-corrected total precipitation (mm/day)"),
    "RH2M": ("humidity", "Relative humidity at 2 meters (%)"),
}

# Location code -> (latitude, longitude, human description).
LOCATIONS: dict[str, tuple[float, float, str]] = {
    "NYC": (40.7128, -74.0060, "New York City, USA — financial hub"),
    "LONDON": (51.5074, -0.1278, "London, UK — financial hub"),
    "TOKYO": (35.6762, 139.6503, "Tokyo, Japan — financial hub"),
    "SINGAPORE": (1.3521, 103.8198, "Singapore — trade & shipping hub"),
    "SHANGHAI": (31.2304, 121.4737, "Shanghai, China — trade & shipping hub"),
    "MUMBAI": (19.0760, 72.8777, "Mumbai, India — financial hub"),
    "SAOPAULO": (-23.5505, -46.6333, "Sao Paulo, Brazil — financial hub"),
    "SYDNEY": (-33.8688, 151.2093, "Sydney, Australia — financial hub"),
    "DUBAI": (25.2048, 55.2708, "Dubai, UAE — trade & logistics hub"),
    "HOUSTON": (29.7604, -95.3698, "Houston, USA — energy hub"),
    "ROTTERDAM": (51.9244, 4.4777, "Rotterdam, Netherlands — largest European seaport"),
    "IOWA": (41.8780, -93.0977, "Iowa, USA — US corn/soybean belt"),
    "MATOGROSSO": (-12.6819, -56.9211, "Mato Grosso, Brazil — soybean-growing region"),
    "PUNJAB": (31.1471, 75.3412, "Punjab, India — wheat/rice belt"),
    "PERTH": (-31.9505, 115.8605, "Perth, Australia — iron ore mining region"),
}


class NasaPowerProvider(DataProvider):
    """Satellite/reanalysis weather & solar data from NASA POWER, by location."""

    name = "nasa-power"

    def fetch(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.ALT_DATA,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        start = start or (date.today() - timedelta(days=365 * 3))
        end = end or (date.today() - timedelta(days=2))  # POWER data lags a couple of days

        cached = cache.read(self.name, symbol)
        gaps = cache.missing_ranges(cached, start, end)
        if not gaps:
            # missing_ranges() only ever reports "no gaps" for a real cached frame.
            if cached is None:
                raise RuntimeError(f"no cached data for {symbol!r} despite no missing ranges")
            return cached.loc[str(start) : str(end)]

        fresh_frames = [f for f in (self._download(symbol, s, e) for s, e in gaps) if not f.empty]
        fresh = pd.concat(fresh_frames) if fresh_frames else pd.DataFrame()
        merged = cache.merge_and_write(self.name, symbol, cached, fresh)

        if merged.empty:
            return merged
        return merged.loc[str(start) : str(end)]

    @staticmethod
    def _download(symbol: str, start: date, end: date) -> pd.DataFrame:
        loc = LOCATIONS.get(symbol.upper())
        if loc is None:
            return pd.DataFrame()
        lat, lon, _desc = loc

        params: dict[str, str] = {
            "parameters": ",".join(PARAMETERS),
            "community": "RE",
            "longitude": str(lon),
            "latitude": str(lat),
            "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "format": "JSON",
        }
        try:
            response = requests.get(_BASE_URL, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return pd.DataFrame()

        raw_params = payload.get("properties", {}).get("parameter", {})
        if not raw_params:
            return pd.DataFrame()

        columns = {}
        for code, (col_name, _desc) in PARAMETERS.items():
            series = raw_params.get(code)
            if series:
                columns[col_name] = series
        if not columns:
            return pd.DataFrame()

        df = pd.DataFrame(columns)
        df.index = pd.to_datetime(df.index, format="%Y%m%d")
        df.index.name = "date"
        df = df.astype(float)
        df = df.mask(df == -999.0)  # NASA POWER's missing-value sentinel
        return df.sort_index()

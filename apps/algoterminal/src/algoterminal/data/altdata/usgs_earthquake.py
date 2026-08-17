"""DataProvider backed by the USGS Earthquake Catalog (FDSNWS event) API —
free, keyless seismic event data, aggregated to a daily time series per
region.

"Symbol" here is a region code (see `REGIONS`). Each fetch downloads
individual M4.5+ events within the region's bounding radius (or globally)
for the requested window and aggregates them to one row per day: event
count (the "close" column, so it plots as a continuous series even on
quiet days) and peak magnitude.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

import pandas as pd
import requests

from algoterminal.data import cache
from algoterminal.data.provider import AssetClass, DataProvider

_BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
_MIN_MAGNITUDE = 4.5

# Region code -> (latitude, longitude, radius_km, human description).
# lat/lon/radius are None for a global (unbounded) query.
REGIONS: dict[str, tuple[float | None, float | None, float | None, str]] = {
    "GLOBAL": (None, None, None, f"All M{_MIN_MAGNITUDE}+ earthquakes worldwide"),
    "CALIFORNIA": (36.7783, -119.4179, 500, "California — San Andreas fault system"),
    "JAPAN": (36.2048, 138.2529, 800, "Japan — Pacific Ring of Fire"),
    "INDONESIA": (-0.7893, 113.9213, 1000, "Indonesia — Sunda megathrust"),
    "CHILE": (-35.6751, -71.5430, 800, "Chile — Nazca/South American subduction zone"),
    "ALASKA": (64.2008, -149.4937, 800, "Alaska — Aleutian subduction zone"),
    "TURKEY": (38.9637, 35.2433, 600, "Turkey — Anatolian fault system"),
}


class UsgsEarthquakeProvider(DataProvider):
    """Daily seismic-event counts and peak magnitude from the USGS catalog, by region."""

    name = "usgs-earthquake"

    def fetch(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.ALT_DATA,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        start = start or (date.today() - timedelta(days=365 * 3))
        end = end or date.today()

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
        region = REGIONS.get(symbol.upper())
        if region is None:
            return pd.DataFrame()
        lat, lon, radius_km, _desc = region

        params: dict[str, str] = {
            "format": "csv",
            "starttime": start.isoformat(),
            "endtime": (end + timedelta(days=1)).isoformat(),
            "minmagnitude": str(_MIN_MAGNITUDE),
        }
        if lat is not None:
            params.update(latitude=str(lat), longitude=str(lon), maxradiuskm=str(radius_km))

        try:
            response = requests.get(_BASE_URL, params=params, timeout=20)
            response.raise_for_status()
        except requests.RequestException:
            return pd.DataFrame()

        try:
            raw = pd.read_csv(io.StringIO(response.text))
        except Exception:
            return pd.DataFrame()
        if raw.empty or "time" not in raw.columns:
            all_days = pd.date_range(start, end, freq="D", name="date")
            return pd.DataFrame({"close": 0, "max_magnitude": float("nan")}, index=all_days)

        # USGS timestamps are UTC ("...Z"); drop the tz so grouping/reindexing
        # lines up with the tz-naive date range used everywhere else here.
        raw["day"] = pd.to_datetime(raw["time"]).dt.tz_localize(None).dt.floor("D")
        daily = raw.groupby("day").agg(close=("mag", "count"), max_magnitude=("mag", "max"))

        all_days = pd.date_range(start, end, freq="D", name="date")
        daily = daily.reindex(all_days)
        daily["close"] = daily["close"].fillna(0)
        daily.index.name = "date"
        return daily

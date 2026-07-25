"""Maps a Universe's `source` field to its DataProvider and to human-readable
descriptions for the Data tab's detail pane.

`source="market"` (the default, and the only source that existed before
alt-data support) means the existing yfinance/Stooq composite provider;
every other key names a non-financial alt-data source with its own
DataProvider implementation and its own symbol vocabulary.
"""

from __future__ import annotations

from algogators.data.altdata.fred import SERIES as _FRED_SERIES
from algogators.data.altdata.fred import FredProvider
from algogators.data.altdata.nasa_power import LOCATIONS as _NASA_LOCATIONS
from algogators.data.altdata.nasa_power import NasaPowerProvider
from algogators.data.altdata.usgs_earthquake import REGIONS as _USGS_REGIONS
from algogators.data.altdata.usgs_earthquake import UsgsEarthquakeProvider
from algogators.data.altdata.world_bank import COUNTRIES as _WB_COUNTRIES
from algogators.data.altdata.world_bank import INDICATORS as _WB_INDICATORS
from algogators.data.altdata.world_bank import WorldBankProvider
from algogators.data.composite_provider import CompositeProvider
from algogators.data.provider import DataProvider
from algogators.data.stooq_provider import StooqProvider
from algogators.data.yfinance_provider import YFinanceProvider

SOURCE_DESCRIPTIONS: dict[str, str] = {
    "market": "Yahoo Finance / Stooq — free, delayed market OHLCV price data.",
    "nasa-power": (
        "NASA POWER — satellite & reanalysis meteorological and solar data by location, "
        "from NASA Langley Research Center. Free, no API key required."
    ),
    "usgs-earthquake": (
        "USGS Earthquake Catalog — real-time and historical seismic event data "
        "(magnitude 4.5+), aggregated daily by region."
    ),
    "fred": (
        "FRED (Federal Reserve Economic Data) — official US macroeconomic time series "
        "published by the Federal Reserve Bank of St. Louis."
    ),
    "world-bank": "World Bank Open Data — annual macroeconomic and development indicators by country.",
}


def provider_for_source(source: str) -> DataProvider:
    """Resolve a universe's `source` key to the DataProvider that fetches it."""
    if source == "nasa-power":
        return NasaPowerProvider()
    if source == "usgs-earthquake":
        return UsgsEarthquakeProvider()
    if source == "fred":
        return FredProvider()
    if source == "world-bank":
        return WorldBankProvider()
    return CompositeProvider([YFinanceProvider(), StooqProvider()])


def describe_symbol(source: str, symbol: str) -> str:
    """One-line human description of a specific symbol within a source.

    Returns "" for market symbols — those are described via yfinance
    instrument metadata (see `algogators.data.metadata`) instead.
    """
    if source == "nasa-power":
        loc = _NASA_LOCATIONS.get(symbol.upper())
        return loc[2] if loc else ""
    if source == "usgs-earthquake":
        region = _USGS_REGIONS.get(symbol.upper())
        return region[3] if region else ""
    if source == "fred":
        return _FRED_SERIES.get(symbol, "")
    if source == "world-bank":
        country, _, indicator = symbol.partition(":")
        if country not in _WB_COUNTRIES or indicator not in _WB_INDICATORS:
            return ""
        return f"{_WB_COUNTRIES[country]} — {_WB_INDICATORS[indicator]}"
    return ""

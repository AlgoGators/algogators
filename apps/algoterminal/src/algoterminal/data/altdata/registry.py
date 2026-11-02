"""Maps a Universe's `source` field to its DataProvider and to human-readable
descriptions for the Data tab's detail pane.

`source="market"` (the default, and the only source that existed before
alt-data support) means the existing yfinance/Stooq composite provider;
every other key names a non-financial alt-data source with its own
DataProvider implementation and its own symbol vocabulary.
"""

from __future__ import annotations

from algoterminal.data.altdata.fear_greed import SYMBOLS as _FEAR_GREED_SYMBOLS
from algoterminal.data.altdata.fear_greed import FearGreedProvider
from algoterminal.data.altdata.fred import SERIES as _FRED_SERIES
from algoterminal.data.altdata.fred import FredProvider
from algoterminal.data.altdata.nasa_power import LOCATIONS as _NASA_LOCATIONS
from algoterminal.data.altdata.nasa_power import NasaPowerProvider
from algoterminal.data.altdata.usgs_earthquake import REGIONS as _USGS_REGIONS
from algoterminal.data.altdata.usgs_earthquake import UsgsEarthquakeProvider
from algoterminal.data.altdata.wiki_pageviews import ARTICLES as _WIKI_ARTICLES
from algoterminal.data.altdata.wiki_pageviews import WikiPageviewsProvider
from algoterminal.data.altdata.world_bank import COUNTRIES as _WB_COUNTRIES
from algoterminal.data.altdata.world_bank import INDICATORS as _WB_INDICATORS
from algoterminal.data.altdata.world_bank import WorldBankProvider
from algoterminal.data.composite_provider import CompositeProvider
from algoterminal.data.provider import DataProvider
from algoterminal.data.stooq_provider import StooqProvider
from algoterminal.data.yfinance_provider import YFinanceProvider

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
    "fear-greed": (
        "alternative.me Crypto Fear & Greed Index — daily crypto market-sentiment composite "
        "(volatility, momentum, social media, dominance, trends)."
    ),
    "wiki-pageviews": (
        "Wikimedia Pageviews — daily human traffic to a Wikipedia article, used as a public-"
        "attention proxy for a company, asset, or macro topic."
    ),
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
    if source == "fear-greed":
        return FearGreedProvider()
    if source == "wiki-pageviews":
        return WikiPageviewsProvider()
    return CompositeProvider([YFinanceProvider(), StooqProvider()])


def describe_symbol(source: str, symbol: str) -> str:
    """One-line human description of a specific symbol within a source.

    Returns "" for market symbols — those are described via yfinance
    instrument metadata (see `algoterminal.data.metadata`) instead.
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
    if source == "fear-greed":
        return _FEAR_GREED_SYMBOLS.get(symbol.upper(), "")
    if source == "wiki-pageviews":
        article = _WIKI_ARTICLES.get(symbol.upper())
        return article[1] if article else ""
    return ""

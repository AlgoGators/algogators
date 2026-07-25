from algogators.data.altdata.registry import SOURCE_DESCRIPTIONS, describe_symbol, provider_for_source
from algogators.data.composite_provider import CompositeProvider
from algogators.data.metadata import InstrumentInfo, get_instrument_info
from algogators.data.provider import AssetClass, DataProvider, DataQualityReport
from algogators.data.stooq_provider import StooqProvider
from algogators.data.universe import Universe, UniverseStore
from algogators.data.yfinance_provider import YFinanceProvider


def default_provider() -> DataProvider:
    """The provider the rest of the tool uses out of the box: yfinance first,
    falling back to Stooq per-symbol if yfinance has nothing. New free/public
    sources get added to this chain over time; this tool is not meant to
    ever connect to AlgoGators' internal data feed.
    """
    return CompositeProvider([YFinanceProvider(), StooqProvider()])


__all__ = [
    "AssetClass",
    "CompositeProvider",
    "DataProvider",
    "DataQualityReport",
    "InstrumentInfo",
    "SOURCE_DESCRIPTIONS",
    "describe_symbol",
    "get_instrument_info",
    "provider_for_source",
    "StooqProvider",
    "Universe",
    "UniverseStore",
    "YFinanceProvider",
    "default_provider",
]

from algoterminal.data.altdata.registry import (
    SOURCE_DESCRIPTIONS,
    describe_symbol,
    provider_for_source,
)
from algoterminal.data.composite_provider import CompositeProvider
from algoterminal.data.metadata import InstrumentInfo, get_instrument_info
from algoterminal.data.provider import AssetClass, DataProvider, DataQualityReport
from algoterminal.data.stooq_provider import StooqProvider
from algoterminal.data.universe import Universe, UniverseStore
from algoterminal.data.yfinance_provider import YFinanceProvider


def default_provider() -> DataProvider:
    """The provider the rest of the tool uses out of the box: yfinance first,
    falling back to Stooq per-symbol if yfinance has nothing. New free/public
    sources get added to this chain over time; this tool is not meant to
    ever connect to AlgoGators' internal data feed.
    """
    return CompositeProvider([YFinanceProvider(), StooqProvider()])


__all__ = [
    "SOURCE_DESCRIPTIONS",
    "AssetClass",
    "CompositeProvider",
    "DataProvider",
    "DataQualityReport",
    "InstrumentInfo",
    "StooqProvider",
    "Universe",
    "UniverseStore",
    "YFinanceProvider",
    "default_provider",
    "describe_symbol",
    "get_instrument_info",
    "provider_for_source",
]

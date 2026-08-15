"""Non-financial alternative-data providers.

These sit next to the market-data providers (yfinance, Stooq) but return
data that isn't a price series: satellite weather, seismic activity,
macroeconomic indicators. Each still implements `DataProvider.fetch()` and
normalizes its primary metric into a "close" column so it flows through the
same cache and Data-tab plumbing as everything else, but the "symbol" for
these sources is a location or series code, not a ticker.
"""

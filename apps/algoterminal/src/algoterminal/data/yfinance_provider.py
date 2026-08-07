"""DataProvider backed by yfinance — a free, public source, and a permanent
part of this tool's data layer (not a stand-in for something else). This
tool is deliberately standalone from AlgoGators' internal data feed; it
keeps being built out against sources like this one instead.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from algoterminal.data import cache
from algoterminal.data.provider import AssetClass, DataProvider

_COLUMN_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


class YFinanceProvider(DataProvider):
    """Free, delayed-data provider — one of this tool's data sources, not a placeholder for one."""

    name = "yfinance"

    def fetch(
        self,
        symbol: str,
        asset_class: AssetClass,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        start = start or (date.today() - timedelta(days=365 * 3))
        end = end or date.today()

        cached = cache.read(self.name, symbol)
        gaps = cache.missing_ranges(cached, start, end)
        if not gaps:
            return cached.loc[str(start) : str(end)]

        fresh_frames = [f for f in (self._download(symbol, s, e) for s, e in gaps) if not f.empty]
        fresh = pd.concat(fresh_frames) if fresh_frames else pd.DataFrame()
        merged = cache.merge_and_write(self.name, symbol, cached, fresh)

        if merged.empty:
            return merged
        return merged.loc[str(start) : str(end)]

    @staticmethod
    def _download(symbol: str, start: date, end: date) -> pd.DataFrame:
        import yfinance as yf

        raw = yf.download(
            symbol,
            start=start,
            end=end + timedelta(days=1),
            progress=False,
            auto_adjust=False,
            multi_level_index=False,
        )
        if raw.empty:
            return raw

        raw = raw.rename(columns=_COLUMN_MAP)
        raw.index.name = "date"
        keep = [c for c in ("open", "high", "low", "close", "adj_close", "volume") if c in raw.columns]
        return raw[keep].sort_index()

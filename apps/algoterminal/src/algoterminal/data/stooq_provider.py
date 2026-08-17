"""Second free DataProvider, backed by Stooq's CSV endpoint.

Stooq needs no API key and has its own (inconsistent) per-asset-class symbol
conventions, so this is mostly useful as a fallback behind `YFinanceProvider`
inside a `CompositeProvider`. This tool is deliberately standalone from
AlgoGators' internal data feed — its data layer is meant to keep growing
with more free/public sources like this one, not be replaced by the fund's
real feed.

Note: Stooq has started fronting its CSV endpoint with a JS proof-of-work
bot check, so this may legitimately return empty results in some
environments — that's expected and handled gracefully by `CompositeProvider`
falling through to the next provider. It's kept here mainly to demonstrate
the multi-provider fallback pattern; no attempt is made to solve the
challenge, since that would mean evading a service's bot protection.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

import pandas as pd
import requests

from algoterminal.data import cache
from algoterminal.data.provider import AssetClass, DataProvider

_BASE_URL = "https://stooq.com/q/d/l/"
_COLUMN_MAP = {
    "Date": "date",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
}


def _to_stooq_symbol(symbol: str, asset_class: AssetClass) -> str:
    """Best-effort mapping to Stooq's symbol conventions. Stooq's coverage
    and naming are inconsistent across asset classes — this handles the
    common cases (US equities, FX, crypto) and passes futures through as-is.
    """
    s = symbol.lower()
    if asset_class is AssetClass.FX:
        return s.replace("=x", "")
    if asset_class is AssetClass.CRYPTO:
        return s.replace("-", "")
    if asset_class is AssetClass.EQUITY and "." not in s:
        return f"{s}.us"
    return s


class StooqProvider(DataProvider):
    """Free, key-less data source used as an alternate/fallback behind yfinance."""

    name = "stooq"

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
            # missing_ranges() only ever reports "no gaps" for a real cached frame.
            if cached is None:
                raise RuntimeError(f"no cached data for {symbol!r} despite no missing ranges")
            return cached.loc[str(start) : str(end)]

        stooq_symbol = _to_stooq_symbol(symbol, asset_class)
        fresh_frames = [
            f for f in (self._download(stooq_symbol, s, e) for s, e in gaps) if not f.empty
        ]
        fresh = pd.concat(fresh_frames) if fresh_frames else pd.DataFrame()
        merged = cache.merge_and_write(self.name, symbol, cached, fresh)

        if merged.empty:
            return merged
        return merged.loc[str(start) : str(end)]

    @staticmethod
    def _download(stooq_symbol: str, start: date, end: date) -> pd.DataFrame:
        params = {
            "s": stooq_symbol,
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        }
        try:
            response = requests.get(_BASE_URL, params=params, timeout=10)
            response.raise_for_status()
        except requests.RequestException:
            return pd.DataFrame()

        text = response.text
        if not text or text.startswith("No data") or "Exceeded the daily hits limit" in text:
            return pd.DataFrame()

        try:
            raw = pd.read_csv(io.StringIO(text))
        except Exception:
            return pd.DataFrame()
        if raw.empty or "Date" not in raw.columns:
            return pd.DataFrame()

        raw = raw.rename(columns=_COLUMN_MAP)
        raw["date"] = pd.to_datetime(raw["date"])
        raw = raw.set_index("date").sort_index()
        keep = [c for c in ("open", "high", "low", "close", "volume") if c in raw.columns]
        return raw[keep]

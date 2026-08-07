"""Abstract data access interface.

Every asset class (equities, futures, FX, crypto, custom baskets) flows
through this one interface. This tool is intentionally standalone from
AlgoGators' internal trading infrastructure (data-ngin-lab, trade-ngin-lab,
etc.) — it is never meant to connect to the fund's real feed. New concrete
`DataProvider` implementations are added over time against free/public
sources (yfinance, Stooq, and others as they're added), composed through
`CompositeProvider`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

import pandas as pd


class AssetClass(str, Enum):
    EQUITY = "equity"
    FUTURE = "future"
    FX = "fx"
    CRYPTO = "crypto"
    CUSTOM = "custom"
    ALT_DATA = "alt_data"


@dataclass
class DataQualityReport:
    """Basic data-quality flags for a pulled series."""

    symbol: str
    rows: int
    missing_values: int
    start: date | None
    end: date | None
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def __rich_repr__(self):
        yield "symbol", self.symbol
        yield "rows", self.rows
        yield "missing_values", self.missing_values
        yield "issues", self.issues


class DataProvider(ABC):
    """Unified interface for pulling normalized OHLCV data.

    Implementations must return a DataFrame indexed by a tz-naive
    DatetimeIndex named "date", with at least a "close" column and,
    where available, "open"/"high"/"low"/"volume".
    """

    name: str = "abstract"

    @abstractmethod
    def fetch(
        self,
        symbol: str,
        asset_class: AssetClass,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        """Return normalized OHLCV data for a single symbol."""
        raise NotImplementedError

    max_workers: int = 8

    def fetch_many(
        self,
        symbols: list[str],
        asset_class: AssetClass,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch several symbols concurrently (network I/O, so threads help).

        Each `fetch()` call resolves its own cache file independently, so
        running symbols in parallel is safe — there's no shared state to race.
        """
        if not symbols:
            return {}
        if len(symbols) == 1:
            return {symbols[0]: self.fetch(symbols[0], asset_class, start, end)}

        results: dict[str, pd.DataFrame] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(symbols))) as pool:
            futures = {pool.submit(self.fetch, sym, asset_class, start, end): sym for sym in symbols}
            for future in futures:
                sym = futures[future]
                try:
                    results[sym] = future.result()
                except Exception:
                    results[sym] = pd.DataFrame()
        return results

    @staticmethod
    def quality_report(symbol: str, df: pd.DataFrame, min_rows: int = 30) -> DataQualityReport:
        issues: list[str] = []
        if df.empty:
            issues.append("no data returned")
            return DataQualityReport(symbol, 0, 0, None, None, issues)

        missing = int(df["close"].isna().sum()) if "close" in df else 0
        if missing:
            issues.append(f"{missing} missing close values")
        if len(df) < min_rows:
            issues.append(f"insufficient history ({len(df)} rows < {min_rows})")

        return DataQualityReport(
            symbol=symbol,
            rows=len(df),
            missing_values=missing,
            start=df.index.min().date() if len(df) else None,
            end=df.index.max().date() if len(df) else None,
            issues=issues,
        )

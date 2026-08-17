"""Parquet-backed benchmark cache."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

from algosystem.marketdata.domain.normalize import normalize_price_series
from algosystem.shared.errors import MarketDataError
from algosystem.shared.values import DateRange

PathInput = str | os.PathLike[str]


class ParquetBenchmarkCache:
    """Benchmark cache stored as parquet files under an explicit directory."""

    def __init__(self, directory: PathInput | None = None) -> None:
        self.directory = Path(directory) if directory is not None else default_cache_dir()

    def has(self, alias: str, date_range: DateRange) -> bool:
        """Return whether cached data covers the requested range."""
        path = self._path(alias)
        if not path.exists():
            return False
        try:
            sliced = _normalize(
                self._read(path),
                date_range=date_range,
                empty_error=f"benchmark cache has no data for alias: {alias}",
            )
        except MarketDataError:
            return False
        return len(sliced) >= 2

    def get(self, alias: str, date_range: DateRange) -> pd.Series:
        """Read cached benchmark prices."""
        path = self._path(alias)
        if not path.exists():
            raise MarketDataError(f"benchmark cache miss for alias: {alias}")
        prices = _normalize(
            self._read(path),
            date_range=date_range,
            empty_error=f"benchmark cache has no data for alias: {alias}",
        )
        prices.name = alias
        return prices

    def put(self, alias: str, prices: pd.Series) -> None:
        """Write benchmark prices to cache, creating the directory on first write."""
        if not isinstance(prices, pd.Series):
            raise MarketDataError("benchmark cache can only store pandas Series")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            series = _normalize(
                prices,
                empty_error=f"no usable benchmark prices to cache for alias: {alias}",
            )
            series.to_frame(name="price").to_parquet(self._path(alias))
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(f"failed to write benchmark cache for alias: {alias}") from exc

    def _path(self, alias: str) -> Path:
        safe_alias = str(alias).strip()
        if not safe_alias or any(char in safe_alias for char in ("/", "\\", ":")):
            raise MarketDataError(f"invalid benchmark cache alias: {alias}")
        return self.directory / f"{safe_alias}.parquet"

    @staticmethod
    def _read(path: Path) -> pd.Series:
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            raise MarketDataError(f"failed to read benchmark cache: {path}") from exc
        if isinstance(frame, pd.Series):
            return frame
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise MarketDataError(f"benchmark cache file is empty: {path}")
        if "price" in frame.columns:
            return frame["price"]
        if frame.shape[1] == 1:
            return frame.iloc[:, 0]
        raise MarketDataError(f"benchmark cache file has no price column: {path}")


def default_cache_dir() -> Path:
    """Return the default benchmark cache directory without creating it."""
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / "AlgoSystem" / "Cache" / "benchmarks"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "algosystem" / "benchmarks"
    else:
        root = os.environ.get("XDG_CACHE_HOME")
        if root:
            return Path(root) / "algosystem" / "benchmarks"
    return Path.home() / ".algosystem" / "benchmarks"


def _normalize(
    prices: pd.Series,
    *,
    date_range: DateRange | None = None,
    empty_error: str,
) -> pd.Series:
    return normalize_price_series(
        prices,
        date_range=date_range,
        index_error="benchmark cache prices must use a DatetimeIndex",
        dtype_error="benchmark cache prices must be numeric",
        empty_error=empty_error,
    )


__all__ = ["ParquetBenchmarkCache", "default_cache_dir"]

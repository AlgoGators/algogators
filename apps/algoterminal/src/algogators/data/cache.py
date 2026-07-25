"""Local parquet cache so repeated pulls of the same symbol/range are cheap.

Beyond a plain read/write cache, this also supports incremental range
extension: if a symbol is already cached but a request asks for an earlier
start or a later end, only the missing edge(s) need to be fetched and
merged in — not the whole series again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from algogators.config import CACHE_DIR, ensure_dirs


def _safe_symbol(symbol: str) -> str:
    return symbol.replace("/", "-").replace("=", "-").replace("^", "").replace(":", "-")


def _cache_path(provider_name: str, symbol: str) -> Path:
    ensure_dirs()
    return CACHE_DIR / f"{provider_name}__{_safe_symbol(symbol)}.parquet"


def read(provider_name: str, symbol: str) -> pd.DataFrame | None:
    path = _cache_path(provider_name, symbol)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def write(provider_name: str, symbol: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    path = _cache_path(provider_name, symbol)
    df.to_parquet(path)


def merge_and_write(provider_name: str, symbol: str, existing: pd.DataFrame | None, fresh: pd.DataFrame) -> pd.DataFrame:
    """Combine cached + newly-fetched rows, de-duplicate, and persist the union."""
    frames = [f for f in (existing, fresh) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames)
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    write(provider_name, symbol, merged)
    return merged


def covers_range(df: pd.DataFrame | None, start: date | None, end: date | None) -> bool:
    """Whether a cached frame already covers the requested date range."""
    if df is None or df.empty:
        return False
    if start is not None and df.index.min().date() > start:
        return False
    if end is not None and df.index.max().date() < end:
        return False
    return True


def missing_ranges(
    df: pd.DataFrame | None, start: date, end: date
) -> list[tuple[date, date]]:
    """Which sub-range(s) of [start, end] are NOT already covered by `df`.

    Returns at most two ranges: a gap before the cached window and a gap
    after it. If the cache already fully covers [start, end], returns [].
    """
    if df is None or df.empty:
        return [(start, end)]

    cached_start = df.index.min().date()
    cached_end = df.index.max().date()

    gaps: list[tuple[date, date]] = []
    if start < cached_start:
        from datetime import timedelta

        gaps.append((start, min(end, cached_start - timedelta(days=1))))
    if end > cached_end:
        from datetime import timedelta

        gaps.append((max(start, cached_end + timedelta(days=1)), end))

    return [(s, e) for s, e in gaps if s <= e]


@dataclass
class CacheEntry:
    provider: str
    symbol: str
    rows: int
    start: date | None
    end: date | None
    size_bytes: int


def list_cached() -> list[CacheEntry]:
    """Inventory every cached symbol across all providers."""
    ensure_dirs()
    entries: list[CacheEntry] = []
    for path in sorted(CACHE_DIR.glob("*.parquet")):
        provider, _, symbol = path.stem.partition("__")
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        entries.append(
            CacheEntry(
                provider=provider,
                symbol=symbol,
                rows=len(df),
                start=df.index.min().date() if len(df) else None,
                end=df.index.max().date() if len(df) else None,
                size_bytes=path.stat().st_size,
            )
        )
    return entries


def clear(provider_name: str | None = None, symbol: str | None = None) -> int:
    """Delete cached parquet files, optionally filtered by provider and/or symbol.
    Returns the number of files removed.
    """
    ensure_dirs()
    removed = 0
    for path in CACHE_DIR.glob("*.parquet"):
        cached_provider, _, cached_symbol = path.stem.partition("__")
        if provider_name and cached_provider != provider_name:
            continue
        if symbol and cached_symbol != _safe_symbol(symbol):
            continue
        path.unlink()
        removed += 1
    return removed

"""Lightweight instrument metadata (name, sector, exchange, currency).

Pulled via yfinance's `.info` (slow and occasionally rate-limited), so
results are cached to disk with a TTL — the Data tab and CLI both read
through this cache rather than hitting the network on every render.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from algogators.config import HOME_DIR, ensure_dirs

_METADATA_PATH = HOME_DIR / "instrument_metadata.json"
_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass
class InstrumentInfo:
    symbol: str
    name: str = ""
    sector: str = ""
    exchange: str = ""
    currency: str = ""
    fetched_at: float = 0.0

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.fetched_at) > _TTL_SECONDS


def _load_store() -> dict[str, dict]:
    ensure_dirs()
    if not _METADATA_PATH.exists():
        return {}
    try:
        with open(_METADATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_store(store: dict[str, dict]) -> None:
    ensure_dirs()
    with open(_METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


def peek_metadata(symbol: str) -> InstrumentInfo | None:
    """Read cached metadata without touching the network, even if stale."""
    store = _load_store()
    cached = store.get(symbol)
    return InstrumentInfo(**cached) if cached else None


def get_instrument_info(symbol: str, refresh: bool = False) -> InstrumentInfo:
    """Cached instrument metadata, refreshed from yfinance when stale or missing."""
    store = _load_store()
    cached = store.get(symbol)
    if cached and not refresh:
        info = InstrumentInfo(**cached)
        if not info.is_stale:
            return info

    info = _fetch_from_yfinance(symbol)
    store[symbol] = asdict(info)
    _save_store(store)
    return info


def _fetch_from_yfinance(symbol: str) -> InstrumentInfo:
    try:
        import yfinance as yf

        raw = yf.Ticker(symbol).info or {}
    except Exception:
        raw = {}

    return InstrumentInfo(
        symbol=symbol,
        name=raw.get("shortName") or raw.get("longName") or "",
        sector=raw.get("sector") or raw.get("category") or "",
        exchange=raw.get("exchange") or "",
        currency=raw.get("currency") or "",
        fetched_at=time.time(),
    )

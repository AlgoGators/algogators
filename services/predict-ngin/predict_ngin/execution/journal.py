"""Append-only audit journal: one JSON line per intent, request, response and decision."""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from ..shared.paths import data_root

_SECRET_KEYS = (
    "signature",
    "key",
    "secret",
    "authorization",
    "x-pm-signature",
    "kalshi-access-signature",
)


def _clean(v: Any) -> Any:
    if isinstance(v, dict):
        return {
            k: ("<redacted>" if any(s in str(k).lower() for s in _SECRET_KEYS) else _clean(x))
            for k, x in v.items()
        }
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


class Journal:
    """``write(event, **fields)`` appends ``{ts, event, venue, mode, ...}`` to
    ``<data>/live/journal/<UTC day>.jsonl``.

    Lines are flushed and fsynced, so a crash cannot lose an order record. Header / signature fields
    are redacted.
    """

    def __init__(self, venue: str, mode: str, directory: Optional[Path] = None):
        self.venue = venue
        self.mode = mode
        self.dir = directory or data_root() / "live" / "journal"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def path(self) -> Path:
        return self.dir / f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d}.jsonl"

    def write(self, event: str, **fields: Any) -> dict:
        rec = {
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds"),
            "event": event,
            "venue": self.venue,
            "mode": self.mode,
            **_clean(fields),
        }
        line = json.dumps(rec, default=str)
        with self._lock, open(self.path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return rec

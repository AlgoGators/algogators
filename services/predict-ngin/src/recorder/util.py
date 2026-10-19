"""Small helpers: timestamp parsing, rate limiting, backoff."""

from __future__ import annotations

import asyncio
import random
import re
import time
from calendar import timegm
from typing import Any, Optional

_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:?\d{2})?$"
)


def rfc3339_to_ns(s: Optional[str]) -> Optional[int]:
    """Parse an RFC3339 timestamp with up to nanosecond precision to epoch ns (UTC)."""
    if not s:
        return None
    m = _RFC3339.match(s.strip())
    if not m:
        return None
    y, mo, d, h, mi, sec, frac, tz = m.groups()
    secs = timegm((int(y), int(mo), int(d), int(h), int(mi), int(sec), 0, 0, 0))
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        tz = tz[1:].replace(":", "")
        secs -= sign * (int(tz[:2]) * 3600 + int(tz[2:]) * 60)
    ns = int((frac or "0").ljust(9, "0")) if frac else 0
    return secs * 1_000_000_000 + ns


def ms_to_ns(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v) * 1_000_000
    except (TypeError, ValueError):
        return None


def fnum(v: Any) -> float:
    return float(v) if v is not None and v != "" else 0.0


def now_ns() -> int:
    return time.time_ns()


class TokenBucket:
    """Async token bucket: ``rate`` requests/s with ``burst`` capacity."""

    def __init__(self, rate: float, burst: Optional[float] = None):
        self.rate = max(float(rate), 0.01)
        self.capacity = float(burst if burst is not None else max(1.0, rate))
        self.tokens = self.capacity
        self.t = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, n: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                await asyncio.sleep((n - self.tokens) / self.rate)


class Backoff:
    def __init__(self, base: float = 1.0, cap: float = 60.0):
        self.base, self.cap, self.n = base, cap, 0

    def next(self) -> float:
        d = min(self.cap, self.base * (2 ** self.n))
        self.n += 1
        return d * (0.5 + random.random() / 2)

    def reset(self) -> None:
        self.n = 0

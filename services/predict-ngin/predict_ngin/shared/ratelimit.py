"""Blocking rate limiters for HTTP clients.

* :class:`TokenBucket` -- ``rate_per_s`` tokens per second up to ``capacity``; a request costs
  tokens (Kalshi charges write tokens: an order 10, each batched cancel 2).
* :class:`MinInterval` -- at least ``min_interval`` seconds between the starts of two requests.
* :class:`AimdLimiter` -- for servers whose limit is NOT published: a rate that probes for the
  limit, TCP style: additive increase while requests succeed, multiplicative decrease on a throttle
  (429/503, or a latency spike for servers that queue instead of refusing), and slow probing near
  the rate that was last throttled, so it settles just under the limit instead of oscillating
  through 429s. Callers report each answer with ``feedback``. When the limit is published, pace
  with a :class:`TokenBucket` a margin under it instead; there is nothing to probe for.

All take an injectable clock and sleep, so tests run without waiting.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Optional

__all__ = ["TokenBucket", "MinInterval", "AimdLimiter", "THROTTLE_STATUSES"]


class TokenBucket:
    def __init__(
        self,
        rate_per_s: float,
        capacity: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if rate_per_s <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self.rate = float(rate_per_s)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
        self._last = now

    def acquire(self, cost: float = 1.0) -> float:
        """Block until ``cost`` tokens are available; returns seconds waited."""
        if cost > self.capacity:
            raise ValueError(f"cost {cost} exceeds bucket capacity {self.capacity}")
        waited = 0.0
        while True:
            self._refill()
            if self.tokens >= cost:
                self.tokens -= cost
                return waited
            dt = (cost - self.tokens) / self.rate
            self._sleep(dt)
            waited += dt


class MinInterval:
    """Space request starts at least ``min_interval`` seconds apart (0 disables the limit).

    ``clock`` / ``sleep`` default to ``time.time`` / ``time.sleep``, looked up at call time.
    """

    def __init__(
        self,
        min_interval: float,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ):
        self.min_interval = float(min_interval)
        self._clock = clock
        self._sleep = sleep
        self.last = 0.0

    def acquire(self, cost: float = 1.0) -> float:
        clock = self._clock or time.time
        wait = self.min_interval - (clock() - self.last)
        if wait > 0:
            (self._sleep or time.sleep)(wait)
        self.last = clock()
        return max(wait, 0.0)


THROTTLE_STATUSES = frozenset({429, 503})


class AimdLimiter:
    """Pace requests at a rate that finds an unpublished server limit by itself (AIMD, as in TCP).

    * Each success adds ``increase / rate`` req/s, so the rate climbs about ``increase`` req/s for
      every second of clean traffic, up to ``max_rate``.
    * A throttle multiplies the rate by ``decrease`` (down to ``min_rate``), at most once per
      ``cooldown`` seconds: requests already in flight report the same congestion, as TCP cuts
      once per round trip. ``Retry-After`` also holds every caller until it has passed.
    * The rate at which the last throttle came is kept as ``ceiling``. Within ``near`` x ceiling the
      increase is multiplied by ``probe`` (as TCP CUBIC flattens near its last window), so after
      the first 429 the rate recovers quickly, then creeps towards the limit and rarely crosses it.
    * ``tolerance`` > 0 makes it loss-tolerant: a throttle cuts only when more than that share of
      the last ``window`` answers were throttles. Use it when 429s also come at low rates (a key
      shared with another job, a noisy quota): cutting on every stray 429 ratchets the rate down,
      as TCP collapses on a lossy link.
    * ``slow_factor`` (off by default) turns a latency spike into a throttle, for servers that
      queue excess requests instead of refusing them (Polymarket's Cloudflare limits): the
      smoothed latency above ``slow_factor`` x the baseline (the lowest latency seen, drifting
      up slowly so a permanently slower server does not read as congestion forever).

    Thread-safe: ``acquire`` reserves the next slot under a lock and sleeps outside it.
    """

    def __init__(
        self,
        rate: float,
        *,
        min_rate: float = 0.1,
        max_rate: float = 100.0,
        increase: float = 1.0,
        decrease: float = 0.5,
        cooldown: float = 1.0,
        slow_factor: Optional[float] = None,
        near: float = 0.9,
        probe: float = 0.1,
        tolerance: float = 0.0,
        window: int = 50,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not 0 < min_rate <= max_rate:
            raise ValueError("need 0 < min_rate <= max_rate")
        if not 0 < decrease < 1:
            raise ValueError("decrease must be in (0, 1)")
        self.min_rate = float(min_rate)
        self.max_rate = float(max_rate)
        self.rate = min(max(float(rate), self.min_rate), self.max_rate)
        self.increase = float(increase)
        self.decrease = float(decrease)
        self.cooldown = float(cooldown)
        self.slow_factor = slow_factor
        self.near = float(near)
        self.probe = float(probe)
        self.ceiling: Optional[float] = None
        self.tolerance = float(tolerance)
        self._recent: deque = deque(maxlen=window)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next = clock()
        self._last_cut = float("-inf")
        self._base: Optional[float] = None
        self._lat: Optional[float] = None
        self.n_ok = 0
        self.n_throttled = 0
        self.n_cuts = 0

    def acquire(self, cost: float = 1.0) -> float:
        """Block until the next slot; returns seconds waited."""
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + cost / self.rate
        wait = start - now
        if wait > 0:
            self._sleep(wait)
        return max(wait, 0.0)

    def _cut(self, now: float) -> None:
        if now - self._last_cut >= self.cooldown:
            self.ceiling = self.rate
            self.rate = max(self.min_rate, self.rate * self.decrease)
            self._last_cut = now
            self.n_cuts += 1

    def success(self, latency: Optional[float] = None) -> None:
        with self._lock:
            self.n_ok += 1
            self._recent.append(0)
            if latency is not None and self.slow_factor:
                if self._base is None:
                    self._base = latency
                else:
                    self._base = min(latency, self._base + 0.01 * (latency - self._base))
                self._lat = latency if self._lat is None else 0.8 * self._lat + 0.2 * latency
                if self._lat > self.slow_factor * self._base:
                    self._cut(self._clock())
                    return
            step = self.increase / self.rate
            if self.ceiling is not None and self.rate >= self.near * self.ceiling:
                step *= self.probe
            self.rate = min(self.max_rate, self.rate + step)

    def throttle(self, retry_after: Optional[float] = None) -> None:
        with self._lock:
            now = self._clock()
            self.n_throttled += 1
            self._recent.append(1)
            if retry_after:
                self._next = max(self._next, now + retry_after)
            if self.tolerance <= 0 or sum(self._recent) > self.tolerance * len(self._recent):
                self._cut(now)

    def feedback(self, status: Optional[int], latency: Optional[float] = None,
                 retry_after: Optional[float] = None) -> None:
        """Report one answer: a throttle status cuts the rate, any other status below 500 (404
        included: the server answered) counts as a success; 5xx and network errors (None) change
        nothing."""
        if status in THROTTLE_STATUSES:
            self.throttle(retry_after)
        elif status is not None and status < 500:
            self.success(latency)

    def stats(self) -> dict:
        return {"rate": round(self.rate, 2), "ceiling": self.ceiling and round(self.ceiling, 2),
                "ok": self.n_ok, "throttled": self.n_throttled, "cuts": self.n_cuts}

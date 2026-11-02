"""Per-stream sequence-number tracking with gap detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Hashable, Optional


@dataclass(frozen=True)
class SeqResult:
    status: str  # "first" | "ok" | "gap" | "stale"
    expected: Optional[int]
    got: int

    @property
    def missing(self) -> int:
        if self.status != "gap" or self.expected is None:
            return 0
        return self.got - self.expected


class SequenceTracker:
    """Tracks the last seen seq per stream key (e.g. Kalshi subscription id).

    ``gap``  -> one or more messages were skipped; caller should resync.
    ``stale``-> duplicate / out-of-order message; caller should drop it.
    After a gap the tracker continues from the new seq so subsequent messages are ``ok``.
    """

    def __init__(self) -> None:
        self._last: Dict[Hashable, int] = {}
        self.gaps = 0
        self.missing_total = 0
        self.stale = 0

    def check(self, key: Hashable, seq: int) -> SeqResult:
        last = self._last.get(key)
        if last is None:
            self._last[key] = seq
            return SeqResult("first", None, seq)
        expected = last + 1
        if seq == expected:
            self._last[key] = seq
            return SeqResult("ok", expected, seq)
        if seq < expected:
            self.stale += 1
            return SeqResult("stale", expected, seq)
        self._last[key] = seq
        self.gaps += 1
        self.missing_total += seq - expected
        return SeqResult("gap", expected, seq)

    def reset(self, key: Optional[Hashable] = None) -> None:
        if key is None:
            self._last.clear()
        else:
            self._last.pop(key, None)

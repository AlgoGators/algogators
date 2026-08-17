from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(frozen=True)
class RollEvent:
    """A detected futures roll: the symbol changed and the new contract's volume exceeded the old one's."""

    index: int
    prior_symbol: str
    new_symbol: str
    adjustment: float


@dataclass(frozen=True)
class BackAdjustment:
    """An ordered set of roll events and the cumulative price adjustment they imply at any row index."""

    roll_events: list[RollEvent] = field(default_factory=list)

    def cumulative_adjustment_at(self, index: int) -> float:
        return sum(event.adjustment for event in self.roll_events if event.index > index)


@dataclass(frozen=True)
class StalenessReport:
    """Result of StalenessChecker.check_staleness -- whether the latest known data is too old, and why."""

    is_stale: bool
    message: str
    latest_date: str | None = None
    age: timedelta | None = None

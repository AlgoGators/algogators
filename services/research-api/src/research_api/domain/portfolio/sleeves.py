"""Which sleeves a book ran, day by day (issue #59).

The sleeve list of a day is the run's `strategy_allocations` (trading.live_run_metadata), never
the strategy id. A run is {"date": date, "allocations": {sleeve name: allocation}}; the
repository reads them oldest first.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any


def _as_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def run_on_or_before(runs: Sequence[Mapping[str, Any]], day: Any) -> Mapping[str, Any] | None:
    """The newest run on or before `day`; the newest run of all when `day` is unknown."""
    if not runs:
        return None
    limit = _as_date(day)
    if limit is None:
        return runs[-1]
    chosen = None
    for run in runs:
        run_day = _as_date(run.get("date"))
        if run_day is not None and run_day <= limit:
            chosen = run
    return chosen


def sleeve_changes(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each date the book's sleeve set changed from its previous run, oldest first.

    [{date: "YYYY-MM-DD", added: [names], removed: [names]}]. The first run sets the
    starting set and is not a change. A switched-off sleeve shows in `removed` on the first
    run without it, and its share goes to the remaining sleeves from that date.
    """
    changes: list[dict[str, Any]] = []
    previous: set[str] | None = None
    for run in runs:
        current = set((run.get("allocations") or {}).keys())
        if previous is not None and current != previous:
            day = _as_date(run.get("date"))
            changes.append(
                {
                    "date": day.isoformat() if day else None,
                    "added": sorted(current - previous),
                    "removed": sorted(previous - current),
                }
            )
        previous = current
    return changes

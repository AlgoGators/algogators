"""Partition publishing helpers: swap a built folder into place (tolerating Windows file locks) and
reconcile it with its source."""

from __future__ import annotations

import shutil
import time
from pathlib import Path


def swap_in(tmp: Path, dst: Path, attempts: int = 30) -> None:
    """Replace ``dst`` with ``tmp``.

    On Windows an antivirus scan or indexer can hold a new file for a few seconds, making rename
    or delete fail with "Access is denied"; retry with backoff (about a minute in total).
    """
    for i in range(attempts):
        try:
            if dst.exists():
                shutil.rmtree(dst)
            tmp.rename(dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(min(0.5 * (i + 1), 5.0))


def reconciles(want, got, rel: float = 1e-9) -> bool:
    """(count, sum, ...) of the source vs the built partition: counts exact, sums within ``rel``.

    Float sums depend on summation order, so exact equality of a rounded sum is too strict.
    """
    if want[0] != got[0]:
        return False
    return all(
        abs((a or 0) - (b or 0)) <= rel * max(1.0, abs(a or 0))
        for a, b in zip(want[1:], got[1:], strict=True)
    )

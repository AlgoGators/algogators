"""Single-instance file locks (Windows and POSIX), released automatically when the process exits."""

from __future__ import annotations

import sys
from pathlib import Path


class Busy(RuntimeError):
    pass


def _try_lock(fh) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


class SingleInstance:
    """Hold an exclusive lock on ``path`` for the life of this object; raises :class:`Busy` if
    already held."""

    def __init__(self, path: Path, what: str = "this job"):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.fh = open(path, "a+")
        if not _try_lock(self.fh):
            self.fh.close()
            raise Busy(f"{what} is already running (lock {path})")

    def release(self) -> None:
        self.fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def is_locked(path: Path) -> bool:
    """True when another process holds the lock at ``path``."""
    if not path.exists():
        return False
    with open(path, "a+") as fh:
        if _try_lock(fh):
            return False
    return True

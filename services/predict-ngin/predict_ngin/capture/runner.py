"""Generic one-pass runner for scheduled collectors and paper runners.

Extracted from ``scripts/live/perps_carry_paper.py`` and
``scripts/research/perps_funding_collect.py`` (step 5.8). One invocation does one pass:

1. create the data directory;
2. if ``<dir>/STOP`` exists, print a message and exit 0 without work;
3. take ``<dir>/.lock`` (``O_CREAT | O_EXCL``; a lock older than ``stale_s`` seconds is removed
   first, so a crashed pass cannot block the schedule forever); if another pass holds it, exit 3;
4. run the pass body, which returns the exit code (0 ok or nothing to do, 2 essential data
   unavailable); the lock is released even if the body raises;
5. optionally call an alert hook when the pass did not succeed.

State and status files are written atomically (temporary file in the same directory, then
``os.replace``), so a reader never sees a half-written file. ``status_fields`` builds the
``status.json`` document with ``name``, ``mode``, ``ok`` and ``last_run`` first.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

__all__ = [
    "EXIT_OK",
    "EXIT_NO_DATA",
    "EXIT_LOCKED",
    "EXIT_ERROR",
    "STOP_FILE",
    "LOCK_FILE",
    "STATUS_FILE",
    "atomic_write_text",
    "atomic_write_json",
    "Lock",
    "stop_requested",
    "status_fields",
    "run_one_pass",
]

EXIT_OK = 0          # pass done, or nothing to do, or STOP present
EXIT_NO_DATA = 2     # essential data unavailable; nothing written except the error status
EXIT_LOCKED = 3      # another pass holds the lock
EXIT_ERROR = 1       # the body raised (reported to the alert hook; the exception propagates)

STOP_FILE = "STOP"
LOCK_FILE = ".lock"
STATUS_FILE = "status.json"

AlertHook = Callable[[int, str], None]


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` via ``<path>.tmp`` and ``os.replace`` (atomic on one disk)."""
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, p)


def atomic_write_json(path, obj: Any, **dumps_kwargs) -> None:
    """``atomic_write_text(path, json.dumps(obj, **dumps_kwargs))``; the keyword arguments go to
    ``json.dumps`` unchanged, so callers keep their exact formatting (indent, default, ...)."""
    atomic_write_text(path, json.dumps(obj, **dumps_kwargs))


class Lock:
    """Exclusive lock file with a stale timeout. ``held`` tells whether this process got it.

    ``write_pid`` stores the PID in the file (informational; nothing reads it back).
    """

    def __init__(self, p, stale_s: int = 900, write_pid: bool = True):
        self.p, self.stale_s, self.write_pid, self.held = Path(p), stale_s, write_pid, False

    def __enter__(self) -> "Lock":
        if self.p.exists() and time.time() - self.p.stat().st_mtime > self.stale_s:
            self.p.unlink(missing_ok=True)
        try:
            fd = os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            if self.write_pid:
                os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.held = True
        except FileExistsError:
            self.held = False
        return self

    def __exit__(self, *exc) -> None:
        if self.held:
            self.p.unlink(missing_ok=True)
            self.held = False


def stop_requested(data_dir) -> bool:
    """True when the operator created ``<data_dir>/STOP``."""
    return (Path(data_dir) / STOP_FILE).exists()


def status_fields(name: str, mode: str, ok: bool, last_run: str, **extra) -> Dict[str, Any]:
    """The ``status.json`` document: ``name``, ``mode``, ``ok``, ``last_run``, then ``extra`` in
    the order given."""
    return {"name": name, "mode": mode, "ok": ok, "last_run": last_run, **extra}


def run_one_pass(data_dir, body: Callable[[], int], *, stale_s: int = 900, write_pid: bool = True,
                 alert: Optional[AlertHook] = None) -> int:
    """Run ``body`` once under the STOP check and the lock; return its exit code.

    ``alert(code, message)``, when given, is called for exit codes other than 0 (2: no data,
    3: locked, 1: the body raised; the exception is re-raised after the hook). A failing hook never
    masks the pass result.
    """
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    if stop_requested(d):
        print("STOP file present; exiting", flush=True)
        return EXIT_OK
    code: int
    with Lock(d / LOCK_FILE, stale_s=stale_s, write_pid=write_pid) as lk:
        if not lk.held:
            print("another pass holds the lock", flush=True)
            code = EXIT_LOCKED
        else:
            try:
                code = int(body())
            except Exception as e:
                _alert(alert, EXIT_ERROR, f"{type(e).__name__}: {e}")
                raise
    if code != EXIT_OK:
        _alert(alert, code, {EXIT_NO_DATA: "essential data unavailable",
                             EXIT_LOCKED: "another pass holds the lock"}.get(code, f"exit {code}"))
    return code


def _alert(hook: Optional[AlertHook], code: int, msg: str) -> None:
    if hook is None:
        return
    try:
        hook(code, msg)
    except Exception as e:  # the alert channel must never change the pass result
        print(f"alert hook failed: {type(e).__name__}: {e}", flush=True)

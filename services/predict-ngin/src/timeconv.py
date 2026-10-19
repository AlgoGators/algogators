"""Compatibility shim: the implementation lives in :mod:`predict_ngin.shared.time`.

Kept so the existing ``from src.timeconv import ...`` / ``from timeconv import ...``
imports keep working unchanged.  When the package is not installed (e.g. the old
``venv/``), the repository root is added to ``sys.path`` so the import still resolves.
"""

from __future__ import annotations

try:
    from predict_ngin.shared import time as _impl
except ImportError:  # predict_ngin not installed: fall back to the repo checkout
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from predict_ngin.shared import time as _impl

EPOCH_UTC = _impl.EPOCH_UTC
ArrayLike = _impl.ArrayLike
_UNITS = _impl._UNITS
_as_utc = _impl._as_utc
to_epoch = _impl.to_epoch
to_epoch_seconds = _impl.to_epoch_seconds

__all__ = ["to_epoch", "to_epoch_seconds", "EPOCH_UTC"]

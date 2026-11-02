"""Logging setup helper shared by entry points."""

from __future__ import annotations

import logging
import sys

DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(name: str | None = None, level: int | str = logging.INFO) -> logging.Logger:
    """Return logger ``name`` with one stderr handler at ``level``.

    Idempotent: calling it again for the same logger does not add a second handler.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level.upper() if isinstance(level, str) else level)
    if not any(getattr(h, "_predict_ngin", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
        handler._predict_ngin = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger

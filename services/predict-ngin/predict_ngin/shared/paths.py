"""Filesystem locations. The data root is configurable, never hard-coded."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DATA_ENV_VAR = "PREDICT_NGIN_DATA"
COLD_ENV_VAR = "PREDICT_NGIN_COLD"
# Machine-local settings file (gitignored); see config/storage.example.yaml
LOCAL_STORAGE_CONFIG = Path("config") / "storage.local.yaml"


def repo_root() -> Path:
    """The repository checkout this package lives in."""
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """Data directory: ``$PREDICT_NGIN_DATA`` if set, else ``<repo>/data``."""
    env = os.environ.get(DATA_ENV_VAR, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return repo_root() / "data"


def cold_root() -> Optional[Path]:
    """Optional slow/archive storage for rarely used data (see scripts/data/storage_tier.py).

    ``$PREDICT_NGIN_COLD`` if set, else ``cold_root`` in the gitignored
    ``config/storage.local.yaml``, else None: with nothing configured, all data stays
    under :func:`data_root`.
    """
    env = os.environ.get(COLD_ENV_VAR, "").strip()
    if env:
        return Path(env).expanduser()
    cfg = repo_root() / LOCAL_STORAGE_CONFIG
    if cfg.exists():
        import yaml

        val = (yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}).get("cold_root")
        if val:
            return Path(str(val)).expanduser()
    return None

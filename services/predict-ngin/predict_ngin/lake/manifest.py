"""``<data>/lake/_manifest.json``: what each lake dataset holds, when it was built, and its last
check results."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict

from ..shared.paths import data_root


def lake_root() -> Path:
    return data_root() / "lake"


def path() -> Path:
    return lake_root() / "_manifest.json"


def load() -> Dict[str, Any]:
    p = path()
    return json.loads(p.read_text()) if p.exists() else {}


def update(dataset: str, **fields: Any) -> None:
    m = load()
    entry = m.get(dataset, {})
    entry.update(fields, updated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    m[dataset] = entry
    _write(m)


def put(entries: Dict[str, Dict[str, Any]]) -> None:
    """Replace whole entries as given (``updated`` included), e.g. from a pulled snapshot."""
    m = load()
    m.update(entries)
    _write(m)


def _write(m: Dict[str, Any]) -> None:
    lake_root().mkdir(parents=True, exist_ok=True)
    tmp = path().with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True, default=str))
    tmp.replace(path())

"""Append-only JSONL journal: every decision, order intent, fill, snapshot estimate and kill."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SECRET_KEYS = ("signature", "private", "secret", "password", "kalshi-access", "authorization", "api_key", "key_id")


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("<redacted>" if any(s in str(k).lower() for s in _SECRET_KEYS) else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact(v) for v in obj]
    return obj


class Journal:
    def __init__(self, path: Optional[Path], clock=time.time, echo: bool = False):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self.echo = echo
        self.records: List[Dict[str, Any]] = []  # in-memory copy (tests, report)

    def write(self, kind: str, **fields: Any) -> Dict[str, Any]:
        rec = {"ts": round(self._clock(), 3), "kind": kind, **_redact(fields)}
        line = json.dumps(rec, default=str, sort_keys=False)
        with self._lock:
            self.records.append(rec)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        if self.echo:
            print(line, flush=True)
        return rec

    def of_kind(self, kind: str) -> List[Dict[str, Any]]:
        return [r for r in self.records if r["kind"] == kind]


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out

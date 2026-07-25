"""Central paths and constants for local, on-disk state.

Everything AlgoGators CLI persists (cache, universes, research records)
lives under ~/.algogators so the tool works the same regardless of which
directory it's invoked from.
"""

from __future__ import annotations

from pathlib import Path

APP_NAME = "AlgoGators CLI"

HOME_DIR = Path.home() / ".algogators"
CACHE_DIR = HOME_DIR / "cache"
UNIVERSE_DIR = HOME_DIR / "universes"
RESEARCH_DIR = HOME_DIR / "research"


def ensure_dirs() -> None:
    for d in (HOME_DIR, CACHE_DIR, UNIVERSE_DIR, RESEARCH_DIR):
        d.mkdir(parents=True, exist_ok=True)

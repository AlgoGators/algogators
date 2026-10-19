"""The three live gates and the kill switch, checked at call time (never cached)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..shared.paths import data_root, repo_root

ENV_VAR = "PREDICT_NGIN_LIVE"
CONFIG = Path("config") / "live.local.yaml"


class MutationBlocked(RuntimeError):
    """A state-changing call was attempted without every live gate armed."""


class KillSwitchOn(MutationBlocked):
    """The kill switch is on: no new orders (cancels are still allowed)."""


def config_path() -> Path:
    return repo_root() / CONFIG


def stop_path() -> Path:
    return data_root() / "live" / "STOP"


def load_config() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    import yaml

    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


@dataclass
class LiveGate:
    """``armed(live)`` is True only when the env var, the config and the caller all say live."""

    venue: str

    def reasons(self, live: bool) -> List[str]:
        """Why live trading is blocked right now (empty list = armed)."""
        out = []
        if os.environ.get(ENV_VAR, "").strip() != "1":
            out.append(f"environment variable {ENV_VAR} is not 1")
        cfg = (load_config().get("live") or {}) if config_path().exists() else None
        if cfg is None:
            out.append(f"{CONFIG.as_posix()} does not exist")
        else:
            if cfg.get("enabled") is not True:
                out.append(f"{CONFIG.as_posix()}: live.enabled is not true")
            if self.venue not in (cfg.get("venues") or []):
                out.append(f"{CONFIG.as_posix()}: venue {self.venue!r} not in live.venues")
        if not live:
            out.append("caller did not pass live=True (--live)")
        return out

    def armed(self, live: bool) -> bool:
        return not self.reasons(live)

    def kill_reason(self) -> Optional[str]:
        if stop_path().exists():
            return f"kill switch file {stop_path()} exists"
        if load_config().get("kill") is True:
            return f"{CONFIG.as_posix()}: kill is true"
        return None

    def require(self, live: bool, *, new_order: bool) -> None:
        """Raise unless a live mutation is allowed now (new orders need the kill switch off)."""
        why = self.reasons(live)
        if why:
            raise MutationBlocked("live mutation refused: " + "; ".join(why))
        if new_order:
            k = self.kill_reason()
            if k:
                raise KillSwitchOn(k)

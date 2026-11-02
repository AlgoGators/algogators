"""Hot reload of config/recorder.yaml discovery groups, without restarting the recorder.

The recorder polls the config file's mtime (``ConfigWatcher``). When it changes and the new file loads, the
discovery groups are diffed by (venue, name) (``diff_groups``) and the runner applies the difference:

* added group   -> a new discovery loop (and, for Polymarket, its own WS feed; for Kalshi the pooled feed is
                   created if it did not exist), so markets start recording on its first refresh;
* removed group -> its loop is cancelled and ``Universe.set_group(venue, name, [])`` rolls its markets out
                   (markets another group or the static config still holds stay subscribed);
* changed group -> its loop restarts with the new settings; the next refresh replaces its market set.
  Per-market settings (depth, heartbeat, sampling) apply to markets subscribed after the change; markets
  already subscribed keep the settings they were subscribed with until they roll over.

Everything else in the file (defaults, venues, static pairs/markets, rtds, settlement, discovery rate limits)
still needs a restart; such changes are logged as "needs restart" and ignored. A file that does not load is
rejected as a whole: the running groups stay, an error is logged, and the next mtime change is tried again.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .config import DiscoveryGroup, RecorderConfig

log = logging.getLogger("recorder.reload")

HOT_VENUES = ("kalshi", "polymarket")  # venues whose discovery groups can be changed live


@dataclass
class GroupDiff:
    added: List[DiscoveryGroup] = field(default_factory=list)
    removed: List[DiscoveryGroup] = field(default_factory=list)
    changed: List[Tuple[DiscoveryGroup, DiscoveryGroup]] = field(default_factory=list)  # (old, new)
    needs_restart: List[str] = field(default_factory=list)  # config sections that changed but are not hot

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def summary(self) -> str:
        parts = [f"+{g.venue}:{g.name}" for g in self.added] + [f"-{g.venue}:{g.name}" for g in self.removed]
        parts += [f"~{n.venue}:{n.name}" for _, n in self.changed]
        return " ".join(parts) or "no group changes"


def active_groups(cfg: RecorderConfig) -> Dict[Tuple[str, str], DiscoveryGroup]:
    out = {}
    for venue in HOT_VENUES:
        if venue in cfg.venues:
            for g in cfg.discovery_groups(venue):
                out[(g.venue, g.name)] = g
    return out


def diff_groups(old: RecorderConfig, new: RecorderConfig) -> GroupDiff:
    d = GroupDiff()
    a, b = active_groups(old), active_groups(new)
    d.added = [b[k] for k in b if k not in a]
    d.removed = [a[k] for k in a if k not in b]
    d.changed = [(a[k], b[k]) for k in b if k in a and a[k] != b[k]]
    for name in ("defaults", "venues", "pairs", "markets", "rtds", "settlement"):
        if getattr(old, name) != getattr(new, name):
            d.needs_restart.append(name)
    if replace(old.discovery, groups=()) != replace(new.discovery, groups=()):
        d.needs_restart.append("discovery (rate limits / grace)")
    return d


class ConfigWatcher:
    """Returns a newly loaded config once per change of the file's (mtime, size); None otherwise.
    A file that fails to load is reported once and not retried until it changes again."""

    def __init__(self, path: Path, loader: Callable[[], RecorderConfig]):
        self.path = Path(path)
        self.loader = loader
        self._sig = self._signature()

    def _signature(self) -> Optional[Tuple[int, int]]:
        try:
            st = os.stat(self.path)
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None

    def poll(self) -> Optional[RecorderConfig]:
        sig = self._signature()
        if sig is None or sig == self._sig:
            return None
        self._sig = sig
        try:
            return self.loader()
        except Exception as e:  # ConfigError, YAML errors, anything: keep the running config
            log.error("config reload REJECTED (%s): %s -- keeping the running discovery groups", self.path, e)
            return None

"""The set of markets the recorder should be subscribed to right now (rollover logic).

Two parts per venue:

* **static** -- ``pairs`` / ``markets`` from config/recorder.yaml. Never removed.
* **dynamic** -- markets found by discovery groups (``discovery:`` in the config). Each
  group's set is *replaced* on every successful refresh (markets that no longer
  qualify roll out) and markets are also expired locally once ``close_time +
  close_grace_s`` has passed, so a 15-minute market is unsubscribed on time even if the
  REST refresh is slow or rate-limited.

Feeds read :meth:`Universe.current` and diff it against what they are subscribed to.
Pure (no I/O); ``version`` bumps on every change and an optional callback fires so
async feeds can wake up.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .config import MarketRef

KINDS = ("static", "dynamic", "all")  # or "group:<name>" (one discovery group, no static)


@dataclass
class DynMarket:
    ref: MarketRef
    close_ns: Optional[int] = None  # None = never expires locally
    info: dict = field(default_factory=dict)  # series/event/title/strikes for the markets table


class Universe:
    def __init__(self, static: Optional[Iterable[MarketRef]] = None,
                 on_change: Optional[Callable[[str], None]] = None, default_depth: int = 10,
                 default_heartbeat_s: float = 60.0):
        self.default_depth = default_depth
        self.default_heartbeat_s = default_heartbeat_s
        self.static: Dict[str, Dict[str, MarketRef]] = {}
        for m in static or []:
            self.static.setdefault(m.venue, {}).setdefault(m.market_id, m)
        # (venue, group) -> market_id -> DynMarket
        self.groups: Dict[Tuple[str, str], Dict[str, DynMarket]] = {}
        self.version = 0
        self.on_change = on_change
        self.log: List[Tuple[str, str, DynMarket]] = []  # ("add"|"remove", venue, market); drained by runner

    # -- queries --------------------------------------------------------------

    def current(self, venue: str, kind: str = "all") -> Dict[str, MarketRef]:
        out: Dict[str, MarketRef] = {}
        if kind.startswith("group:"):
            name = kind[6:]
            st = self.static.get(venue, {})
            return {mid: dm.ref for mid, dm in self.groups.get((venue, name), {}).items() if mid not in st}
        if kind in ("static", "all"):
            out.update(self.static.get(venue, {}))
        if kind in ("dynamic", "all"):
            for (v, _g), ms in self.groups.items():
                if v != venue:
                    continue
                for mid, dm in ms.items():
                    if kind == "dynamic" and mid in self.static.get(venue, {}):
                        continue  # static wins; served by the static feed
                    prev = out.get(mid)
                    if prev is None:
                        out[mid] = dm.ref
                    elif mid not in self.static.get(venue, {}):
                        # a market held by several groups keeps the first group's settings but
                        # the deepest book any of them asks for (k-lip needs depth to the
                        # 1,000-contract LIP target even on markets k-mentions records at 5)
                        # and the most frequent heartbeat (k-uflow-open needs 30 s on markets
                        # k-econ / k-lip hold at their slower defaults)
                        d_new = dm.ref.depth or self.default_depth
                        if d_new > (prev.depth or self.default_depth):
                            out[mid] = prev = replace(prev, depth=d_new)
                        hb_new = dm.ref.heartbeat_s
                        if hb_new is not None and hb_new < (prev.heartbeat_s or self.default_heartbeat_s):
                            out[mid] = replace(prev, heartbeat_s=hb_new)
        return out

    def group_ids(self, venue: str, group: str) -> set:
        return set(self.groups.get((venue, group), {}))

    def dynamic_count(self, venue: Optional[str] = None) -> int:
        ids = set()
        for (v, _g), ms in self.groups.items():
            if venue is None or v == venue:
                ids.update((v, m) for m in ms)
        return len(ids)

    # -- updates --------------------------------------------------------------

    def _present_elsewhere(self, venue: str, group: str, mid: str) -> bool:
        if mid in self.static.get(venue, {}):
            return True
        return any(v == venue and g != group and mid in ms for (v, g), ms in self.groups.items())

    def set_group(self, venue: str, group: str, markets: Iterable[DynMarket],
                  now_ns: Optional[int] = None, grace_ns: int = 0) -> Tuple[List[str], List[str]]:
        """Replace one group's market set. Markets already past ``close + grace`` are skipped.

        Returns (added, removed) market ids *for the venue as a whole* -- a market that is
        still held by another group or by the static config is not reported as removed.
        """
        new: Dict[str, DynMarket] = {}
        for dm in markets:
            if now_ns is not None and dm.close_ns is not None and dm.close_ns + grace_ns < now_ns:
                continue
            new.setdefault(dm.ref.market_id, dm)
        old = self.groups.get((venue, group), {})
        added = [m for m in new if m not in old and not self._present_elsewhere(venue, group, m)]
        gone = [m for m in old if m not in new]
        self.groups[(venue, group)] = new
        removed = [m for m in gone if not self._present_elsewhere(venue, group, m)]
        for m in added:
            self.log.append(("add", venue, new[m]))
        for m in removed:
            self.log.append(("remove", venue, old[m]))
        if added or removed:
            self._changed(venue)
        return added, removed

    def expire(self, now_ns: int, grace_ns: int = 0) -> Dict[str, List[str]]:
        """Drop dynamic markets whose close time + grace has passed. Returns venue -> removed ids."""
        out: Dict[str, List[str]] = {}
        for (venue, group), ms in list(self.groups.items()):
            dead = [mid for mid, dm in ms.items() if dm.close_ns is not None and dm.close_ns + grace_ns < now_ns]
            for mid in dead:
                dm = ms.pop(mid)
                if not self._present_elsewhere(venue, group, mid):
                    out.setdefault(venue, []).append(mid)
                    self.log.append(("remove", venue, dm))
        for venue in out:
            self._changed(venue)
        return out

    def drain_log(self) -> List[Tuple[str, str, DynMarket]]:
        out, self.log = self.log, []
        return out

    def _changed(self, venue: str) -> None:
        self.version += 1
        if self.on_change is not None:
            self.on_change(venue)


def assign_shards(desired: Iterable[str], assigned: Dict[str, int], sizes: Dict[int, int],
                  cap: int) -> Tuple[Dict[str, int], List[str], Dict[int, List[str]], Dict[int, List[str]]]:
    """Sticky assignment of markets to connections (shards) with at most ``cap`` each.

    ``assigned``: market -> shard currently holding it. ``sizes`` is ignored for shards
    and recomputed (kept for API symmetry). Returns (new assignment, removed markets,
    adds per shard, removes per shard). Existing markets never move; new ones fill the
    lowest-numbered shard with room, opening new shards as needed.
    """
    desired = list(dict.fromkeys(desired))
    want = set(desired)
    cap = max(1, int(cap))
    new_assign = {m: s for m, s in assigned.items() if m in want}
    removed = [m for m in assigned if m not in want]
    rem_by: Dict[int, List[str]] = {}
    for m in removed:
        rem_by.setdefault(assigned[m], []).append(m)
    count: Dict[int, int] = {}
    for s in new_assign.values():
        count[s] = count.get(s, 0) + 1
    add_by: Dict[int, List[str]] = {}
    shard = 0
    for m in desired:
        if m in new_assign:
            continue
        while count.get(shard, 0) >= cap:
            shard += 1
        new_assign[m] = shard
        count[shard] = count.get(shard, 0) + 1
        add_by.setdefault(shard, []).append(m)
    return new_assign, removed, add_by, rem_by

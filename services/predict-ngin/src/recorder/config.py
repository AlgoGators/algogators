"""Parse config/recorder.yaml into typed objects.

See docs/RECORDER.md for the file format.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Dict, List, Optional

import yaml

VENUES = ("kalshi", "polymarket", "polymarket_us")
MODES = ("auto", "ws", "rest")
PAIR_STATUSES = ("verified", "needs-review")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SampleWindow:
    """A time window [start_ns, end_ns) in which a group samples faster (e.g. election night).

    ``interval_s`` replaces the group's book sampling interval inside the window; ``heartbeat_s``
    (optional) replaces its heartbeat. Config: ``{start: ISO-8601, end: ISO-8601, interval_s: 1}``;
    a timestamp without an offset is taken as UTC.
    """

    start_ns: int
    end_ns: int
    interval_s: float
    heartbeat_s: Optional[float] = None

    def contains(self, t_ns: int) -> bool:
        return self.start_ns <= t_ns < self.end_ns


def _ts_ns(v, what: str) -> int:
    from ..timeconv import to_epoch

    try:
        out = to_epoch(str(v), "ns")
    except Exception as e:
        raise ConfigError(f"{what}: bad timestamp {v!r}: {e}") from e
    if out is None:
        raise ConfigError(f"{what}: bad timestamp {v!r}")
    return int(out)


def parse_windows(raw, what: str, need_interval: bool = True) -> tuple:
    """List of ``{start, end, interval_s[, heartbeat_s]}`` mappings -> tuple of SampleWindow."""
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ConfigError(f"{what}: windows must be a list")
    out = []
    for w in raw:
        if not isinstance(w, dict):
            raise ConfigError(f"{what}: each window must be a mapping")
        unknown = set(w) - {"start", "end", "interval_s", "heartbeat_s"}
        if unknown:
            raise ConfigError(f"{what}: unknown window keys {sorted(unknown)}")
        if "start" not in w or "end" not in w:
            raise ConfigError(f"{what}: window needs start and end")
        a, b = _ts_ns(w["start"], what), _ts_ns(w["end"], what)
        if b <= a:
            raise ConfigError(f"{what}: window end must be after start")
        if need_interval and "interval_s" not in w:
            raise ConfigError(f"{what}: window needs interval_s")
        iv = float(w.get("interval_s", 0.0))
        if need_interval and iv <= 0:
            raise ConfigError(f"{what}: interval_s must be > 0")
        hb = w.get("heartbeat_s")
        out.append(SampleWindow(a, b, iv, None if hb is None else float(hb)))
    return tuple(sorted(out, key=lambda x: x.start_ns))


@dataclass(frozen=True)
class Defaults:
    depth: int = 10
    ws_sample_interval_s: float = 1.0
    heartbeat_s: float = 60.0
    flush_interval_s: float = 300.0
    output_dir: str = "data/live_books"


@dataclass(frozen=True)
class VenueConfig:
    enabled: bool = True
    mode: str = "auto"  # auto: ws if possible else rest
    rest_poll_interval_s: float = 5.0
    max_requests_per_s: float = 5.0
    trades_poll_interval_s: float = 15.0  # REST trade polling (kalshi)
    resync_interval_s: float = 600.0  # periodic REST snapshot while on WS (polymarket)
    # Kalshi WS: markets per connection for discovered (dynamic) markets. Static pairs/markets
    # always get their own connection so discovery churn can never disturb them. One
    # subscription accepted 6,865 markets without error (probe 2026-10-13); the cap bounds
    # how many books a sequence-gap reconnect has to rebuild.
    max_markets_per_conn: int = 1000
    # Kalshi WS silence audit: a book with no WS message for ``silence_audit_s`` is compared
    # with a REST snapshot (at most ``audit_max_per_round`` books every ``audit_interval_s``).
    # A confirmed mismatch means the subscription stopped delivering -> reconnect that shard.
    silence_audit_s: float = 120.0  # 0 disables
    audit_interval_s: float = 60.0
    audit_max_per_round: int = 10


_VENUE_DEFAULTS = {
    # Kalshi Basic tier = 200 read tokens/s ~= 20 req/s (authenticated); stay well under.
    "kalshi": VenueConfig(mode="auto", rest_poll_interval_s=2.0, max_requests_per_s=8.0),
    "polymarket": VenueConfig(mode="ws", rest_poll_interval_s=2.0, max_requests_per_s=10.0),
    # Polymarket US public gateway: 25 req/s per IP shared across endpoints.
    "polymarket_us": VenueConfig(mode="rest", rest_poll_interval_s=2.0, max_requests_per_s=8.0),
}


@dataclass(frozen=True)
class MarketRef:
    venue: str
    market_id: str  # kalshi ticker | polymarket token id | polymarket_us slug
    outcome: str = "yes"
    pair: Optional[str] = None
    label: Optional[str] = None
    # per-market overrides (set by discovery groups); None = use defaults
    depth: Optional[int] = None
    heartbeat_s: Optional[float] = None
    group: Optional[str] = None
    bbo: bool = True  # polymarket: record venue best_bid_ask events
    sample_interval_s: Optional[float] = None  # book sampling interval (None = defaults)
    sample_windows: tuple = ()  # SampleWindow overrides (faster sampling inside a window)


@dataclass(frozen=True)
class DiscoveryGroup:
    """A rolling set of markets found by series/tag instead of listed by id.

    Kalshi: ``series`` (explicit tickers) and/or ``category`` (every series in that Kalshi
    category that currently has an open event, e.g. ``Mentions``).
    Polymarket: ``tags`` (Gamma tag slugs) filtered by ``slug_regex``.
    """

    name: str
    venue: str
    series: tuple = ()
    category: Optional[str] = None
    tags: tuple = ()
    slug_regex: Optional[str] = None
    refresh_s: float = 60.0
    max_hours_to_close: Optional[float] = None  # only markets closing within N hours
    nearest_strikes: Optional[int] = None  # per event: the K strikes nearest the implied price
    include_unopened: bool = False  # kalshi: also subscribe markets listed but not yet open
    depth: Optional[int] = None
    heartbeat_s: Optional[float] = None
    bbo: bool = False  # polymarket: record best_bid_ask events for this group
    tokens: str = "first"  # polymarket: first | both outcome tokens
    enabled: bool = True
    # kalshi category groups: only series whose ticker matches (re.search)
    series_regex: Optional[str] = None
    # kalshi: only markets whose event_ticker matches (re.search), e.g. '-(26|2026)$'
    event_regex: Optional[str] = None
    sample_interval_s: Optional[float] = None  # book sampling interval for this group
    sample_windows: tuple = ()  # SampleWindow(s): faster sampling inside a time window
    track_settlement: bool = False  # kalshi: log status/result changes (see settlement:)
    # kalshi: take markets from GET /incentive_programs (Liquidity Incentive Program) instead of
    # series/category. Ranked by reward $/day; at most ``max_markets``; programmes that start
    # within ``incentive_lookahead_s`` (status=upcoming) count too, so a 15-minute window is
    # subscribed before it opens. Programme snapshots go to the ``lip`` table.
    incentives: bool = False
    max_markets: Optional[int] = None
    incentive_lookahead_s: float = 0.0
    min_reward_per_day: float = 0.0  # USD/day (programme period_reward spread over its period)


@dataclass(frozen=True)
class DiscoveryConfig:
    kalshi_max_requests_per_s: float = 1.0  # REST budget for discovery (other processes share the IP)
    polymarket_max_requests_per_s: float = 2.0
    close_grace_s: float = 120.0  # keep recording this long after close_time, then unsubscribe
    series_scan_s: float = 21600.0  # category groups: re-scan which series have open events
    groups: tuple = ()


@dataclass(frozen=True)
class RtdsConfig:
    """Polymarket real-time data socket (public): reference crypto prices."""

    enabled: bool = False
    url: str = "wss://ws-live-data.polymarket.com"
    topics: tuple = ()  # ((topic, (symbols...)), ...); empty symbols = all


@dataclass(frozen=True)
class SettlementConfig:
    """Kalshi close/settlement logging for the markets of ``track_settlement`` groups."""

    enabled: bool = False
    interval_s: float = 3600.0  # poll interval outside ``fast_windows``
    fast_interval_s: float = 300.0  # poll interval inside ``fast_windows``
    fast_windows: tuple = ()  # SampleWindow(s); only start/end are used
    max_requests_per_s: float = 1.0
    batch: int = 100  # tickers per GET /markets?tickers=
    state_file: str = "kalshi/settlement_state.json"  # relative to output_dir

    def interval_at(self, t_ns: int) -> float:
        return self.fast_interval_s if any(w.contains(t_ns) for w in self.fast_windows) else self.interval_s


@dataclass
class Pair:
    name: str
    legs: List[MarketRef]
    note: Optional[str] = None
    status: str = "needs-review"  # verified | needs-review (same event + resolution checked by a human)


@dataclass
class RecorderConfig:
    defaults: Defaults = field(default_factory=Defaults)
    venues: Dict[str, VenueConfig] = field(default_factory=dict)
    pairs: List[Pair] = field(default_factory=list)
    markets: List[MarketRef] = field(default_factory=list)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    rtds: RtdsConfig = field(default_factory=RtdsConfig)
    settlement: SettlementConfig = field(default_factory=SettlementConfig)

    def discovery_groups(self, venue: str) -> List[DiscoveryGroup]:
        if not self.venues[venue].enabled:
            return []
        return [g for g in self.discovery.groups if g.venue == venue and g.enabled]

    def subscriptions(self) -> Dict[str, List[MarketRef]]:
        """All markets to record, grouped by venue, de-duplicated (pair legs win)."""
        out: Dict[str, Dict[str, MarketRef]] = {v: {} for v in VENUES}
        for p in self.pairs:
            for leg in p.legs:
                out[leg.venue].setdefault(leg.market_id, leg)
        for m in self.markets:
            out[m.venue].setdefault(m.market_id, m)
        return {v: list(d.values()) for v, d in out.items() if self.venues[v].enabled}


def _sid(v) -> str:
    """IDs must be strings. Unquoted 78-digit token ids parse as Python ints -- exact, so str() is safe."""
    if v is None:
        raise ConfigError("missing id")
    if isinstance(v, float):
        raise ConfigError(f"id {v!r} parsed as float -- quote it in YAML")
    return str(v).strip()


def _build(cls, raw: dict, base):
    if raw is None:
        return base
    if not isinstance(raw, dict):
        raise ConfigError(f"expected mapping for {cls.__name__}")
    names = {f.name for f in fields(cls)}
    unknown = set(raw) - names
    if unknown:
        raise ConfigError(f"unknown keys for {cls.__name__}: {sorted(unknown)}")
    return replace(base, **raw)


def _legs_from_pair(name: str, raw: dict) -> List[MarketRef]:
    legs: List[MarketRef] = []
    if "kalshi" in raw:
        k = raw["kalshi"] or {}
        legs.append(MarketRef("kalshi", _sid(k.get("ticker")), "yes", name))
    if "polymarket" in raw:
        p = raw["polymarket"] or {}
        if p.get("yes_token") is None:
            raise ConfigError(f"pair {name}: polymarket needs yes_token")
        legs.append(MarketRef("polymarket", _sid(p["yes_token"]), "yes", name))
        if p.get("no_token") is not None:
            legs.append(MarketRef("polymarket", _sid(p["no_token"]), "no", name))
    if "polymarket_us" in raw:
        u = raw["polymarket_us"] or {}
        legs.append(MarketRef("polymarket_us", _sid(u.get("slug")), u.get("outcome", "yes"), name))
    venues = {l.venue for l in legs}
    if len(venues) < 2:
        raise ConfigError(f"pair {name}: needs legs on at least two venues")
    return legs


def _single(raw: dict) -> MarketRef:
    venue = raw.get("venue")
    if venue not in VENUES:
        raise ConfigError(f"unknown venue {venue!r}")
    key = {"kalshi": "ticker", "polymarket": "token_id", "polymarket_us": "slug"}[venue]
    return MarketRef(venue, _sid(raw.get(key)), str(raw.get("outcome", "yes")), None, raw.get("label"))


def parse_config(text: str) -> RecorderConfig:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ConfigError(str(e)) from e
    if not isinstance(raw, dict):
        raise ConfigError("top level must be a mapping")

    defaults = _build(Defaults, raw.get("defaults"), Defaults())
    venues: Dict[str, VenueConfig] = {}
    vraw = raw.get("venues") or {}
    unknown = set(vraw) - set(VENUES)
    if unknown:
        raise ConfigError(f"unknown venues: {sorted(unknown)}")
    for v in VENUES:
        vc = _build(VenueConfig, vraw.get(v), _VENUE_DEFAULTS[v])
        if vc.mode not in MODES:
            raise ConfigError(f"{v}: mode must be one of {MODES}")
        venues[v] = vc

    pairs: List[Pair] = []
    seen = set()
    for p in raw.get("pairs") or []:
        name = p.get("name")
        if not name:
            raise ConfigError("every pair needs a name")
        if name in seen:
            raise ConfigError(f"duplicate pair name {name}")
        seen.add(name)
        status = str(p.get("status", "needs-review"))
        if status not in PAIR_STATUSES:
            raise ConfigError(f"pair {name}: status must be one of {PAIR_STATUSES}")
        pairs.append(Pair(name, _legs_from_pair(name, p), p.get("note"), status))

    markets = [_single(m) for m in raw.get("markets") or []]
    discovery = _discovery(raw.get("discovery"))
    rtds = _rtds(raw.get("rtds"))
    settlement = _settlement(raw.get("settlement"))
    if settlement.enabled and not any(g.track_settlement for g in discovery.groups):
        raise ConfigError("settlement.enabled but no discovery group has track_settlement: true")
    return RecorderConfig(defaults, venues, pairs, markets, discovery, rtds, settlement)


_GROUP_KEYS = {f.name for f in fields(DiscoveryGroup)} - {"venue"}


def _tuple(v) -> tuple:
    if v is None:
        return ()
    if isinstance(v, (str, int)):
        return (str(v),)
    return tuple(str(x) for x in v)


def _discovery(raw) -> DiscoveryConfig:
    if raw is None:
        return DiscoveryConfig()
    if not isinstance(raw, dict):
        raise ConfigError("discovery must be a mapping")
    groups: List[DiscoveryGroup] = []
    names = set()
    top = {}
    for venue, body in raw.items():
        if venue in ("close_grace_s", "series_scan_s"):
            top[venue] = float(body)
            continue
        if venue not in ("kalshi", "polymarket"):
            raise ConfigError(f"discovery: unsupported venue {venue!r}")
        body = body or {}
        if "max_requests_per_s" in body:
            top[f"{venue}_max_requests_per_s"] = float(body["max_requests_per_s"])
        for g in body.get("groups") or []:
            unknown = set(g) - _GROUP_KEYS
            if unknown:
                raise ConfigError(f"discovery group {g.get('name')}: unknown keys {sorted(unknown)}")
            name = g.get("name")
            if not name or name in names:
                raise ConfigError(f"discovery group needs a unique name (got {name!r})")
            names.add(name)
            kw = dict(g)
            for k in ("series", "tags"):
                kw[k] = _tuple(kw.get(k))
            kw["sample_windows"] = parse_windows(kw.get("sample_windows"), f"discovery group {name}")
            for k in ("incentive_lookahead_s", "min_reward_per_day"):
                if k in kw:
                    kw[k] = float(kw[k])
            if kw.get("max_markets") is not None:
                kw["max_markets"] = int(kw["max_markets"])
            if kw.get("sample_interval_s") is not None:
                kw["sample_interval_s"] = float(kw["sample_interval_s"])
                if kw["sample_interval_s"] <= 0:
                    raise ConfigError(f"discovery group {name}: sample_interval_s must be > 0")
            grp = DiscoveryGroup(venue=venue, **kw)
            if venue == "kalshi" and not (grp.series or grp.category or grp.incentives):
                raise ConfigError(f"discovery group {name}: kalshi needs series, category or incentives")
            if grp.incentives and venue != "kalshi":
                raise ConfigError(f"discovery group {name}: incentives is kalshi-only")
            if grp.incentives and (grp.series or grp.category):
                raise ConfigError(f"discovery group {name}: incentives excludes series/category")
            if grp.max_markets is not None and int(grp.max_markets) < 1:
                raise ConfigError(f"discovery group {name}: max_markets must be >= 1")
            if venue == "polymarket" and not grp.tags:
                raise ConfigError(f"discovery group {name}: polymarket needs tags")
            if grp.tokens not in ("first", "both"):
                raise ConfigError(f"discovery group {name}: tokens must be first|both")
            if grp.track_settlement and venue != "kalshi":
                raise ConfigError(f"discovery group {name}: track_settlement is kalshi-only")
            for key in ("slug_regex", "series_regex", "event_regex"):
                rx = getattr(grp, key)
                if rx:
                    import re

                    try:
                        re.compile(rx)
                    except re.error as e:
                        raise ConfigError(f"discovery group {name}: bad {key}: {e}") from e
            groups.append(grp)
    return DiscoveryConfig(groups=tuple(groups), **top)


def _rtds(raw) -> RtdsConfig:
    if raw is None:
        return RtdsConfig()
    topics = []
    for t in raw.get("topics") or []:
        topics.append((str(t["topic"]), _tuple(t.get("symbols"))))
    kw = {k: v for k, v in raw.items() if k != "topics"}
    unknown = set(kw) - {"enabled", "url"}
    if unknown:
        raise ConfigError(f"rtds: unknown keys {sorted(unknown)}")
    return RtdsConfig(topics=tuple(topics), **kw)


def _settlement(raw) -> SettlementConfig:
    if raw is None:
        return SettlementConfig()
    if not isinstance(raw, dict):
        raise ConfigError("settlement must be a mapping")
    names = {f.name for f in fields(SettlementConfig)}
    unknown = set(raw) - names
    if unknown:
        raise ConfigError(f"settlement: unknown keys {sorted(unknown)}")
    kw = dict(raw)
    kw["fast_windows"] = parse_windows(kw.get("fast_windows"), "settlement.fast_windows", need_interval=False)
    for k in ("interval_s", "fast_interval_s", "max_requests_per_s"):
        if k in kw:
            kw[k] = float(kw[k])
            if kw[k] <= 0:
                raise ConfigError(f"settlement.{k} must be > 0")
    if "batch" in kw:
        kw["batch"] = int(kw["batch"])
        if not 1 <= kw["batch"] <= 1000:
            raise ConfigError("settlement.batch must be 1..1000")
    return SettlementConfig(**kw)


def load_config(path: Path | str) -> RecorderConfig:
    return parse_config(Path(path).read_text(encoding="utf-8"))

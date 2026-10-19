"""Series / tag based market discovery for the recorder (read-only, public REST).

Kalshi markets roll (15-minute crypto, hourly index, daily commodities, monthly econ),
so instead of listing tickers the config lists *series* (or a Kalshi *category* such as
``Mentions``). Every ``refresh_s`` each group is re-queried and the universe is replaced:

* Kalshi: ``GET /markets?series_ticker=S&status=open`` (and ``status=unopened`` when
  ``include_unopened``; 15-minute markets are listed ahead of their window). Category
  groups first find which series of that category have an open event
  (``GET /series?category=..`` + one pass over ``GET /events?status=open``), refreshed every
  ``series_scan_s``.
* Polymarket: Gamma ``GET /events?tag_slug=T&closed=false&end_date_min=..&end_date_max=..``,
  filtered by ``slug_regex``; the first outcome token (``Up``) of each market.

* Kalshi incentives groups (``incentives: true``, e.g. ``k-lip``): markets with a Liquidity
  Incentive Program from ``GET /incentive_programs?status=active|upcoming&type=liquidity``,
  ranked by reward $/day and capped at ``max_markets``; close times / status come from
  ``GET /markets?tickers=`` (100 per request, cached per ticker). Every programme seen is
  written to the ``lip`` table each refresh.

Filters: ``max_hours_to_close`` and ``nearest_strikes`` (per event, the K markets whose
strike is closest to the price implied by the event's own quotes, with hysteresis so the
set does not churn every refresh).

The selection functions are pure and unit-tested; the async loops only fetch.
Kalshi's ``max_close_ts`` filter is NOT combinable with ``status=open`` (returns 0
markets, checked 2026-10-13), so close-time filtering is done client-side.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence

from .config import DiscoveryConfig, DiscoveryGroup, MarketRef
from .universe import DynMarket, Universe
from .util import Backoff, TokenBucket, rfc3339_to_ns

log = logging.getLogger("recorder.discovery")

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
_NS = 1_000_000_000
HYSTERESIS = 1.5  # keep an already-subscribed strike while it ranks within ceil(K * 1.5)


# ---------------------------------------------------------------------------
# Pure helpers: Kalshi
# ---------------------------------------------------------------------------


def _f(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def kalshi_strike(m: dict) -> Optional[float]:
    """Representative strike: floor/cap for directional markets, bracket midpoint for ranges."""
    st = m.get("strike_type")
    lo, hi = _f(m.get("floor_strike")), _f(m.get("cap_strike"))
    if st == "between" and lo is not None and hi is not None:
        return (lo + hi) / 2.0
    if lo is not None:
        return lo
    return hi


def kalshi_mid(m: dict) -> Optional[float]:
    """YES mid from the REST snapshot; falls back to last price. None if unquoted."""
    b, a = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    if b is not None and a is not None and a > 0 and (b > 0 or a < 1):
        return (b + a) / 2.0
    lp = _f(m.get("last_price_dollars"))
    return lp if lp else None


def implied_center(markets: Sequence[dict]) -> Optional[float]:
    """The underlying level implied by one event's strike ladder.

    * Directional ("greater"/"less" etc.): the strike whose YES mid is closest to 0.5.
    * Ranges ("between"): the bracket with the highest YES mid.
    * Unquoted ladder: the median strike.
    """
    pts = [(kalshi_strike(m), kalshi_mid(m), m.get("strike_type")) for m in markets]
    pts = [p for p in pts if p[0] is not None]
    if not pts:
        return None
    quoted = [p for p in pts if p[1] is not None]
    if quoted:
        if any(p[2] == "between" for p in quoted):
            return max(quoted, key=lambda p: (p[1], -abs(p[1] - 0.5)))[0]
        return min(quoted, key=lambda p: abs(p[1] - 0.5))[0]
    ks = sorted(p[0] for p in pts)
    return ks[len(ks) // 2]


def nearest_strikes(markets: Sequence[dict], k: int, keep: Iterable[str] = ()) -> List[dict]:
    """Per event: the ``k`` markets nearest the implied center. Markets in ``keep`` (already
    subscribed) stay while they rank within ceil(k * HYSTERESIS). Non-strike markets pass."""
    keep = set(keep)
    by_event: Dict[str, List[dict]] = {}
    out: List[dict] = []
    for m in markets:
        if kalshi_strike(m) is None:
            out.append(m)
        else:
            by_event.setdefault(m.get("event_ticker") or "", []).append(m)
    soft = int(math.ceil(k * HYSTERESIS))
    for ev, ms in by_event.items():
        c = implied_center(ms)
        ranked = sorted(ms, key=lambda m: (abs(kalshi_strike(m) - c), kalshi_strike(m)))
        for i, m in enumerate(ranked):
            if i < k or (i < soft and m.get("ticker") in keep):
                out.append(m)
    return out


def select_kalshi(markets: Sequence[dict], g: DiscoveryGroup, now_ns: int,
                  current: Iterable[str] = ()) -> List[DynMarket]:
    """Apply a group's filters to raw ``/markets`` rows -> DynMarkets."""
    rows = []
    erx = re.compile(g.event_regex) if g.event_regex else None
    for m in markets:
        if m.get("status") not in (None, "active", "open", "initialized", "unopened"):
            continue
        if erx is not None and not erx.search(m.get("event_ticker") or ""):
            continue
        close = rfc3339_to_ns(m.get("close_time"))
        if close is not None and close < now_ns:
            continue
        if g.max_hours_to_close is not None and close is not None and close > now_ns + g.max_hours_to_close * 3600 * _NS:
            continue
        rows.append(m)
    if g.nearest_strikes:
        rows = nearest_strikes(rows, g.nearest_strikes, keep=current)
    out = []
    for m in rows:
        tk = m["ticker"]
        out.append(DynMarket(
            MarketRef("kalshi", tk, "yes", None, g.name, g.depth, g.heartbeat_s, g.name, True,
                      g.sample_interval_s, g.sample_windows),
            rfc3339_to_ns(m.get("close_time")),
            {"series": tk.split("-")[0], "event": m.get("event_ticker"), "title":
             " ".join(x for x in (m.get("title"), m.get("yes_sub_title")) if x),
             "strike_type": m.get("strike_type"), "floor_strike": _f(m.get("floor_strike")),
             "cap_strike": _f(m.get("cap_strike"))},
        ))
    return out


def active_series_in_category(events: Iterable[dict], category_series: Iterable[str]) -> List[str]:
    """Series of a category that have at least one open event."""
    cat = set(category_series)
    return sorted({e.get("series_ticker") for e in events if e.get("series_ticker") in cat})


def filter_series(series: Iterable[str], series_regex: Optional[str]) -> List[str]:
    """Category series narrowed by a group's ``series_regex`` (re.search); order kept."""
    if not series_regex:
        return list(series)
    rx = re.compile(series_regex)
    return [s for s in series if rx.search(s)]


# ---------------------------------------------------------------------------
# Pure helpers: Kalshi Liquidity Incentive Program (incentives groups)
# ---------------------------------------------------------------------------

INCENTIVE_STICKY = 1.25  # an already-subscribed market ranks as if its reward were 25% higher


def lip_programs(programs: Iterable[dict], now_ns: int, lookahead_s: float = 0.0) -> List[dict]:
    """Parse raw ``/incentive_programs`` rows (liquidity only) into dicts with ns times and USD.

    Keeps programmes that have not ended and have started, or start within ``lookahead_s``.
    ``period_reward`` is in centi-cents (1e-4 USD); ``reward_usd_day`` spreads it uniformly over
    the period (at least 15 minutes)."""
    out = []
    horizon = now_ns + int(lookahead_s * _NS)
    for p in programs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        tk = p.get("market_ticker")
        a, b = rfc3339_to_ns(p.get("start_date")), rfc3339_to_ns(p.get("end_date"))
        if not tk or a is None or b is None or b <= now_ns or a > horizon:
            continue
        usd = (_f(p.get("period_reward")) or 0.0) / 1e4
        days = max((b - a) / (86400 * _NS), 1.0 / 96)
        out.append({
            "program_id": str(p.get("id") or ""), "market_id": tk,
            "status": "active" if a <= now_ns else "upcoming",
            "start_ns": a, "end_ns": b, "period_reward_usd": usd, "reward_usd_day": usd / days,
            "target_size": _f(p.get("target_size_fp")) or _f(p.get("target_size")),
            "discount_factor_bps": int(_f(p.get("discount_factor_bps")) or 0),
            "description": p.get("incentive_description"), "paid_out": bool(p.get("paid_out")),
        })
    return out


def rank_incentive_markets(progs: Sequence[dict], min_reward_per_day: float = 0.0,
                           current: Iterable[str] = ()) -> List[tuple]:
    """Per market: total reward $/day over its programmes -> [(ticker, usd_day)] sorted by
    descending priority. Markets in ``current`` (already subscribed) rank with an
    ``INCENTIVE_STICKY`` bonus so the set does not churn around the cap. Ties: ticker."""
    cur = set(current)
    tot: Dict[str, float] = {}
    for p in progs:
        tot[p["market_id"]] = tot.get(p["market_id"], 0.0) + float(p["reward_usd_day"])
    rows = [(tk, v) for tk, v in tot.items() if v >= min_reward_per_day]
    rows.sort(key=lambda r: (-(r[1] * (INCENTIVE_STICKY if r[0] in cur else 1.0)), r[0]))
    return rows


def select_incentive(markets: Sequence[dict], ranked: Sequence[tuple], g: DiscoveryGroup,
                     now_ns: int) -> List[DynMarket]:
    """Top ``g.max_markets`` of ``ranked`` whose ``/markets`` row passes the group's filters
    (status open/unopened, not closed, ``max_hours_to_close``, ``event_regex``). Incentive
    groups never apply ``nearest_strikes`` (the reward ranking decides)."""
    from dataclasses import replace

    gg = replace(g, nearest_strikes=None)
    by_tk = {m.get("ticker"): m for m in markets if m.get("ticker")}
    ok = {dm.ref.market_id: dm for dm in select_kalshi(
        [by_tk[tk] for tk, _ in ranked if tk in by_tk], gg, now_ns)}
    out = []
    for tk, _ in ranked:
        if tk in ok:
            out.append(ok[tk])
            if g.max_markets is not None and len(out) >= g.max_markets:
                break
    return out


def lip_rows(progs: Sequence[dict], ranked: Sequence[tuple], selected: Iterable[str], group: str,
             recv_ns: int) -> List[dict]:
    """Rows for the ``lip`` table (writer.LIP_SCHEMA), one per programme."""
    sel = set(selected)
    rank = {tk: i + 1 for i, (tk, _) in enumerate(ranked)}
    return [{"venue": "kalshi", "group": group, **p, "selected": p["market_id"] in sel,
             "rank": rank.get(p["market_id"]), "ts_recv_ns": recv_ns} for p in progs]


# ---------------------------------------------------------------------------
# Pure helpers: Polymarket
# ---------------------------------------------------------------------------


def _tokens(m: dict) -> List[str]:
    t = m.get("clobTokenIds")
    if isinstance(t, str):
        try:
            t = json.loads(t)
        except ValueError:
            return []
    return [str(x) for x in t or []]


def select_polymarket(events: Sequence[dict], g: DiscoveryGroup, now_ns: int) -> List[DynMarket]:
    rx = re.compile(g.slug_regex) if g.slug_regex else None
    out = []
    for e in events:
        slug = e.get("slug") or ""
        if rx is not None and not rx.search(slug):
            continue
        for m in e.get("markets") or []:
            if m.get("closed") or m.get("enableOrderBook") is False:
                continue
            close = rfc3339_to_ns(m.get("endDate") or e.get("endDate"))
            if close is not None and close < now_ns:
                continue
            if g.max_hours_to_close is not None and close is not None and close > now_ns + g.max_hours_to_close * 3600 * _NS:
                continue
            toks = _tokens(m)
            outs = m.get("outcomes")
            if isinstance(outs, str):
                try:
                    outs = json.loads(outs)
                except ValueError:
                    outs = None
            n = 1 if g.tokens == "first" else len(toks)
            for i, tok in enumerate(toks[:n]):
                name = str(outs[i]).lower() if outs and i < len(outs) else ("yes" if i == 0 else "no")
                out.append(DynMarket(
                    MarketRef("polymarket", tok, name, None, m.get("slug") or slug, g.depth, g.heartbeat_s, g.name, g.bbo,
                              g.sample_interval_s, g.sample_windows),
                    close,
                    {"series": slug.rsplit("-", 1)[0] if re.search(r"-\d{9,}$", slug) else slug,
                     "event": m.get("slug") or slug, "title": m.get("question")},
                ))
    return out


# ---------------------------------------------------------------------------
# Async loops
# ---------------------------------------------------------------------------


class _Rest:
    """Rate-limited GET with 429 backoff (shared by all groups of one venue)."""

    def __init__(self, client, rate: float, stats):
        self.client = client
        self.bucket = TokenBucket(rate, burst=1)
        self.stats = stats

    async def get(self, url: str, params=None, stop: Optional[asyncio.Event] = None):
        backoff = Backoff(base=5.0, cap=120.0)
        for _ in range(6):
            await self.bucket.acquire()
            try:
                r = await self.client.get(url, params=params)
            except Exception as e:  # network
                self.stats["discovery_errors"] += 1
                log.warning("discovery GET %s: %s", url.rsplit("/", 1)[-1], e)
                r = None
            if r is not None and r.status_code != 429:
                self.stats["discovery_requests"] += 1
                r.raise_for_status()
                return r.json()
            if r is not None:
                self.stats["discovery_429"] += 1
            delay = backoff.next()
            if stop is not None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                    raise asyncio.CancelledError
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(delay)
        raise RuntimeError(f"giving up on {url} after repeated 429/errors")


async def _kalshi_series_markets(rest: _Rest, series: str, statuses: Sequence[str], stop) -> List[dict]:
    out: List[dict] = []
    for status in statuses:
        cursor = None
        for _ in range(10):
            p = {"series_ticker": series, "status": status, "limit": 1000}
            if cursor:
                p["cursor"] = cursor
            d = await rest.get(f"{KALSHI_API}/markets", p, stop)
            out.extend(d.get("markets") or [])
            cursor = d.get("cursor")
            if not cursor:
                break
    return out


async def _kalshi_category_series(rest: _Rest, category: str, stop) -> List[str]:
    d = await rest.get(f"{KALSHI_API}/series", {"category": category}, stop)
    cat = [s["ticker"] for s in d.get("series") or []]
    events: List[dict] = []
    cursor = None
    for _ in range(300):
        p = {"status": "open", "limit": 200}
        if cursor:
            p["cursor"] = cursor
        d = await rest.get(f"{KALSHI_API}/events", p, stop)
        events.extend({"series_ticker": e.get("series_ticker")} for e in d.get("events") or [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return active_series_in_category(events, cat)


async def _kalshi_incentive_programs(rest: _Rest, statuses: Sequence[str], stop) -> List[dict]:
    out: List[dict] = []
    for status in statuses:
        cursor = None
        for _ in range(20):
            p = {"status": status, "type": "liquidity", "limit": 10000}
            if cursor:
                p["cursor"] = cursor
            d = await rest.get(f"{KALSHI_API}/incentive_programs", p, stop)
            out.extend(d.get("incentive_programs") or [])
            cursor = d.get("next_cursor") or d.get("cursor")
            if not cursor:
                break
    return out


async def _kalshi_markets_by_ticker(rest: _Rest, tickers: Sequence[str], cache: Dict[str, dict], stop,
                                    now_ns: int, batch: int = 100) -> List[dict]:
    """``/markets?tickers=`` rows for ``tickers``; rows already cached (and not yet closed) are
    reused, so a steady-state refresh only asks for new tickers."""
    for tk in [t for t, m in cache.items() if (rfc3339_to_ns(m.get("close_time")) or now_ns + 1) < now_ns]:
        cache.pop(tk, None)
    need = [t for t in tickers if t not in cache]
    for i in range(0, len(need), batch):
        chunk = need[i:i + batch]
        d = await rest.get(f"{KALSHI_API}/markets", {"tickers": ",".join(chunk), "limit": 1000}, stop)
        for m in d.get("markets") or []:
            if m.get("ticker"):
                cache[m["ticker"]] = m
    return [cache[t] for t in tickers if t in cache]


async def _sleep(stop: asyncio.Event, s: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, s))
        return True
    except asyncio.TimeoutError:
        return False


class GroupLoops:
    """One task per group (sharing the venue's rate limiter), so a slow category scan never delays the
    fast-rolling crypto groups. ``ready`` is set once every group present at the start has finished (or
    failed) its first refresh.

    Groups can be started and stopped while running (config hot reload, src/recorder/reload.py): ``start``
    adds a loop, ``stop_group`` cancels one. ``run`` lasts until ``stop`` is set, so a venue with no groups
    yet can still be given one later."""

    def __init__(self, refresh_one, stop: asyncio.Event, stats, ready: Optional[asyncio.Event], venue: str):
        self.refresh_one, self.stop, self.stats, self.ready, self.venue = refresh_one, stop, stats, ready, venue
        self.tasks: Dict[str, asyncio.Task] = {}
        self.groups: Dict[str, DiscoveryGroup] = {}
        self._pending: set = set()
        self._started = False

    def start(self, g: DiscoveryGroup) -> None:
        if g.name in self.tasks:
            raise ValueError(f"discovery group {g.name} is already running")
        self.groups[g.name] = g
        if not self._started:
            self._pending.add(g.name)
        self.tasks[g.name] = asyncio.create_task(self._loop(g), name=f"discovery:{self.venue}:{g.name}")

    async def stop_group(self, name: str) -> bool:
        t = self.tasks.pop(name, None)
        self.groups.pop(name, None)
        if t is None:
            return False
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        return True

    def _first_done(self, name: str) -> None:
        self._pending.discard(name)
        if not self._pending and self.ready is not None:
            self.ready.set()

    async def _loop(self, g: DiscoveryGroup) -> None:
        first = True
        stop, stats = self.stop, self.stats
        while not stop.is_set():
            try:
                added, removed, n, extra = await self.refresh_one(g)
                stats[f"disc_{g.name}"] = n
                if added or removed or first:
                    log.info("discovery %s: %d %s markets (+%d -%d)%s", g.name, n, self.venue,
                             len(added), len(removed), extra)
                delay = g.refresh_s
            except asyncio.CancelledError:
                return
            except Exception as e:
                stats["discovery_errors"] += 1
                log.warning("discovery %s failed (keeping previous set): %s", g.name, e)
                delay = min(g.refresh_s, 60.0)
            if first:
                first = False
                self._first_done(g.name)
            if await _sleep(stop, delay):
                return

    async def run(self, groups) -> None:
        for g in groups:
            self.start(g)
        self._started = True
        if not self._pending and self.ready is not None:
            self.ready.set()
        await self.stop.wait()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


async def _group_loops(groups, refresh_one, stop, stats, ready, venue, handle=None) -> None:
    loops = GroupLoops(refresh_one, stop, stats, ready, venue)
    if handle is not None:
        handle["loops"] = loops
    await loops.run(groups)


async def run_kalshi_discovery(dc: DiscoveryConfig, groups: List[DiscoveryGroup], universe: Universe,
                               client, stop: asyncio.Event, stats, ready: Optional[asyncio.Event] = None,
                               sink=None, handle: Optional[dict] = None) -> None:
    """``handle``: optional dict; receives ``handle["loops"]`` (a :class:`GroupLoops`) for hot reload."""
    rest = _Rest(client, dc.kalshi_max_requests_per_s, stats)
    cat_series: Dict[str, List[str]] = {}
    cat_scanned: Dict[str, float] = {}
    grace = int(dc.close_grace_s * _NS)
    mcache: Dict[str, dict] = {}
    add_lip = getattr(sink, "add_lip", None)

    async def refresh_incentives(g: DiscoveryGroup):
        statuses = ("active", "upcoming") if g.incentive_lookahead_s > 0 else ("active",)
        raw = await _kalshi_incentive_programs(rest, statuses, stop)
        now = time.time_ns()
        progs = lip_programs(raw, now, g.incentive_lookahead_s)
        ranked = rank_incentive_markets(progs, g.min_reward_per_day, universe.group_ids("kalshi", g.name))
        # metadata only for the head of the ranking (+50% covers markets that fail the filters)
        head = [tk for tk, _ in ranked[: int((g.max_markets or len(ranked)) * 1.5) + 50]]
        rows = await _kalshi_markets_by_ticker(rest, head, mcache, stop, now)
        sel = select_incentive(rows, ranked, g, time.time_ns())
        added, removed = universe.set_group("kalshi", g.name, sel, time.time_ns(), grace)
        ids = {dm.ref.market_id for dm in sel}
        if add_lip is not None:
            for r in lip_rows(progs, ranked, ids, g.name, now):
                add_lip(r)
        usd = sum(v for tk, v in ranked if tk in ids)
        stats[f"lip_{g.name}_usd_day"] = round(usd)
        return added, removed, len(sel), (f"; {len(progs)} programmes on {len(ranked)} markets, "
                                          f"selected ${usd:,.0f}/day of ${sum(v for _, v in ranked):,.0f}")

    async def refresh(g: DiscoveryGroup):
        if g.incentives:
            return await refresh_incentives(g)
        series = list(g.series)
        extra = ""
        if g.category:
            now = time.monotonic()
            if g.category not in cat_series or now - cat_scanned.get(g.category, -1e18) >= dc.series_scan_s:
                cat_series[g.category] = await _kalshi_category_series(rest, g.category, stop)
                cat_scanned[g.category] = time.monotonic()
                extra = f"; {len(cat_series[g.category])} active {g.category} series"
            picked = filter_series(cat_series[g.category], g.series_regex)
            if g.series_regex and extra:
                extra += f", {len(picked)} match series_regex"
            series += [s for s in picked if s not in series]
        statuses = ("open", "unopened") if g.include_unopened else ("open",)
        raw: List[dict] = []
        for s in series:
            raw.extend(await _kalshi_series_markets(rest, s, statuses, stop))
        sel = select_kalshi(raw, g, time.time_ns(), universe.group_ids("kalshi", g.name))
        added, removed = universe.set_group("kalshi", g.name, sel, time.time_ns(), grace)
        return added, removed, len(sel), extra

    await _group_loops(groups, refresh, stop, stats, ready, "kalshi", handle)


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / _NS, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def run_polymarket_discovery(dc: DiscoveryConfig, groups: List[DiscoveryGroup], universe: Universe,
                                   client, stop: asyncio.Event, stats, ready: Optional[asyncio.Event] = None,
                                   handle: Optional[dict] = None) -> None:
    rest = _Rest(client, dc.polymarket_max_requests_per_s, stats)
    grace = int(dc.close_grace_s * _NS)

    async def refresh(g: DiscoveryGroup):
        now = time.time_ns()
        events: List[dict] = []
        for tag in g.tags:
            p = {"tag_slug": tag, "closed": "false", "limit": 200, "end_date_min": _iso(now)}
            if g.max_hours_to_close is not None:
                p["end_date_max"] = _iso(now + int(g.max_hours_to_close * 3600 * _NS))
            events.extend(await rest.get(f"{GAMMA}/events", p, stop) or [])
        sel = select_polymarket(events, g, time.time_ns())
        added, removed = universe.set_group("polymarket", g.name, sel, time.time_ns(), grace)
        return added, removed, len(sel), ""

    await _group_loops(groups, refresh, stop, stats, ready, "polymarket", handle)


def market_row(action: str, venue: str, dm: DynMarket, recv_ns: int) -> dict:
    i = dm.info or {}
    return {
        "venue": venue, "market_id": dm.ref.market_id, "group": dm.ref.group,
        "series": i.get("series"), "event": i.get("event"), "title": i.get("title"),
        "strike_type": i.get("strike_type"), "floor_strike": i.get("floor_strike"),
        "cap_strike": i.get("cap_strike"), "close_ns": dm.close_ns, "outcome": dm.ref.outcome,
        "ts_recv_ns": recv_ns, "action": action,
    }

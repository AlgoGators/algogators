"""Orchestrates venue feeds, periodic flush/compaction and graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, Optional

from .config import RecorderConfig
from .core import FeedContext
from .reload import ConfigWatcher, diff_groups
from .sampler import Sampler
from .series_discovery import market_row, run_kalshi_discovery, run_polymarket_discovery
from .settlement import run_settlement
from .universe import Universe
from .util import now_ns
from .venues import kalshi, polymarket, polymarket_rtds, polymarket_us

log = logging.getLogger("recorder")

FEEDS = {"kalshi": kalshi.run, "polymarket": polymarket.run, "polymarket_us": polymarket_us.run}


async def run_recorder(
    cfg: RecorderConfig,
    sink,
    duration_s: Optional[float] = None,
    stop: Optional[asyncio.Event] = None,
    status_every_s: float = 60.0,
    stop_file=None,
    config_path: Optional[Path] = None,
    config_loader: Optional[Callable[[], RecorderConfig]] = None,
    reload_every_s: float = 30.0,
) -> Dict[str, Counter]:
    """``stop_file``: optional path; if it appears the recorder shuts down cleanly and deletes
    it (portable graceful stop for detached Windows processes, where Ctrl+C can't be sent).

    ``config_path`` + ``config_loader``: hot reload. The file's mtime is checked every ``reload_every_s``
    and discovery-group adds/removes/changes are applied live (src/recorder/reload.py); other config
    changes are logged as needing a restart. An invalid file is rejected and the running groups kept."""
    import httpx

    stop = stop or asyncio.Event()
    d = cfg.defaults
    subs = cfg.subscriptions()
    universe = Universe([m for ms in subs.values() for m in ms], default_depth=d.depth,
                        default_heartbeat_s=d.heartbeat_s)
    groups = {v: cfg.discovery_groups(v) for v in ("kalshi", "polymarket")}
    contexts = []  # (name, FeedContext)

    def add_ctx(name, venue, markets, kind):
        contexts.append((name, FeedContext(
            venue=venue, markets=markets, venue_cfg=cfg.venues[venue], depth=d.depth,
            sampler=Sampler(d.ws_sample_interval_s, d.heartbeat_s), sink=sink, stop=stop,
            universe=universe, universe_kind=kind,
        )))

    for venue, markets in subs.items():
        if venue == "kalshi":
            if markets or groups["kalshi"]:
                add_ctx("kalshi", venue, markets, "all")  # pool keeps static on its own connection
        elif venue == "polymarket":
            if markets:
                add_ctx("polymarket", venue, markets, "static")
            for g in groups["polymarket"]:
                # one WS per group: the 5-minute crypto books burst hard enough that Polymarket
                # drops the connection ("1013 slow consumer") now and then; keep that isolated
                add_ctx(f"polymarket:{g.name}", venue, [], f"group:{g.name}")
        elif markets:
            add_ctx(venue, venue, markets, "static")
    hot = config_path is not None and config_loader is not None
    if not contexts and not cfg.rtds.enabled:
        raise SystemExit("no markets configured")
    disc_stats = Counter()
    rtds_stats = Counter()
    grace_ns = int(cfg.discovery.close_grace_s * 1e9)

    def drain_universe() -> None:
        t = now_ns()
        universe.expire(t, grace_ns)
        add = getattr(sink, "add_market", None)
        for action, venue, dm in universe.drain_log():
            if add is not None:
                add(market_row(action, venue, dm, t))

    _install_signal_handlers(stop)
    async def flusher():
        last_status = last_flush = time.monotonic()
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
                break
            except asyncio.TimeoutError:
                pass
            if stop_file is not None and stop_file.exists():
                log.info("stop file %s found -> shutting down", stop_file)
                try:
                    stop_file.unlink()
                except OSError:
                    pass
                stop.set()
                break
            drain_universe()
            now = time.monotonic()
            needs = getattr(sink, "needs_flush", lambda: False)()
            if needs or now - last_flush >= d.flush_interval_s:
                last_flush = now
                n = await asyncio.to_thread(sink.flush)
                await asyncio.to_thread(sink.compact_completed, now_ns())
                if n:
                    log.info("flushed %d rows", n)
            if now - last_status >= status_every_s:
                last_status = now
                for name, c in contexts:
                    log.info("%s: %s", name, dict(c.stats))
                if disc_stats:
                    log.info("discovery: %s", dict(disc_stats))
                if rtds_stats:
                    log.info("polymarket_rtds: %s", dict(rtds_stats))

    async def timer():
        if duration_s is None:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=duration_s)
        except asyncio.TimeoutError:
            stop.set()

    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(timeout=15, limits=limits,
                                 headers={"User-Agent": "predict-ngin-recorder/0.1 (read-only)"}) as client:
        feed_tasks = {name: asyncio.create_task(FEEDS[c.venue](c, client), name=name) for name, c in contexts}
        tasks = list(feed_tasks.values())
        tasks += [asyncio.create_task(flusher()), asyncio.create_task(timer())]
        handles = {"kalshi": {}, "polymarket": {}}
        # with hot reload on, discovery runs for every enabled venue, so a group can be added to a venue
        # that has none yet
        if groups["kalshi"] or (hot and cfg.venues["kalshi"].enabled):
            tasks.append(asyncio.create_task(run_kalshi_discovery(
                cfg.discovery, groups["kalshi"], universe, client, stop, disc_stats, sink=sink,
                handle=handles["kalshi"]), name="kalshi-discovery"))
        if groups["polymarket"] or (hot and cfg.venues["polymarket"].enabled):
            tasks.append(asyncio.create_task(run_polymarket_discovery(
                cfg.discovery, groups["polymarket"], universe, client, stop, disc_stats,
                handle=handles["polymarket"]), name="polymarket-discovery"))
        if cfg.rtds.enabled:
            tasks.append(asyncio.create_task(polymarket_rtds.run(cfg.rtds, sink, stop, rtds_stats), name="rtds"))
        tracked = [g.name for g in groups["kalshi"] if g.track_settlement]  # mutated in place on reload
        # dry runs (no sink root) keep state in memory only, so they never touch the real file
        root = getattr(sink, "root", None)
        state_path = Path(root) / cfg.settlement.state_file if root is not None else None

        def start_settlement() -> None:
            tasks.append(asyncio.create_task(run_settlement(
                cfg.settlement, tracked, universe, client, sink, stop, disc_stats, state_path),
                name="kalshi-settlement"))

        settlement_on = cfg.settlement.enabled and bool(tracked)
        if settlement_on:
            start_settlement()

        def start_feed(name: str, venue: str, kind: str) -> None:
            add_ctx(name, venue, [], kind)
            t = asyncio.create_task(FEEDS[venue](contexts[-1][1], client), name=name)
            feed_tasks[name] = t
            tasks.append(t)

        async def stop_feed(name: str) -> None:
            t = feed_tasks.pop(name, None)
            contexts[:] = [(n, c) for n, c in contexts if n != name]
            if t is not None:
                t.cancel()
                await asyncio.gather(t, return_exceptions=True)

        async def apply_reload(new_cfg: RecorderConfig) -> None:
            nonlocal cfg, settlement_on
            d = diff_groups(cfg, new_cfg)
            if d.needs_restart:
                log.warning("config reload: %s changed; those parts need a recorder restart and were NOT "
                            "applied", ", ".join(d.needs_restart))
            if d.empty:
                log.info("config reload: no discovery-group changes")
                return
            touched = d.added + d.removed + [n for _, n in d.changed]
            missing = sorted({g.venue for g in touched if "loops" not in handles[g.venue]})
            if missing:
                log.warning("config reload: discovery for %s is not running (venue disabled at start?); "
                            "those group changes need a restart", ", ".join(missing))
            t = now_ns()
            for g in d.removed:
                if g.venue in missing:
                    continue
                await handles[g.venue]["loops"].stop_group(g.name)
                universe.set_group(g.venue, g.name, [], t, grace_ns)
                disc_stats.pop(f"disc_{g.name}", None)
                if g.venue == "polymarket":
                    await stop_feed(f"polymarket:{g.name}")
            for old, new in d.changed:
                if new.venue in missing:
                    continue
                await handles[new.venue]["loops"].stop_group(old.name)
                handles[new.venue]["loops"].start(new)
            for g in d.added:
                if g.venue in missing:
                    continue
                if g.venue == "kalshi" and "kalshi" not in feed_tasks:
                    start_feed("kalshi", "kalshi", "all")
                if g.venue == "polymarket" and f"polymarket:{g.name}" not in feed_tasks:
                    start_feed(f"polymarket:{g.name}", "polymarket", f"group:{g.name}")
                handles[g.venue]["loops"].start(g)
            applied = [g for g in new_cfg.discovery.groups if g.venue not in missing] + \
                [g for g in cfg.discovery.groups if g.venue in missing]
            cfg = replace_groups(cfg, tuple(applied))
            tracked[:] = [g.name for g in cfg.discovery_groups("kalshi") if g.track_settlement]
            if not settlement_on and cfg.settlement.enabled and tracked:
                settlement_on = True
                start_settlement()
            log.info("config reload applied: %s", d.summary())

        async def reloader():
            watcher = ConfigWatcher(config_path, config_loader)
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=reload_every_s)
                    return
                except asyncio.TimeoutError:
                    pass
                new_cfg = await asyncio.to_thread(watcher.poll)
                if new_cfg is None:
                    continue
                try:
                    await apply_reload(new_cfg)
                except Exception as e:  # a reload must never take the recorder down
                    log.error("config reload failed while applying: %r", e)

        if hot:
            tasks.append(asyncio.create_task(reloader(), name="config-reload"))
        try:
            await stop.wait()
        finally:
            done, pending = await asyncio.wait(tasks, timeout=15)
            for t in pending:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for t in tasks:
                if t.done() and not t.cancelled() and t.exception():
                    log.error("task %s failed: %r", t.get_name(), t.exception())
            drain_universe()
            n = await asyncio.to_thread(sink.close, now_ns())
            log.info("final flush %d rows", n)
    out = {name: c.stats for name, c in contexts}
    if disc_stats:
        out["discovery"] = disc_stats
    if rtds_stats:
        out["polymarket_rtds"] = rtds_stats
    return out


def replace_groups(running: RecorderConfig, groups: tuple) -> RecorderConfig:
    """The running config with only its discovery groups replaced (nothing else is hot)."""
    from dataclasses import replace

    return replace(running, discovery=replace(running.discovery, groups=groups))


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _handler(*_):
        loop.call_soon_threadsafe(stop.set)

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None), getattr(signal, "SIGBREAK", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            try:  # Windows: no loop signal handlers
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass

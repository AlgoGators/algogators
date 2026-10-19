"""Recorder hot reload: discovery groups follow config/recorder.yaml without a restart (src/recorder/reload.py)."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.recorder import runner, series_discovery  # noqa: E402
from src.recorder.config import MarketRef, load_config, parse_config  # noqa: E402
from src.recorder.reload import ConfigWatcher, diff_groups  # noqa: E402
from src.recorder.universe import DynMarket  # noqa: E402
from src.recorder.writer import CountingSink  # noqa: E402

BASE = """
defaults: {{depth: 5}}
venues:
  kalshi: {{enabled: true}}
  polymarket: {{enabled: true}}
discovery:
  kalshi:
    groups:
{kalshi}
  polymarket:
    groups:
{poly}
"""


def cfg_text(kalshi=("k-a",), poly=("p-a",), refresh=60, depth=5):
    k = "\n".join(f"      - {{name: {n}, series: [KX{n.upper().replace('-', '')}], refresh_s: {refresh}}}"
                  for n in kalshi) or "      []"
    p = "\n".join(f"      - {{name: {n}, tags: [crypto], refresh_s: {refresh}}}" for n in poly) or "      []"
    if not kalshi:
        k = ""
    if not poly:
        p = ""
    text = BASE.format(kalshi=k, poly=p).replace("depth: 5", f"depth: {depth}")
    return text.replace("    groups:\n\n", "    groups: []\n")


def write(path: Path, text: str, bump: float) -> None:
    path.write_text(text, encoding="utf-8")
    t = time.time() + bump  # a distinct mtime even on coarse filesystems
    os.utime(path, (t, t))


# ---- pure parts ------------------------------------------------------------------------------------------------

def test_diff_groups_adds_removes_changes_and_flags_restart_only_sections():
    a = parse_config(cfg_text(("k-a", "k-b"), ("p-a",)))
    b = parse_config(cfg_text(("k-b", "k-c"), ("p-a",), depth=7))
    d = diff_groups(a, b)
    assert [g.name for g in d.added] == ["k-c"]
    assert [g.name for g in d.removed] == ["k-a"]
    assert d.changed == []
    assert d.needs_restart == ["defaults"]
    c = parse_config(cfg_text(("k-b", "k-c"), ("p-a",), refresh=30, depth=7))
    d2 = diff_groups(b, c)
    assert sorted(n.name for _, n in d2.changed) == ["k-b", "k-c", "p-a"] and d2.needs_restart == []
    assert diff_groups(a, a).empty


def test_disabled_groups_count_as_removed():
    a = parse_config(cfg_text(("k-a",), ()))
    b = parse_config(cfg_text(("k-a",), ()).replace("refresh_s: 60}", "refresh_s: 60, enabled: false}"))
    assert [g.name for g in diff_groups(a, b).removed] == ["k-a"]


def test_watcher_rejects_invalid_files_and_retries_on_the_next_change(tmp_path, caplog):
    p = tmp_path / "recorder.yaml"
    write(p, cfg_text(), 0)
    w = ConfigWatcher(p, lambda: load_config(p))
    assert w.poll() is None  # unchanged
    write(p, "discovery: [not, a, mapping]\n", 5)
    with caplog.at_level(logging.ERROR, logger="recorder.reload"):
        assert w.poll() is None
    assert "REJECTED" in caplog.text
    assert w.poll() is None  # not retried until it changes
    write(p, cfg_text(("k-z",)), 10)
    assert [g.name for g in w.poll().discovery.groups if g.venue == "kalshi"] == ["k-z"]


def test_group_loops_start_and_stop_while_running():
    calls = Counter()

    async def refresh(g):
        calls[g.name] += 1
        return [], [], 0, ""

    async def go():
        stop, ready = asyncio.Event(), asyncio.Event()
        h = {}
        from src.recorder.config import DiscoveryGroup
        g1 = DiscoveryGroup(name="a", venue="kalshi", series=("X",), refresh_s=0.01)
        task = asyncio.create_task(series_discovery._group_loops([g1], refresh, stop, Counter(), ready, "kalshi", h))
        await asyncio.wait_for(ready.wait(), 2)
        loops = h["loops"]
        loops.start(DiscoveryGroup(name="b", venue="kalshi", series=("Y",), refresh_s=0.01))
        await asyncio.sleep(0.05)
        assert await loops.stop_group("a")
        n_a = calls["a"]
        await asyncio.sleep(0.05)
        assert calls["a"] == n_a and calls["b"] >= 2  # a stopped, b keeps going
        assert not await loops.stop_group("missing")
        stop.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(go())


def test_no_groups_still_signals_ready_and_waits_for_stop():
    async def go():
        stop, ready = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(series_discovery._group_loops([], None, stop, Counter(), ready, "kalshi"))
        await asyncio.wait_for(ready.wait(), 1)
        assert not task.done()
        stop.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(go())


# ---- end to end through run_recorder (fake feeds and discovery, no network) -------------------------------------

def test_run_recorder_applies_group_changes_live(tmp_path, monkeypatch):
    path = tmp_path / "recorder.yaml"
    write(path, cfg_text(("k-a",), ("p-a",)), 0)
    seen, feeds = {}, []

    async def fake_feed(ctx, client):
        feeds.append(("start", ctx.universe_kind))
        try:
            await ctx.stop.wait()
        except asyncio.CancelledError:
            feeds.append(("cancel", ctx.universe_kind))
            raise

    def fake_discovery(venue):
        async def run(dc, groups, universe, client, stop, stats, ready=None, sink=None, handle=None):
            seen["u"] = universe

            async def refresh(g):
                a, r = universe.set_group(venue, g.name, [DynMarket(MarketRef(venue, f"{g.name}-M1", group=g.name))])
                seen.setdefault("refreshed", []).append((g.name, g.refresh_s))
                return a, r, 1, ""

            await series_discovery._group_loops(groups, refresh, stop, stats, ready, venue, handle)
        return run

    monkeypatch.setitem(runner.FEEDS, "kalshi", fake_feed)
    monkeypatch.setitem(runner.FEEDS, "polymarket", fake_feed)
    monkeypatch.setattr(runner, "run_kalshi_discovery", fake_discovery("kalshi"))
    monkeypatch.setattr(runner, "run_polymarket_discovery", fake_discovery("polymarket"))
    monkeypatch.setattr(runner, "_install_signal_handlers", lambda stop: None)

    async def wait_for(cond, timeout=3.0):
        t0 = time.monotonic()
        while not cond():
            if time.monotonic() - t0 > timeout:
                raise AssertionError("timed out")
            await asyncio.sleep(0.02)

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(runner.run_recorder(
            parse_config(path.read_text()), CountingSink(), stop=stop, status_every_s=3600,
            config_path=path, config_loader=lambda: load_config(path), reload_every_s=0.05))
        await wait_for(lambda: "u" in seen and seen["u"].group_ids("polymarket", "p-a"))
        u = seen["u"]
        assert u.group_ids("kalshi", "k-a") == {"k-a-M1"}

        # remove k-a, add k-b, add p-b, change p-a
        write(path, cfg_text(("k-b",), ("p-a", "p-b")).replace(
            "{name: p-a, tags: [crypto], refresh_s: 60}", "{name: p-a, tags: [crypto], refresh_s: 30}"), 5)
        await wait_for(lambda: u.group_ids("kalshi", "k-b") and u.group_ids("polymarket", "p-b"))
        await wait_for(lambda: ("p-a", 30) in seen["refreshed"])
        assert u.group_ids("kalshi", "k-a") == set()
        assert ("start", "group:p-b") in feeds

        # an invalid file changes nothing
        write(path, "discovery: {kalshi: {groups: [{name: k-x}]}}\n", 10)
        await asyncio.sleep(0.3)
        assert u.group_ids("kalshi", "k-b") == {"k-b-M1"} and u.group_ids("polymarket", "p-b")

        # removing a polymarket group stops its feed and rolls its markets out
        write(path, cfg_text(("k-b",), ("p-a",)), 15)
        await wait_for(lambda: not u.group_ids("polymarket", "p-b"))
        await wait_for(lambda: ("cancel", "group:p-b") in feeds)
        stop.set()
        await asyncio.wait_for(task, 20)

    asyncio.run(go())

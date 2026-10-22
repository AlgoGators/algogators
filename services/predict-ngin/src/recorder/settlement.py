"""Kalshi close/settlement logging (read-only, public REST).

For every market of a discovery group with ``track_settlement: true`` the recorder polls
``GET /markets?tickers=A,B,...`` (``settlement.batch`` tickers per request, at most
``settlement.max_requests_per_s``) and writes one ``status`` row whenever a market's
status / result / close time / settlement time changes, plus one baseline row the first
time it sees a market. Poll interval: ``settlement.fast_interval_s`` inside
``settlement.fast_windows`` (election week), ``settlement.interval_s`` otherwise; a newly
discovered market is polled within ~30 s.

Markets keep being tracked after they leave the book universe (closed markets are no
longer "open", so discovery drops them) until Kalshi reports ``finalized``. The tracked
set and last-known state persist in ``settlement.state_file`` so a restart neither loses
closed markets nor re-logs unchanged ones.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .config import SettlementConfig
from .util import rfc3339_to_ns

log = logging.getLogger("recorder.settlement")

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
TERMINAL = ("finalized",)
# a change in any of these fields produces a row
WATCH = ("status", "result", "close_time", "settlement_ts", "expiration_value")


def snapshot(m: dict) -> dict:
    """The fields we track from one ``/markets`` row."""
    return {
        "status": m.get("status") or None,
        "result": m.get("result") or None,
        "close_time": m.get("close_time") or None,
        "settlement_ts": m.get("settlement_ts") or None,
        "expiration_value": m.get("expiration_value") or None,
        "settlement_value": m.get("settlement_value_dollars") or None,
        "event": m.get("event_ticker") or None,
    }


def _fnum(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def status_changes(state: Dict[str, dict], markets: Iterable[dict], recv_ns: int,
                   groups: Dict[str, str]) -> List[dict]:
    """Compare fresh ``/markets`` rows with ``state`` (ticker -> last snapshot, updated in place).

    Returns one row per market that is new or whose WATCH fields changed. ``groups`` maps
    ticker -> discovery group (for the row; the state remembers it once known).
    """
    rows = []
    for m in markets:
        tk = m.get("ticker")
        if not tk:
            continue
        new = snapshot(m)
        old = state.get(tk)
        grp = groups.get(tk) or (old or {}).get("group")
        new["group"] = grp
        if old is not None and all(old.get(k) == new.get(k) for k in WATCH):
            if grp and not old.get("group"):
                old["group"] = grp
            continue
        rows.append({
            "venue": "kalshi",
            "market_id": tk,
            "group": grp,
            "event": new["event"],
            "status": new["status"],
            "result": new["result"],
            "prev_status": (old or {}).get("status"),
            "prev_result": (old or {}).get("result"),
            "close_ns": rfc3339_to_ns(new["close_time"]),
            "settlement_ns": rfc3339_to_ns(new["settlement_ts"]),
            "expiration_value": new["expiration_value"],
            "settlement_value": _fnum(new["settlement_value"]),
            "first_seen": old is None,
            "ts_recv_ns": recv_ns,
        })
        state[tk] = new
    return rows


def to_poll(state: Dict[str, dict], tracked: Iterable[str]) -> List[str]:
    """Tickers to poll: everything tracked or remembered that is not yet finalized."""
    out = dict.fromkeys(tracked)
    out.update(dict.fromkeys(state))
    return sorted(t for t in out if (state.get(t) or {}).get("status") not in TERMINAL)


def load_state(path: Path) -> Dict[str, dict]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("settlement state %s unreadable (%s); starting empty", path, e)
        return {}


def save_state(path: Path, state: Dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def tracked_markets(universe, group_names: Iterable[str]) -> Dict[str, str]:
    """ticker -> group for the markets the tracked groups hold right now."""
    out: Dict[str, str] = {}
    for g in group_names:
        for tk in universe.group_ids("kalshi", g):
            out.setdefault(tk, g)
    return out


async def run_settlement(cfg: SettlementConfig, group_names: List[str], universe, client, sink,
                         stop: asyncio.Event, stats, state_path: Optional[Path], tick_s: float = 30.0) -> None:
    from .series_discovery import _Rest
    from .util import now_ns

    rest = _Rest(client, cfg.max_requests_per_s, stats)
    state = load_state(state_path) if state_path is not None else {}
    known: Dict[str, str] = {tk: s.get("group") for tk, s in state.items() if s.get("group")}
    last_sweep = None  # ns
    add = getattr(sink, "add_status", None)

    while not stop.is_set():
        t = now_ns()
        cur = tracked_markets(universe, group_names)
        known.update(cur)
        new = [tk for tk in cur if tk not in state]
        due = last_sweep is None or (t - last_sweep) >= cfg.interval_at(t) * 1e9
        if (due and (known or state)) or new:
            tickers = to_poll(state, known) if due else sorted(new)
            n_rows = 0
            for i in range(0, len(tickers), cfg.batch):
                chunk = tickers[i:i + cfg.batch]
                try:
                    d = await rest.get(f"{KALSHI_API}/markets",
                                       {"tickers": ",".join(chunk), "limit": 1000}, stop)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    stats["settlement_errors"] += 1
                    log.warning("settlement poll failed (%d tickers): %s", len(chunk), e)
                    continue
                rows = status_changes(state, d.get("markets") or [], now_ns(), known)
                for r in rows:
                    if add is not None:
                        add(r)
                    if not r["first_seen"]:
                        log.info("settlement %s: %s/%s -> %s/%s", r["market_id"], r["prev_status"],
                                 r["prev_result"], r["status"], r["result"])
                n_rows += len(rows)
            stats["settlement_polls"] += 1
            stats["settlement_rows"] += n_rows
            stats["settlement_tracked"] = len(to_poll(state, known))
            if due:
                last_sweep = t
            try:
                if state_path is not None:
                    await asyncio.to_thread(save_state, state_path, state)
            except OSError as e:
                log.warning("settlement state save failed: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=tick_s)
            return
        except asyncio.TimeoutError:
            pass

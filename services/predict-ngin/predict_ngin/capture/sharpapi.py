"""SharpAPI recorder: sportsbook and exchange odds to Parquet, as fast as the key's tier allows.

    uv run --no-sync python -m predict_ngin.capture.sharpapi probe          # key, tier, books
    uv run --no-sync python -m predict_ngin.capture.sharpapi run --hours 70 # record
    uv run --no-sync python -m predict_ngin.capture.sharpapi status

SharpAPI has no history below Enterprise, so the only way to get data is to record it live. One
process runs a small scheduler; each step makes one request, the most urgent job first:

==========  =====================================================  ==============================
job         request                                                every (paid / free tier)
==========  =====================================================  ==============================
delta       ``/odds/delta?since=`` (changes; all pages)            5 s / 20 s
snapshot    ``/odds`` full book, one page per step (interleaved)   30 min
events      ``/events`` upcoming + live, and ``status=final``      5 min, final 30 min
closing     ``/odds/closing?event_id=`` per game, 10 min after start  1 min check
splits      ``/splits`` betting splits (Pro and up)                30 min
reference   ``/sportsbooks``, ``/leagues``, ``/markets``           6 h
usage       ``/account``, ``/account/usage``                       15 min
==========  =====================================================  ==============================

Requests are paced to 85% of the tier's per-minute limit (read from the response headers).
Deltas are fetched from 10 s before the newest ``timestamp`` seen, and rows already stored are
dropped (key: id, timestamp, price, active flag), so nothing falls between polls.

Output under ``<data>/sharpapi/`` (one folder per UTC day; a part file per kind per flush):

* ``YYYY-MM-DD/odds/part-HHMMSS-N.parquet`` - every odds row as received, typed columns for the
  documented fields, ``extra`` (JSON) for any other field, plus ``receipt_ts`` (UTC),
  ``source`` (snapshot | delta) and ``snapshot_id``;
* ``YYYY-MM-DD/{events,closing,splits}/part-*.parquet`` - rows as received, nested values as JSON;
* ``reference/<name>-<ts>.json``, ``usage.jsonl``, ``status.json`` (counters, rate limit, errors).

Stops at ``--hours`` / ``--until``, on Ctrl+C, or when ``<data>/sharpapi/STOP`` exists (buffers
are flushed first). One instance at a time (a lock file). The key comes from ``SHARPAPI_KEY``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import socket
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..shared.lock import SingleInstance
from ..shared.paths import data_root
from ..venues.sharpapi import SharpAPIClient, SharpAPIError
from .runner import atomic_write_json, status_fields

ODDS_COLS = {
    "id": "str",
    "sportsbook": "str",
    "sportsbook_name": "str",
    "event_id": "str",
    "event_uuid": "str",
    "external_event_id": "str",
    "sport": "str",
    "league": "str",
    "home_team": "str",
    "away_team": "str",
    "market_type": "str",
    "market_id": "str",
    "selection": "str",
    "selection_id": "str",
    "selection_type": "str",
    "team_side": "str",
    "player_name": "str",
    "stat_category": "str",
    "line": "float",
    "odds_american": "float",
    "odds_decimal": "float",
    "odds_probability": "float",
    "probability": "float",
    "is_player_prop": "bool",
    "is_stale_pregame_price": "bool",
    "is_main_line": "bool",
    "is_alternate_line": "bool",
    "is_live": "bool",
    "is_active": "bool",
    "event_start_time": "str",
    "timestamp": "str",
    "volume": "float",
    "volume_24h": "float",
    "open_interest": "float",
    "max_bet": "float",
    "best_bid_liquidity": "float",
    "total_liquidity": "float",
    "size_currency": "str",
    "home_pitcher": "str",
    "away_pitcher": "str",
    "polymarket_resolution": "str",
    "deep_link": "str",
}
# the host name goes into part file names: files from atlas and the PC sync into one folder
HOST = re.sub(r"[^A-Za-z0-9_.-]", "_", socket.gethostname().split(".")[0].lower()) or "host"
OVERLAP_S = 10
FLUSH_S = 120
DEDUP_TTL_S = 900


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(ts: Optional[str]) -> Optional[dt.datetime]:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def root() -> Path:
    return data_root() / "sharpapi"


# --------------------------------------------------------------------------------------- storage
class Sink:
    """Buffers rows per kind and writes one Parquet part file per kind per flush."""

    def __init__(self, base: Path):
        self.base = base
        self.buf: Dict[str, List[dict]] = {}
        self.rows: Dict[str, int] = {}
        self.files = 0
        self._seq = 0

    def add(self, kind: str, rows: List[dict]) -> None:
        if rows:
            self.buf.setdefault(kind, []).extend(rows)
            self.rows[kind] = self.rows.get(kind, 0) + len(rows)

    def pending(self) -> int:
        return sum(len(v) for v in self.buf.values())

    def flush(self) -> None:
        import pandas as pd

        now = _now()
        for kind, rows in list(self.buf.items()):
            if not rows:
                continue
            if kind == "odds":
                df = _odds_frame(rows)
            else:
                df = pd.DataFrame(
                    [
                        {
                            k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v)
                            for k, v in r.items()
                        }
                        for r in rows
                    ]
                )
            d = self.base / now.strftime("%Y-%m-%d") / kind
            d.mkdir(parents=True, exist_ok=True)
            self._seq += 1
            name = f"part-{HOST}-{now:%H%M%S}-{self._seq}.parquet"
            tmp = d / f".{name}.tmp"
            df.to_parquet(tmp, index=False, compression="zstd")
            tmp.replace(d / name)
            self.files += 1
            self.buf[kind] = []


def _odds_frame(rows: List[dict]):
    import pandas as pd

    meta = ("receipt_ts", "source", "snapshot_id")
    out: Dict[str, list] = {c: [] for c in (*meta, *ODDS_COLS, "extra")}
    for r in rows:
        for m in meta:
            out[m].append(r.get(m))
        for c in ODDS_COLS:
            out[c].append(r.get(c))
        ex = {k: v for k, v in r.items() if k not in ODDS_COLS and k not in meta}
        out["extra"].append(json.dumps(ex, default=str) if ex else None)
    df = pd.DataFrame(out)
    for c, t in ODDS_COLS.items():
        if t == "float":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        elif t == "bool":
            df[c] = df[c].astype("boolean")
        else:
            df[c] = df[c].astype("string")
    return df


# ------------------------------------------------------------------------------------- recorder
class Recorder:
    def __init__(
        self,
        client: SharpAPIClient,
        base: Path,
        *,
        delta_s: Optional[float] = None,
        snapshot_s: Optional[float] = None,
        filters: Optional[dict] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.c, self.base, self.clock = client, base, clock
        self.filters = {k: v for k, v in (filters or {}).items() if v}
        self.sink = Sink(base)
        self._delta_s, self._snapshot_s = delta_s, snapshot_s
        self.last: Dict[str, float] = {}
        self.disabled: Dict[str, str] = {}
        self.counts: Dict[str, int] = {}
        self.errors: deque = deque(maxlen=20)
        self.newest_ts: Optional[dt.datetime] = None
        self.seen: Dict[tuple, float] = {}
        self.snap: Optional[dict] = None  # an in-progress snapshot: {"id", "cursor", "pages"}
        self.snapshots_done = 0
        self.started = _now()
        self.event_start: Dict[str, dt.datetime] = {}  # event id -> scheduled start
        self.closing_done: set = set()

    # schedule ---------------------------------------------------------------------------------
    @property
    def paid(self) -> bool:
        return (self.c.per_minute or 0) >= 100

    def every(self, job: str) -> float:
        if job == "delta":
            return self._delta_s or (5.0 if self.paid else 20.0)
        if job == "snapshot":
            return self._snapshot_s or 1800.0
        return {
            "events": 300.0,
            "events_final": 1800.0,
            "closing": 60.0,
            "splits": 1800.0,
            "reference": 21600.0,
            "usage": 900.0,
        }[job]

    def due(self, job: str) -> bool:
        if job in self.disabled:
            return False
        t = self.last.get(job)
        return t is None or self.clock() - t >= self.every(job)

    def step(self) -> bool:
        """Run the most urgent due job (one request or one delta sweep). False if none was due."""
        order = ["delta", "usage", "reference", "events", "events_final", "closing", "splits"]
        if self.snap is not None:  # continue a snapshot between other jobs
            order.insert(1, "_snapshot_page")
        elif self.due("snapshot"):
            order.insert(1, "snapshot")
        for job in order:
            if job == "_snapshot_page" or self.due(job):
                try:
                    getattr(self, "job_" + job.lstrip("_"))()
                except SharpAPIError as e:
                    self._error(job, e)
                    if e.status == 403 or e.code in ("tier_restricted", "book_not_selected"):
                        self.disabled[job] = e.code
                    if e.status == 401:
                        raise
                if job != "_snapshot_page":
                    self.last[job] = self.clock()
                return True
        return False

    def _error(self, job: str, e: Exception) -> None:
        self.errors.append({"ts": _iso(_now()), "job": job, "error": str(e)[:300]})
        self.counts["errors"] = self.counts.get("errors", 0) + 1

    def _count(self, k: str, n: int = 1) -> None:
        self.counts[k] = self.counts.get(k, 0) + n

    # odds -------------------------------------------------------------------------------------
    def _keep(self, rows: List[dict], source: str, snap_id: Optional[str] = None) -> List[dict]:
        now, mono, out = _iso(_now()), self.clock(), []
        for r in rows:
            k = (r.get("id"), r.get("timestamp"), r.get("odds_american"), r.get("is_active"))
            if source == "delta" and k in self.seen:
                continue
            self.seen[k] = mono
            t = _parse(r.get("timestamp"))
            if t and (self.newest_ts is None or t > self.newest_ts):
                self.newest_ts = t
            out.append({**r, "receipt_ts": now, "source": source, "snapshot_id": snap_id})
        return out

    def _prune_seen(self) -> None:
        cut = self.clock() - DEDUP_TTL_S
        if len(self.seen) > 200_000 or (self.seen and next(iter(self.seen.values())) < cut):
            self.seen = {k: v for k, v in self.seen.items() if v >= cut}

    def job_delta(self) -> None:
        if self.newest_ts is None:  # nothing seen yet: the first snapshot sets the start
            if self.snapshots_done == 0 and self.snap is None:
                return
        since = (self.newest_ts or self.started) - dt.timedelta(seconds=OVERLAP_S)
        q = {**self.filters, "since": _iso(since), "limit": 500}
        n = 0
        for _ in range(40):
            rows, cur, more = self.c.page("/odds/delta", q)
            kept = self._keep(rows, "delta")
            self.sink.add("odds", kept)
            n += len(kept)
            if not (more and cur):
                break
            q = {**q, "cursor": cur}
        self._count("delta_polls")
        self._count("delta_rows", n)
        self._prune_seen()

    def job_snapshot(self) -> None:
        self.snap = {"id": _iso(_now()), "cursor": None, "pages": 0}
        self.job_snapshot_page()

    def job_snapshot_page(self) -> None:
        s = self.snap
        assert s is not None, "job_snapshot starts a snapshot"
        q = {**self.filters, "limit": 500}
        if s["cursor"]:
            q["cursor"] = s["cursor"]
        rows, cur, more = self.c.page("/odds", q)
        self.sink.add("odds", self._keep(rows, "snapshot", s["id"]))
        s["pages"] += 1
        self._count("snapshot_rows", len(rows))
        if more and cur and s["pages"] < 5000:
            s["cursor"] = cur
        else:
            self.snap = None
            self.snapshots_done += 1
            self._count("snapshots")
            self.last["snapshot"] = self.clock()

    # other data -------------------------------------------------------------------------------
    def _rows(self, kind: str, path: str, params: Optional[dict] = None, pages: bool = True):
        now = _iso(_now())
        rows: Any
        if pages:
            rows = list(self.c.pages(path, {**(params or {})}, max_pages=200))
        else:
            body, _ = self.c.get(path, params)
            rows = body.get("data") if isinstance(body, dict) else body
            rows = rows if isinstance(rows, list) else [rows] if rows else []
        self.sink.add(
            kind,
            [
                {**r, "receipt_ts": now} if isinstance(r, dict) else {"value": r, "receipt_ts": now}
                for r in rows
            ],
        )
        self._count(kind + "_rows", len(rows))
        return rows

    def job_events(self) -> None:
        f = {k: v for k, v in self.filters.items() if k in ("sport", "league", "sportsbook")}
        for e in self._rows("events", "/events", {**f, "limit": 500}):
            t = _parse(e.get("start_time")) if isinstance(e, dict) else None
            if t and e.get("id"):
                self.event_start[e["id"]] = t

    def job_events_final(self) -> None:
        f = {k: v for k, v in self.filters.items() if k in ("sport", "league", "sportsbook")}
        self._rows("events", "/events", {**f, "status": "final", "limit": 500})

    def job_closing(self) -> None:
        """Closing lines of games that started 10+ minutes ago (one request per game, once).

        ``/odds/closing`` needs an ``event_id`` and returns ``{event fields, captured_at,
        books: {book: {odds: [...]}}}``; stored flat, one row per price.
        """
        now = _now()
        todo = [
            e
            for e, t in self.event_start.items()
            if e not in self.closing_done
            and now - dt.timedelta(hours=12) <= t <= now - dt.timedelta(minutes=10)
        ]
        for eid in todo[:60]:
            self.closing_done.add(eid)
            body, _ = self.c.get("/odds/closing", {"event_id": eid})
            d = (body or {}).get("data") or {}
            head = {k: v for k, v in d.items() if k != "books"}
            rows = [
                {**head, **o, "book_key": book, "receipt_ts": _iso(now)}
                for book, v in (d.get("books") or {}).items()
                for o in (v or {}).get("odds") or []
            ]
            self.sink.add("closing", rows)
            self._count("closing_events")
            self._count("closing_rows", len(rows))

    def job_splits(self) -> None:
        self._rows("splits", "/splits", {"limit": 200})

    def job_reference(self) -> None:
        d = self.base / "reference"
        d.mkdir(parents=True, exist_ok=True)
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
        for name, path, q in (
            ("sportsbooks", "/sportsbooks", {"include_inactive": "true"}),
            ("leagues", "/leagues", None),
            ("markets", "/markets", None),
        ):
            body, _ = self.c.get(path, q)
            atomic_write_json(d / f"{name}-{stamp}.json", body, indent=1, default=str)

    def job_usage(self) -> None:
        acct, _ = self.c.get("/account")
        use, _ = self.c.get("/account/usage")
        with open(self.base / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"ts": _iso(_now()), "account": _redact(acct), "usage": _redact(use)},
                    default=str,
                )
                + "\n"
            )
        self.account = _redact(acct)

    # status -----------------------------------------------------------------------------------
    def status(self, ok: bool = True, **extra) -> dict:
        return status_fields(
            "sharpapi-recorder",
            "data",
            ok,
            _iso(_now()),
            started=_iso(self.started),
            per_minute=self.c.per_minute,
            rate_remaining=self.c.remaining,
            data_delay_s=self.c.data_delay_s,
            requests=self.c.n_requests,
            http_429=self.c.n_429,
            http_errors=self.c.n_errors,
            rows_written=dict(self.sink.rows),
            files=self.sink.files,
            counts=dict(self.counts),
            newest_odds_ts=_iso(self.newest_ts) if self.newest_ts else None,
            snapshot_in_progress=self.snap is not None,
            disabled=dict(self.disabled),
            filters=self.filters,
            errors=list(self.errors),
            **extra,
        )


def _redact(x: Any) -> Any:
    if isinstance(x, dict):
        return {
            k: (
                "***"
                if "key" in k.lower() or "token" in k.lower() or "email" in k.lower()
                else _redact(v)
            )
            for k, v in x.items()
        }
    if isinstance(x, list):
        return [_redact(v) for v in x]
    return x


def run(
    hours: Optional[float],
    until: Optional[str],
    filters: dict,
    delta_s: Optional[float],
    snapshot_s: Optional[float],
    stop_at_end: bool = False,
) -> int:
    """Record until the deadline. ``stop_at_end``: also create the STOP file at the deadline or
    when the key is refused (401), so a service manager that restarts the process (systemd
    ``Restart=``) does not start calling the API again; delete STOP to record again."""
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    deadline = _parse(until) if until else (_now() + dt.timedelta(hours=hours) if hours else None)
    with SingleInstance(base / ".lock", "the SharpAPI recorder"):
        rec = Recorder(
            SharpAPIClient(), base, delta_s=delta_s, snapshot_s=snapshot_s, filters=filters
        )
        print(
            f"recording to {base} until {deadline or 'stopped'}; STOP file: {base / 'STOP'}",
            flush=True,
        )
        last_flush = last_status = time.monotonic()
        code, reason = 0, "deadline"
        try:
            while True:
                if (base / "STOP").exists():
                    reason = "STOP file"
                    break
                if deadline and _now() >= deadline:
                    break
                if not rec.step():
                    time.sleep(0.25)
                m = time.monotonic()
                if m - last_flush >= FLUSH_S or rec.sink.pending() > 150_000:
                    rec.sink.flush()
                    last_flush = m
                if m - last_status >= 30:
                    atomic_write_json(base / "status.json", rec.status(), indent=1, default=str)
                    last_status = m
                    print(
                        f"{_iso(_now())} req={rec.c.n_requests} rows={dict(rec.sink.rows)} "
                        f"limit={rec.c.per_minute:g}/min left={rec.c.remaining} "
                        f"errors={rec.counts.get('errors', 0)}",
                        flush=True,
                    )
        except KeyboardInterrupt:
            reason = "interrupted"
        except SharpAPIError as e:
            reason, code = f"fatal: {e}", 2
        finally:
            rec.sink.flush()
            if stop_at_end and (reason == "deadline" or code == 2):
                (base / "STOP").write_text(f"{_iso(_now())} {reason}" + chr(10))
            atomic_write_json(
                base / "status.json",
                rec.status(ok=code == 0, stopped=reason),
                indent=1,
                default=str,
            )
        print(f"stopped ({reason}); rows {dict(rec.sink.rows)}", flush=True)
        return code


def probe() -> int:
    c = SharpAPIClient()
    acct, h = c.get("/account")
    print("account:", json.dumps(_redact(acct), default=str)[:600])
    print(
        f"rate limit: {h.get('X-RateLimit-Limit')}/min, remaining "
        f"{h.get('X-RateLimit-Remaining')}, data delay {h.get('X-Data-Delay')} s"
    )
    use, _ = c.get("/account/usage")
    print("usage:", json.dumps(use, default=str)[:400])
    books, _ = c.get("/sportsbooks")
    rows = books.get("data") if isinstance(books, dict) else books
    print(
        f"sportsbooks ({len(rows or [])}):",
        ", ".join(
            f"{b.get('id') or b.get('key') or b.get('name')}"
            f"{'*' if b.get('sharp') or b.get('is_sharp') else ''}"
            for b in (rows or [])[:60]
        ),
        "(* = sharp)",
    )
    rows, cur, more = c.page("/odds", {"limit": 5})
    print(f"/odds sample: {len(rows)} rows, more pages: {more}")
    if rows:
        print("fields:", sorted(rows[0]))
        new = sorted(set(rows[0]) - set(ODDS_COLS))
        if new:
            print("fields kept in `extra`:", new)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m predict_ngin.capture.sharpapi",
        description="record SharpAPI odds to Parquet",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="check the key: tier, rate limit, books, a sample row")
    r = sub.add_parser("run", help="record until --hours / --until / STOP / Ctrl+C")
    r.add_argument("--hours", type=float)
    r.add_argument("--until", help="UTC ISO time to stop, e.g. 2026-10-26T02:00:00Z")
    r.add_argument("--sport", help="comma list, e.g. football,basketball (default: all)")
    r.add_argument("--league")
    r.add_argument("--sportsbook")
    r.add_argument("--delta-s", type=float, help="seconds between delta polls")
    r.add_argument("--snapshot-s", type=float, help="seconds between full snapshots")
    r.add_argument(
        "--stop-file-at-end",
        action="store_true",
        help="create the STOP file at the deadline or on a refused key (for services)",
    )
    sub.add_parser("status", help="print the recorder's status.json")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "probe":
            return probe()
        if a.cmd == "status":
            p = root() / "status.json"
            print(p.read_text() if p.exists() else "no status yet")
            return 0
        return run(
            a.hours,
            a.until,
            {"sport": a.sport, "league": a.league, "sportsbook": a.sportsbook},
            a.delta_s,
            a.snapshot_s,
            a.stop_file_at_end,
        )
    except SharpAPIError as e:
        print(f"SharpAPI: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

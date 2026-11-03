"""Command line: snapshot (the one read-only DB export), shock (Tier 1), selfcheck,
replay (Tier 2, through the trade-ngin engine), replay-check and engine-info."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from . import engine, replay
from .catalogue import load_events
from .report import BOOK_LABELS, EDIT, merge_identical, run_all, selfcheck, write_report
from .shock import SCOPES, apply_edits, parse_edits
from .snapshot import BOOKS, DB_CONFIG_ENV, default_db_config, export_snapshot, load_snapshot

OUT_ENV = "STRESS_TEST_OUT"


def default_out() -> Path:
    """$STRESS_TEST_OUT, else `out/` beside this tool in a source checkout (gitignored there),
    else `~/.stress-test/out`: snapshots and reports hold fund-internal positions and prices and
    must never land in a repository by accident."""
    named = os.environ.get(OUT_ENV)
    if named:
        return Path(named)
    tool = Path(__file__).resolve().parents[2]
    if (tool / "pyproject.toml").is_file() and (tool / ".gitignore").is_file():
        return tool / "out"
    return Path.home() / ".stress-test" / "out"


def _snapshot(a: argparse.Namespace) -> int:
    day = date.fromisoformat(a.date) if a.date else None
    db_config = a.db_config or default_db_config()
    if db_config is None:
        print(
            f"no database config: pass --db-config or set {DB_CONFIG_ENV} (the engine's "
            "config/defaults.json layout)",
            file=sys.stderr,
        )
        return 2
    print(export_snapshot(a.portfolio, day, a.out, db_config))
    return 0


def _shock(a: argparse.Namespace) -> int:
    snap = load_snapshot(a.snapshot)
    events = load_events(a.events)
    if a.only:
        wanted = {e.strip().upper() for e in a.only.split(",")}
        events = [e for e in events if e.id in wanted]
    books = merge_identical({b: snap.books.get(b, {}) for b in BOOKS})
    base = next(name for name in books if BOOK_LABELS[a.edit_base] in name.split(" = "))
    if a.edit:
        edits = parse_edits(a.edit)
        unknown = sorted(set(edits) - set(snap.notional_per_contract()))
        if unknown:
            print(f"no price or contract size for {unknown}", file=sys.stderr)
            return 2
        books[EDIT] = apply_edits(books[base], edits)
    results = run_all(snap, events, books)
    out = a.out / f"{snap.portfolio_id}_{snap.day}"
    path = write_report(snap, books, results, out, base, a.edit)
    print(path.read_text())
    print(f"wrote {path} and {out / 'results.csv'}")
    return 0


def _selfcheck(a: argparse.Namespace) -> int:
    snap = load_snapshot(a.snapshot, weekdays_only=False)
    df = selfcheck(load_events(a.events), snap.closes)
    measured = df[df.metric != "missing"]
    print(
        f"selfcheck: {len(measured)} event x instrument measurements recomputed from our prices, "
        f"{int(measured.ok.sum())} within 0.001 of the package, "
        f"{int((df.metric == 'missing').sum())} not covered by our history"
    )
    bad = measured[~measured.ok]
    if len(bad):
        print(bad.to_string(index=False, float_format=lambda v: f"{v:+.4f}"))
    return 0


def _engine(a: argparse.Namespace) -> tuple[engine.Capabilities, str | None]:
    module_dir = str(a.engine) if a.engine else None
    caps = engine.capabilities(engine.import_engine(module_dir))
    return caps, module_dir


def _engine_info(a: argparse.Namespace) -> int:
    caps, _ = _engine(a)
    print(f"tradengin {caps.version or '(version unknown)'} at {caps.location}")
    for name in ("config_runs", "bar_instrument_id", "fee_per_contract"):
        print(f"  {name}: {'yes' if getattr(caps, name) else 'no'}")
    missing = caps.missing_for_replay()
    print("replay: " + ("available" if not missing else "needs " + ", ".join(missing)))
    for c in caps.caveats():
        print(f"caveat: {c}")
    return 0 if not missing else 3


def _progress(name: str, r: engine.RunResult) -> None:
    status = "ok" if r.ok else f"FAILED: {r.error[:300]}"
    print(f"  {name} ({r.seconds:.1f} s) {status}", flush=True)


def _ready(a: argparse.Namespace) -> tuple[engine.Capabilities, str | None] | None:
    caps, module_dir = _engine(a)
    missing = caps.missing_for_replay()
    if missing:
        print(f"this engine cannot replay: it lacks {', '.join(missing)}", file=sys.stderr)
        return None
    return caps, module_dir


def _replay(a: argparse.Namespace) -> int:
    ready = _ready(a)
    if ready is None:
        return 3
    caps, module_dir = ready
    snap = load_snapshot(a.snapshot)
    events = load_events(a.events)
    if a.only:
        wanted = {e.strip().upper() for e in a.only.split(",")}
        events = [e for e in events if e.id in wanted]
    edits = parse_edits(a.edit) if a.edit else None
    if edits:
        unknown = sorted(set(edits) - set(snap.notional_per_contract()))
        if unknown:
            print(f"no price or contract size for {unknown}", file=sys.stderr)
            return 2
    books = replay.replay_books(snap, edits, a.edit_base)
    out = a.out / f"{snap.portfolio_id}_{snap.day}"
    session = replay.Session(
        snap, out / "engine-runs", module_dir, a.lead_in, adjust_rolls=not caps.bar_instrument_id
    )
    specs, paths, skipped = replay.plan_replays(session, events, a.scope)
    print(f"{len(specs)} engine runs, {a.jobs} at a time", flush=True)
    results = replay.run_all(specs, a.jobs, progress=_progress)
    table = replay.summarise(session, paths, results, books, a.scope, a.difference)
    path = replay.write_replay_report(
        snap, caps, table, books, out, a.lead_in, skipped, a.difference
    )
    print(path.read_text())
    print(f"wrote {path} and {out / 'replay.csv'}")
    return 0 if bool(table["ok"].all()) else 1


def _replay_check(a: argparse.Namespace) -> int:
    ready = _ready(a)
    if ready is None:
        return 3
    caps, module_dir = ready
    snap = load_snapshot(a.snapshot)
    out = a.out / f"{snap.portfolio_id}_{snap.day}"
    session = replay.Session(
        snap, out / "engine-runs", module_dir, a.lead_in, adjust_rolls=not caps.bar_instrument_id
    )
    specs, stored = replay.plan_check(session, a.book, a.last)
    results = replay.run_all(specs, a.jobs, progress=_progress)
    table = replay.check_table(results, stored)
    table.to_csv(out / "replay_check.csv", index=False)
    print(table.drop(columns=["error"]).to_string(index=False))
    done = table[table["ok"]]
    if done.empty:
        return 1
    print(
        f"the strategy's own book (trading from flat over the {a.lead_in} sessions before each "
        f"day) equals the stored {a.book} book on {int(done.match.sum())} of {len(done)} stored "
        f"days; {done.contracts_apart.mean():.1f} contracts apart on average. A replay carries "
        "that difference alongside the strategy."
    )
    for c in caps.caveats():
        print(f"caveat: {c}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stress-test", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="read-only export of one day from the team database")
    s.add_argument("--portfolio", default="QT_CONSERVATIVE_PORTFOLIO")
    s.add_argument("--date", help="positions date (default: the latest)")
    s.add_argument("--out", type=Path, default=default_out())
    s.add_argument(
        "--db-config",
        type=Path,
        help=f"the database config (default: ${DB_CONFIG_ENV}, then the engine's "
        "services/trade-ngin/config/defaults.json in this checkout)",
    )
    s.set_defaults(func=_snapshot)

    k = sub.add_parser("shock", help="Tier 1: every event against the books, held unchanged")
    k.add_argument("snapshot", type=Path, help="a folder written by `snapshot`")
    k.add_argument("--events", type=Path, required=True, help="the events-package folder")
    k.add_argument("--edit", help='proposed positions, e.g. "6S=0,ZT=-1" (target contracts)')
    k.add_argument("--edit-base", default="qt", choices=BOOKS, help="book the edit starts from")
    k.add_argument("--only", help="comma-separated event ids")
    k.add_argument("--out", type=Path, default=default_out())
    k.set_defaults(func=_shock)

    c = sub.add_parser("selfcheck", help="recompute the package's measured moves from our prices")
    c.add_argument("snapshot", type=Path)
    c.add_argument("--events", type=Path, required=True)
    c.set_defaults(func=_selfcheck)

    def engine_arg(x: argparse.ArgumentParser) -> None:
        x.add_argument(
            "--engine",
            type=Path,
            help=f"directory of a built tradengin module (default: ${engine.ENGINE_ENV}, "
            "then the installed package)",
        )

    i = sub.add_parser("engine-info", help="what the installed trade-ngin engine supports")
    engine_arg(i)
    i.set_defaults(func=_engine_info)

    r = sub.add_parser("replay", help="Tier 2: the strategy trades through each event")
    r.add_argument("snapshot", type=Path)
    r.add_argument("--events", type=Path, required=True)
    r.add_argument("--edit", help='proposed positions, e.g. "6S=0,ZT=-1" (target contracts)')
    r.add_argument("--edit-base", default="qt", choices=BOOKS)
    r.add_argument("--only", help="comma-separated event ids")
    r.add_argument("--scope", default=replay.HEADLINE_SCOPE, choices=SCOPES)
    r.add_argument(
        "--lead-in",
        type=int,
        default=replay.DEFAULT_LEAD_IN,
        help="sessions of real history the strategy trades before the day",
    )
    r.add_argument(
        "--difference",
        default=replay.DIFFERENCE_MODES[0],
        choices=replay.DIFFERENCE_MODES,
        help="how long a tested book's difference from the strategy's own book lasts",
    )
    r.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    r.add_argument("--out", type=Path, default=default_out())
    engine_arg(r)
    r.set_defaults(func=_replay)

    v = sub.add_parser(
        "replay-check", help="how far is the strategy's own book from the stored books?"
    )
    v.add_argument("snapshot", type=Path)
    v.add_argument("--book", default="system", choices=BOOKS)
    v.add_argument(
        "--lead-in",
        type=int,
        default=replay.DEFAULT_LEAD_IN,
        help="sessions of real history the strategy trades before each stored day",
    )
    v.add_argument("--last", type=int, help="check only the last N stored days")
    v.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    v.add_argument("--out", type=Path, default=default_out())
    engine_arg(v)
    v.set_defaults(func=_replay_check)

    a = p.parse_args(argv)
    return int(a.func(a))

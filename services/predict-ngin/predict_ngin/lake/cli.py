"""``predict-ngin data ...``: one command for the data pipeline.

    # what is on disk, how fresh, what is running
    uv run predict-ngin data status
    # fetch new fills, then rebuild changed lake months
    uv run predict-ngin data update polymarket
    # new setup: download the published Polymarket snapshot, then catch up from it
    uv run predict-ngin data pull polymarket [--repo user/name] [--no-update]
    # maintainer: upload the local Polymarket lake as that snapshot (`hf auth login` first)
    uv run predict-ngin data publish polymarket [--repo user/name] [--dry-run]
    # resume the Kalshi tape refresh (long-running)
    uv run predict-ngin data update kalshi
    uv run predict-ngin data build polymarket|kalshi [--markets] [--months 2026-03 ...] [--rebuild]
    uv run predict-ngin data verify [polymarket/fills | kalshi/trades | polymarket/markets]
    # write <data>/lake/catalog.duckdb for external tools
    uv run predict-ngin data catalog
    # optional cold storage (scripts/data/storage_tier.py)
    uv run predict-ngin data tier list|demote|promote …
    # local data explorer in the browser (needs the viz extra)
    uv run predict-ngin viz [--port 8501]
    # portfolio -> book -> strategy report (HTML; see docs/PORTFOLIO_REPORT.md)
    uv run predict-ngin report [--no-open] [--out PATH]
    # orders (live is off by default; see docs/EXECUTION.md)
    uv run predict-ngin trade status|latency-test|cancel-all|kill …

Fetchers stay as scripts (they talk to venue APIs and are run as subprocesses); builders, readers
and checks live in the core. Each step takes a lock, so two runs cannot overlap.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import List

from ..shared.lock import Busy, SingleInstance, is_locked
from ..shared.paths import cold_root, data_root, repo_root
from ..tapes import kalshi, polymarket
from . import catalog, contracts, manifest

REPO_HELP = "Hugging Face dataset (default: $PREDICT_NGIN_HF_REPO, else domdd/predict_ngin)"


def _run(script: str, *args: str) -> int:
    cmd = [sys.executable, "-u", str(repo_root() / script), *args]
    print("$", " ".join(cmd[2:]), flush=True)
    return subprocess.call(cmd, cwd=repo_root())


def _day(ts) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d") if ts else "-"


def _lock(name: str) -> Path:
    return data_root() / "lake" / f".lock_{name}"


# ------------------------------------------------------------------------------------------- status
def status() -> int:
    d = data_root()
    m = manifest.load()
    rows = []
    # Kalshi tape
    ext = kalshi.ext_files()
    gaps = kalshi.missing_days()
    refreshing = [
        r.name for r in sorted((d / "kalshi").glob("trades_fixed_ext*")) if is_locked(r / ".lock")
    ]
    rows.append(
        (
            "kalshi trades",
            "lake" if kalshi.lake_ready() else "legacy",
            f"{len(kalshi.old_files())} monthly + {len(ext)} daily files, "
            f"last day {_day((kalshi.tape_end_ts() or 86400) - 86400)}"
            + (
                f"; {len(gaps)} days missing since the cutover (first {gaps[0]})"
                if gaps
                else "; no gaps"
            ),
            f"refresh running: {', '.join(refreshing)}" if refreshing else "",
        )
    )
    # Polymarket tape
    pm_state = d / "lake" / "polymarket" / "fills_state.json"
    st = json.loads(pm_state.read_text()) if pm_state.exists() else {}
    built = sorted(k for k in st if not k.startswith("_"))
    rows.append(
        (
            "polymarket fills",
            "lake" if polymarket.lake_ready() else "legacy",
            f"source through {_day(polymarket.tape_end_ts())} (excl.); "
            f"lake months built {len(built)}" + (f" ({built[0]}..{built[-1]})" if built else ""),
            "build running" if is_locked(_lock("polymarket")) else "",
        )
    )
    mk = m.get("polymarket/markets")
    rows.append(
        (
            "polymarket markets",
            "lake" if mk else "-",
            f"{mk['rows']:,} markets, category v{mk['category_version']}, "
            f"tags {'yes' if mk['tagged_events'] else 'no'}"
            if mk
            else "not built",
            "",
        )
    )
    tags = d / "polymarket_clean" / "event_tags.parquet"
    rows.append(
        (
            "polymarket event tags",
            "-",
            "present" if tags.exists() else "missing (scripts/data/fetch_pm_event_tags.py)",
            "",
        )
    )
    # recorder books
    for venue in ("kalshi", "polymarket"):
        days = sorted(p.name for p in (d / "live_books" / venue).glob("20??-??-??"))
        rows.append(
            (
                f"{venue} recorded books",
                "-",
                f"{len(days)} days ({days[0]}..{days[-1]})" if days else "none",
                "",
            )
        )
    cold = cold_root()
    rows.append(
        (
            "cold storage",
            "-",
            str(cold) if cold else "not configured (all data in the data folder)",
            "",
        )
    )
    w = max(len(r[0]) for r in rows)
    print(f"data folder: {d}")
    for name, layout, what, note in rows:
        print(f"  {name:<{w}}  {layout:<6}  {what}{'  [' + note + ']' if note else ''}")
    return 0


# -------------------------------------------------------------------------------------------- build
def build_polymarket(markets: bool, months: List[str] | None, rebuild: bool) -> int:
    from . import build_polymarket as b

    with SingleInstance(_lock("polymarket"), "a Polymarket lake build"):
        tags = data_root() / "polymarket_clean" / "event_tags.parquet"
        mk = b.lake() / "markets.parquet"
        if (
            markets
            or not mk.exists()
            or (tags.exists() and tags.stat().st_mtime > mk.stat().st_mtime)
        ):
            b.build_markets()
            rebuild = True  # categories may have changed, and fill files are sorted by category
        b.build_fills(months, rebuild)
    return 0


def build_kalshi(markets: bool, months: List[str] | None, rebuild: bool) -> int:
    from . import build_kalshi as b

    gaps = kalshi.missing_days()
    if gaps:
        print(
            f"note: the Kalshi tape has {len(gaps)} missing days (first {gaps[0]}); "
            "months build, but the lake is "
            "not served until the refresh fills them (predict-ngin data status)"
        )
    with SingleInstance(_lock("kalshi"), "a Kalshi lake build"):
        if markets or not (b.lake() / "markets.parquet").exists():
            try:
                b.build_markets()
            except SystemExit as e:  # metadata is optional for the trade files
                print(f"market table skipped: {e}")
            # categories may have changed; trade files sort by category
            rebuild = rebuild or markets
        b.build_trades(months, rebuild)
    return 0


def update_polymarket(skip_fetch: bool) -> int:
    if not skip_fetch:
        rc = _run("scripts/data/polymarket_extend.py", "tail")
        if rc:
            print(f"fetch failed (exit {rc}); lake not rebuilt", file=sys.stderr)
            return rc
    return build_polymarket(False, None, False)


def update_kalshi() -> int:
    print(
        "Resuming the Kalshi tape refresh (one process, ~3 req/s). "
        "It skips finished days and can take hours."
    )
    print(
        "Parallel workers: scripts/data/kalshi_tape_parallel.py. "
        "The Kalshi lake build follows once the refresh"
    )
    print("is complete and verified (scripts/data/kalshi_tape_parallel.py verify).")
    return _run("scripts/research/tape_refresh.py", "run")


# ------------------------------------------------------------------------------------------- verify
def verify(which: List[str]) -> int:
    from . import connect

    con = connect(register_views=False)
    srcs = {
        "polymarket/fills": lambda: polymarket.clean_trades_sql(
            polymarket.ALL_COLUMNS + polymarket.LAKE_ONLY, source="lake"
        ),
        "polymarket/markets": lambda: (
            "SELECT * FROM read_parquet("
            f"'{(data_root() / 'lake/polymarket/markets.parquet').as_posix()}')"
        ),
        "kalshi/trades": lambda: kalshi.tape_sql(kalshi.ALL_COLUMNS),
    }
    bad = 0
    for name in which or list(srcs):
        try:
            sql = srcs[name]()
        except (FileNotFoundError, IndexError):
            print(f"{name}: not built here, skipped")
            continue
        res = contracts.ALL[name].run(con, sql)
        failed = contracts.failed(res)
        bad += len(failed)
        manifest.update(
            name,
            last_verify={
                "ok": not failed,
                "failed": failed,
                "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            },
        )
        print(f"{name}: {'OK' if not failed else 'FAILED'} ({len(res)} checks)")
        for r in failed:
            print(f"    {r['check']}: {r['violations']:,} bad rows {r.get('detail', '')}")
    return 1 if bad else 0


# ---------------------------------------------------------------------------------------------- viz
def viz(port: int, headless: bool) -> int:
    """Launch the Streamlit explorer (predict_ngin/viz/app.py). Runs it by file path, so this
    module never imports the viz package (it sits above lake in the import layers)."""
    import importlib.util

    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("plotly") is None:
        print(
            "the explorer needs the viz extra: uv sync --extra viz (or --all-extras)",
            file=sys.stderr,
        )
        return 1
    app = Path(__file__).resolve().parents[1] / "viz" / "app.py"
    # always headless: otherwise Streamlit's first run asks for an email on stdin and hangs when
    # there is no terminal; the browser is opened here instead
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app),
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    url = f"http://localhost:{port}"
    print(f"data explorer: {url}  (Ctrl+C to stop)", flush=True)
    if not headless:
        import threading
        import webbrowser

        threading.Timer(3.0, webbrowser.open, args=(url,)).start()
    try:
        return subprocess.call(cmd, cwd=repo_root())
    except KeyboardInterrupt:
        return 0


# --------------------------------------------------------------------------------------------- main
def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    # forwarded as-is (argparse's REMAINDER drops a leading --flag); subprocesses, because
    # lake imports neither viz nor execution (independent layers)
    forward = {"report": "predict_ngin.viz.report", "trade": "predict_ngin.execution.cli"}
    if argv and argv[0] in forward:
        cmd = [sys.executable, "-m", forward[argv[0]], *argv[1:]]
        return subprocess.call(cmd, cwd=repo_root())
    ap = argparse.ArgumentParser(prog="predict-ngin", description="predict-ngin command line")
    top = ap.add_subparsers(dest="area", required=True)
    data = top.add_parser(
        "data", help="data pipeline: status, update, pull, publish, build, verify, catalog, tier"
    )
    sub = data.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    up = sub.add_parser("update")
    up.add_argument("venue", choices=["polymarket", "kalshi"])
    up.add_argument(
        "--skip-fetch",
        action="store_true",
        help="polymarket: rebuild the lake from files on disk only",
    )
    pl = sub.add_parser("pull", help="download the published snapshot, then catch up from it")
    pl.add_argument("venue", choices=["polymarket"])
    pl.add_argument("--repo", help=REPO_HELP)
    pl.add_argument("--no-update", action="store_true", help="download only; skip the catch-up")
    pl.add_argument("--force", action="store_true", help="replace a lake that was built here")
    pb = sub.add_parser("publish", help="upload the local lake as the shared snapshot")
    pb.add_argument("venue", choices=["polymarket"])
    pb.add_argument("--repo", help=REPO_HELP)
    pb.add_argument("--dry-run", action="store_true", help="list what would be uploaded")
    bd = sub.add_parser("build")
    bd.add_argument("venue", choices=["polymarket", "kalshi"])
    bd.add_argument(
        "--markets", action="store_true", help="rebuild the market table (and every fill month)"
    )
    bd.add_argument("--months", nargs="*")
    bd.add_argument("--rebuild", action="store_true")
    vf = sub.add_parser("verify")
    vf.add_argument(
        "datasets", nargs="*", help=f"any of {', '.join(sorted(contracts.ALL))} (default: all)"
    )
    sub.add_parser("catalog")
    tr = sub.add_parser("tier")
    tr.add_argument("rest", nargs=argparse.REMAINDER)
    vz = top.add_parser("viz", help="interactive data explorer in the browser (Streamlit)")
    vz.add_argument("--port", type=int, default=8501)
    vz.add_argument("--headless", action="store_true", help="do not open a browser")
    rp = top.add_parser("report", help="portfolio / book / strategy performance report (HTML)")
    rp.add_argument("rest", nargs=argparse.REMAINDER)
    tg = top.add_parser("trade", help="orders: status, latency-test, cancel-all, kill (live off)")
    tg.add_argument("rest", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.area == "viz":
        return viz(a.port, a.headless)
    if a.area == "report":
        cmd = [sys.executable, "-m", "predict_ngin.viz.report", *a.rest]
        return subprocess.call(cmd, cwd=repo_root())
    if a.area == "trade":
        # a subprocess, because lake and execution are independent layers
        cmd = [sys.executable, "-m", "predict_ngin.execution.cli", *a.rest]
        return subprocess.call(cmd, cwd=repo_root())
    try:
        if a.cmd == "status":
            return status()
        if a.cmd == "update":
            return update_polymarket(a.skip_fetch) if a.venue == "polymarket" else update_kalshi()
        if a.cmd == "pull":
            from . import hub

            return hub.pull(a.repo, update=not a.no_update, force=a.force)
        if a.cmd == "publish":
            from . import hub

            return hub.publish(a.repo, dry_run=a.dry_run)
        if a.cmd == "build":
            if a.venue == "kalshi":
                return build_kalshi(a.markets, a.months, a.rebuild)
            return build_polymarket(a.markets, a.months, a.rebuild)
        if a.cmd == "verify":
            unknown = [x for x in a.datasets if x not in contracts.ALL]
            if unknown:
                ap.error(f"unknown datasets {unknown}; choose from {sorted(contracts.ALL)}")
            return verify(a.datasets)
        if a.cmd == "catalog":
            print(catalog.write_catalog())
            return 0
        if a.cmd == "tier":
            return _run("scripts/data/storage_tier.py", *a.rest)
    except Busy as e:
        print(e, file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())

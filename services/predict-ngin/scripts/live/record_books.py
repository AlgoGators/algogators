#!/usr/bin/env python
"""Read-only order-book recorder (Kalshi, Polymarket, Polymarket US, Polymarket RTDS prices).
NEVER places, cancels or signs orders (the only signature is Kalshi's WS login header).

Examples
--------
    # 60 s dry run: connect, reconstruct books, print summary, write nothing
    python scripts/live/record_books.py --dry-run --duration 60

    # record until Ctrl+C into data/live_books/
    python scripts/live/record_books.py

    # find candidate cross-venue pairs for human review
    python scripts/live/record_books.py discover --topics fed cpi sports

See docs/RECORDER.md.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.recorder.config import load_config  # noqa: E402


def _load_env_file(path: Path) -> int:
    """KEY=VALUE lines -> os.environ (existing variables win). Values are never logged."""
    import os

    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if not t or t.startswith("#") or "=" not in t:
            continue
        if t.startswith("export "):
            t = t[7:].strip()
        k, v = t.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            n += 1
    return n


def _record(args) -> int:
    from dataclasses import replace

    from src.recorder.runner import run_recorder
    from src.recorder.writer import CountingSink, ParquetSink

    def load():
        c = load_config(args.config)
        if args.venues:
            for v in list(c.venues):
                if v not in args.venues:
                    c.venues[v] = replace(c.venues[v], enabled=False)
        return c

    cfg = load()
    out = Path(args.out or cfg.defaults.output_dir)
    if not out.is_absolute():
        out = ROOT / out
    sink = CountingSink() if args.dry_run else ParquetSink(out)
    subs = cfg.subscriptions()
    logging.info("recording static %s + %d discovery groups%s -> %s", {v: len(m) for v, m in subs.items()},
                 len(cfg.discovery.groups), " + rtds" if cfg.rtds.enabled else "",
                 "DRY RUN" if args.dry_run else out)

    try:
        stop_file = None if args.dry_run else out / "STOP"
        if stop_file is not None and stop_file.exists():
            stop_file.unlink()  # stale request from a previous run
        # discovery groups in --config are re-read when the file changes (no restart; docs/RECORDER.md)
        stats = asyncio.run(run_recorder(cfg, sink, duration_s=args.duration, stop_file=stop_file,
                                         config_path=None if args.no_reload else Path(args.config),
                                         config_loader=load))
    except KeyboardInterrupt:
        logging.info("interrupted")
        return 130

    print("\n=== recorder summary ===")
    for venue, st in stats.items():
        print(f"{venue:14s} {dict(st)}")
    print("rows by venue:", {k: dict(v) for k, v in sink.counts.items()})
    if args.dry_run:
        print("\nlatest top-of-book (dry run):")
        for (venue, mid), row in sorted(sink.latest.items()):
            if "value" in row:
                print(f"  {venue:15s} {mid[:40]:40s} {row['value']}")
                continue
            print(f"  {venue:13s} {mid[:40]:40s} {row['outcome']:>4s} bid {row['best_bid']} ask {row['best_ask']}"
                  f"  levels {len(row['bid_px'])}/{len(row['ask_px'])}")
    else:
        print(f"files written: {sink.files_written} under {out}")
    return 0


def _discover(args) -> int:
    import yaml

    from src.recorder.discovery import discover

    res = discover(args.topics, sports_horizon_days=args.horizon_days, min_score=args.min_score)
    out = Path(args.out) if args.out else ROOT / "data" / "live_books" / (
        f"candidate_pairs_{datetime.now(timezone.utc):%Y%m%d_%H%M}.yaml")
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# CANDIDATE pairs -- NOT verified. Read BOTH venues' resolution rules before copying\n"
        "# any entry into config/recorder.yaml (drop the review-only keys when you do).\n"
    )
    out.write_text(header + yaml.safe_dump(res, sort_keys=False, width=200), encoding="utf-8")
    for topic, c in res.items():
        print(f"{topic}: {len(c)} candidates")
        for e in c[:5]:
            print(f"   {e['score']:.2f}  {e['kalshi_title'][:60]!r}  <->  {e['other_title'][:60]!r}")
    print(f"wrote {out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=str(ROOT / "config" / "recorder.yaml"))
    p.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    p.add_argument("--dry-run", action="store_true", help="do not write files; print summary")
    p.add_argument("--out", default=None, help="output root (default from config)")
    p.add_argument("--venues", nargs="*", help="restrict to these venues")
    p.add_argument("--no-reload", action="store_true", help="do not hot-reload discovery groups from --config")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--log-file", default=None, help="append logs here instead of stderr (detached runs)")
    p.add_argument("--env-file", default=None, help="load KEY=VALUE lines (e.g. Kalshi WS keys) from this file")
    sub = p.add_subparsers(dest="cmd")
    d = sub.add_parser("discover", help="list candidate cross-venue pairs for review")
    d.add_argument("--topics", nargs="+", default=["fed", "cpi", "sports"], choices=["fed", "cpi", "sports"])
    d.add_argument("--horizon-days", type=int, default=10, help="sports: only games ending within N days")
    d.add_argument("--min-score", type=float, default=0.35)
    d.add_argument("--out", default=None)
    args = p.parse_args(argv)

    log_kw = {}
    if args.log_file:
        Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
        log_kw = {"filename": args.log_file, "encoding": "utf-8"}
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", **log_kw)
    if args.env_file:
        n = _load_env_file(Path(args.env_file))
        logging.info("loaded %d variables from %s", n, args.env_file)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    if args.cmd == "discover":
        return _discover(args)
    return _record(args)


if __name__ == "__main__":
    sys.exit(main())

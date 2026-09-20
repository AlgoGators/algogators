"""Command-line front for the people import.

    python -m platform_db.people_import applications --workbook X.xlsx \
        --track analyst --cycle fall-2026 [--sheet Submissions] [--dry-run]
    python -m platform_db.people_import members --csv members.csv [--dry-run]
    python -m platform_db.people_import close-cycle --cycle fall-2026 [--dry-run]

Connects with the DB_* environment variables every service uses (or --dsn).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from platform_db.config import DatabaseConfig
from platform_db.people_import.forms import read_decisions, read_submissions
from platform_db.people_import.importer import close_cycle, import_applications, import_members
from platform_db.people_import.members import read_members
from platform_db.people_import.model import Cycle


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _connect(dsn: str | None) -> Any:
    import psycopg2

    if dsn:
        return psycopg2.connect(dsn)
    cfg = DatabaseConfig.from_env()
    return psycopg2.connect(
        host=cfg.host, port=cfg.port, dbname=cfg.database, user=cfg.user, password=cfg.password
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m platform_db.people_import", description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dsn", help="libpq DSN; default is the DB_* environment")
    common.add_argument(
        "--actor", default=os.environ.get("USERNAME") or getpass.getuser(), help="who is importing"
    )
    common.add_argument("--dry-run", action="store_true", help="run everything, then roll back")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser(
        "applications", parents=[common], help="import a Forms .xlsx export for one cycle"
    )
    a.add_argument("--workbook", required=True, type=Path)
    a.add_argument("--track", required=True, choices=("analyst", "relations"))
    a.add_argument("--cycle", required=True, help="e.g. fall-2026")
    a.add_argument("--sheet", help="submissions sheet; default first sheet")
    a.add_argument(
        "--decisions-sheet",
        default="Composite Score",
        help="sheet with Interview/Fund Invite columns",
    )
    a.add_argument("--no-decisions", action="store_true", help="ignore the decisions sheet")
    a.add_argument(
        "--form-url", help="the form's URL for application_form; default file://<workbook name>"
    )
    a.add_argument(
        "--timezone", default="America/New_York", help="zone of the export's naive timestamps"
    )

    m = sub.add_parser("members", parents=[common], help="import the roster CSV")
    m.add_argument("--csv", required=True, type=Path)
    m.add_argument(
        "--joined-on", type=date.fromisoformat, help="joined_on for NEW member rows; default today"
    )

    c = sub.add_parser(
        "close-cycle", parents=[common], help="reject every still-pending application of a cycle"
    )
    c.add_argument("--cycle", required=True, help="e.g. fall-2026")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = _connect(args.dsn)
    try:
        if args.command == "applications":
            subs = read_submissions(args.workbook, sheet=args.sheet, tz=args.timezone)
            decisions = (
                {}
                if args.no_decisions
                else read_decisions(args.workbook, sheet=args.decisions_sheet)
            )
            print(
                f"read {len(subs)} submissions, {len(decisions)} decision rows from {args.workbook.name}"
            )
            report = import_applications(
                conn,
                subs,
                cycle=Cycle.parse(args.cycle),
                track=args.track,
                decisions=decisions,
                actor=args.actor,
                filename=args.workbook.name,
                file_sha256=_sha256(args.workbook),
                form_url=args.form_url or f"file://{args.workbook.name}",
                dry_run=args.dry_run,
            )
        elif args.command == "close-cycle":
            report = close_cycle(
                conn, cycle=Cycle.parse(args.cycle), actor=args.actor, dry_run=args.dry_run
            )
        else:
            members = read_members(args.csv)
            print(f"read {len(members)} members from {args.csv.name}")
            report = import_members(
                conn,
                members,
                actor=args.actor,
                filename=args.csv.name,
                file_sha256=_sha256(args.csv),
                joined_on=args.joined_on,
                dry_run=args.dry_run,
            )
        if not args.dry_run:
            conn.commit()
        print(report.summary())
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

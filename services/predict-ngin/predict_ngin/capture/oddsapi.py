"""The Odds API (the-odds-api.com) snapshot: one pass, comparison books' lines to Parquet.

    uv run --no-sync python -m predict_ngin.capture.oddsapi --sport basketball_nba
    uv run --no-sync python -m predict_ngin.capture.oddsapi --sport americanfootball_nfl --dry-run

Free tier: 500 credits a month. One ``/v4/sports/{sport}/odds`` call costs
``markets x regions`` credits, and up to 10 ``bookmakers`` count as one region, so the default
call (h2h, spreads, totals; the 10 books below) costs 3 credits whatever the number of games.
The schedule lives in the collector registry (timer); keep it inside the monthly credit budget.
A pass is skipped when fewer than ``--reserve`` credits are left.

Output under ``<data>/oddsapi/``: ``YYYY-MM-DD/odds/<sport>-<HHMMSS>.parquet`` (one row per
book x market x outcome, ``captured_at`` = our receipt time, ``last_update`` = the book's),
``raw/<sport>-<ts>.json.gz`` (the response as received), ``usage.jsonl``, ``status.json``.
The key comes from ``ODDS_API_KEY`` (environment or the repo ``.env``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from ..shared.paths import data_root, repo_root
from .runner import EXIT_NO_DATA, EXIT_OK, atomic_write_json, run_one_pass, status_fields

API = "https://api.the-odds-api.com/v4"
KEY_VAR = "ODDS_API_KEY"
BOOKS = (
    "hardrockbet,pinnacle,draftkings,fanduel,betmgm,williamhill_us,"
    "betrivers,fanatics,espnbet,bovada"
)
MARKETS = "h2h,spreads,totals"
SCHEMA = 1


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def root() -> Path:
    return data_root() / "oddsapi"


def api_key() -> Optional[str]:
    from ..venues.kalshi.auth import read_dotenv

    return os.environ.get(KEY_VAR) or read_dotenv(repo_root() / ".env").get(KEY_VAR)


def flatten(events: List[dict], captured_at: str, sport: str) -> List[dict]:
    rows = []
    for e in events:
        for b in e.get("bookmakers") or []:
            for m in b.get("markets") or []:
                for o in m.get("outcomes") or []:
                    rows.append(
                        {
                            "captured_at": captured_at,
                            "sport": sport,
                            "event_id": e.get("id"),
                            "commence_time": e.get("commence_time"),
                            "home_team": e.get("home_team"),
                            "away_team": e.get("away_team"),
                            "book": b.get("key"),
                            "market": m.get("key"),
                            "last_update": m.get("last_update") or b.get("last_update"),
                            "outcome": o.get("name"),
                            "point": o.get("point"),
                            "american": o.get("price"),
                            "schema": SCHEMA,
                        }
                    )
    return rows


def one_pass(sport: str, books: str, markets: str, reserve: int, dry_run: bool) -> int:
    import pandas as pd
    import requests

    base = root()
    key = api_key()
    if not key:
        print(f"set {KEY_VAR} in the environment or .env", file=sys.stderr)
        return EXIT_NO_DATA
    s = requests.Session()
    # /sports is free and returns the quota headers
    r = s.get(f"{API}/sports", params={"apiKey": key}, timeout=30)
    remaining = r.headers.get("x-requests-remaining")
    now = _now()
    if r.status_code != 200 or not remaining:
        # a failed quota read is an error, not "0 credits left"
        # (2026-10-17: that skipped a pass silently)
        print(f"quota check failed: HTTP {r.status_code}: {r.text[:300]}", file=sys.stderr)
        atomic_write_json(
            base / "status.json",
            status_fields(
                "oddsapi", "data", False, _iso(now), sport=sport, error="quota check failed",
                http=r.status_code, remaining_header=remaining,
            ),
            indent=1,
        )
        return EXIT_NO_DATA
    left = int(float(remaining))
    cost = len(markets.split(",")) * ((len(books.split(",")) + 9) // 10)
    print(f"credits left {left}; this call costs {cost}", flush=True)
    if dry_run:
        return EXIT_OK
    status = {"sport": sport, "credits_left_before": left, "cost": cost}
    if left - cost < reserve:
        atomic_write_json(
            base / "status.json",
            status_fields("oddsapi", "data", False, _iso(now), skipped="reserve", **status),
            indent=1,
        )
        print(f"skipped: would leave fewer than {reserve} credits", flush=True)
        return EXIT_OK
    r = s.get(
        f"{API}/sports/{sport}/odds",
        params={
            "apiKey": key,
            "bookmakers": books,
            "markets": markets,
            "oddsFormat": "american",
            "dateFormat": "iso",
        },
        timeout=60,
    )
    captured = _iso(now)
    usage = {
        k: r.headers.get(k) for k in ("x-requests-remaining", "x-requests-used", "x-requests-last")
    }
    with open(base / "usage.jsonl", "a", encoding="utf-8") as f:
        f.write(
            json.dumps({"ts": captured, "sport": sport, "status": r.status_code, **usage}) + "\n"
        )
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:300]}", file=sys.stderr)
        atomic_write_json(
            base / "status.json",
            status_fields(
                "oddsapi", "data", False, captured, http=r.status_code, **status, **usage
            ),
            indent=1,
        )
        return EXIT_NO_DATA
    events = r.json()
    raw = base / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with gzip.open(raw / f"{sport}-{now:%Y%m%dT%H%M%SZ}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(events, f)
    rows = flatten(events, captured, sport)
    if rows:
        d = base / now.strftime("%Y-%m-%d") / "odds"
        d.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(rows)
        df["point"] = pd.to_numeric(df["point"], errors="coerce").astype("float64")
        df["american"] = pd.to_numeric(df["american"], errors="coerce").astype("float64")
        tmp = d / f".{sport}-{now:%H%M%S}.parquet.tmp"
        df.to_parquet(tmp, index=False, compression="zstd")
        tmp.replace(d / f"{sport}-{now:%H%M%S}.parquet")
    atomic_write_json(
        base / "status.json",
        status_fields(
            "oddsapi", "data", True, captured, events=len(events), rows=len(rows), **status, **usage
        ),
        indent=1,
    )
    print(f"{sport}: {len(events)} events, {len(rows)} rows; {usage}", flush=True)
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m predict_ngin.capture.oddsapi")
    ap.add_argument("--sport", required=True, help="e.g. basketball_nba, americanfootball_nfl")
    ap.add_argument("--books", default=BOOKS)
    ap.add_argument("--markets", default=MARKETS)
    ap.add_argument("--reserve", type=int, default=40, help="credits to keep (default 40)")
    ap.add_argument("--dry-run", action="store_true", help="only print credits left and cost")
    a = ap.parse_args(argv)
    return run_one_pass(root(), lambda: one_pass(a.sport, a.books, a.markets, a.reserve, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())

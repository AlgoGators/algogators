"""OddsPapi free-tier harvest (research only).

`/v4/historical-odds` is unmetered (5 s cooldown, max 3 books per call); every other endpoint costs 1 of the
250 monthly requests. So: spend ~20 metered calls listing each tournament's 2026 fixtures, then pull the sharp-book
tick history for every finished fixture. History is available from 2026-01-01.

    ODDSPAPI_KEY=... python -I scripts/research/oddspapi_harvest.py fixtures
    ODDSPAPI_KEY=... python -I scripts/research/oddspapi_harvest.py history [--books pinnacle,circasports,sbobet]
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2] / "data" / "vendor" / "oddspapi"
FIX = ROOT / "fixtures"
BASE = "https://api.oddspapi.io/v4"
QUOTA_STOP = 220  # leave headroom out of the 250/month

# Ordered by Kalshi/Polymarket sports volume; history is fetched in this order, newest fixtures first.
TOURNAMENTS = {
    31: "nfl", 132: "nba", 109: "mlb", 234: "nhl", 27653: "ncaaf", 27625: "ncaaf_post",
    17: "epl", 7: "ucl", 24660: "world_cup", 16: "world_cup_old", 8: "laliga", 35: "bundesliga", 23: "serie_a",
    34: "ligue1", 679: "uel", 242: "mls", 486: "wnba", 648: "ncaab", 28370: "ncaab_tourney", 25325: "ufc",
    18: "championship", 27466: "liga_mx_clausura", 325: "brasileiro", 34480: "uecl", 2382: "nba_preseason",
}


def key() -> str:
    k = os.environ.get("ODDSPAPI_KEY")
    env = ROOT.parents[2] / ".env"
    if not k and env.exists():  # atlas units do not export .env
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ODDSPAPI_KEY="):
                k = line.split("=", 1)[1].strip()
    if not k:
        raise SystemExit("set ODDSPAPI_KEY")
    return k


def get(path: str, **params):
    params["apiKey"] = key()
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    for a in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                body = e.read()
                if b"REQUEST_LIMIT_EXCEEDED" in body:
                    raise SystemExit("monthly quota exhausted")
                time.sleep(10 * (a + 1))
                continue
            return e.code, e.read()
        except Exception:
            time.sleep(5 * (a + 1))
    return -1, b""


def used() -> int:
    _, b = get("account")
    return json.loads(b)["subscriptions"][0]["request_count"]


def stage_fixtures() -> None:
    FIX.mkdir(parents=True, exist_ok=True)
    to = (dt.date.today() + dt.timedelta(1)).isoformat()
    for tid, name in TOURNAMENTS.items():
        out = FIX / f"{tid}_{name}.json"
        if out.exists():
            continue
        n = used()
        if n >= QUOTA_STOP:
            print(f"stopping: {n} metered requests used")
            return
        code, body = get("fixtures", tournamentId=tid, **{"from": "2026-01-01", "to": to})
        print(tid, name, code, len(body), f"(used {n + 1})", flush=True)
        if code == 200:
            out.write_bytes(body)
        time.sleep(2.2)


def stage_history(books: str, cooldown: float) -> None:
    tag = books.replace(",", "+")
    out_dir = ROOT / "history" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for f in FIX.glob("*.json"):
        name = f.stem.split("_", 1)[1]
        rank = list(TOURNAMENTS.values()).index(name)
        for fx in json.loads(f.read_bytes()):
            fid = fx["fixtureId"]
            if fx["statusId"] == 2 and not (out_dir / f"{fid}.json.gz").exists() and not (out_dir / f"{fid}.404").exists():
                jobs.append((rank, fx["startTime"], fx["fixtureId"]))
    # Round-robin across tournaments, newest first within each, so a partial run still covers every sport.
    by_t: dict[int, list] = {}
    for j in sorted(jobs, key=lambda j: j[1], reverse=True):
        by_t.setdefault(j[0], []).append(j)
    order = []
    while any(by_t.values()):
        for r in sorted(by_t):
            if by_t[r]:
                order.append(by_t[r].pop(0))
    print(f"{len(order)} fixtures to fetch, ~{len(order) * cooldown / 3600:.1f} h", flush=True)
    empty = 0
    for i, (_, start, fid) in enumerate(order):
        t0 = time.time()
        code, body = get("historical-odds", fixtureId=fid, bookmakers=books)
        if code == 200:
            (out_dir / f"{fid}.json.gz").write_bytes(gzip.compress(body))
            empty += b'"markets"' not in body
        elif code == 404:  # no history for these books; don't retry on the next run
            (out_dir / f"{fid}.404").touch()
        else:
            print(fid, code, body[:200], flush=True)
        if i % 100 == 0:
            print(f"{i}/{len(order)} {start} empty={empty}", flush=True)
        time.sleep(max(0.0, cooldown - (time.time() - t0)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["fixtures", "history"])
    ap.add_argument("--books", default="pinnacle,circasports,sbobet")
    ap.add_argument("--cooldown", type=float, default=5.3)
    a = ap.parse_args()
    if a.stage == "fixtures":
        stage_fixtures()
    else:
        stage_history(a.books, a.cooldown)


if __name__ == "__main__":
    main()

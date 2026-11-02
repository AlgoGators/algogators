"""SportsGameOdds Pro-trial harvest (research only).

Finalized events with every bookmaker's odds (Hard Rock, Pinnacle, Circa, Kalshi, Polymarket, Novig, ...), alt
lines, props and results. Per-book prices start ~2025-03 (last pre-game price); explicit per-book openOdds /
closeOdds start ~2026-01. No tick history on this plan.

Walks every league in weekly windows, newest first, Florida-tradable leagues first, one gzip file per page;
a `.done` marker per window makes reruns resume. Writes STOP when the key is rejected (trial over).

    SGO_KEY=... python -I scripts/research/sgo_harvest.py [--since 2024-10-01]
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

ROOT = pathlib.Path(__file__).resolve().parents[2] / "data" / "vendor" / "sgo"
BASE = "https://api.sportsgameodds.com/v2"
# Kalshi/Polymarket/Hard Rock volume first; everything else after.
FIRST = ["NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAB", "WNBA", "EPL", "UEFA_CHAMPIONS_LEAGUE", "MLS", "LA_LIGA",
         "BUNDESLIGA", "IT_SERIE_A", "FR_LIGUE_1", "INTERNATIONAL_SOCCER", "UFC", "ATP", "WTA", "POLITICS"]
MIN_GAP = 60 / 280  # stay under 300 requests/min


def key() -> str:
    k = os.environ.get("SGO_KEY")
    env = ROOT.parents[2] / ".env"
    if not k and env.exists():  # atlas units do not export .env
        k = next((ln.split("=", 1)[1].strip() for ln in env.read_text(encoding="utf-8").splitlines()
                  if ln.startswith("SGO_KEY=")), None)
    if not k:
        raise SystemExit("set SGO_KEY")
    return k


_last = 0.0


def get(path: str, **params):
    global _last
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    # Cloudflare rejects urllib's default User-Agent with 403
    req = urllib.request.Request(url, headers={"x-api-key": key(), "User-Agent": "predict-ngin-research/1.0",
                                               "Accept": "application/json"})
    for a in range(6):
        time.sleep(max(0.0, MIN_GAP - (time.time() - _last)))
        _last = time.time()
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code == 429:
                time.sleep(30 * (a + 1))
                continue
            if e.code >= 500:
                time.sleep(10 * (a + 1))
                continue
            return e.code, body
        except Exception:
            time.sleep(10 * (a + 1))
    return -1, b""


def leagues() -> list[str]:
    code, body = get("leagues")
    if code in (401, 403):
        stop(code, body)
    ids = [x["leagueID"] for x in json.loads(body)["data"]]
    return [lg for lg in FIRST if lg in ids] + sorted(lg for lg in ids if lg not in FIRST)


def stop(code, body) -> None:
    (ROOT / "STOP").touch()
    raise SystemExit(f"key rejected ({code} {body[:200]!r}): trial over, wrote STOP")


def run(since: dt.date) -> None:
    out = ROOT / "events"
    today = dt.date.today()
    weeks = []
    end = today
    while end > since:
        weeks.append((max(since, end - dt.timedelta(7)), end))
        end -= dt.timedelta(7)
    lgs = leagues()
    # newest week across all leagues first, so a short trial still covers the most recent seasons everywhere
    jobs = [(lg, a, b) for (a, b) in weeks for lg in lgs]
    print(f"{len(lgs)} leagues x {len(weeks)} weeks", flush=True)
    n_ev = 0
    for i, (lg, a, b) in enumerate(jobs):
        d = out / lg
        done = d / f"{a}.done"
        if done.exists():
            continue
        d.mkdir(parents=True, exist_ok=True)
        cursor, page, n = None, 0, 0
        while True:
            params = dict(leagueID=lg, finalized="true", startsAfter=f"{a}T00:00:00Z", startsBefore=f"{b}T00:00:00Z",
                          includeOpenCloseOdds="true", includeAltLines="true", limit=25)
            if cursor:
                params["cursor"] = cursor
            code, body = get("events", **params)
            if code in (401, 403):
                stop(code, body)
            if code != 200:
                print(lg, a, "page", page, code, body[:200], flush=True)
                break
            doc = json.loads(body)
            if doc.get("data"):
                (d / f"{a}_p{page:03d}.json.gz").write_bytes(gzip.compress(body))
                n += len(doc["data"])
            cursor = doc.get("nextCursor")
            page += 1
            if not cursor:
                done.write_text(str(n))
                break
        n_ev += n
        if i % 50 == 0:
            print(f"{i}/{len(jobs)} {lg} {a} events so far {n_ev}", flush=True)
    print("done", n_ev, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-10-01")
    a = ap.parse_args()
    run(dt.date.fromisoformat(a.since))


if __name__ == "__main__":
    main()

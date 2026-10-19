"""Forward SGP price snapshots from OddsBlaze during the trial (research only).

Historical SGP prices are not available (historical.sgp returns empty unless a parlay was priced live), so this
prices a fixed grid of Kalshi-style combos (game ML / spread / total plus the player legs Kalshi lists) for every
event starting within the next 36 h, at each SGP-capable book, every --every minutes until the key stops working.
Each sweep also stores the single-leg prices, so the correlation premium can be measured later and the grid joined
to Kalshi combo prints by team/player/line.

    OB_KEY=... python -I scripts/research/oddsblaze_sgp.py --every 90
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import gzip
import itertools
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

OUT = pathlib.Path(__file__).resolve().parents[2] / "data" / "vendor" / "oddsblaze" / "sgp"
BOOKS = ["draftkings", "fanatics", "caesars", "betmgm"]
LEAGUES = ["nfl", "ncaaf", "nhl", "nba"]
TOTALS = {"Total Points", "Total Goals", "Total Runs"}
PLAYER = {"Player Touchdowns", "Player Receiving Yards", "Player Rushing Yards", "Player Passing Yards", "Player Receptions"}


def american_to_dec(p: str) -> float | None:
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    return 1 + (v / 100 if v > 0 else 100 / -v) if v else None


def fetch(url: str, data: bytes | None = None):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    for a in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** a * 2)
                continue
            try:
                return e.code, json.loads(e.read())
            except ValueError:
                return e.code, {}
        except Exception:
            time.sleep(2 ** a)
    return -1, {}


def combos(odds: list[dict]) -> list[tuple[str, list[dict]]]:
    main = [o for o in odds if o.get("main") and o.get("sgp")]
    ml = [o for o in main if o["market"] == "Moneyline"]
    sp = [o for o in main if o["market"] == "Point Spread"][:2]
    tot = [o for o in main if o["market"] in TOTALS][:2]
    dec = lambda o: american_to_dec(o["price"]) or 99
    td = sorted((o for o in main if o["market"] == "Player Touchdowns" and "Over" in o["name"]), key=dec)[:4]
    rec = sorted((o for o in main if o["market"] == "Player Receiving Yards" and "Over" in o["name"]),
                 key=lambda o: -(o.get("selection") or {}).get("line", 0))[:3]
    out = []
    out += [("ml_tot", [a, b]) for a in ml for b in tot]
    out += [("sp_tot", [a, b]) for a in sp for b in tot]
    out += [("ml_td", [a, b]) for a in ml for b in td]
    out += [("ml_rec", [a, b]) for a in ml for b in rec]
    out += [("td_td", list(p)) for p in itertools.combinations(td, 2)]
    if ml:
        fav = min(ml, key=dec)
        out += [("fav_tot_td", [fav, b, c]) for b in tot for c in td]
    return out


def sweep(key: str, threads: int) -> int:
    now = dt.datetime.now(dt.UTC)
    horizon = now + dt.timedelta(hours=36)
    snap = {"taken": now.isoformat(), "events": []}
    jobs = []
    for book in BOOKS:
        for lg in LEAGUES:
            code, doc = fetch(f"https://odds.oddsblaze.com/?key={key}&sportsbook={book}&league={lg}&main=true")
            if code in (401, 403):
                return code
            for ev in doc.get("events", []) if code == 200 else []:
                start = dt.datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
                if ev.get("live") or not now < start < horizon:
                    continue
                legs = [o for o in ev["odds"] if o["market"] in PLAYER | TOTALS | {"Moneyline", "Point Spread"} and o.get("main")]
                snap["events"].append({"book": book, "league": lg, "event": ev["id"], "teams": ev["teams"], "date": ev["date"],
                                       "legs": [{k: o.get(k) for k in ("id", "market", "name", "price", "selection", "player", "updated")} for o in legs]})
                for kind, cs in combos(ev["odds"]):
                    jobs.append((book, lg, ev["id"], kind, cs))
    print(f"{now:%H:%M} {len(snap['events'])} events, {len(jobs)} combos", flush=True)

    def price(job):
        book, lg, eid, kind, cs = job
        body = json.dumps([c["sgp"] for c in cs]).encode()  # tokens go back exactly as served (still %-encoded)
        code, doc = fetch(f"https://{book}.sgp.oddsblaze.com/?key={key}&price=decimal", body)
        return {"book": book, "league": lg, "event": eid, "kind": kind, "legs": [c["id"] for c in cs],
                "leg_prices": [c["price"] for c in cs], "code": code, "resp": doc}

    # Legs first, then one line per priced combo, so a sweep cut short (trial expiry, kill) still keeps its prices.
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"legs_{now:%Y%m%dT%H%M}.json.gz").write_bytes(gzip.compress(json.dumps(snap).encode()))
    ok = 0
    with gzip.open(OUT / f"sgp_{now:%Y%m%dT%H%M}.jsonl.gz", "wt", encoding="utf-8") as fh, cf.ThreadPoolExecutor(threads) as ex:
        for i, r in enumerate(ex.map(price, jobs)):
            r["taken"] = snap["taken"]
            fh.write(json.dumps(r) + "\n")
            ok += "price" in r["resp"]
            if i % 500 == 0:
                fh.flush()
                print(f"  {i}/{len(jobs)} priced {ok}", flush=True)
    print(f"  priced {ok}/{len(jobs)}", flush=True)
    return 200


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=90, help="minutes between sweeps")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("OB_KEY")
    env = OUT.parents[3] / ".env"
    if not key and env.exists():  # atlas units do not export .env
        key = next((ln.split("=", 1)[1].strip() for ln in env.read_text(encoding="utf-8").splitlines()
                    if ln.startswith("OB_KEY=")), None)
    if not key:
        raise SystemExit("set OB_KEY")
    while True:
        t0 = time.time()
        if sweep(key, a.threads) in (401, 403):
            (OUT.parent / "STOP").touch()  # stops the atlas units instead of relaunching them
            print("key rejected: trial over, wrote STOP", flush=True)
            return
        if a.once:
            return
        time.sleep(max(60.0, a.every * 60 - (time.time() - t0)))


if __name__ == "__main__":
    main()

"""OddsBlaze trial harvest (research only).

Stage `markets`: enumerate every odds id per league/date from historical.markets (archive starts ~2026-04-27).
Stage `history`: fetch the full time series for a prioritised subset of ids from historical.oddsblaze.com.

Resumable: each stage skips work already on disk. Key comes from $OB_KEY.

    OB_KEY=... python -I scripts/research/oddsblaze_harvest.py markets
    OB_KEY=... python -I scripts/research/oddsblaze_harvest.py plan
    OB_KEY=... python -I scripts/research/oddsblaze_harvest.py history --tier 1
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import pathlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from predict_ngin.shared.ratelimit import AimdLimiter  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2] / "data" / "vendor" / "oddsblaze"
MARKETS = ROOT / "markets"
HIST = ROOT / "history"
START = dt.date(2026, 4, 25)

# Duplicate state skins of the same book: same prices, skip.
CLONES = {"Hard Rock Illinois", "Hard Rock Indiana", "Hard Rock Ohio", "BetMGM Michigan", "bet365 Ontario"}
# No historical series on OddsBlaze (we have our own Kalshi/Polymarket data anyway).
NO_HISTORY = {"Kalshi", "Polymarket", "Polymarket US", "bet365"}
GAME_MARKETS = {"Moneyline", "Point Spread", "Total Points", "Total Runs", "Total Goals", "Run Line", "Puck Line",
                "Spread", "Total", "Match Result", "3-Way Moneyline", "Draw No Bet", "Both Teams To Score"}
# Books whose alt spreads/totals are worth the calls; elsewhere only moneylines (tier 1).
LINE_BOOKS = {"DraftKings", "Novig", "ProphetX", "BetOnline", "Bovada", "Fanatics", "Caesars", "BetMGM", "Hard Rock"}
# Fetched before every other book: Hard Rock history exists nowhere else at tick level (added 2026-10-17).
FIRST_BOOKS = {"Hard Rock"}
# Prop books for the Kalshi player-prop comparison (tier 2).
# Narrowed 2026-10-17 to fit the trial: market leader + the two exchanges (3.6M -> ~1.7M prop ids).
PROP_BOOKS = {"DraftKings", "Novig", "ProphetX"}

_lock = threading.Lock()
_stats = collections.Counter()
# The trial key is rate limited (429, no Retry-After or limit headers): AIMD finds the pace. It used to
# run 12 threads flat out with exponential sleeps, and ~40% of requests came back 429.
# Measured 2026-10-17 (5,000-id chunks): successes rise with offered load even as 429s rise, so this
# quota acts like a burst/concurrency limit, not a steady rate. AIMD cutting on every 429: 3.3 ok/s;
# tolerating 10% 429s: 3.6; 35%: 4.1; 12 threads flat out with per-thread exponential back-off on
# 429: 4.8. So the limiter never cuts here (tolerance 1.0); it only honours Retry-After and keeps
# counts, and the threads back off themselves.
_limiter = AimdLimiter(4.0, min_rate=0.5, max_rate=60.0, increase=0.5, tolerance=1.0)


def key() -> str:
    k = os.environ.get("OB_KEY")
    env = ROOT.parents[2] / ".env"
    if not k and env.exists():  # atlas units do not export .env
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OB_KEY="):
                k = line.split("=", 1)[1].strip()
    if not k:
        raise SystemExit("set OB_KEY")
    return k


def trial_over() -> None:
    """The key was rejected: stop the atlas units (their ConditionPathExists=!STOP) instead of relaunching."""
    (ROOT / "STOP").touch()
    print("key rejected: trial over, wrote STOP", flush=True)


def get(url: str, retries: int = 6):
    for a in range(retries):
        _limiter.acquire()
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                _limiter.success()
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            ra = e.headers.get("Retry-After") if e.headers else None
            _limiter.feedback(e.code, retry_after=float(ra) if ra and ra.isdigit() else None)
            if e.code == 404:
                return 404, b""
            if e.code in (429, 500, 502, 503, 504):
                with _lock:
                    _stats[f"http{e.code}"] += 1
                time.sleep(2 ** a * 2)
                continue
            return e.code, b""
        except Exception:
            time.sleep(2 ** a)
    return -1, b""


def leagues() -> list[str]:
    with open(ROOT / "raw" / "status.json", encoding="utf-8") as fh:
        st = json.load(fh)
    # Disabled leagues (e.g. fifa-world-cup) are out of season now but can sit inside the archive window.
    return sorted({lg["id"] for b in st["sportsbooks"] for lg in b["leagues"]})


def stage_markets(threads: int) -> None:
    MARKETS.mkdir(parents=True, exist_ok=True)
    k = key()
    days = [START + dt.timedelta(d) for d in range((dt.date.today() + dt.timedelta(7) - START).days)]
    jobs = [(lg, d) for lg in leagues() for d in days if not (MARKETS / f"{lg}_{d}.json.gz").exists()
            and not (MARKETS / f"{lg}_{d}.404").exists()]
    print(f"{len(jobs)} league-days to fetch")

    def one(job):
        lg, d = job
        code, body = get(f"https://historical.markets.oddsblaze.com/?key={k}&league={lg}&date={d}")
        if code == 200:
            (MARKETS / f"{lg}_{d}.json.gz").write_bytes(gzip.compress(body))
        elif code == 404:
            (MARKETS / f"{lg}_{d}.404").touch()
        with _lock:
            _stats[code] += 1

    with cf.ThreadPoolExecutor(threads) as ex:
        for i, _ in enumerate(ex.map(one, jobs)):
            if i % 500 == 0:
                print(i, dict(_stats), flush=True)
    print(dict(_stats))


def iter_ids():
    for f in sorted(MARKETS.glob("*.json.gz")):
        lg, d = f.name[:-8].rsplit("_", 1)
        doc = json.loads(gzip.decompress(f.read_bytes()))
        for e in doc.get("events", []):
            for oid in e["odds"]:
                yield lg, d, e["id"], oid


SKIP_LEAGUES = {"tt-elite-series", "itf-men", "itf-women", "utr-men", "utr-women", "challenger", "mlb-micro-markets"}
# Prop types Kalshi lists (KXNFLRECYDS, KXNBAPTS, ...).
KALSHI_PROPS = {"Player Receiving Yards", "Player Rushing Yards", "Player Passing Yards", "Player Receptions",
                "Player Touchdowns", "Player Passing Touchdowns", "Player Points", "Player Rebounds", "Player Assists",
                "Player Threes", "Player Steals", "Player Blocks", "Player Hits", "Player Home Runs",
                "Player Strikeouts", "Player Total Bases", "Player Goals", "Player Shots On Goal"}


def _line(sel: str) -> float | None:
    try:
        return float(sel.rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        return None


def select(league: str, ids: list[str]):
    """Yield (oid, tier) for one event's id list. Ids arrive grouped per book/market with the main line first."""
    if league in SKIP_LEAGUES:
        return
    groups: dict[tuple, list[list[str]]] = {}
    for oid in ids:
        p = oid.split("#")
        if p[0] in CLONES or p[0] in NO_HISTORY:
            continue
        groups.setdefault((p[0], p[2], p[4] if len(p) > 4 else ""), []).append(p)
    for (book, market, player), ps in groups.items():
        if market in {"Moneyline", "Match Result", "3-Way Moneyline", "Draw No Bet"}:
            for p in ps:
                yield "#".join(p), 1
        elif market in GAME_MARKETS and book in LINE_BOOKS:
            # main pair = first-listed spread/total line and its mirror
            main = abs(_line(ps[0][3]) or 0)
            for p in ps:
                if abs(_line(p[3]) or -1) == main:
                    yield "#".join(p), 1
        elif market in KALSHI_PROPS and book in PROP_BOOKS:
            sides: dict[float, set] = {}
            for p in ps:
                ln = _line(p[3])
                if ln is not None:
                    sides.setdefault(ln, set()).add("Under" if " Under " in f" {p[3]} " else "Over")
            for p in ps:
                ln = _line(p[3])
                if ln is None:
                    continue
                main_line = len(sides[ln]) == 2
                # Kalshi ladders sit at round thresholds: 0.5/1.5/... for counts, x4.5/x9.5 for yardage
                rung = " Over " in f" {p[3]} " and (ln < 15 or (ln + 0.5) % 5 == 0)
                if main_line or rung:
                    yield "#".join(p), 2


def iter_selected():
    for f in sorted(MARKETS.glob("*.json.gz"), key=lambda f: f.name.rsplit("_", 1)[1], reverse=True):
        lg, d = f.name[:-8].rsplit("_", 1)
        doc = json.loads(gzip.decompress(f.read_bytes()))
        for e in doc.get("events", []):
            for oid, t in select(lg, e["odds"]):
                yield lg, d, oid, t


def stage_plan() -> None:
    by = collections.Counter()
    books = collections.Counter()
    seen = set()
    for lg, d, oid, t in iter_selected():
        if oid in seen:
            continue
        seen.add(oid)
        by[(t, lg)] += 1
        books[(t, oid.split("#")[0])] += 1
    tot = collections.Counter()
    for (t, lg), n in by.items():
        tot[t] += n
    print("unique ids per tier:", dict(tot))
    for t in (1, 2, 3):
        print(f"tier {t} leagues:", sorted(((lg, n) for (tt, lg), n in by.items() if tt == t), key=lambda x: -x[1])[:25])
        print(f"tier {t} books:", sorted(((b, n) for (tt, b), n in books.items() if tt == t), key=lambda x: -x[1])[:25])


def stage_history(tiers: set[int], threads: int, leagues_only: set[str] | None) -> None:
    HIST.mkdir(parents=True, exist_ok=True)
    k = key()
    done = set()
    for f in HIST.glob("*.jsonl.gz"):
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    done.add(json.loads(line)["id"])
        except (EOFError, OSError, ValueError):
            pass  # truncated shard from a killed run; its completed lines are kept
    todo, seen = [], set()
    for lg, d, oid, t in iter_selected():
        if t not in tiers or (leagues_only and lg not in leagues_only) or oid in seen or oid in done:
            continue
        seen.add(oid)
        todo.append((lg, d, oid))
    # Newest first: if the trial ends early we still have the most recent, most Kalshi-overlapping data.
    # FIRST_BOOKS go ahead of everything else (stable sort keeps them newest first).
    todo.sort(key=lambda x: x[1], reverse=True)
    todo.sort(key=lambda x: x[2].split("#")[0] not in FIRST_BOOKS)
    print(f"{len(todo)} ids to fetch ({len(done)} already done)", flush=True)

    shard = HIST / f"hist_t{''.join(map(str, sorted(tiers)))}_{int(time.time())}.jsonl.gz"
    out = gzip.open(shard, "at", encoding="utf-8")
    t0 = time.time()

    def one(job):
        lg, d, oid = job
        code, body = get(f"https://historical.oddsblaze.com/?key={k}&id={urllib.parse.quote(oid)}&time_series&locked&price=decimal")
        with _lock:
            _stats[code] += 1
        if code != 200:
            return
        try:
            doc = json.loads(body)
        except ValueError:
            return
        doc["league"], doc["date"] = lg, d
        with _lock:
            out.write(json.dumps(doc, separators=(",", ":")) + "\n")

    try:
        with cf.ThreadPoolExecutor(threads) as ex:
            # chunks of 5000 so a 1.7M-id tier does not queue 1.7M futures on the Pi
            for i in range(0, len(todo), 5000):
                list(ex.map(one, todo[i:i + 5000]))
                with _lock:
                    out.flush()
                print(f"{i + 5000}/{len(todo)} {(i + 5000) / (time.time() - t0):.1f}/s {dict(_stats)} "
                      f"limiter {_limiter.stats()}", flush=True)
                # atlas user units have no journal: progress goes to a file as well
                (ROOT / "history_progress.json").write_text(json.dumps(
                    {"tiers": sorted(tiers), "done": min(i + 5000, len(todo)), "todo": len(todo),
                     "per_s": round((i + 5000) / (time.time() - t0), 2), "stats": dict(_stats),
                     "limiter": _limiter.stats(), "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
                if _stats[401] + _stats[403] > 50:
                    trial_over()
                    break
    finally:
        out.close()
    print("done", dict(_stats))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["markets", "plan", "history"])
    ap.add_argument("--tier", default="1")
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--leagues", default="")
    a = ap.parse_args()
    if a.stage == "markets":
        stage_markets(a.threads)
    elif a.stage == "plan":
        stage_plan()
    else:
        stage_history({int(t) for t in a.tier.split(",")}, a.threads, set(filter(None, a.leagues.split(","))) or None)


if __name__ == "__main__":
    main()

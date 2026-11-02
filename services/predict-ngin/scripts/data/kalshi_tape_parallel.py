"""Merge and verify a parallel second-root run of scripts/research/tape_refresh.py.

tape_refresh.py is single-instance per root (lock file) and skips any day whose trades_YYYY-MM-DD.parquet
already exists in its root. To split the remaining dates across two processes, run the second one with its
own --root and a later --start; this script then

  prepare (once, with no tape_refresh running) moves day files written with rounded counts (from
          --refetch-from, default 2026-03-10) aside so they are re-fetched with the exact count_fp, and adds
          count_fp = count to earlier days (no fractional trades existed then)
  merge   moves finished day files (and new market-metadata parts) from the second root into the main
          root, so the main process skips those days when it reaches them. Never overwrites a day file
          that already exists in the main root. Safe to run repeatedly (e.g. every 10 minutes).
  watch   runs `merge` every --every seconds until no tape_refresh lock is held in either root
  verify  checks the main root end to end: every UTC day from the cutover to yesterday present, row and
          distinct trade_id counts per day, no trade_id duplicated within or across days, no day file
          empty, price fields in range, events filled, and optionally a live re-fetch of sample hours
          compared row-for-row (--sample N).

Read-only toward Kalshi (GETs only); local file moves only.

    python scripts/data/kalshi_tape_parallel.py prepare
    python scripts/data/kalshi_tape_parallel.py merge
    python scripts/data/kalshi_tape_parallel.py watch --every 600
    python scripts/data/kalshi_tape_parallel.py verify --sample 6
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "data" / "kalshi" / "trades_fixed_ext"
CUTOVER = dt.date(2026, 1, 28)
API = "https://api.elections.kalshi.com/trade-api/v2"


def log(msg: str) -> None:
    print(f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%SZ} {msg}", flush=True)


def extra_roots(main: Path = MAIN) -> list:
    """Every parallel worker root next to the main one (trades_fixed_ext_<tag>)."""
    return sorted(p for p in main.parent.glob(main.name + "_*") if p.is_dir())


def merge(main: Path = MAIN) -> dict:
    """Move finished day files and market parts from every extra root into the main root. Returns counts."""
    out = {"moved_days": 0, "already_in_main": 0, "market_parts_copied": 0}
    for second in extra_roots(main):
        r = _merge_one(main, second)
        for k in out:
            out[k] += r[k]
    log(f"merge {out}")
    return out


def _merge_one(main: Path, second: Path) -> dict:
    moved = skipped = parts = 0
    for f in sorted(second.glob("trades_*.parquet")):  # finished files only (.tmp files are excluded)
        dst = main / f.name
        if dst.exists():
            skipped += 1
            continue
        os.replace(f, dst)  # same volume: atomic rename
        moved += 1
    (main / "markets").mkdir(exist_ok=True)
    for p in sorted((second / "markets").glob("part_*.parquet")):
        dst = main / "markets" / p.name
        if not dst.exists():
            shutil.copy2(p, dst)
            parts += 1
    # carry the second root's per-day stats into the main progress file (informational)
    prog_b = second / "progress.json"
    if prog_b.exists():
        pm_f = main / "progress.json"
        pm = json.loads(pm_f.read_text()) if pm_f.exists() else {"days": {}}
        added = {k: v for k, v in json.loads(prog_b.read_text()).get("days", {}).items() if k not in pm["days"]}
        if added:
            pm.setdefault("parallel_days", {}).update(added)
            tmp = pm_f.with_suffix(".merge.tmp")
            tmp.write_text(json.dumps(pm, indent=1))
            os.replace(tmp, pm_f)
    return {"moved_days": moved, "already_in_main": skipped, "market_parts_copied": parts}


def _locked(root: Path) -> bool:
    """True while a tape_refresh process holds the root's lock (best effort, Windows + POSIX)."""
    lock = root / ".lock"  # tape_refresh.SingleInstance(root / ".lock")
    if not lock.exists():
        return False
    try:
        fh = open(lock, "a+")
    except OSError:
        return True
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


def watch(every: int) -> None:
    seen = False  # don't exit before the workers have taken their locks
    while True:
        merge()
        running = _locked(MAIN) or any(_locked(r) for r in extra_roots())
        seen = seen or running
        if seen and not running:
            log("no tape_refresh running in either root; final merge done")
            merge()
            return
        time.sleep(every)


def prepare(refetch_from: dt.date, main: Path = MAIN) -> dict:
    """Get the main root ready for the count_fp fix (run with no tape_refresh running).

    Day files from `refetch_from` on were written with counts rounded to integers; they and their partial
    hour checkpoints are MOVED (not deleted) to _superseded_rounded_counts/ so tape_refresh re-fetches them.
    Earlier days had no fractional trades (checked live), so count_fp = count is added to them locally.
    """
    import duckdb

    if _locked(main) or any(_locked(r) for r in extra_roots(main)):
        raise SystemExit("a tape_refresh process is still running; stop it before prepare")
    sup = main / "_superseded_rounded_counts"
    (sup / "_staging").mkdir(parents=True, exist_ok=True)
    moved = staged = backfilled = 0
    for f in sorted(main.glob("trades_*.parquet")):
        day = dt.date.fromisoformat(f.stem.replace("trades_", ""))
        if day >= refetch_from:
            os.replace(f, sup / f.name)
            moved += 1
    for d in sorted((main / "_staging").glob("*")):
        if d.is_dir() and dt.date.fromisoformat(d.name) >= refetch_from:
            shutil.move(str(d), str(sup / "_staging" / d.name))
            staged += 1
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'; SET threads=2")
    for f in sorted(main.glob("trades_*.parquet")):
        cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{f.as_posix()}')").fetchall()]
        if "count_fp" in cols:
            continue
        tmp = f.with_suffix(".parquet.tmp")
        con.execute(f"COPY (SELECT *, CAST(count AS DOUBLE) AS count_fp FROM read_parquet('{f.as_posix()}')) "
                    f"TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        n_old = con.execute(f"SELECT count(*) FROM read_parquet('{f.as_posix()}')").fetchone()[0]
        n_new = con.execute(f"SELECT count(*) FROM read_parquet('{tmp.as_posix()}')").fetchone()[0]
        if n_old != n_new:
            raise RuntimeError(f"{f.name}: row count changed in backfill ({n_old} -> {n_new})")
        os.replace(tmp, f)
        backfilled += 1
    out = {"superseded_days": moved, "superseded_staging": staged, "backfilled_count_fp": backfilled}
    log(f"prepare {out}")
    return out


def verify(main: Path = MAIN, sample: int = 0, seed: int = 7) -> dict:
    import duckdb

    today = dt.datetime.now(dt.timezone.utc).date()
    want = [CUTOVER + dt.timedelta(days=i) for i in range((today - CUTOVER).days)]  # through yesterday
    files = {d: main / f"trades_{d.isoformat()}.parquet" for d in want}
    missing = [d.isoformat() for d, f in files.items() if not f.exists()]
    con = duckdb.connect()
    spill = main / "_verify_tmp"
    spill.mkdir(exist_ok=True)
    con.execute(f"SET memory_limit='3GB'; SET threads=4; SET preserve_insertion_order=false; "
                f"SET temp_directory='{spill.as_posix()}'")
    present = [f for f in files.values() if f.exists()]
    per_day, problems = {}, []
    for f in present:
        d = f.stem.replace("trades_", "")
        d0 = int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp())
        q = f"""SELECT count(*), count(DISTINCT trade_id),
                  sum(CASE WHEN timestamp_unix < {d0} OR timestamp_unix >= {d0 + 86400} THEN 1 ELSE 0 END),
                  sum(CASE WHEN yes_price < 0 OR yes_price > 1 OR no_price < 0 OR no_price > 1 THEN 1 ELSE 0 END),
                  sum(CASE WHEN event_ticker IS NULL OR event_ticker = '' THEN 1 ELSE 0 END),
                  sum(CASE WHEN count_fp IS NULL OR count_fp <= 0 THEN 1 ELSE 0 END)
                FROM read_parquet('{f.as_posix()}')"""
        n, nid, off_day, bad_px, no_ev, bad_cnt = con.execute(q).fetchone()
        per_day[d] = {"rows": n, "distinct_ids": nid}
        if n == 0:
            problems.append(f"{d}: empty")
        if nid != n:
            problems.append(f"{d}: {n - nid} duplicate trade_ids within day")
        if off_day:
            problems.append(f"{d}: {off_day} rows outside the UTC day")
        if bad_px:
            problems.append(f"{d}: {bad_px} prices outside [0,1]")
        if no_ev:
            problems.append(f"{d}: {no_ev} rows without event_ticker")
        if bad_cnt:
            problems.append(f"{d}: {bad_cnt} rows with count_fp missing or <= 0")
    glob = (main / "trades_*.parquet").as_posix()
    tot = sum(v["rows"] for v in per_day.values())
    # cross-day duplicates: ids are unique within each day (checked above), so a duplicate must appear
    # in two different day files; GROUP BY spills to disk instead of holding every id in memory
    buckets = 32  # hash-partition the ids so each pass holds ~1/32 of them in memory
    dup_x = sum(con.execute(f"SELECT count(*) FROM (SELECT trade_id FROM read_parquet('{glob}') "
                            f"WHERE hash(trade_id) % {buckets} = {k} GROUP BY trade_id HAVING count(*) > 1)"
                            ).fetchone()[0] for k in range(buckets))
    tot_ids = tot - dup_x
    if dup_x:
        problems.append(f"{dup_x} trade_ids appear more than once across the tape")
    # day-over-day outliers: a day under 25% of its 14-day median is suspicious (partial fetch)
    rows = [per_day[d.isoformat()]["rows"] for d in want if d.isoformat() in per_day]
    days = [d.isoformat() for d in want if d.isoformat() in per_day]
    for i, d in enumerate(days):
        window = sorted(rows[max(0, i - 7):i + 8])
        med = window[len(window) // 2]
        if med and rows[i] < 0.25 * med:
            problems.append(f"{d}: {rows[i]:,} rows vs ~{med:,} neighbouring median (possible partial day)")
    resample = _resample(main, days, sample, seed) if sample else []
    for r in resample:
        if r["only_live"] or r["only_file"]:
            problems.append(f"re-fetch {r['hour']}: {r['only_live']} trades only live, {r['only_file']} only in file")
    out = {"days_expected": len(want), "days_present": len(present), "missing_days": missing,
           "total_rows": tot, "distinct_trade_ids": tot_ids, "resample": resample, "problems": problems,
           "ok": not missing and not problems}
    shutil.rmtree(spill, ignore_errors=True)
    (main / "verify_parallel.json").write_text(json.dumps({**out, "per_day": per_day}, indent=1))
    log(f"verify: {len(present)}/{len(want)} days, {tot:,} rows, {len(missing)} missing, "
        f"{len(problems)} problems -> {'OK' if out['ok'] else 'FAIL'} (details in {main / 'verify_parallel.json'})")
    for p in problems[:30]:
        log("  " + p)
    return out


def _resample(main: Path, days: list, n: int, seed: int) -> list:
    """Re-fetch n random whole hours live and compare trade_id sets with the stored day file."""
    import duckdb
    import requests

    rng = random.Random(seed)
    picks = rng.sample(days, min(n, len(days)))
    s = requests.Session()
    s.headers["User-Agent"] = "predict-ngin-research/tape_verify"
    out = []
    for d in picks:
        h0 = int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp()) + 3600 * rng.randrange(24)
        live, cursor = set(), None
        for ep in ("/historical/trades", "/markets/trades"):
            cursor = None
            while True:
                p = {"limit": 1000, "min_ts": h0, "max_ts": h0 + 3600 - 1}
                if cursor:
                    p["cursor"] = cursor
                r = s.get(API + ep, params=p, timeout=60)
                if r.status_code == 429:
                    time.sleep(2)
                    continue
                r.raise_for_status()
                j = r.json()
                live |= {t["trade_id"] for t in j.get("trades") or []}
                cursor = j.get("cursor")
                time.sleep(0.34)
                if not cursor:
                    break
        f = (main / f"trades_{d}.parquet").as_posix()
        con = duckdb.connect()
        day_ids = {x[0] for x in con.execute(f"SELECT trade_id FROM read_parquet('{f}')").fetchall()}
        hour_ids = {x[0] for x in con.execute(
            f"SELECT trade_id FROM read_parquet('{f}') WHERE timestamp_unix >= {h0} AND timestamp_unix < {h0 + 3600}"
        ).fetchall()}
        # missing data is what matters: a live trade absent from the whole day file. Hour-edge trades can sit
        # in the neighbouring hour, so only_file is reported against the hour but tolerates edge effects.
        res = {"hour": dt.datetime.fromtimestamp(h0, dt.timezone.utc).isoformat(), "live": len(live),
               "file_hour": len(hour_ids), "only_live": len(live - day_ids),
               "only_file": max(0, len(hour_ids - live) - 2)}
        log(f"re-fetch {res}")
        out.append(res)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["prepare", "merge", "watch", "verify"])
    ap.add_argument("--refetch-from", default="2026-03-10", help="prepare: first day to re-fetch")
    ap.add_argument("--every", type=int, default=600, help="watch: seconds between merges")
    ap.add_argument("--sample", type=int, default=0, help="verify: live re-fetch this many random hours")
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(dt.date.fromisoformat(a.refetch_from))
    elif a.cmd == "merge":
        merge()
    elif a.cmd == "watch":
        watch(a.every)
    else:
        return 0 if verify(sample=a.sample)["ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

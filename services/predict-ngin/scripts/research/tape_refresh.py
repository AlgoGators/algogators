"""Extend the full Kalshi trade tape (data/kalshi/trades_fixed) from the API, resumably.

RESEARCH ONLY: unauthenticated public GET requests; never places, cancels or signs orders; reads no keys.

Why the cutover is 2026-01-28 and not the last print (2026-02-11): the HF-derived tape (trades_fixed) is
complete through 2026-01-28 (verified trade-for-trade) but from 2026-01-29 holds only ~1.7k tickers/day (vs 35-160k before), i.e. only the
markets that were in the snapshot. The extension therefore starts at CUTOVER_TS = 2026-01-28 00:00 UTC and
loaders must take trades_fixed for ts < CUTOVER_TS and trades_fixed_ext for ts >= CUTOVER_TS
(src/trading/arbitrage/kalshi_tape.py does this).

Output (data/kalshi/trades_fixed_ext/):
  trades_YYYY-MM-DD.parquet   one UTC day, same 10 columns/types/conventions as trades_fixed, zstd,
                              sorted by timestamp_unix. Written only when all 24 hours are fetched.
  _staging/YYYY-MM-DD/HH.parquet  per-hour checkpoint (atomic tmp+rename); merged + removed at day end.
  markets/part_*.parquet      market metadata + settlement for every non-MVE ticker seen (event_ticker,
                              status, result, close/settlement times, ...). MVE (KXMVE*) tickers are not
                              looked up: their event_ticker is the ticker minus its last '-' segment
                              (100% true in the old tape).
  verify/                     the overlap week re-fetched for the trade-for-trade diff (not a loader input).
  progress.json, fetch.log

Endpoints: /historical/trades for created_ts < GET /historical/cutoff trades_created_ts, /markets/trades
after it; /historical/markets then /markets (tickers=...) for metadata. min_ts is inclusive, max_ts is
exclusive (checked), so hourly windows [h, h+3600) neither overlap nor gap.

Usage (one process only; a lock file enforces it):
  venv/Scripts/python.exe scripts/research/tape_refresh.py run              # verify week, then main range
  venv/Scripts/python.exe scripts/research/tape_refresh.py run --skip-verify
  venv/Scripts/python.exe scripts/research/tape_refresh.py diff             # verify/ vs trades_fixed
  venv/Scripts/python.exe scripts/research/tape_refresh.py settle           # refresh unsettled markets
  venv/Scripts/python.exe scripts/research/tape_refresh.py status
Re-running `run` resumes from the checkpoints.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from predict_ngin.tapes.kalshi import ext_dir, old_dir  # noqa: E402

OLD_DIR = old_dir()
OUT = ext_dir()
API = "https://api.elections.kalshi.com/trade-api/v2"
CUTOVER_TS = 1769558400  # 2026-01-28 00:00:00 UTC
VERIFY_START = "2026-01-21"
VERIFY_DAYS = 7

SCHEMA = pa.schema([
    ("trade_id", pa.string()), ("ticker", pa.string()), ("event_ticker", pa.string()),
    ("yes_price", pa.float64()), ("no_price", pa.float64()), ("count", pa.int64()),
    ("taker_side", pa.string()), ("created_time", pa.string()),
    ("timestamp_unix", pa.int64()), ("timestamp_unix_orig", pa.int64()),
    # Exact contract count (Kalshi count_fp). Fractional contracts started ~2026-03-13; `count` keeps the
    # integer trades_fixed parity (rounded), so sub-0.5 trades show count 0 there. Use count_fp for volume.
    ("count_fp", pa.float64()),
])
MKT_KEEP = ["ticker", "event_ticker", "market_type", "title", "yes_sub_title", "status", "result",
            "open_time", "close_time", "expiration_time", "settlement_ts", "settlement_value_dollars",
            "expiration_value", "volume_fp", "price_level_structure"]
SETTLED = {"settled", "finalized"}


def log(msg: str) -> None:
    line = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%S}Z {msg}"
    print(line, flush=True)
    try:
        with open(OUT / "fetch.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------------------------------ HTTP
class Api:
    """Public GETs with a process-wide rate limit and backoff on 429/5xx/network errors."""

    def __init__(self, rps: float = 3.0):
        self.min_interval = 1.0 / rps
        self.lock = threading.Lock()
        self.next_slot = 0.0
        self.local = threading.local()
        self.n_req = 0
        self.n_429 = 0

    def _session(self) -> requests.Session:
        s = getattr(self.local, "s", None)
        if s is None:
            s = self.local.s = requests.Session()
            s.headers["User-Agent"] = "predict-ngin-research/tape_refresh"
        return s

    def _wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next_slot)
            self.next_slot = slot + self.min_interval
        if slot > now:
            time.sleep(slot - now)

    def get(self, ep: str, params: dict) -> dict:
        delay = 2.0
        for attempt in range(40):
            self._wait()
            try:
                r = self._session().get(API + ep, params=params, timeout=60)
            except requests.RequestException as e:
                log(f"net error {type(e).__name__} on {ep}; sleep {delay:.0f}s")
                time.sleep(delay); delay = min(delay * 2, 300); continue
            self.n_req += 1
            if r.status_code == 429:
                self.n_429 += 1
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else delay
                with self.lock:  # push every thread back, not just this one
                    self.next_slot = max(self.next_slot, time.monotonic() + wait)
                log(f"429 on {ep}; backing off {wait:.0f}s (total 429s {self.n_429})")
                delay = min(delay * 2, 120); continue
            if r.status_code >= 500:
                log(f"HTTP {r.status_code} on {ep}; sleep {delay:.0f}s")
                time.sleep(delay); delay = min(delay * 2, 300); continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"GET {ep} {params} failed after retries")

    def cutoff(self) -> int:
        j = self.get("/historical/cutoff", {})
        return int(pd.Timestamp(j["trades_created_ts"]).timestamp())


# -------------------------------------------------------------------------------------- trades
def fetch_window(api: Api, lo: int, hi: int, cutoff: int) -> list[dict]:
    """All trades with lo <= created second < hi, from the historical and/or live endpoint."""
    segs = []
    if lo < min(hi, cutoff):
        segs.append(("/historical/trades", lo, min(hi, cutoff)))
    if max(lo, cutoff) < hi:
        segs.append(("/markets/trades", max(lo, cutoff), hi))
    out: dict[str, dict] = {}
    for ep, a, b in segs:
        cursor, seen_cursors = "", set()
        while True:
            p = {"limit": 1000, "min_ts": a, "max_ts": b}
            if cursor:
                p["cursor"] = cursor
            j = api.get(ep, p)
            for t in j.get("trades") or []:
                out[t["trade_id"]] = t
            cursor = j.get("cursor") or ""
            if not cursor or not j.get("trades") or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
    return list(out.values())


def _floor_cent(x: pd.Series) -> pd.Series:
    """Truncate a dollar price (<= 4 decimals) to the cent below, float-safe (0.29 stays 0.29)."""
    return ((x * 10000).round().astype("int64") // 100) / 100.0


def to_frame(rows: list[dict]) -> tuple[pd.DataFrame, dict]:
    """API trade dicts -> trades_fixed schema (event_ticker filled later). Returns (df, stats)."""
    if not rows:
        return pd.DataFrame({f.name: pd.Series(dtype=f.type.to_pandas_dtype()) for f in SCHEMA}), {}
    d = pd.DataFrame(rows)
    ct = pd.to_datetime(d["created_time"], utc=True, format="ISO8601")
    cnt = pd.to_numeric(d["count_fp"], errors="coerce") if "count_fp" in d else d["count"].astype(float)
    ts = ((ct - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).astype("int64")
    yes = pd.to_numeric(d["yes_price_dollars"], errors="coerce") if "yes_price_dollars" in d \
        else d["yes_price"] / 100.0
    no = pd.to_numeric(d["no_price_dollars"], errors="coerce") if "no_price_dollars" in d \
        else d["no_price"] / 100.0
    df = pd.DataFrame({
        "trade_id": d["trade_id"].astype(str), "ticker": d["ticker"].astype(str), "event_ticker": None,
        # Parity with trades_fixed (HF source stored integer cents): sub-cent prices are truncated DOWN
        # to the cent on both legs (checked trade-for-trade: old = floor(new, 1c) on 100% of them).
        "yes_price": _floor_cent(yes), "no_price": _floor_cent(no),
        "count": cnt.round().astype("int64"), "taker_side": d["taker_side"].astype(str),
        "created_time": ct.dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "timestamp_unix": ts, "timestamp_unix_orig": ts,
        "count_fp": cnt.astype("float64"),
    })
    st = {
        "rows": len(df),
        "frac_count": int((cnt != cnt.round()).sum()),
        "subcent_price": int(((yes * 100).round(6) % 1 != 0).sum()),
        "block": int(d.get("is_block_trade", pd.Series(False, index=d.index)).fillna(False).astype(bool).sum()),
        "side_mismatch": int((d["taker_side"] != d.get("taker_outcome_side", d["taker_side"])).sum()),
    }
    return df, st


def write_atomic(table: pa.Table, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def fetch_hour(api: Api, hour_ts: int, stage: Path, cutoff: int) -> dict:
    f = stage / f"{dt.datetime.fromtimestamp(hour_ts, dt.timezone.utc):%H}.parquet"
    if f.exists():
        return {"skipped": 1}
    rows = fetch_window(api, hour_ts, hour_ts + 3600, cutoff)
    df, st = to_frame(rows)
    # max_ts is inclusive on the API side, so a trade stamped exactly at the next hour's first second can come
    # back here too. That hour fetches it (min_ts = its start), so drop it here; anything else outside is a bug.
    edge = df.timestamp_unix == hour_ts + 3600
    if edge.any():
        df = df[~edge].reset_index(drop=True)
        st["rows"] = len(df)
        st["edge_dropped"] = int(edge.sum())
    bad = df[(df.timestamp_unix < hour_ts) | (df.timestamp_unix >= hour_ts + 3600)]
    if len(bad):
        raise RuntimeError(f"{len(bad)} trades outside window {hour_ts}")
    write_atomic(pa.Table.from_pandas(df, schema=SCHEMA, preserve_index=False), f)
    (stage / f.name.replace(".parquet", ".json")).write_text(json.dumps(st))
    return st


# ------------------------------------------------------------------------------------- markets
class Markets:
    def __init__(self, root: Path):
        self.dir = root / "markets"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.known: dict[str, str] = {}
        self.missing: set[str] = set()
        for f in sorted(self.dir.glob("part_*.parquet")):
            m = pd.read_parquet(f, columns=["ticker", "event_ticker"])
            self.known.update(zip(m.ticker, m.event_ticker))
        mf = self.dir / "not_found.txt"
        if mf.exists():
            self.missing = set(mf.read_text().split())

    @staticmethod
    def chunks(tickers, max_chars=5500, max_n=200):
        cur, n = [], 0
        for t in tickers:
            if cur and (n + len(t) + 1 > max_chars or len(cur) >= max_n):
                yield cur
                cur, n = [], 0
            cur.append(t)
            n += len(t) + 1
        if cur:
            yield cur

    def lookup(self, api: Api, tickers, force: bool = False) -> int:
        todo = sorted(t for t in set(tickers) if force or (t not in self.known and t not in self.missing))
        if not todo:
            return 0
        got: list[dict] = []
        for ep in ("/historical/markets", "/markets"):
            rest = [t for t in todo if t not in {g["ticker"] for g in got}]
            for ch in self.chunks(rest):
                j = api.get(ep, {"tickers": ",".join(ch), "limit": 1000})
                for m in j.get("markets") or []:
                    rec = {c: (json.dumps(m.get(c)) if isinstance(m.get(c), (dict, list)) else m.get(c))
                           for c in MKT_KEEP}
                    rec["source"] = ep
                    got.append(rec)
        now = int(time.time())
        if got:
            df = pd.DataFrame(got).drop_duplicates("ticker", keep="first")
            df = df.astype({c: "string" for c in MKT_KEEP + ["source"]})
            df["fetched_at"] = now
            df.to_parquet(self.dir / f"part_{now}_{len(self.known)}.parquet", index=False)
            self.known.update(zip(df.ticker, df.event_ticker))
        miss = [t for t in todo if t not in self.known]
        if miss:
            self.missing.update(miss)
            with open(self.dir / "not_found.txt", "a") as fh:
                fh.write("\n".join(miss) + "\n")
        return len(got)

    def load(self) -> pd.DataFrame:
        fs = sorted(self.dir.glob("part_*.parquet"))
        if not fs:
            return pd.DataFrame(columns=MKT_KEEP)
        m = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
        return m.sort_values("fetched_at").drop_duplicates("ticker", keep="last")


def is_mve(t: str) -> bool:
    return t.startswith("KXMVE")


# ------------------------------------------------------------------------------------------ day
def day_bounds(day: dt.date) -> int:
    return int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp())


def finalize_day(day: dt.date, root: Path, markets: Markets, api: Api) -> dict:
    import duckdb

    stage = root / "_staging" / day.isoformat()
    files = sorted(stage.glob("*.parquet"))
    assert len(files) == 24, f"{day}: {len(files)} staged hours"
    con = duckdb.connect()
    con.execute("SET memory_limit='1500MB'; SET threads=2; SET preserve_insertion_order=false")
    tmpd = root / "_duck_tmp"
    tmpd.mkdir(exist_ok=True)
    con.execute(f"SET temp_directory='{tmpd.as_posix()}'")
    glob = (stage / "*.parquet").as_posix()
    tickers = [r[0] for r in con.execute(f"SELECT DISTINCT ticker FROM read_parquet('{glob}')").fetchall()]
    nm = markets.lookup(api, [t for t in tickers if not is_mve(t)])
    emap = pd.DataFrame({"ticker": tickers, "ev": [markets.known.get(t) for t in tickers]})
    con.register("emap", emap)
    dst = root / f"trades_{day.isoformat()}.parquet"
    tmp = dst.with_suffix(".parquet.tmp")
    cols = ", ".join(f"t.{f.name}" if f.name != "event_ticker" else
                     "coalesce(e.ev, regexp_replace(t.ticker, '-[^-]*$', ''))::VARCHAR AS event_ticker"
                     for f in SCHEMA)
    con.execute(f"""COPY (SELECT {cols} FROM read_parquet('{glob}') t LEFT JOIN emap e USING (ticker)
                          ORDER BY t.timestamp_unix, t.created_time, t.trade_id)
                    TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    n, nid = con.execute(f"SELECT count(*), count(DISTINCT trade_id) FROM read_parquet('{tmp.as_posix()}')").fetchone()
    con.close()
    if n != nid:
        raise RuntimeError(f"{day}: duplicate trade_ids ({n} rows, {nid} ids)")
    os.replace(tmp, dst)
    st = {"day": day.isoformat(), "rows": n, "tickers": len(tickers), "new_markets": nm,
          "event_fallback_nonmve": int(sum(1 for t in tickers if not is_mve(t) and t not in markets.known)),
          "bytes": dst.stat().st_size}
    for k in ("frac_count", "subcent_price", "block", "side_mismatch"):
        st[k] = sum(json.loads(j.read_text()).get(k, 0) for j in stage.glob("*.json"))
    shutil.rmtree(stage)
    return st


def run_range(api: Api, root: Path, start: dt.date, end_day: dt.date | None, workers: int, tag: str,
              markets: Markets) -> None:
    prog_f = root / "progress.json"
    prog = json.loads(prog_f.read_text()) if prog_f.exists() else {"days": {}}
    day = start
    while True:
        now_hour = int(time.time()) // 3600 * 3600
        d0 = day_bounds(day)
        if (end_day and day >= end_day) or d0 >= now_hour:
            break
        dst = root / f"trades_{day.isoformat()}.parquet"
        if dst.exists():
            day += dt.timedelta(days=1)
            continue
        stage = root / "_staging" / day.isoformat()
        stage.mkdir(parents=True, exist_ok=True)
        cutoff = api.cutoff()
        hours = [h for h in range(d0, d0 + 86400, 3600) if h < now_hour]
        t0, r0 = time.time(), api.n_req
        with ThreadPoolExecutor(max_workers=workers) as ex:
            res = list(ex.map(lambda h: fetch_hour(api, h, stage, cutoff), hours))
        rows = sum(r.get("rows", 0) for r in res)
        if len(hours) < 24:
            log(f"[{tag}] {day} partial: {len(hours)}/24 hours staged (day not over); stopping")
            break
        st = finalize_day(day, root, markets, api)
        st["secs"] = round(time.time() - t0)
        st["requests"] = api.n_req - r0
        prog["days"][day.isoformat()] = st
        prog["updated"] = dt.datetime.now(dt.timezone.utc).isoformat()
        prog["n_429"] = api.n_429
        tmpj = prog_f.with_suffix(".tmp")
        tmpj.write_text(json.dumps(prog, indent=1))
        os.replace(tmpj, prog_f)
        log(f"[{tag}] {day} rows={st['rows']:,} (fetched now {rows:,}) tickers={st['tickers']:,} "
            f"new_mkts={st['new_markets']} req={st['requests']} {st['secs']}s 429s={api.n_429}")
        day += dt.timedelta(days=1)


# ---------------------------------------------------------------------------------------- diff
def diff(root: Path, vdir: Path | None = None) -> dict:
    import duckdb

    vdir = vdir or (root / "verify")
    con = duckdb.connect()
    con.execute("SET memory_limit='2500MB'; SET threads=4")
    out = {}
    for f in sorted(vdir.glob("trades_*.parquet")):
        day = f.stem.replace("trades_", "")
        d0 = day_bounds(dt.date.fromisoformat(day))
        old = f"(SELECT * FROM read_parquet('{OLD_DIR.as_posix()}/trades_*.parquet') " \
              f"WHERE timestamp_unix >= {d0} AND timestamp_unix < {d0 + 86400})"
        new = f"read_parquet('{f.as_posix()}')"
        q = f"""
          WITH o AS {old}, n AS (SELECT * FROM {new})
          SELECT
            (SELECT count(*) FROM o) n_old, (SELECT count(*) FROM n) n_new,
            (SELECT count(*) FROM o JOIN n USING (trade_id)) n_matched,
            (SELECT count(*) FROM o ANTI JOIN n USING (trade_id)) only_old,
            (SELECT count(*) FROM n ANTI JOIN o USING (trade_id)) only_new,
            (SELECT count(*) FROM o JOIN n USING (trade_id) WHERE o.ticker <> n.ticker) d_ticker,
            (SELECT count(*) FROM o JOIN n USING (trade_id) WHERE o.event_ticker <> n.event_ticker) d_event,
            (SELECT count(*) FROM o JOIN n USING (trade_id) WHERE abs(o.yes_price - n.yes_price) > 1e-9) d_yes,
            (SELECT count(*) FROM o JOIN n USING (trade_id) WHERE abs(o.no_price - n.no_price) > 1e-9) d_no,
            (SELECT count(*) FROM o JOIN n USING (trade_id) WHERE o.count <> n.count) d_count,
            (SELECT count(*) FROM o JOIN n USING (trade_id) WHERE o.taker_side <> n.taker_side) d_side,
            (SELECT count(*) FROM o JOIN n USING (trade_id) WHERE o.timestamp_unix <> n.timestamp_unix) d_ts,
            (SELECT count(*) FROM o JOIN n USING (trade_id) WHERE o.created_time <> n.created_time) d_ctime_str,
            (SELECT count(*) FROM o JOIN n USING (trade_id)
              WHERE o.created_time::TIMESTAMPTZ <> n.created_time::TIMESTAMPTZ) d_ctime_instant,
            (SELECT count(*) FROM o JOIN n USING (trade_id) WHERE abs(o.yes_price + o.no_price - 1) > 1e-9) old_sum_ne1,
            (SELECT coalesce(sum(count), 0) FROM o) vol_old, (SELECT coalesce(sum(count), 0) FROM n) vol_new
        """
        if not con.execute(f"SELECT count(*) FROM {old}").fetchone()[0]:
            continue
        r = con.execute(q).df().iloc[0].to_dict()
        r = {k: int(v) for k, v in r.items()}
        ex = con.execute(f"""WITH o AS {old}, n AS (SELECT * FROM {new})
            SELECT 'only_old' w, ticker, count(*) c FROM o ANTI JOIN n USING (trade_id) GROUP BY 2
            UNION ALL SELECT 'only_new', ticker, count(*) FROM n ANTI JOIN o USING (trade_id) GROUP BY 2
            ORDER BY c DESC LIMIT 6""").fetchall()
        r["top_unmatched"] = [list(x) for x in ex]
        ev = con.execute(f"""WITH o AS {old}, n AS (SELECT * FROM {new})
            SELECT o.ticker, o.event_ticker, n.event_ticker FROM o JOIN n USING (trade_id)
            WHERE o.event_ticker <> n.event_ticker GROUP BY ALL LIMIT 5""").fetchall()
        r["event_diff_examples"] = [list(x) for x in ev]
        out[day] = r
        print(day, json.dumps(r), flush=True)
    (vdir / "diff.json").write_text(json.dumps(out, indent=1))
    return out


# ------------------------------------------------------------------------------------- locking
class SingleInstance:
    def __init__(self, path: Path):
        self.fh = open(path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit(f"another tape_refresh is running (lock {path})")


def main() -> int:
    global OUT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "diff", "settle", "status"])
    ap.add_argument("--rps", type=float, default=3.0, help="max requests/s for this process (cap 4)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--skip-verify", action="store_true")
    ap.add_argument("--start", default=None, help="main range start date (default cutover 2026-01-28)")
    ap.add_argument("--end", default=None, help="exclusive end date (default: now)")
    ap.add_argument("--root", default=str(OUT))
    ap.add_argument("--diff-dir", default=None, help="diff this dir's day files instead of <root>/verify")
    a = ap.parse_args()
    root = Path(a.root)
    root.mkdir(parents=True, exist_ok=True)
    OUT = root
    if a.cmd == "diff":
        diff(root, Path(a.diff_dir) if a.diff_dir else None)
        return 0
    if a.cmd == "status":
        p = json.loads((root / "progress.json").read_text()) if (root / "progress.json").exists() else {}
        days = sorted(p.get("days", {}))
        print(f"days done: {len(days)} ({days[0] if days else '-'} .. {days[-1] if days else '-'}); "
              f"rows {sum(v['rows'] for v in p.get('days', {}).values()):,}")
        return 0
    lock = SingleInstance(root / ".lock")  # noqa: F841  (held for process lifetime)
    api = Api(min(a.rps, 4.0))
    if a.cmd == "settle":
        mk = Markets(root)
        m = mk.load()
        todo = m[~m.status.isin(SETTLED) | m.result.isna() | (m.result == "")].ticker.tolist()
        log(f"settle: refreshing {len(todo)} unsettled markets")
        mk.lookup(api, todo, force=True)
        return 0
    log(f"run start rps={a.rps} workers={a.workers} root={root}")
    mk = Markets(root)
    if not a.skip_verify:
        vroot = root / "verify"
        vroot.mkdir(exist_ok=True)
        v0 = dt.date.fromisoformat(VERIFY_START)
        run_range(api, vroot, v0, v0 + dt.timedelta(days=VERIFY_DAYS), a.workers, "verify", mk)
    start = dt.date.fromisoformat(a.start) if a.start else dt.datetime.fromtimestamp(CUTOVER_TS, dt.timezone.utc).date()
    end = dt.date.fromisoformat(a.end) if a.end else None
    run_range(api, root, start, end, a.workers, "main", mk)
    log(f"run finished; requests={api.n_req} 429s={api.n_429}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

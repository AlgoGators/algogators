#!/usr/bin/env python3
"""Extend the Polymarket fill tape past the Feb-2026 HF snapshot (append-only; originals untouched).

Output: <data>/polymarket_clean/trades_ext/  (read it with predict_ngin.tapes.polymarket.clean_trades_sql)
  hf/part-rgNNNNN.parquet   one file per row group of the HF 2026-10-05 trades.parquet holding rows with
                            timestamp >= EXT_START_TS (2025-12-23; 7-day overlap with the old tape for seam checks),
                            or every row with --full
  hf/_manifest/*.json       per-part row counts (resume marker: a part is done when its manifest exists)
  coverage.json             contiguous coverage end (exclusive) used by tape_end_ts()
  build.log                 progress log

Source: HuggingFace dataset SII-WANGZJ/Polymarket_data, pinned revision REV (release 2026-10-05, data through
2026-10-04 23:59:59 UTC). Row groups are read by HTTP range request, so only the needed ~40 GB is transferred.
Every row is YES-normalised by the pmfix helper (normalize_trades, schema 'onchain') with a TokenMap built from
the 2026-10-05 markets.parquet + the old markets.parquet + pmxt Gamma markets (the last two only if present).

New setup: the Feb-2026 release behind the old tape (polymarket_clean/trades/) is no longer on the Hub, so build
the whole history from the 2026-10-05 release with --full (~46 GB read; pre-cutover ts_chain comes from the
committed Polygon block anchors, because the release's own timestamps before 2025-12-30 are off by minutes to
hours). coverage.json then records start_ts 0 and the tape reader serves every ext row.

Commands (repo root):
  uv run python scripts/data/download_hf_polymarket.py   # markets.parquet (needed first)
  uv run python scripts/data/polymarket_extend.py hf --full [--workers 3]  # new setup
  uv run python scripts/data/polymarket_extend.py hf       # extend an existing old tape
  uv run python scripts/data/polymarket_extend.py tail                           # days after the release (RPC)
  uv run python scripts/data/polymarket_extend.py status
  uv run python scripts/data/polymarket_extend.py seam               # seam checks -> <data>/vendor/pmext/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from predict_ngin.shared.paths import data_root  # noqa: E402
from predict_ngin.shared.ratelimit import AimdLimiter, TokenBucket  # noqa: E402
from predict_ngin.tapes.polymarket import HF_REPO as REPO, HF_REV as REV, hf_snapshot_dir  # noqa: E402
from src.data_quality.polymarket_tape import (  # noqa: E402
    CUTOVER_TS, EXT_DIR, EXT_START_TS, OLD_DIR, tape_end_ts, to_ext_frame, utc_day,
)

HF_END_TS = 1791158400  # 2026-10-05 00:00:00 UTC (exclusive end of the release)
HF_PATH = f"datasets/{REPO}@{REV}/trades.parquet"
NEW_MARKETS = hf_snapshot_dir() / "markets.parquet"
HF_DIR = EXT_DIR / "hf"
MAN_DIR = HF_DIR / "_manifest"
LOG = EXT_DIR / "build.log"
LOCK = EXT_DIR / ".lock"
VEN = data_root() / "vendor" / "pmext"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def token_map_paths() -> tuple:
    from src.data_quality.polymarket_tokens import DEFAULT_METADATA

    return (str(NEW_MARKETS),) + tuple(str(p) for p in DEFAULT_METADATA)


def open_hf():
    from huggingface_hub import HfFileSystem

    return pq.ParquetFile(HfFileSystem().open(HF_PATH, block_size=8 << 20), pre_buffer=True)


def plan_row_groups(md, full: bool = False) -> list[int]:
    ti = md.schema.to_arrow_schema().names.index("timestamp")
    start = 0 if full else EXT_START_TS
    return [i for i in range(md.num_row_groups) if md.row_group(i).column(ti).statistics.max >= start]


# --------------------------------------------------------------------------------------------- worker

_TM = None
_ANCHORS = None  # Polygon block anchors, loaded only for --full (pre-cutover ts_chain)


def _init_worker(full: bool = False):
    global _TM, _ANCHORS
    from src.data_quality.polymarket_tokens import _load_default_cached

    _TM = _load_default_cached(token_map_paths())
    if full:
        from src.data_quality.polygon_blocks import load_anchors

        _ANCHORS = load_anchors()


def _do_rg(rg: int, full: bool = False) -> dict:
    man = MAN_DIR / f"part-rg{rg:05d}.json"
    if man.exists():
        return json.loads(man.read_text())
    t0 = time.time()
    for attempt in range(8):
        try:
            tbl = open_hf().read_row_group(rg)
            break
        except Exception as e:  # noqa: BLE001
            if attempt == 7:
                raise
            time.sleep(min(300, 5 * 2 ** attempt))
            err = repr(e)[:200]
            print(f"rg {rg} retry {attempt + 1}: {err}", flush=True)
    t_dl = time.time() - t0
    df = tbl.to_pandas()
    n_in = len(df)
    df = df[(df["timestamp"] >= (0 if full else EXT_START_TS)) & (df["timestamp"] < HF_END_TS)]
    out = to_ext_frame(df, _TM, source="hf_20261005", anchors=_ANCHORS) if len(df) else None
    dst = HF_DIR / f"part-rg{rg:05d}.parquet"
    rec = {"rg": rg, "rows_in": n_in, "rows_kept": int(len(df)), "secs_dl": round(t_dl, 1)}
    if out is not None and len(out):
        tmp = dst.with_suffix(".parquet.tmp")
        pq.write_table(pa.Table.from_pandas(out, preserve_index=False), tmp, compression="zstd",
                       row_group_size=250_000)
        os.replace(tmp, dst)
        rec.update(min_ts=int(out.ts_local.min()), max_ts=int(out.ts_local.max()),
                   unknown_token=int(out.yes_price.isna().sum()), token_conflict=int(out.token_conflict.sum()),
                   taker_order_rows=int(out.is_taker_order_row.sum()), usd=float(out.usd_amount.sum()),
                   bytes=dst.stat().st_size)
    rec["secs"] = round(time.time() - t0, 1)
    tmp = man.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, man)
    return rec


# --------------------------------------------------------------------------------------------- commands


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> None:
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip())
        except ValueError:
            pid = -1
        if pid > 0 and _pid_alive(pid):
            raise SystemExit(f"another builder is running (pid {pid}); lock {LOCK}")
    LOCK.write_text(str(os.getpid()))


def write_coverage() -> dict:
    """Contiguous coverage: HF part complete -> end = HF_END_TS; then extend by consecutive tail days.
    ``start_ts`` is where the tape reader starts serving ext rows: 0 for a --full build, else CUTOVER_TS."""
    plan = json.loads((EXT_DIR / "hf_plan.json").read_text()) if (EXT_DIR / "hf_plan.json").exists() else {}
    md_rgs = plan.get("row_groups")
    done = {int(p.stem[len("part-rg"):]) for p in MAN_DIR.glob("part-rg*.json")} if MAN_DIR.exists() else set()
    cov = {"start_ts": 0 if plan.get("full") else CUTOVER_TS, "end_ts": None,
           "hf_complete": bool(md_rgs) and set(md_rgs) <= done,
           "hf_parts_done": len(done), "hf_parts_total": len(md_rgs or [])}
    if cov["hf_complete"]:
        end = HF_END_TS
        tail = EXT_DIR / "tail"
        while (tail / f"fills_{utc_day(end)}.parquet").exists():
            end += 86400
        cov["end_ts"] = end
        cov["end_day_exclusive"] = utc_day(end)
    tmp = EXT_DIR / "coverage.json.tmp"
    tmp.write_text(json.dumps(cov, indent=1))
    os.replace(tmp, EXT_DIR / "coverage.json")
    return cov


def cmd_hf(workers: int, full: bool = False) -> None:
    if not NEW_MARKETS.exists():
        raise SystemExit(f"{NEW_MARKETS} missing: run\n"
                         "  uv run python scripts/data/download_hf_polymarket.py")
    plan_file = EXT_DIR / "hf_plan.json"
    if plan_file.exists() and bool(json.loads(plan_file.read_text()).get("full")) != full:
        # parts already written in the other mode cover a different time range; never mix them
        raise SystemExit(f"{HF_DIR} was started {'with' if not full else 'without'} --full; "
                         f"re-run in that mode, or move {EXT_DIR} aside to start over")
    acquire_lock()
    try:
        HF_DIR.mkdir(parents=True, exist_ok=True)
        MAN_DIR.mkdir(parents=True, exist_ok=True)
        md = open_hf().metadata
        rgs = plan_row_groups(md, full)
        plan_file.write_text(json.dumps({"rev": REV, "row_groups": rgs, "num_rows": md.num_rows, "full": full}))
        todo = [r for r in rgs if not (MAN_DIR / f"part-rg{r:05d}.json").exists()]
        log(f"hf: {len(rgs)} row groups planned, {len(todo)} to do, workers={workers}, full={full}")
        t0, done_rows, n = time.time(), 0, 0
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(full,)) as ex:
            futs = {ex.submit(_do_rg, r, full): r for r in todo}
            for fut in as_completed(futs):
                r = futs[fut]
                try:
                    rec = fut.result()
                except Exception:  # noqa: BLE001
                    log(f"hf: rg {r} FAILED\n{traceback.format_exc()}")
                    continue
                n += 1
                done_rows += rec["rows_kept"]
                if n % 10 == 0 or n == len(todo):
                    el = time.time() - t0
                    log(f"hf: {n}/{len(todo)} rgs, {done_rows:,} rows, {el / 60:.1f} min, "
                        f"eta {el / n * (len(todo) - n) / 60:.0f} min (last rg {r}: {rec})")
        cov = write_coverage()
        log(f"hf: finished; coverage {cov}")
    finally:
        if LOCK.exists() and LOCK.read_text().strip() == str(os.getpid()):
            LOCK.unlink()


# --------------------------------------------------------------------------------------------- tail (RPC)

RPCS = ["https://polygon.drpc.org", "https://polygon-bor-rpc.publicnode.com"]
TAIL_DIR = EXT_DIR / "tail"
TAIL_LOCK = EXT_DIR / ".tail.lock"
GAMMA_TAIL = hf_snapshot_dir() / "markets_gamma_tail.parquet"
CHUNK_BLOCKS = 1000  # staging checkpoint granularity
REQ_BLOCKS = 50  # blocks per eth_getLogs request


# Public Polygon nodes publish no limit (they answer 429): AIMD per node finds it.
RPC_RATE, RPC_MAX_RATE = 2.0, 30.0
# Gamma /markets publishes 300 per 10 s per IP: a fixed pace at 90% of it, nothing to probe.
GAMMA_RATE = 27.0


def _throttled(err) -> bool:
    """A JSON-RPC error that means "slow down" (public nodes word it differently)."""
    m = str(err).lower()
    return any(k in m for k in ("rate limit", "too many", "exceeded", "capacity", "throttl"))


class Rpc:
    def __init__(self, urls=RPCS):
        import threading

        self.urls, self.i, self.lock = list(urls), 0, threading.Lock()
        self.limiters = {u: AimdLimiter(RPC_RATE, min_rate=0.5, max_rate=RPC_MAX_RATE) for u in self.urls}
        self.ts_cache: dict[int, int] = {}

    def stats(self) -> dict:
        return {u.split("/")[2]: lim.stats() for u, lim in self.limiters.items()}

    def call(self, method: str, params: list):
        import urllib.error
        import urllib.request

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last = None
        for attempt in range(12):
            with self.lock:
                url = self.urls[self.i % len(self.urls)]
                self.i += 1
            lim = self.limiters[url]
            lim.acquire()
            try:
                req = urllib.request.Request(url, data=body, headers={"content-type": "application/json",
                                                                      "user-agent": "predict-ngin-research/1.0"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    res = json.loads(r.read())
                if "error" in res:
                    if _throttled(res["error"]):
                        lim.throttle()
                    raise RuntimeError(f"{url}: {res['error']}")
                lim.success()
                return res["result"]
            except urllib.error.HTTPError as e:
                last = e
                lim.feedback(e.code, retry_after=float(e.headers.get("Retry-After") or 0) or None)
                time.sleep(min(60, 1.5 * 2 ** min(attempt, 5)))
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(min(60, 1.5 * 2 ** min(attempt, 5)))
        raise RuntimeError(f"rpc {method} failed after retries: {last!r}")

    def block_ts(self, n: int) -> int:
        if n not in self.ts_cache:
            self.ts_cache[n] = int(self.call("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)
        return self.ts_cache[n]

    def head(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)


def _fetch_range(rpc: Rpc, a: int, b: int) -> list:
    """All OrderFilled logs in blocks [a, b] (inclusive), splitting the request if a node refuses it."""
    from src.data_quality.polymarket_onchain import CONTRACTS, V1_TOPIC, V2_TOPIC

    try:
        return rpc.call("eth_getLogs", [{"fromBlock": hex(a), "toBlock": hex(b), "address": list(CONTRACTS),
                                         "topics": [[V1_TOPIC, V2_TOPIC]]}])
    except RuntimeError:
        if a == b:
            raise
        m = (a + b) // 2
        return _fetch_range(rpc, a, m) + _fetch_range(rpc, m + 1, b)


def _fetch_chunk(rpc: Rpc, a: int, b: int, dst: Path) -> int:
    from src.data_quality.polymarket_onchain import decode_logs

    logs = []
    for s in range(a, b, REQ_BLOCKS):
        logs += _fetch_range(rpc, s, min(s + REQ_BLOCKS, b) - 1)
    df = decode_logs(logs)
    for c in ("maker_amount_filled", "taker_amount_filled", "fee"):
        df[c] = df[c].map(str)  # uint256 kept exact as decimal strings
    tmp = dst.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, dst)
    return len(df)


def _gamma_lookup(token_ids: list[str]) -> pd.DataFrame:
    import urllib.error
    import urllib.request

    from src.data_quality.polymarket_onchain import gamma_markets_to_hf

    from concurrent.futures import ThreadPoolExecutor

    lim = TokenBucket(GAMMA_RATE, 1.0)
    n429 = 0

    def one(url: str) -> list:
        for attempt in range(6):
            nonlocal n429
            lim.acquire()
            try:
                req = urllib.request.Request(url, headers={"user-agent": "predict-ngin-research/1.0"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                n429 += e.code == 429
                time.sleep(2 * 2 ** attempt)
            except Exception:  # noqa: BLE001
                time.sleep(2 * 2 ** attempt)
        return []

    urls = []
    for i in range(0, len(token_ids), 40):
        q = "&".join("clob_token_ids=" + t for t in token_ids[i:i + 40])
        urls += [f"https://gamma-api.polymarket.com/markets?limit=100&closed={c}&{q}" for c in ("true", "false")]
    with ThreadPoolExecutor(8) as ex:
        rows = [m for page in ex.map(one, urls) for m in page]
    log(f"gamma lookup: {len(urls)} requests at {GAMMA_RATE}/s, {n429} answered 429")
    return gamma_markets_to_hf(rows)


def _tail_markets(of: pd.DataFrame) -> pd.DataFrame:
    """Token lookup frame from HF markets + cached Gamma rows, fetching Gamma for any unknown token in ``of``."""
    from src.data_quality.polymarket_onchain import markets_token_frame

    hf = pd.read_parquet(NEW_MARKETS, columns=["id", "condition_id", "token1", "token2", "event_id"])
    g = pd.read_parquet(GAMMA_TAIL) if GAMMA_TAIL.exists() else pd.DataFrame()
    fr = markets_token_frame(pd.concat([hf, g[hf.columns]] if len(g) else [hf], ignore_index=True))
    toks = set(np.where(of["maker_asset_id"].eq("0"), of["taker_asset_id"], of["maker_asset_id"]))
    missing = sorted(toks - set(fr.token_id))
    if missing:
        new = _gamma_lookup(missing)
        log(f"tail: {len(missing)} tokens unknown -> gamma returned {len(new)} markets")
        if len(new):
            g = pd.concat([g, new], ignore_index=True).drop_duplicates("id", keep="last") if len(g) else new
            tmp = GAMMA_TAIL.with_suffix(".parquet.tmp")
            g.to_parquet(tmp, index=False)
            os.replace(tmp, GAMMA_TAIL)
            fr = markets_token_frame(pd.concat([hf, g[hf.columns]], ignore_index=True))
    return fr


def fetch_orderfilled(rpc: Rpc, day: str, out_dir: Path, workers: int = 4) -> dict:
    """One UTC day of raw OrderFilled logs -> out_dir/raw/orderfilled_{day}.parquet (amounts as decimal strings).
    Streams the chunk files into one parquet, so memory stays small (atlas runs this part)."""
    import datetime as dt
    import shutil
    from concurrent.futures import ThreadPoolExecutor

    import pyarrow.compute as pc

    from src.data_quality.polymarket_onchain import first_block_at_or_after

    t0 = time.time()
    d0 = int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp())
    head = rpc.head()
    lo_guess = 94_000_000
    b0 = first_block_at_or_after(d0, rpc.block_ts, lo_guess, head)
    b1 = first_block_at_or_after(d0 + 86400, rpc.block_ts, b0, head)
    if rpc.block_ts(head) < d0 + 86400 + 60:
        raise SystemExit(f"day {day} not finished on chain yet")
    stage = out_dir / "_staging" / day
    stage.mkdir(parents=True, exist_ok=True)
    chunks = [(a, min(a + CHUNK_BLOCKS, b1)) for a in range(b0, b1, CHUNK_BLOCKS)]
    todo = [(a, b) for a, b in chunks if not (stage / f"b{a}-{b}.parquet").exists()]
    log(f"tail {day}: blocks [{b0}, {b1}) {len(chunks)} chunks, {len(todo)} to fetch")
    with ThreadPoolExecutor(workers) as ex:
        for n, _ in enumerate(ex.map(lambda ab: _fetch_chunk(rpc, ab[0], ab[1], stage / f"b{ab[0]}-{ab[1]}.parquet"),
                                     todo), 1):
            if n % 10 == 0:
                log(f"tail {day}: {n}/{len(todo)} chunks, {time.time() - t0:.0f}s, rpc {rpc.stats()}")
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    dst = raw_dir / f"orderfilled_{day}.parquet"
    tmp = dst.with_suffix(".parquet.tmp")
    writer, n = None, 0
    try:
        for a, b in chunks:
            t = pq.read_table(stage / f"b{a}-{b}.parquet")
            if not t.num_rows:
                continue
            ts = t.column("timestamp")
            assert pc.min(ts).as_py() >= d0 and pc.max(ts).as_py() <= d0 + 86399, "row outside its UTC day"
            if writer is None:
                writer = pq.ParquetWriter(tmp, t.schema, compression="zstd")
            writer.write_table(t.cast(writer.schema))
            n += t.num_rows
    finally:
        if writer is not None:
            writer.close()
    assert n, f"no OrderFilled logs for {day}"
    os.replace(tmp, dst)
    shutil.rmtree(stage)
    rec = {"day": day, "blocks": [b0, b1], "orderfilled": n, "secs": round(time.time() - t0)}
    log(f"tail {day}: raw {rec}")
    return rec


def tail_day(rpc: Rpc, day: str, out_dir: Path, workers: int = 4) -> dict:
    """One UTC day -> out_dir/fills_{day}.parquet, from raw/orderfilled_{day}.parquet when it is already on disk
    (fetched by atlas), otherwise fetched now."""
    from src.data_quality.polymarket_onchain import orderfilled_to_trades
    from src.data_quality.polymarket_tokens import TokenMap, _load_default_cached

    t0 = time.time()
    raw = out_dir / "raw" / f"orderfilled_{day}.parquet"
    fetched = None if raw.exists() else fetch_orderfilled(rpc, day, out_dir, workers)
    of = pd.read_parquet(raw)
    assert not of.duplicated(["transaction_hash", "log_index"]).any(), "duplicate logs"
    for c in ("maker_amount_filled", "taker_amount_filled"):
        of[c] = of[c].map(int)
    tr, unk = orderfilled_to_trades(of, _tail_markets(of))
    tm = _load_default_cached(token_map_paths())
    if GAMMA_TAIL.exists():
        tm = tm.merge(TokenMap.from_markets(pd.read_parquet(GAMMA_TAIL)))
    ext = to_ext_frame(tr, tm, source="rpc_orderfilled")
    dst = out_dir / f"fills_{day}.parquet"
    tmp = dst.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pandas(ext, preserve_index=False), tmp, compression="zstd", row_group_size=250_000)
    os.replace(tmp, dst)
    rec = {"day": day, "blocks": (fetched or {}).get("blocks"), "raw": "fetched" if fetched else "on disk",
           "orderfilled": len(of), "trades": len(ext),
           "unknown_token_trades": len(unk), "unknown_yes": int(ext.yes_price.isna().sum()),
           "usd": float(ext.usd_amount.sum()), "secs": round(time.time() - t0)}
    log(f"tail {day}: done {rec}")
    return rec


def cmd_tail(start: str | None, end: str | None, verify_day: str | None, workers: int,
             raw_only: bool = False) -> None:
    import datetime as dt

    EXT_DIR.mkdir(parents=True, exist_ok=True)
    if TAIL_LOCK.exists():
        pid = int(TAIL_LOCK.read_text().strip() or -1)
        if pid > 0 and _pid_alive(pid):
            raise SystemExit(f"tail already running (pid {pid})")
    TAIL_LOCK.write_text(str(os.getpid()))
    try:
        rpc = Rpc()
        prog_p = TAIL_DIR / "progress.json"
        TAIL_DIR.mkdir(parents=True, exist_ok=True)
        prog = json.loads(prog_p.read_text()) if prog_p.exists() else {}
        if verify_day:
            rec = tail_day(rpc, verify_day, TAIL_DIR / "verify", workers)
            prog[f"verify_{verify_day}"] = rec
        else:
            today = dt.datetime.now(dt.timezone.utc).date()
            last = dt.date.fromisoformat(end) if end else today - dt.timedelta(days=1)
            if start:
                d = dt.date.fromisoformat(start)
            elif raw_only:  # atlas: continue after the newest raw day it holds (old ones are pruned once synced)
                have = sorted(p.stem[len("orderfilled_"):] for p in (TAIL_DIR / "raw").glob("orderfilled_*.parquet"))
                d = dt.date.fromisoformat(have[-1]) + dt.timedelta(days=1) if have else last
            else:
                d = dt.date.fromisoformat(utc_day(HF_END_TS))
            while d <= last:
                day = d.isoformat()
                if raw_only:
                    if not (TAIL_DIR / "raw" / f"orderfilled_{day}.parquet").exists():
                        prog[day] = fetch_orderfilled(rpc, day, TAIL_DIR, workers)
                        prog_p.write_text(json.dumps(prog, indent=1))
                elif not (TAIL_DIR / f"fills_{day}.parquet").exists():
                    prog[day] = tail_day(rpc, day, TAIL_DIR, workers)
                    prog_p.write_text(json.dumps(prog, indent=1))
                d += dt.timedelta(days=1)
        prog_p.write_text(json.dumps(prog, indent=1))
        if not raw_only:
            log(f"tail: coverage {write_coverage()}")
    finally:
        if TAIL_LOCK.exists() and TAIL_LOCK.read_text().strip() == str(os.getpid()):
            TAIL_LOCK.unlink()


def cmd_seam() -> None:
    """Seam checks -> data/vendor/pmext/: old-vs-new overlap by day and fill, daily series across both seams."""
    import duckdb

    from src.data_quality.polymarket_tape import clean_trades_sql

    VEN.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute("SET memory_limit='8GB'")
    c.execute("SET TimeZone='UTC'")
    c.execute(f"SET temp_directory='{(VEN / 'duck_tmp').as_posix()}'")
    olds = ", ".join(f"'{p.as_posix()}'" for p in sorted(OLD_DIR.glob("part-*.parquet")))
    lo, hi = EXT_START_TS, CUTOVER_TS + 86400  # 2025-12-23 .. 2025-12-30 inclusive
    c.execute(f"""CREATE TEMP TABLE o AS SELECT CASE WHEN transaction_hash LIKE '0x%' THEN lower(transaction_hash)
        ELSE '0x' || lower(transaction_hash) END tx, lower(maker) maker, token_id, round(token_amount, 2) tok,
        round(usd_amount, 2) usd, ts_chain, yes_price, yes_side, market_id
        FROM read_parquet([{olds}]) WHERE NOT is_taker_order_row AND ts_chain >= {lo} AND ts_chain < {hi}""")
    c.execute(f"""CREATE TEMP TABLE n AS SELECT transaction_hash tx, maker, token_id, round(token_amount, 2) tok,
        round(usd_amount, 2) usd, ts_chain, yes_price, yes_side, market_id
        FROM read_parquet('{(HF_DIR / 'part-*.parquet').as_posix()}') WHERE ts_chain >= {lo} AND ts_chain < {hi}""")
    for t in ("o", "n"):
        c.execute(f"CREATE TEMP TABLE {t}k AS SELECT *, row_number() OVER (PARTITION BY tx, maker, token_id, tok, usd "
                  f"ORDER BY ts_chain) k FROM {t}")
    day = "strftime(to_timestamp(coalesce(o.ts_chain, n.ts_chain)), '%Y-%m-%d')"
    overlap = c.sql(f"""SELECT {day} AS d, count(o.tx) old_rows, count(n.tx) new_rows,
        count(*) FILTER (WHERE o.tx IS NOT NULL AND n.tx IS NOT NULL) AS n_matched,
        count(*) FILTER (WHERE n.tx IS NULL) AS only_old, count(*) FILTER (WHERE o.tx IS NULL) AS only_new,
        sum(o.usd) old_usd, sum(n.usd) new_usd,
        count(*) FILTER (WHERE o.tx IS NOT NULL AND n.tx IS NOT NULL AND abs(o.yes_price - n.yes_price) > 1e-6) AS d_yes_price,
        count(*) FILTER (WHERE o.tx IS NOT NULL AND n.tx IS NOT NULL AND o.yes_side IS DISTINCT FROM n.yes_side) AS d_yes_side,
        count(*) FILTER (WHERE o.tx IS NOT NULL AND n.tx IS NOT NULL AND o.market_id IS DISTINCT FROM n.market_id) AS d_market,
        max(abs(o.ts_chain - n.ts_chain)) AS max_abs_ts_diff
        FROM ok o FULL OUTER JOIN nk n USING (tx, maker, token_id, tok, usd, k) GROUP BY 1 ORDER BY 1""").df()
    overlap.to_csv(VEN / "seam_overlap_2025-12.csv", index=False)
    print(overlap.to_string())
    lo2 = CUTOVER_TS - 14 * 86400
    daily = c.sql(f"""SELECT strftime(to_timestamp(ts_chain), '%Y-%m-%d') AS d, count(*) fills, sum(usd_amount) usd,
        count(DISTINCT market_id) markets, count(*) FILTER (WHERE yes_price IS NULL) unknown_yes
        FROM ({clean_trades_sql(['ts_chain', 'usd_amount', 'market_id', 'yes_price'], start_ts=lo2, maker_rows_only=True)})
        GROUP BY 1 ORDER BY 1""").df()
    daily.to_csv(VEN / "daily_combined.csv", index=False)
    print(daily.head(20).to_string())
    print(daily.tail(20).to_string())
    # A fill has one block time, so duplicates can only collide inside one window: check 3-day windows to keep
    # DuckDB's hash table in memory (a single 1.1B-row DISTINCT spilled ~70 GB to temp and filled the disk).
    dup, end = 0, tape_end_ts() or HF_END_TS
    for w in range(CUTOVER_TS, end, 3 * 86400):
        dup += c.sql(f"""SELECT count(*) - count(DISTINCT hash(transaction_hash, log_index)) FROM
            ({clean_trades_sql(['transaction_hash', 'log_index'], start_ts=w, end_ts=min(w + 3 * 86400, end))})"""
                     ).fetchone()[0]
    print("duplicate (tx, log_index) in ext range:", dup)


def cmd_status() -> None:
    cov = write_coverage()
    mans = [json.loads(p.read_text()) for p in MAN_DIR.glob("*.json")] if MAN_DIR.exists() else []
    rows = sum(m["rows_kept"] for m in mans)
    size = sum(p.stat().st_size for p in EXT_DIR.rglob("*.parquet"))
    print(json.dumps(cov, indent=1))
    print(f"hf parts done {len(mans)}, rows {rows:,}, ext size {size / 1e9:.1f} GB")
    if LOCK.exists():
        pid = int(LOCK.read_text().strip() or -1)
        print(f"lock pid {pid} alive={_pid_alive(pid)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hf")
    h.add_argument("--workers", type=int, default=3)
    h.add_argument("--full", action="store_true",
                   help="every row of the release (new setup with no old tape), not just rows from 2025-12-23")
    t = sub.add_parser("tail", help="fetch OrderFilled logs after the HF release via public Polygon RPC")
    t.add_argument("--start", help="first UTC day (default: day after the HF release)")
    t.add_argument("--end", help="last UTC day (default: yesterday)")
    t.add_argument("--verify-day", help="fetch one day into tail/verify/ (e.g. 2026-10-04) to diff against HF")
    t.add_argument("--workers", type=int, default=12, help="concurrent chunks; each node's AIMD limiter sets the pace")
    t.add_argument("--raw-only", action="store_true",
                   help="only fetch raw OrderFilled logs into tail/raw/ (no token maps needed). atlas runs this daily; "
                        "the PC converts the synced files without touching the RPC")
    sub.add_parser("status")
    sub.add_parser("seam")
    a = ap.parse_args()
    if a.cmd == "seam":
        cmd_seam()
    if a.cmd == "hf":
        cmd_hf(a.workers, a.full)
    elif a.cmd == "tail":
        cmd_tail(a.start, a.end, a.verify_day, a.workers, a.raw_only)
    elif a.cmd == "status":
        cmd_status()

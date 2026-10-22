"""Every Polymarket event's tags (Gamma API), for the category column of the Polymarket data lake.

Public read-only GETs to https://gamma-api.polymarket.com/events/keyset (cursor paging; plain /events refuses deep
offsets), once with closed=true and once with closed=false. Keeps id, slug, title, the raw category field and the
tag labels/slugs; the rest of the payload is dropped. Resumable: the cursor is checkpointed after each page.

    uv run python scripts/data/fetch_pm_event_tags.py            # -> data/polymarket_clean/event_tags.parquet
    uv run python scripts/data/fetch_pm_event_tags.py --by-id    # remaining non-up/down events by id, 4 in parallel
                                                                 # (deep keyset pages get slow: 2-40 s each)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from predict_ngin.shared.paths import data_root  # noqa: E402
from predict_ngin.tapes.polymarket import hf_snapshot_dir  # noqa: E402

URL = "https://gamma-api.polymarket.com/events/keyset"
OUT = data_root() / "polymarket_clean" / "event_tags.parquet"
WORK = data_root() / "polymarket_clean" / "_event_tags_work"
LIMIT = 100  # the endpoint caps pages at 100
MIN_INTERVAL_S = 0.35  # ~3 req/s, polite to a public endpoint


def page(sess: requests.Session, closed: str, cursor: str | None) -> dict:
    params = {"limit": LIMIT, "closed": closed}
    if cursor:
        params["after_cursor"] = cursor
    for attempt in range(8):
        r = sess.get(URL, params=params, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(60, 2 ** attempt))
            continue
        raise SystemExit(f"Gamma {r.status_code}: {r.text[:300]}")
    raise SystemExit("Gamma kept failing; rerun to resume")


def slim(e: dict) -> dict:
    tags = e.get("tags") or []
    return {"event_id": str(e.get("id")), "slug": e.get("slug"), "title": e.get("title"),
            "category_raw": e.get("category"), "closed": e.get("closed"),
            "tag_labels": [t.get("label") for t in tags if t.get("label")],
            "tag_slugs": [t.get("slug") for t in tags if t.get("slug")]}


def run_pass(sess: requests.Session, closed: str) -> None:
    ck = WORK / f"cursor_{closed}.json"
    state = json.loads(ck.read_text()) if ck.exists() else {"cursor": None, "part": 0, "done": False}
    if state["done"]:
        return
    last = 0.0
    while True:
        wait = MIN_INTERVAL_S - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()
        j = page(sess, closed, state["cursor"])
        evs = j.get("events") or []
        if evs:
            pd.DataFrame([slim(e) for e in evs]).to_parquet(WORK / f"{closed}_{state['part']:06d}.parquet", index=False)
            state["part"] += 1
        state["cursor"] = j.get("next_cursor")
        state["done"] = not evs or not state["cursor"]
        ck.write_text(json.dumps(state))
        if state["part"] % 100 == 0:
            print(f"{time.strftime('%H:%M:%S')} closed={closed} pages={state['part']}", flush=True)
        if state["done"]:
            print(f"closed={closed} done: {state['part']} pages", flush=True)
            return


def needed_ids() -> list[str]:
    """Events in the local market lists without tags yet, crypto up/down excluded (slug rules classify those)."""
    import duckdb

    lists = [hf_snapshot_dir() / "markets.parquet",
             hf_snapshot_dir() / "markets_gamma_tail.parquet", data_root() / "polymarket/markets.parquet"]
    lst = ", ".join("'" + f.as_posix() + "'" for f in lists if f.exists())
    done = f"read_parquet('{WORK.as_posix()}/*.parquet')"
    return [r[0] for r in duckdb.sql(f"""
        SELECT DISTINCT CAST(event_id AS VARCHAR) FROM read_parquet([{lst}], union_by_name=true)
        WHERE event_id IS NOT NULL AND NOT regexp_matches(coalesce(event_slug, slug, ''), 'updown|up-or-down')
          AND CAST(event_id AS VARCHAR) NOT IN (SELECT event_id FROM {done})""").fetchall()]


def run_by_id(workers: int = 4) -> None:
    """Fetch tags for the remaining events by id, 100 per request, a few requests in parallel (they are independent)."""
    from concurrent.futures import ThreadPoolExecutor

    ids = needed_ids()
    batches = [ids[i:i + 100] for i in range(0, len(ids), 100)]
    print(f"{len(ids):,} events to fetch by id in {len(batches):,} requests", flush=True)
    sess = requests.Session()

    def one(k_batch):
        k, batch = k_batch
        for attempt in range(8):
            r = sess.get("https://gamma-api.polymarket.com/events",
                         params=[("id", x) for x in batch] + [("limit", "100")], timeout=120)
            if r.status_code == 200:
                evs = r.json()
                if evs:
                    pd.DataFrame([slim(e) for e in evs]).to_parquet(WORK / f"byid_{k:06d}_{batch[0]}.parquet",
                                                                    index=False)
                time.sleep(MIN_INTERVAL_S)
                return len(evs)
            time.sleep(min(60, 2 ** attempt))
        return 0

    got = 0
    with ThreadPoolExecutor(workers) as ex:
        for i, n in enumerate(ex.map(one, enumerate(batches))):
            got += n
            if i % 100 == 0:
                print(f"{time.strftime('%H:%M:%S')} by-id requests {i:,}/{len(batches):,}, events {got:,}", flush=True)


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    if "--by-id" in sys.argv:
        run_by_id()
    else:
        for closed in ("true", "false"):
            run_pass(sess, closed)
    parts = sorted(WORK.glob("*_*.parquet"))
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True).drop_duplicates("event_id", keep="last")
    df.to_parquet(OUT, index=False, compression="zstd")
    print(f"{len(df):,} events -> {OUT}")


if __name__ == "__main__":
    main()

"""tailcarry: build the tape slice and market metadata for the long-dated tail-NO carry study (research only).

No orders, no keys. Public read-only Kalshi REST only (<= 4 req/s).

Steps (each checkpointed under data/vendor/tailcarry/, re-run skips finished steps):
  tape     prints of every ticker whose series is in the study categories (from trades_fixed + trades_fixed_ext via
           kalshi_tape.tape_sql), columns ticker, event_ticker, ts, yc (YES cents), tyes, qty -> tape.parquet
  cand     tickers with at least one YES print <= 8c -> cand.parquet (market metadata is fetched only for these)
  meta     GET /historical/markets?tickers= then /markets?tickers= (200 per call) -> markets.parquet
           (status, result, settlement_value, close/expiration times, rules, can_close_early)
  events   GET /events/{event_ticker} -> events.parquet (mutually_exclusive, collateral_return_type, category)

Study categories (Kalshi /series category): Entertainment, World, Social (high maker-taker gap) and Politics,
Elections (low-gap comparison). Mention series are excluded (studied elsewhere; MNPI risk).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.trading.arbitrage.kalshi_tape import tape_sql  # noqa: E402

OUT = ROOT / "data/vendor/tailcarry"
OUT.mkdir(parents=True, exist_ok=True)
SERIES = (ROOT / "data/vendor/kalshi_research/series.parquet").as_posix()
API = "https://api.elections.kalshi.com/trade-api/v2"
CATS_HIGH = ("Entertainment", "World", "Social")
CATS_LOW = ("Politics", "Elections")
CATS = CATS_HIGH + CATS_LOW
UA = {"User-Agent": "Mozilla/5.0 (research; read-only)"}


def con():
    c = duckdb.connect()
    c.execute("SET memory_limit='3GB'; SET threads=1; SET preserve_insertion_order=false")
    c.execute(f"SET temp_directory='{(OUT / '_duck_tmp').as_posix()}'")
    return c


def series_table(c) -> pd.DataFrame:
    s = c.execute(f"select series_ticker series, category, title, fee_type, fee_multiplier from '{SERIES}'").df()
    s = s[s.category.isin(CATS) & ~s.series.str.upper().str.contains("MENTION")]
    return s


def step_tape():
    f = OUT / "tape.parquet"
    if f.exists():
        print("tape: exists"); return
    c = con()
    s = series_table(c)
    c.register("ser", s[["series"]])
    sql = tape_sql(["ticker", "event_ticker", "yes_price", "count", "taker_side", "timestamp_unix"])
    c.execute(f"""COPY (
        SELECT t.ticker, t.event_ticker, t.timestamp_unix::BIGINT ts,
               CAST(round(t.yes_price*100) AS SMALLINT) yc, (t.taker_side='yes') tyes, t."count"::DOUBLE qty
        FROM ({sql}) t JOIN ser ON split_part(t.event_ticker,'-',1)=ser.series
        ORDER BY t.ticker, ts) TO '{(OUT / 'tape.tmp.parquet').as_posix()}' (FORMAT parquet)""")
    (OUT / "tape.tmp.parquet").replace(f)
    print("tape:", c.execute(f"select count(*), count(distinct ticker), min(ts), max(ts) from '{f.as_posix()}'").fetchall())


def step_cand():
    f = OUT / "cand.parquet"
    if f.exists():
        print("cand: exists"); return
    c = con()
    s = series_table(c)
    c.register("ser", s)
    d = c.execute(f"""SELECT ticker, any_value(event_ticker) event_ticker, min(ts) first_ts, max(ts) last_ts,
            count(*) n, sum(qty) vol, min(yc) min_yc
        FROM '{(OUT / 'tape.parquet').as_posix()}' GROUP BY ticker HAVING min(yc) <= 8""").df()
    d["series"] = d.event_ticker.str.split("-").str[0]
    d = d.merge(s, on="series", how="left")
    d.to_parquet(f, index=False)
    print("cand:", len(d), d.category.value_counts().to_dict())


def _get(url, params=None, tries=5):
    for k in range(tries):
        if k:
            time.sleep(1 + 2 * k)
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            if r.status_code == 429:
                time.sleep(2 + 2 * k); continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(1 + 2 * k)
    raise RuntimeError(f"failed {url} {params}")


KEEP = ["ticker", "event_ticker", "market_type", "status", "result", "settlement_value_dollars", "close_time",
        "open_time", "created_time", "expected_expiration_time", "expiration_time", "latest_expiration_time",
        "settlement_ts", "can_close_early", "rules_primary", "rules_secondary", "title", "yes_sub_title",
        "expiration_value", "early_close_condition", "last_price_dollars"]


def step_meta():
    f = OUT / "markets.parquet"
    if f.exists():
        print("meta: exists"); return
    tick = pd.read_parquet(OUT / "cand.parquet").ticker.tolist()
    ck = OUT / "_meta_ck.jsonl"
    done = set()
    if ck.exists():
        for line in ck.read_text().splitlines():
            done.add(json.loads(line)["ticker"])
    todo = [t for t in tick if t not in done]
    print("meta: todo", len(todo))
    with ck.open("a") as fh:
        for i in range(0, len(todo), 100):
            chunk = todo[i:i + 100]
            got = {}
            for ep in ("historical/markets", "markets"):
                miss = [t for t in chunk if t not in got]
                if not miss:
                    break
                try:
                    j = _get(f"{API}/{ep}", {"tickers": ",".join(miss), "limit": 200}, tries=2) or {}
                    ms = j.get("markets", [])
                except RuntimeError:  # over-long / odd tickers: fall back to one call per ticker
                    ms = []
                    for t in miss:
                        jj = _get(f"{API}/{ep}/{requests.utils.quote(t, safe='')}", tries=2)
                        if jj and jj.get("market"):
                            ms.append(jj["market"])
                        time.sleep(0.25)
                for m in ms:
                    got[m["ticker"]] = {k: m.get(k) for k in KEEP}
                time.sleep(0.3)
            for t in chunk:
                fh.write(json.dumps(got.get(t, {"ticker": t, "status": "_missing"})) + "\n")
            fh.flush()
            if i % 2000 == 0:
                print("meta", i, len(todo), flush=True)
    rows = [json.loads(x) for x in ck.read_text().splitlines()]
    pd.DataFrame(rows).drop_duplicates("ticker", keep="last").to_parquet(f, index=False)
    print("meta: done", len(rows))


def step_events():
    f = OUT / "events.parquet"
    if f.exists():
        print("events: exists"); return
    evs = sorted(pd.read_parquet(OUT / "cand.parquet").event_ticker.unique())
    ck = OUT / "_ev_ck.jsonl"
    done = set()
    if ck.exists():
        done = {json.loads(x)["event_ticker"] for x in ck.read_text().splitlines()}
    todo = [e for e in evs if e not in done]
    print("events: todo", len(todo))
    with ck.open("a") as fh:
        for i, e in enumerate(todo):
            j = _get(f"{API}/events/{e}", {"with_nested_markets": "false"})
            ev = (j or {}).get("event") or {}
            fh.write(json.dumps({"event_ticker": e, "mutually_exclusive": ev.get("mutually_exclusive"),
                                 "collateral_return_type": ev.get("collateral_return_type"),
                                 "ev_category": ev.get("category"), "ev_title": ev.get("title"),
                                 "strike_date": ev.get("strike_date")}) + "\n")
            if i % 500 == 0:
                fh.flush(); print("events", i, len(todo), flush=True)
            time.sleep(0.25)
    rows = [json.loads(x) for x in ck.read_text().splitlines()]
    pd.DataFrame(rows).drop_duplicates("event_ticker", keep="last").to_parquet(f, index=False)
    print("events: done", len(rows))


if __name__ == "__main__":
    steps = sys.argv[1:] or ["tape", "cand", "meta", "events"]
    for s in steps:
        {"tape": step_tape, "cand": step_cand, "meta": step_meta, "events": step_events}[s]()

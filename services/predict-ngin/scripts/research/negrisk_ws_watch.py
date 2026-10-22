"""Tick-level (WebSocket) watch of every open Polymarket NegRisk event (RESEARCH ONLY, public feed).

Maintains full books for the YES token of every open market (src/recorder PolymarketWsState) and, on
every book change, recomputes per event the top-of-book net-of-fee edge of
  buy-all-YES (sum of asks + fees < 1; only if every open market has a valid book with asks) and
  sell/convert (best subset of YES bids net of fees > 1).
Whenever an event's edge is above ``--log-above`` (default -0.005), or crosses back below it, a row is
written with exchange and receive timestamps plus a depth-walked size/profit when the edge is > 0.
Rows -> data/vendor/negrisk/ws/edges_<stamp>.parquet (flushed every 60s).

    venv/Scripts/python.exe scripts/research/negrisk_ws_watch.py --minutes 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from negrisk_common import buy_all_yes, fee_per_share, sell_all_yes  # noqa: E402
from src.recorder.venues.polymarket import WS_URL, PolymarketWsState  # noqa: E402

log = logging.getLogger("negrisk_ws")
OUT = Path("data/vendor/negrisk/ws")
UNI = Path("data/vendor/negrisk/universe_latest.parquet")
CLOB = "https://clob.polymarket.com"


def resolved_yes(prices) -> bool:
    try:
        p = json.loads(prices) if isinstance(prices, str) else prices
        return float(p[0]) >= 0.99
    except Exception:  # noqa: BLE001
        return False


class Watch:
    def __init__(self, u: pd.DataFrame, log_above: float):
        self.state = PolymarketWsState()
        self.log_above = log_above
        self.events = {}  # event_id -> dict(tokens, rates, exps, complete_meta)
        self.tok2ev = {}
        for eid, g in u.groupby("nr_id"):
            if g[g.closed].outcome_prices.map(resolved_yes).any():
                continue
            o = g[~g.closed]
            if len(o) < 2:
                continue
            tradable = o[o.accepting & o.yes_token.notna()]
            if len(tradable) < 2:
                continue
            self.events[eid] = dict(tokens=list(tradable.yes_token), rates=list(tradable.fee_rate),
                                    exps=list(tradable.fee_exp), all_tradable=len(tradable) == len(o),
                                    slug=o.event_slug.iloc[0])
            for t in tradable.yes_token:
                self.tok2ev[t] = eid
        self.last = {}  # event -> (buy_edge, sell_edge) last logged
        self.rows = []
        self.n_msgs = 0

    def tokens(self):
        return list(self.tok2ev)

    def evaluate(self, eid: str, t_recv_ns: int, t_exch_ns):
        e = self.events[eid]
        books = [self.state.books.get(t) for t in e["tokens"]]
        if any(b is None or not b.valid for b in books):
            return
        buy_edge = None
        if e["all_tradable"] and all(b.asks for b in books):
            buy_edge = 1.0 - sum(b.best_ask + fee_per_share(b.best_ask, r, x)
                                 for b, r, x in zip(books, e["rates"], e["exps"]))
        legs = sorted(((b.best_bid - fee_per_share(b.best_bid, r, x), i)
                       for i, (b, r, x) in enumerate(zip(books, e["rates"], e["exps"])) if b.bids), reverse=True)
        S = [i for v, i in legs if v > 0]
        if len(S) < 2:
            S = [i for _, i in legs[:2]]
        sell_edge = (sum(v for v, i in legs if i in S) - 1.0) if len(S) >= 2 else None
        prev = self.last.get(eid)
        hot = lambda v: v is not None and v > self.log_above  # noqa: E731
        is_hot = hot(buy_edge) or hot(sell_edge)
        was_hot = prev is not None and (hot(prev[0]) or hot(prev[1]))
        if not (is_hot or was_hot):
            self.last[eid] = (buy_edge, sell_edge)
            return
        if prev == (buy_edge, sell_edge):
            return
        row = dict(event_id=eid, t_recv_ns=t_recv_ns, t_exch_ns=t_exch_ns, n=len(books),
                   buy_edge=buy_edge, sell_edge=sell_edge, sell_k=len(S))
        if buy_edge is not None and buy_edge > 0:
            f = buy_all_yes([sorted(b.asks.items()) for b in books], e["rates"], exponents=e["exps"], min_shares=5)
            row.update(buy_shares=f.shares, buy_profit=f.profit, buy_capital=f.capital,
                       **{f"buy_profit_{c}": f.at_capital(c)[1] for c in (1000, 2500, 5000, 10000)})
        if sell_edge is not None and sell_edge > 0:
            f = sell_all_yes([sorted(books[i].bids.items(), key=lambda x: -x[0]) for i in S],
                             [e["rates"][i] for i in S], exponents=[e["exps"][i] for i in S], min_shares=5)
            row.update(sell_shares=f.shares, sell_profit=f.profit, sell_capital=f.capital,
                       **{f"sell_profit_{c}": f.at_capital(c)[1] for c in (1000, 2500, 5000, 10000)})
        self.rows.append(row)
        self.last[eid] = (buy_edge, sell_edge)

    def on_raw(self, raw: str, recv_ns: int):
        if not raw or raw in ("PONG", "PING"):
            return set()
        try:
            msg = json.loads(raw)
        except ValueError:
            return set()
        self.n_msgs += 1
        ev = self.state.process(msg, recv_ns)
        self.state.verify_pending(recv_ns)
        ts = None
        for m in msg if isinstance(msg, list) else [msg]:
            if isinstance(m, dict) and m.get("timestamp"):
                try:
                    ts = int(m["timestamp"]) * 1_000_000
                except (TypeError, ValueError):
                    pass
        for eid in {self.tok2ev[a] for a in ev.book_updates if a in self.tok2ev}:
            self.evaluate(eid, recv_ns, ts)
        return {a for a in ev.resync_ids if a in self.tok2ev}


async def conn_loop(w: Watch, tokens, stop: asyncio.Event, resync_q: set):
    while not stop.is_set():
        try:
            async with websockets.connect(WS_URL, ping_interval=None, max_size=2 ** 24) as ws:
                await ws.send(json.dumps({"assets_ids": tokens, "type": "market"}))
                last_ping = time.time()
                while not stop.is_set():
                    if time.time() - last_ping >= 10:
                        await ws.send("PING")
                        last_ping = time.time()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    resync_q |= w.on_raw(raw, time.time_ns())
        except Exception as e:  # noqa: BLE001
            log.warning("ws error (%d tokens): %s", len(tokens), e)
        for t in tokens:
            b = w.state.books.get(t)
            if b is not None:
                b.invalidate()
        await asyncio.sleep(2)


async def resync_loop(w: Watch, stop: asyncio.Event, resync_q: set):
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        await asyncio.sleep(3)
        todo = list(resync_q)[:500]
        resync_q.difference_update(todo)
        if not todo:
            continue
        try:
            r = await loop.run_in_executor(None, lambda: requests.post(
                f"{CLOB}/books", json=[{"token_id": t} for t in todo], timeout=30))
            for b in r.json() or []:
                a = w.state.apply_rest(b, time.time_ns())
                if a in w.tok2ev:
                    w.evaluate(w.tok2ev[a], time.time_ns(), None)
        except Exception as e:  # noqa: BLE001
            log.warning("resync: %s", e)


async def seed_loop(w: Watch, stop: asyncio.Event, every_s: float = 600):
    """REST snapshot of every token at start and every `every_s` (WS book snapshots are not sent for all
    assets on subscribe; price_change carries absolute level sizes so deltas apply on top of REST books)."""
    from negrisk_live_scan import fetch_books
    loop = asyncio.get_running_loop()
    s = requests.Session()
    while not stop.is_set():
        t0 = time.time()
        books = await loop.run_in_executor(None, lambda: fetch_books(s, w.tokens()))
        now = time.time_ns()
        for b in books.values():
            w.state.apply_rest(b, now)
        for t in w.tokens():  # no book returned = empty book (no resting orders)
            if t not in books and (t not in w.state.books or not w.state.books[t].valid):
                w.state.apply_rest({"asset_id": t, "bids": [], "asks": [], "timestamp": None}, now)
        for eid in w.events:
            w.evaluate(eid, now, None)
        print(f"seeded {len(books)} books in {time.time() - t0:.0f}s", flush=True)
        for _ in range(int(every_s)):
            if stop.is_set():
                return
            await asyncio.sleep(1)


async def flush_loop(w: Watch, stop: asyncio.Event, stamp: str):
    part = 0
    while not stop.is_set():
        await asyncio.sleep(60)
        rows, w.rows = w.rows, []
        if rows:
            pd.DataFrame(rows).to_parquet(OUT / f"edges_{stamp}_{part:04d}.parquet", index=False)
            part += 1
        valid = sum(b.valid for b in w.state.books.values())
        print(f"{datetime.now(timezone.utc):%H:%M:%S} msgs {w.n_msgs} rows {len(rows)} valid_books {valid}/"
              f"{len(w.tok2ev)} bbo_mismatch {w.state.bbo_mismatches}", flush=True)


async def main_async(a):
    OUT.mkdir(parents=True, exist_ok=True)
    u = pd.read_parquet(UNI)
    w = Watch(u, a.log_above)
    toks = w.tokens()
    print(f"events {len(w.events)} tokens {len(toks)}", flush=True)
    stop = asyncio.Event()
    resync_q: set = set()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    tasks = [asyncio.create_task(conn_loop(w, toks[i:i + a.chunk], stop, resync_q))
             for i in range(0, len(toks), a.chunk)]
    tasks += [asyncio.create_task(seed_loop(w, stop)), asyncio.create_task(resync_loop(w, stop, resync_q)), asyncio.create_task(flush_loop(w, stop, stamp))]
    await asyncio.sleep(a.minutes * 60)
    stop.set()
    await asyncio.sleep(3)
    if w.rows:
        pd.DataFrame(w.rows).to_parquet(OUT / f"edges_{stamp}_final.parquet", index=False)
    for t in tasks:
        t.cancel()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=100)
    ap.add_argument("--chunk", type=int, default=2000)
    ap.add_argument("--log-above", type=float, default=-0.005)
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()

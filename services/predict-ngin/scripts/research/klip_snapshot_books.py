"""Read-only snapshot of every ACTIVE Kalshi liquidity-incentive programme and its full order book.

Per round:
  1. GET /incentive_programs?status=active (public)        -> programs_<stamp>.parquet
  2. GET /markets/orderbooks?tickers=..&tickers=.. (public) -> books_<stamp>.parquet  (long: one row per level)
Writes under data/vendor/klip/snapshots/. Rate <= 1 req/s, backoff on 429. Never places orders.

    venv/Scripts/python.exe -I scripts/research/klip_snapshot_books.py                 # one round
    venv/Scripts/python.exe -I scripts/research/klip_snapshot_books.py --every-h 3 --rounds 6
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "klip" / "snapshots"
BATCH = 100


class Getter:
    def __init__(self, min_interval=1.0):
        self.s = requests.Session(); self.last = 0.0; self.mi = min_interval

    def get(self, path, params):
        for i in range(8):
            w = self.mi - (time.time() - self.last)
            if w > 0:
                time.sleep(w)
            self.last = time.time()
            try:
                r = self.s.get(API + path, params=params, timeout=60)
            except requests.RequestException:
                time.sleep(2 ** i); continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(60, 2 ** i)); continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"GET {path} failed")


def one_round(g: Getter):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    progs, cur = [], None
    while True:
        p = {"status": "active", "limit": 10000}
        if cur:
            p["cursor"] = cur
        d = g.get("/incentive_programs", p)
        progs += d.get("incentive_programs", [])
        cur = d.get("next_cursor")
        if not cur:
            break
    pr = pd.DataFrame(progs)
    pr["snap"] = stamp
    tickers = sorted(pr.market_ticker.unique())
    rows = []
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        t_req = time.time()
        d = g.get("/markets/orderbooks", [("tickers", t) for t in chunk])
        for ob in d.get("orderbooks", []):
            fp = ob.get("orderbook_fp") or {}
            for side, key in (("yes", "yes_dollars"), ("no", "no_dollars")):
                for px, sz in fp.get(key) or []:
                    rows.append((ob["ticker"], side, float(px), float(sz), t_req))
            if not (fp.get("yes_dollars") or fp.get("no_dollars")):
                rows.append((ob["ticker"], "empty", float("nan"), 0.0, t_req))
    bk = pd.DataFrame(rows, columns=["ticker", "side", "price", "size", "t_req"])
    bk["snap"] = stamp
    OUT.mkdir(parents=True, exist_ok=True)
    pr.to_parquet(OUT / f"programs_{stamp}.parquet", index=False)
    bk.to_parquet(OUT / f"books_{stamp}.parquet", index=False)
    print(stamp, "programs", len(pr), "markets", len(tickers), "books_returned", bk.ticker.nunique(), "levels", len(bk), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every-h", type=float, default=0)
    ap.add_argument("--rounds", type=int, default=1)
    a = ap.parse_args()
    g = Getter()
    for k in range(a.rounds):
        t0 = time.time()
        try:
            one_round(g)
        except Exception as e:  # keep the monitor alive across transient errors
            print("round failed:", repr(e), flush=True)
        if k < a.rounds - 1:
            time.sleep(max(0, a.every_h * 3600 - (time.time() - t0)))


if __name__ == "__main__":
    main()

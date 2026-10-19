"""nowc: fetch Kalshi markets + trades for given series (public read-only GETs, <=0.29 req/s).

usage: nowc_kalshi_fetch.py <tag> <series1,series2,...> [min_close_iso]
Writes data/vendor/nowc/<tag>_markets.parquet and data/vendor/nowc/<tag>_trades/<ticker>.parquet (resumable).
"""
from __future__ import annotations
import sys, json, time
from pathlib import Path
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/vendor/nowc"
BASE = "https://api.elections.kalshi.com/trade-api/v2"
MIN_INTERVAL = 3.5  # seconds -> <= 0.29 req/s
S = requests.Session()
S.headers["User-Agent"] = "predict-ngin-research/0.1"
_last = [0.0]


def get(path, params=None, retries=7):
    for i in range(retries):
        w = MIN_INTERVAL - (time.time() - _last[0])
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()
        try:
            r = S.get(BASE + path, params=params, timeout=30)
        except requests.RequestException:
            time.sleep(2 ** i); continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(120, 5 * 2 ** i)); continue
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"failed {path} {params}")


def paged(path, key, params=None, limit=1000, max_pages=200):
    params = dict(params or {}); params["limit"] = limit
    out = []
    for _ in range(max_pages):
        j = get(path, params)
        out.extend(j.get(key, []))
        cur = j.get("cursor")
        if not cur:
            break
        params["cursor"] = cur
    return out


def main(tag, series, min_close="2000-01-01"):
    OUT.mkdir(parents=True, exist_ok=True)
    mp = OUT / f"{tag}_markets.parquet"
    if mp.exists():
        mk = pd.read_parquet(mp)
    else:
        rows = []
        for st in series.split(","):
            for ep in ["/historical/markets", "/markets"]:
                for m in paged(ep, "markets", {"series_ticker": st}):
                    rows.append({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in m.items()})
            print("series", st, len(rows), flush=True)
        mk = pd.DataFrame(rows).drop_duplicates("ticker", keep="last")
        mk.to_parquet(mp)
    print("markets", len(mk), flush=True)
    sel = mk[mk.close_time >= min_close]
    tp = OUT / f"{tag}_trades"
    tp.mkdir(exist_ok=True)
    done = {p.stem for p in tp.glob("*.parquet")}
    todo = [t for t in sel.sort_values("close_time", ascending=False).ticker if t not in done]
    print("trade fetch todo", len(todo), flush=True)
    for i, t in enumerate(todo):
        try:
            tr = paged("/historical/trades", "trades", {"ticker": t}, max_pages=300)
            if not tr:
                tr = paged("/markets/trades", "trades", {"ticker": t}, max_pages=300)
        except RuntimeError as e:
            print("skip", t, e, flush=True); continue
        df = pd.DataFrame(tr)
        if df.empty:
            df = pd.DataFrame({"ticker": [t], "trade_id": [None]})
        df.to_parquet(tp / f"{t}.parquet")
        if i % 25 == 0:
            print("trades", i, len(todo), flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])

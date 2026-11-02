#!/usr/bin/env python3
"""Refresh data/kalshi/markets.parquet (market metadata + settlement results) through today.

The file is the high-volume Kalshi universe (volume >= 500k contracts, 9,651 markets when last written on
2026-02-11) in the old project schema (prices in integer cents, settlement_value 0/100 cents, series_ticker,
category). This script keeps that schema and universe rule and:

1. re-queries every old ticker that was not yet finalized (status/result/settlement may have changed);
2. discovers markets that closed from 2026-02-11 to now on ``/historical/markets`` (paged newest-first; the
   endpoint ignores close-time filters; markets settled before the historical cutoff) and ``/markets`` with
   ``min_close_ts`` (after it), both with ``mve_filter=exclude``;
3. adds currently open markets (``/markets?status=open``) above the volume floor.

Public GET requests only, no key. It is rate-limited to ``--rps`` (default 1.5) so it does not starve
``scripts/research/tape_refresh.py``, which shares the IP. Historical paging is checkpointed under
``data/kalshi/markets_refresh/`` so a re-run resumes. The final write goes to a temp file, the old file is copied
to ``data/kalshi/markets_<date>.bak.parquet`` (date = its last-modified day), then the temp file replaces
``markets.parquet`` atomically. tape_refresh never writes this file (it writes trades_fixed_ext/markets/).

Usage:  venv/Scripts/python.exe scripts/data/refresh_kalshi_markets.py [--min-volume 500000] [--rps 1.5]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from predict_ngin.shared.paths import data_root  # noqa: E402

API ="https://api.elections.kalshi.com/trade-api/v2"
MARKETS = data_root() / "kalshi" / "markets.parquet"
WORK = data_root() / "kalshi" / "markets_refresh"
START_DAY = dt.date(2026, 2, 11)
COLUMNS = ["ticker", "event_ticker", "series_ticker", "market_type", "title", "subtitle", "status", "result",
           "yes_bid", "yes_ask", "no_bid", "no_ask", "last_price", "volume", "volume_24h", "open_interest",
           "liquidity", "created_time", "open_time", "close_time", "expiration_time", "settlement_value", "category"]


class Client:
    def __init__(self, rps: float):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "PredictionMarketResearch/1.0"
        self.gap, self.last, self.n, self.n429 = 1.0 / rps, 0.0, 0, 0

    def get(self, path: str, params: dict) -> dict:
        for attempt in range(10):
            wait = self.last + self.gap - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                r = self.s.get(API + path, params=params, timeout=60)
            except requests.RequestException:
                time.sleep(min(120, 2 ** attempt))
                continue
            self.n += 1
            if r.status_code == 429:
                self.n429 += 1
                time.sleep(float(r.headers.get("Retry-After") or min(60, 2 ** (attempt + 1))))
                continue
            if r.status_code >= 500:
                time.sleep(min(120, 2 ** attempt))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"GET {path} {params} failed")

    def pages(self, path: str, params: dict):
        cur = ""
        while True:
            d = self.get(path, {**params, "limit": 1000, **({"cursor": cur} if cur else {})})
            yield from d.get("markets", [])
            cur = d.get("cursor") or ""
            if not cur:
                return


def _num(x, scale: float = 1.0):
    try:
        return float(x) * scale
    except (TypeError, ValueError):
        return None


def to_row(m: dict) -> dict:
    """API market (``*_dollars`` / ``*_fp`` fields) -> old schema (integer cents, integer contracts)."""
    cents = lambda k: int(round(_num(m.get(k), 100.0) or 0))  # noqa: E731
    sv = _num(m.get("settlement_value_dollars"), 100.0)
    return {
        "ticker": m.get("ticker", ""), "event_ticker": m.get("event_ticker", ""),
        "market_type": m.get("market_type", ""), "title": str(m.get("title", ""))[:200],
        "subtitle": str(m.get("subtitle") or "")[:200], "status": m.get("status", ""), "result": m.get("result", ""),
        "yes_bid": cents("yes_bid_dollars"), "yes_ask": cents("yes_ask_dollars"), "no_bid": cents("no_bid_dollars"),
        "no_ask": cents("no_ask_dollars"), "last_price": cents("last_price_dollars"),
        "volume": int(round(_num(m.get("volume_fp")) or 0)), "volume_24h": int(round(_num(m.get("volume_24h_fp")) or 0)),
        "open_interest": int(round(_num(m.get("open_interest_fp")) or 0)),
        "created_time": m.get("created_time", ""), "open_time": m.get("open_time", ""),
        "close_time": m.get("close_time", ""), "expiration_time": m.get("expiration_time", ""),
        "settlement_value": None if sv is None or m.get("status") not in ("finalized", "settled") else round(sv, 4),
        "updated_time": m.get("updated_time", ""),
    }


def finalize_frame(df: pd.DataFrame, old: pd.DataFrame, series: dict) -> pd.DataFrame:
    """Fill series_ticker/category/liquidity (old values win for known tickers) and order columns."""
    prev = old.set_index("ticker")
    df = df.copy()
    pre = df["event_ticker"].astype(str).str.split("-").str[0]
    df["series_ticker"] = df["ticker"].map(prev["series_ticker"]).fillna(pre)
    df["category"] = df["ticker"].map(prev["category"]).fillna(df["series_ticker"].map(series)).fillna("")
    df["liquidity"] = df["ticker"].map(prev["liquidity"]).fillna(0).astype("int64")
    for c in ("yes_bid", "yes_ask", "no_bid", "no_ask", "last_price", "volume", "volume_24h", "open_interest"):
        df[c] = df[c].astype("int64")
    df["settlement_value"] = df["settlement_value"].astype("float64")
    return df[COLUMNS]


def merge_markets(old: pd.DataFrame, fresh: pd.DataFrame, min_volume: float) -> pd.DataFrame:
    """Old universe updated by fresh rows (fresh wins per ticker) + fresh rows above the volume floor."""
    fresh = fresh.drop_duplicates("ticker", keep="last")
    keep_new = fresh[(fresh["volume"] >= min_volume) | fresh["ticker"].isin(old["ticker"])]
    out = pd.concat([old[~old["ticker"].isin(keep_new["ticker"])], keep_new], ignore_index=True)
    return out.sort_values("ticker").reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-volume", type=float, default=500_000)
    ap.add_argument("--rps", type=float, default=1.5)
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    days_dir = WORK / "days"
    days_dir.mkdir(exist_ok=True)
    c = Client(min(a.rps, 3.0))
    old = pd.read_parquet(MARKETS)
    t0 = time.time()

    # 1. old tickers not finalized
    stale = old.loc[old["status"] != "finalized", "ticker"].tolist()
    rows = []
    for i in range(0, len(stale), 100):
        tk = ",".join(stale[i:i + 100])
        for path in ("/historical/markets", "/markets"):
            rows += [to_row(m) for m in c.get(path, {"tickers": tk}).get("markets", [])]
    print(f"stale old tickers {len(stale)} -> {len(rows)} rows", flush=True)
    refreshed = pd.DataFrame(rows)

    # 2a. /historical/markets IGNORES min/max_close_ts (checked 2026-10-16) and pages newest close first, so page
    # it unfiltered and stop once a whole page closed before START_DAY - 14 d. Checkpointed every 25 pages.
    start_s = (START_DAY - dt.timedelta(days=14)).isoformat()
    state_p = days_dir / "historical_state.json"
    st = json.loads(state_p.read_text()) if state_p.exists() else {"cursor": "", "pages": 0, "done": False, "part": 0}
    buf = []

    def flush_hist():
        if buf:
            pd.DataFrame(buf).to_parquet(days_dir / f"hist_{st['part']:05d}.parquet", index=False)
            st["part"] += 1
            buf.clear()
        tmp = state_p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        os.replace(tmp, state_p)

    while not st["done"]:
        p = {"limit": 1000, "mve_filter": "exclude", **({"cursor": st["cursor"]} if st["cursor"] else {})}
        dd = c.get("/historical/markets", p)
        ms = dd.get("markets", [])
        buf += [to_row(m) for m in ms if (_num(m.get("volume_fp")) or 0) >= a.min_volume]
        st["pages"] += 1
        st["cursor"] = dd.get("cursor") or ""
        newest = max((m.get("close_time", "") for m in ms), default="")
        st["done"] = not st["cursor"] or not ms or newest[:10] < start_s
        if st["pages"] % 25 == 0 or st["done"]:
            flush_hist()
            print(f"historical page {st['pages']} newest close {newest} req={c.n} 429={c.n429} "
                  f"{time.time() - t0:.0f}s", flush=True)

    # 2b. live /markets honours the close filter: everything closing after the historical cutoff - margin
    live_lo = int(dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc).timestamp())
    got = [to_row(m) for m in c.pages("/markets", {"min_close_ts": live_lo, "mve_filter": "exclude"})
           if (_num(m.get("volume_fp")) or 0) >= a.min_volume]
    pd.DataFrame(got).to_parquet(days_dir / "live_closed.parquet", index=False)
    print(f"live closes since 2026-08-01 above floor: {len(got)} req={c.n}", flush=True)

    # 3. open markets
    got = [to_row(m) for m in c.pages("/markets", {"status": "open", "mve_filter": "exclude"})
           if (_num(m.get("volume_fp")) or 0) >= a.min_volume]
    print(f"open markets above floor: {len(got)}", flush=True)

    parts = [pd.read_parquet(p) for p in sorted(days_dir.glob("*.parquet"))]
    fresh = pd.concat([x for x in parts + [pd.DataFrame(got), refreshed] if len(x)], ignore_index=True)
    fresh = fresh.sort_values("updated_time", kind="mergesort")
    series = {s["ticker"]: s.get("category", "") for s in c.get("/series", {}).get("series", [])}
    merged = merge_markets(old, finalize_frame(fresh, old, series), a.min_volume)

    tmp = MARKETS.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False, compression="zstd")
    stamp = dt.datetime.fromtimestamp(MARKETS.stat().st_mtime).strftime("%Y-%m-%d")
    bak = MARKETS.with_name(f"markets_{stamp}.bak.parquet")
    if not bak.exists():
        shutil.copy2(MARKETS, bak)
    os.replace(tmp, MARKETS)
    summary = {"old_rows": len(old), "new_rows": len(merged), "added": int((~merged.ticker.isin(old.ticker)).sum()),
               "status": merged.status.value_counts().to_dict(), "max_close": merged.close_time.max(),
               "requests": c.n, "429s": c.n429, "backup": bak.name, "written": dt.datetime.now(dt.timezone.utc).isoformat()}
    (WORK / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

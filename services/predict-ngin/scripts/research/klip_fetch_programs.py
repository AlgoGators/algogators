"""Snapshot Kalshi incentive programmes (public, read-only GET /incentive_programs).

Writes data/vendor/klip/programs/<status>_<UTCstamp>.parquet. Never places orders.
Rate: <= 1 req/s with backoff on 429.

    venv/Scripts/python.exe -I scripts/research/klip_fetch_programs.py --status active upcoming
    venv/Scripts/python.exe -I scripts/research/klip_fetch_programs.py --status paid_out --max-pages 200
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

URL = "https://api.elections.kalshi.com/trade-api/v2/incentive_programs"
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "klip" / "programs"


def fetch(status: str, max_pages: int, s: requests.Session) -> pd.DataFrame:
    rows, cursor = [], None
    for page in range(max_pages):
        params = {"status": status, "limit": 10000}
        if cursor:
            params["cursor"] = cursor
        for i in range(8):
            t0 = time.time()
            r = s.get(URL, params=params, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(60, 2 ** i)); continue
            r.raise_for_status()
            break
        d = r.json()
        rows += d.get("incentive_programs", [])
        cursor = d.get("next_cursor")
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))
        if not cursor:
            break
    df = pd.DataFrame(rows)
    if len(df):
        df["status_query"] = status
    return df, cursor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", nargs="+", default=["active", "upcoming"])
    ap.add_argument("--max-pages", type=int, default=50)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    s = requests.Session()
    for st in a.status:
        df, cur = fetch(st, a.max_pages, s)
        p = OUT / f"{st}_{stamp}.parquet"
        df.to_parquet(p, index=False)
        print(st, len(df), "rows", "MORE" if cur else "complete", p.name)


if __name__ == "__main__":
    main()

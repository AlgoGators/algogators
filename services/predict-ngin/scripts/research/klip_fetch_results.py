"""Fetch market status/result for the LIP x recorder overlap tickers (public GET /markets?tickers=, read-only).
Writes data/vendor/klip/results_<stamp>.parquet."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
API = "https://api.elections.kalshi.com/trade-api/v2/markets"


def main():
    ov = pd.read_parquet(ROOT / "data/vendor/klip/recorder_lip_overlap.parquet")
    tk = sorted(ov.market_ticker.unique())
    s = requests.Session(); rows = []
    for i in range(0, len(tk), 100):
        for k in range(6):
            r = s.get(API, params={"tickers": ",".join(tk[i:i + 100]), "limit": 1000}, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** k); continue
            r.raise_for_status(); break
        for m in r.json().get("markets", []):
            rows.append({k2: m.get(k2) for k2 in ("ticker", "event_ticker", "status", "result", "close_time",
                                                   "settlement_ts", "expiration_value", "last_price_dollars")})
        time.sleep(1.0)
    df = pd.DataFrame(rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    df.to_parquet(ROOT / f"data/vendor/klip/results_{stamp}.parquet", index=False)
    print(len(df), df.status.value_counts().to_dict(), df.result.value_counts().to_dict())


if __name__ == "__main__":
    main()

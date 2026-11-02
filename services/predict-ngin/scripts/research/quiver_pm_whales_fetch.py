"""Download Quiver's full Polymarket large-trade feed (/beta/live/polymarkettrades).

Pages newest->oldest at <= 1 req/s until an empty page. Non-sports by default
(Quiver's default); pass --sports to include sports markets.
Output: data/vendor/quiver/pm_whales.parquet (local only, Quiver ToS).
"""
from __future__ import annotations

import sys

import pandas as pd

from quiver_probe import OUT, Client

def main():
    sports = "--sports" in sys.argv
    cli = Client("Bearer")
    rows, page = [], 1
    while True:
        params = {"page": page, "page_size": 500, "sort_by": "trade_time", "sort_order": "desc"}
        if sports:
            params["include_sports_markets"] = "true"
        r = cli.get("/beta/live/polymarkettrades", params)
        if r.status_code != 200:
            print("stop", page, r.status_code, r.text[:100]); break
        js = r.json()
        if not js:
            break
        rows.extend(js)
        if page % 25 == 0:
            print(page, len(rows), js[-1]["trade_time"], flush=True)
        page += 1
    df = pd.DataFrame(rows).drop_duplicates(["transaction_hash", "asset", "proxy_wallet", "size", "price"])
    fn = OUT / ("pm_whales_sports.parquet" if sports else "pm_whales.parquet")
    df.to_parquet(fn, index=False)
    print("rows", len(df), df.trade_time.min(), df.trade_time.max())


if __name__ == "__main__":
    main()

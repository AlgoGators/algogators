"""Fetch Kalshi series fee metadata + market results for the legal-venue test (read-only, public REST).

Outputs data/vendor/probalytics/analysis/legal_venue_kalshi_series.parquet  (series, fee_type, fee_multiplier, category)
        data/vendor/probalytics/analysis/legal_venue_kalshi_markets.parquet (ticker, status, result, settlement, close)
No auth is used; never places orders.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "vendor" / "probalytics"
OUT = D / "analysis"
BASE = "https://api.elections.kalshi.com/trade-api/v2"


def get(path: str) -> dict:
    for attempt in range(4):
        try:
            with urllib.request.urlopen(BASE + path, timeout=20) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                print("FAIL", path, e, file=sys.stderr)
                return {}
            time.sleep(1.5 * (attempt + 1))
    return {}


def tickers() -> list[str]:
    t = set()
    for sub in ("books", "books_inplay", "fills"):
        p = D / sub / "kalshi"
        if p.exists():
            t |= {f.stem for f in p.glob("*.parquet")}
    cfg = yaml.safe_load((ROOT / "config" / "recorder.yaml").read_text())
    for pr in cfg.get("pairs", []):
        if pr.get("kalshi"):
            t.add(pr["kalshi"]["ticker"])
    return sorted(t)


def main() -> None:
    tk = tickers()
    rows = []
    for i, t in enumerate(tk):
        m = get(f"/markets/{t}").get("market", {})
        rows.append(dict(ticker=t, event=m.get("event_ticker"), status=m.get("status"), result=m.get("result"),
                         settlement=m.get("settlement_value_dollars"), close=m.get("close_time"),
                         occurrence=m.get("occurrence_datetime"), settlement_ts=m.get("settlement_ts"),
                         volume=m.get("volume_fp"), title=m.get("title")))
        time.sleep(0.13)
    mk = pd.DataFrame(rows)
    mk["series"] = mk.ticker.str.split("-").str[0]
    mk.to_parquet(OUT / "legal_venue_kalshi_markets.parquet")
    srows = []
    for s in sorted(mk.series.unique()):
        js = get(f"/series/{s}").get("series", {})
        srows.append(dict(series=s, fee_type=js.get("fee_type"), fee_multiplier=js.get("fee_multiplier"),
                          category=js.get("category")))
        time.sleep(0.13)
    sr = pd.DataFrame(srows)
    sr.to_parquet(OUT / "legal_venue_kalshi_series.parquet")
    print(sr.to_string())
    print(mk.groupby(["series", "status", "result"], dropna=False).size().to_string())


if __name__ == "__main__":
    main()

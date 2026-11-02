"""Quiver Quantitative API inventory probe (research only).

Hits every documented endpoint (plus legacy endpoints from the quiverquant
python client) once, at <= 1 req/s, and records HTTP status, row count, date
range, columns and any rate-limit headers.  Raw JSON stays local under
data/vendor/quiver/raw/ (gitignored; Quiver ToS forbids redistribution).
The API key is read from .env and never printed.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "quiver"
RAW = OUT / "raw"
BASE = "https://api.quiverquant.com"


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("QUIVER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("QUIVER_API_KEY missing")


class Client:
    def __init__(self, scheme: str = "Bearer"):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"{scheme} {load_key()}", "Accept": "application/json"})
        self.last = 0.0

    def get(self, path: str, params: dict | None = None, timeout: int = 120):
        wait = 1.05 - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            self.last = time.time()
            r = self.s.get(BASE + path, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            return r
        return r


DATE_COLS = ["Date", "date", "TransactionDate", "Traded", "Filed", "ReportDate", "trade_time",
             "Time", "action_date", "Year", "Quarter", "AwardDate", "filing_date", "Created"]

ENDPOINTS = [
    # documented (schema.json)
    ("/beta/auth/premium", None), ("/beta/bulk/congress/politicians", None),
    ("/beta/bulk/congresstrading", None), ("/beta/bulk/corporatedonors", {"page_size": 200}),
    ("/beta/bulk/trumpstocktrades", None), ("/beta/coins", None), ("/beta/companies", None),
    ("/beta/funds", None), ("/beta/historical/allpatents/TSLA", None),
    ("/beta/historical/congresstrading/LMT", None), ("/beta/historical/corporatedonors/LMT", None),
    ("/beta/historical/dailyprices/TSLA", None), ("/beta/historical/eventsbeta/TSLA", None),
    ("/beta/historical/executivecompensation/TSLA", None), ("/beta/historical/govcontracts/LMT", None),
    ("/beta/historical/govcontractsall/LMT", None), ("/beta/historical/housetrading/LMT", None),
    ("/beta/historical/lobbying/LMT", None), ("/beta/historical/offexchange/TSLA", None),
    ("/beta/historical/senatetrading/LMT", None), ("/beta/live/allpatents", None),
    ("/beta/live/appratings", None), ("/beta/live/bill_summaries", {"page_size": 100}),
    ("/beta/live/cnbc", None), ("/beta/live/congress/politicians", None),
    ("/beta/live/congress_stock_holdings", None), ("/beta/live/congressholdings", None),
    ("/beta/live/congresstrading", None), ("/beta/live/earningsdistortionscores/TSLA", None),
    ("/beta/live/flights", None), ("/beta/live/govcontracts", None), ("/beta/live/govcontractsall", None),
    ("/beta/live/housetrading", None), ("/beta/live/insiders", None), ("/beta/live/legislation", None),
    ("/beta/live/lobbying", None), ("/beta/live/offexchange", None), ("/beta/live/patentdrift", None),
    ("/beta/live/patentmomentum", None), ("/beta/live/polymarkettrades", {"page_size": 200}),
    ("/beta/live/quivernews", None), ("/beta/live/revolvingdoor", None), ("/beta/live/rss", None),
    ("/beta/live/sec13f", None), ("/beta/live/sec13fchanges", None), ("/beta/live/senatetrading", None),
    ("/beta/live/topshareholders/TSLA", None), ("/beta/personalities", None),
    ("/beta/strategies/holdings", None), ("/beta/tickerdata", None), ("/beta/today/eventsbeta", None),
    # legacy (quiverquant client 0.2.6, not in current docs)
    ("/beta/historical/wikipedia/TSLA", None), ("/beta/live/wikipedia", None),
    ("/beta/historical/wallstreetbets/TSLA", None), ("/beta/live/wallstreetbets", None),
    ("/beta/historical/twitter/TSLA", None), ("/beta/live/twitter", None),
    ("/beta/historical/flights/TSLA", None), ("/beta/historical/politicalbeta/TSLA", None),
    ("/beta/live/politicalbeta", None), ("/beta/historical/spacs/TSLA", None),
    ("/beta/live/wsbcomments", None), ("/beta/live/cryptocomments", None),
    ("/beta/historical/appratings/TSLA", None), ("/beta/historical/jobs/TSLA", None),
    ("/beta/historical/webtraffic/TSLA", None), ("/beta/historical/sec13f/TSLA", None),
]


def summarize(path, r):
    row = {"endpoint": path, "status": r.status_code, "bytes": len(r.content),
           "ratelimit_headers": json.dumps({k: v for k, v in r.headers.items()
                                            if "limit" in k.lower() or "retry" in k.lower()})}
    try:
        js = r.json()
    except Exception:
        row["note"] = r.text[:120].replace("\n", " ")
        return row, None
    data = js
    if isinstance(js, dict):
        for k in ("data", "results", "items"):
            if isinstance(js.get(k), list):
                data = js[k]
                break
    if isinstance(data, list):
        df = pd.DataFrame(data) if data and isinstance(data[0], dict) else pd.DataFrame({"v": data})
        row["rows"] = len(df)
        row["columns"] = ",".join(map(str, df.columns))[:300]
        for c in DATE_COLS:
            if c in df.columns:
                d = pd.to_datetime(df[c], errors="coerce", utc=True, format="mixed")
                if d.notna().any():
                    row["date_col"] = c
                    row["min_date"] = str(d.min())[:19]
                    row["max_date"] = str(d.max())[:19]
                    break
    else:
        row["rows"] = 1
        txt = json.dumps(js)[:200]
        if "token" not in txt.lower():
            row["note"] = txt
    return row, js


def main():
    cli = Client(sys.argv[1] if len(sys.argv) > 1 else "Bearer")
    RAW.mkdir(parents=True, exist_ok=True)
    rows = []
    for path, params in ENDPOINTS:
        try:
            r = cli.get(path, params)
        except Exception as e:  # network
            rows.append({"endpoint": path, "status": -1, "note": str(e)[:120]})
            continue
        row, js = summarize(path, r)
        rows.append(row)
        if js is not None and r.status_code == 200:
            fn = path.strip("/").replace("/", "__") + ".json"
            (RAW / fn).write_text(json.dumps(js), encoding="utf-8")
        print(row["endpoint"], row["status"], row.get("rows"), row.get("min_date"), row.get("max_date"),
              row.get("note", "")[:80], flush=True)
    pd.DataFrame(rows).to_csv(OUT / "inventory_probe.csv", index=False)


if __name__ == "__main__":
    main()

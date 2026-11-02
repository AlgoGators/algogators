"""twc: download the public Kalshi Weather Index (settlement source of the hourly KXTEMP*H* markets).

GET https://external-api.kalshi.com/trade-api/v2/live_data/weather/{city}?from&to&detailed=true (public, no auth)
and /live_data/weather/{city}/calibrations. One call per city-day, default <= 0.5 req/s. Read-only.
Writes data/vendor/twc/index/<city>/<YYYYMMDD>.parquet with one row per (minute, station):
  t (event minute, ms), v, status, contributors, station_id, code, source, temp_f, obs_time_ms, received_at_ms.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "twc" / "index"
H = "https://external-api.kalshi.com/trade-api/v2/live_data/weather"
CITIES = ["miami", "dfw", "houston", "phl-delaware-valley", "puget-sound", "sf-bay", "greater-boston",
          "southeast-michigan", "kansas-city", "minneapolis-st-paul", "nyc", "chicago", "la-coastal"]
UA = {"User-Agent": "predict-ngin research (read-only)"}


class Getter:
    def __init__(self, rate):
        self.dt = 1.0 / rate
        self.last = 0.0
        self.s = requests.Session()

    def get(self, url, params=None):
        for i in range(8):
            w = self.dt - (time.time() - self.last)
            if w > 0:
                time.sleep(w)
            self.last = time.time()
            try:
                r = self.s.get(url, params=params, headers=UA, timeout=90)
            except requests.RequestException:
                time.sleep(2 ** i)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(60, 2 ** (i + 1)))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(url)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=0.5)
    ap.add_argument("--cities", default=",".join(CITIES))
    ap.add_argument("--end", default=None, help="UTC date (exclusive), default today")
    a = ap.parse_args()
    g = Getter(a.rate)
    end = pd.Timestamp(a.end or pd.Timestamp.utcnow().date(), tz="UTC")
    for c in a.cities.split(","):
        od = OUT / c
        od.mkdir(parents=True, exist_ok=True)
        cal = g.get(f"{H}/{c}/calibrations")
        json.dump(cal, open(od / "calibrations.json", "w"))
        start = min(x["effective_at_ms"] for x in cal["calibrations"])
        d = pd.Timestamp(start, unit="ms", tz="UTC").normalize()
        while d < end:
            p = od / f"{d:%Y%m%d}.parquet"
            if not p.exists():
                f = int(d.timestamp() * 1000)
                js = g.get(f"{H}/{c}", {"from": f, "to": f + 86400000 - 1, "detailed": "true"})
                rows = []
                for pt in js.get("timeseries", []) or []:
                    base = dict(t=pt["t"], v=pt.get("v"), status=pt.get("status"), contributors=pt.get("contributors"),
                                receipt_basis=pt.get("receipt_basis"), config=js.get("config_version"))
                    sts = pt.get("stations") or [{}]
                    for s in sts:
                        r = dict(base)
                        r.update(station_id=s.get("station_id"), code=s.get("code"), source=s.get("source"),
                                 temp_f=s.get("temp_f"), obs_time_ms=s.get("obs_time_ms"),
                                 received_at_ms=s.get("received_at_ms"), primary_code=s.get("primary_code"))
                        rows.append(r)
                pd.DataFrame(rows).to_parquet(p, index=False)
                print(c, d.date(), len(rows), flush=True)
            d += pd.Timedelta(days=1)


if __name__ == "__main__":
    main()

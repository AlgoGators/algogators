"""wx: fetch free Iowa Environmental Mesonet archives (no key) for Kalshi temperature stations.

  mos   NBM station text guidance (model=NBS): runtime, ftime, tmp, txn (12h max/min), xnd (txn spread)
  asos  METAR/ASOS reports (routine + specials) with raw METAR (T-group, 6h max/min groups)
  cli   NWS CLI daily high/low (IEM parsed JSON) incl. product id (issue time)
  afos  raw CLI text products (all issuances: preliminary afternoon + final) -> issue times

Polite: sequential, 1 s between requests. Outputs data/vendor/wx/iem/<kind>/<code>_<chunk>.parquet
"""
from __future__ import annotations
import argparse
import datetime as dt
import io
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wx_common import OUT, STATIONS  # noqa: E402

IEM = "https://mesonet.agron.iastate.edu"
S = requests.Session()


def get(url, params=None, tries=40):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=300)
            if r.status_code == 200:
                time.sleep(2.0)
                return r
            print("HTTP", r.status_code, url, params, flush=True)
        except requests.RequestException as e:
            print("ERR", e, flush=True)
        time.sleep(min(300, 30 * (i + 1)))  # IEM throttles bursts (429): back off hard
    raise RuntimeError(url)


def months(a, b):
    d = pd.Timestamp(a).replace(day=1)
    while d <= pd.Timestamp(b):
        e = d + pd.offsets.MonthBegin(1)
        yield d, e
        d = e


def fetch_mos(codes, a, b):
    od = OUT / "iem" / "mos"; od.mkdir(parents=True, exist_ok=True)
    for c in codes:
        icao = STATIONS[c][0]
        for d, e in months(a, b):
            p = od / f"{c}_{d:%Y%m}.parquet"
            if p.exists():
                continue
            r = get(f"{IEM}/cgi-bin/request/mos.py", dict(station=icao, model="NBS", sts=f"{d:%Y-%m-%d}T00:00Z",
                                                            ets=f"{e:%Y-%m-%d}T00:00Z", format="csv"))
            df = pd.read_csv(io.StringIO(r.text), usecols=lambda x: x in ("runtime", "ftime", "tmp", "txn", "xnd"))
            df.to_parquet(p, index=False)
            print("mos", c, d.date(), len(df), flush=True)


def fetch_asos(codes, a, b):
    od = OUT / "iem" / "asos"; od.mkdir(parents=True, exist_ok=True)
    for c in codes:
        sid = STATIONS[c][1]
        for d, e in months(a, b):
            p = od / f"{c}_{d:%Y%m}.parquet"
            if p.exists():
                continue
            r = get(f"{IEM}/cgi-bin/request/asos.py", [("station", sid), ("data", "tmpf"), ("data", "metar"),
                     ("year1", d.year), ("month1", d.month), ("day1", 1), ("year2", e.year), ("month2", e.month),
                     ("day2", 1), ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"), ("missing", "M"),
                     ("report_type", 3), ("report_type", 4)])
            df = pd.read_csv(io.StringIO(r.text), na_values=["M"])
            df.to_parquet(p, index=False)
            print("asos", c, d.date(), len(df), flush=True)


def fetch_cli(codes, years):
    od = OUT / "iem" / "cli"; od.mkdir(parents=True, exist_ok=True)
    for c in codes:
        for y in years:
            p = od / f"{c}_{y}.parquet"
            if p.exists() and y < dt.date.today().year:
                continue
            r = get(f"{IEM}/json/cli.py", dict(station=STATIONS[c][0], year=y))
            df = pd.DataFrame(r.json().get("results", []))
            keep = [k for k in ("station", "valid", "product", "high", "high_time", "low", "low_time") if k in df]
            df[keep].astype(str).to_parquet(p, index=False)
            print("cli", c, y, len(df), flush=True)


def fetch_afos(codes, a, b):
    """All CLI issuances; keep WMO issue time (ddhhmm), summary date, VALID line, max/min."""
    od = OUT / "iem" / "afos"; od.mkdir(parents=True, exist_ok=True)
    for c in codes:
        pil = STATIONS[c][3]
        for d, e in months(a, b):
            p = od / f"{c}_{d:%Y%m}.parquet"
            if p.exists():
                continue
            r = get(f"{IEM}/cgi-bin/afos/retrieve.py", dict(pil=pil, sdate=f"{d:%Y-%m-%d}", edate=f"{e:%Y-%m-%d}",
                                                             fmt="text", limit=9999))
            rows = []
            for prod in r.text.split("\x01"):
                m = re.search(r"(\w{6}) (\w{4}) (\d{6})", prod)
                if not m:
                    continue
                summ = re.search(r"CLIMATE SUMMARY FOR ([A-Z]+ +\d+ +\d{4})", prod)
                valid = re.search(r"VALID (TODAY AS OF [0-9]+ [AP]M|AS OF [0-9]+ [AP]M[^\n]*)", prod)
                mx = re.search(r"\n\s*MAXIMUM\s+(-?\d+|MM)", prod)
                mn = re.search(r"\n\s*MINIMUM\s+(-?\d+|MM)", prod)
                cor = bool(re.search(r"\bCC[A-Z]\b|CORRECTED", prod[:400]))
                rows.append(dict(wmo_ddhhmm=m.group(3), summary_date=summ.group(1) if summ else None,
                                 valid=valid.group(0) if valid else None, tmax=mx.group(1) if mx else None,
                                 tmin=mn.group(1) if mn else None, correction=cor, month=f"{d:%Y-%m}"))
            pd.DataFrame(rows).to_parquet(p, index=False)
            print("afos", c, d.date(), len(rows), flush=True)


def fetch_1min(codes, a, b):
    """ASOS 1-minute temperatures (whole F; IEM archive, delayed - NOT real time). 5-min public obs are every
    5th sample of this stream, so this is used to emulate the free real-time 5-min feed."""
    od = OUT / "iem" / "asos1min"; od.mkdir(parents=True, exist_ok=True)
    for c in codes:
        sid = STATIONS[c][1]
        for d, e in months(a, b):
            p = od / f"{c}_{d:%Y%m}.parquet"
            if p.exists():
                continue
            s0 = max(d, pd.Timestamp(a)); e0 = min(e, pd.Timestamp(b) + pd.Timedelta(days=1))
            r = get(f"{IEM}/cgi-bin/request/asos1min.py", dict(station=sid, vars="tmpf", sts=f"{s0:%Y-%m-%dT%H:%M}Z",
                                                                ets=f"{e0:%Y-%m-%dT%H:%M}Z", sample="1min", what="download", tz="UTC"))
            df = pd.read_csv(io.StringIO(r.text), na_values=["M"])
            df = df.rename(columns={"valid(UTC)": "valid"})[["valid", "tmpf"]].dropna()
            df.to_parquet(p, index=False)
            print("1min", c, d.date(), len(df), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["mos", "asos", "cli", "afos", "onemin"])
    ap.add_argument("--codes", default=",".join(STATIONS))
    ap.add_argument("--start", default="2024-10-01")
    ap.add_argument("--end", default="2026-10-14")
    a = ap.parse_args()
    codes = a.codes.split(",")
    if a.kind == "mos":
        fetch_mos(codes, a.start, a.end)
    elif a.kind == "asos":
        fetch_asos(codes, a.start, a.end)
    elif a.kind == "cli":
        fetch_cli(codes, range(int(a.start[:4]), int(a.end[:4]) + 1))
    elif a.kind == "afos":
        fetch_afos(codes, a.start, a.end)
    else:
        fetch_1min(codes, a.start, a.end)

"""twc: measure aviationweather.gov METAR availability latency (obs time -> first seen). Free, no key. Read-only.
Polls https://aviationweather.gov/api/data/metar?ids=...&format=json every 20 s for --minutes; appends to
data/vendor/twc/awc_latency.jsonl."""
import argparse, json, time
from pathlib import Path
import requests
OUT = Path(__file__).resolve().parents[2] / "data" / "vendor" / "twc" / "awc_latency.jsonl"
ap = argparse.ArgumentParser(); ap.add_argument("--minutes", type=float, default=30)
ap.add_argument("--ids", default="KMIA,KLGA,KORD,KLAX,KNYC,KDCA,KAUS,KMDW,KEWR,KBOS")
a = ap.parse_args()
seen = set(); end = time.time() + a.minutes * 60
with open(OUT, "a") as f:
    while time.time() < end:
        try:
            r = requests.get("https://aviationweather.gov/api/data/metar", params={"ids": a.ids, "format": "json"},
                             headers={"User-Agent": "predict-ngin research"}, timeout=20)
            now = time.time()
            for m in r.json():
                key = (m.get("icaoId"), m.get("obsTime"), m.get("rawOb"))
                if key in seen:
                    continue
                seen.add(key)
                f.write(json.dumps(dict(recv=now, stn=m.get("icaoId"), obs=m.get("obsTime"), temp=m.get("temp"),
                                        raw=m.get("rawOb"), report_time=m.get("reportTime"), boot=len(seen) <= 10)) + "\n")
            f.flush()
        except Exception as e:  # noqa: BLE001
            print("err", e, flush=True)
        time.sleep(20)

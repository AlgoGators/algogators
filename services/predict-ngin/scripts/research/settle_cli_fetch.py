"""settle: fetch NWS CLI daily climate summaries (precip / snow / temps + product id) from IEM json/cli.py (free).
usage: settle_cli_fetch.py KNYC KLAX ...  -> data/vendor/settle/cli/<STATION>.parquet"""
import sys, time
from pathlib import Path
import pandas as pd, requests
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/vendor/settle/cli"; OUT.mkdir(parents=True, exist_ok=True)
for st in sys.argv[1:]:
    rows = []
    for y in [2023, 2024, 2025, 2026]:
        for i in range(5):
            r = requests.get("https://mesonet.agron.iastate.edu/json/cli.py", params={"station": st, "year": y}, timeout=60)
            if r.status_code == 200:
                break
            time.sleep(5 * (i + 1))
        rows += r.json().get("results", [])
        time.sleep(1)
    d = pd.DataFrame(rows)
    keep = [c for c in ["station", "valid", "product", "high", "low", "precip", "precip_month", "snow", "snow_month", "snowdepth"] if c in d]
    d = d[keep].astype(str)
    d.to_parquet(OUT / f"{st}.parquet")
    print(st, len(d), d.valid.min(), d.valid.max(), flush=True)

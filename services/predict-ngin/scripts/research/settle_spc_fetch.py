"""settle: SPC daily filtered tornado reports (free, spc.noaa.gov/climo/reports/YYMMDD_rpts_filtered_torn.csv).
SPC report day D covers 12Z D .. 12Z D+1. Output data/vendor/settle/spc_torn_daily.parquet (count per report day)."""
import time, io
from pathlib import Path
import pandas as pd, requests
ROOT = Path(__file__).resolve().parents[2]
f = ROOT / "data/vendor/settle/spc_torn_daily.parquet"
old = pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=["day", "n", "status"])
have = set(old.day)
rows = old.to_dict("records")
s = requests.Session()
for d in pd.date_range("2024-06-01", "2026-10-12"):
    k = d.strftime("%Y-%m-%d")
    if k in have:
        continue
    url = f"https://www.spc.noaa.gov/climo/reports/{d:%y%m%d}_rpts_filtered_torn.csv"
    r = s.get(url, timeout=60)
    n = None
    if r.status_code == 200:
        lines = [l for l in r.text.splitlines() if l.strip()]
        n = max(0, len(lines) - 1)
    rows.append(dict(day=k, n=n, status=r.status_code))
    time.sleep(0.3)
pd.DataFrame(rows).to_parquet(f)
d = pd.DataFrame(rows); print(d.status.value_counts(), d.n.sum())

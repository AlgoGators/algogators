"""settle: parse TSA checkpoint throughput (tsa.gov/travel/passenger-volumes, free) into data/vendor/settle/tsa_daily.parquet."""
import re, sys, time
from pathlib import Path
import pandas as pd, requests
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/vendor/settle/raw"; RAW.mkdir(parents=True, exist_ok=True)
rows = []
for y in ["cur", "2025", "2024", "2023", "2022"]:
    f = RAW / f"tsa_{y}.html"
    if not f.exists():
        url = "https://www.tsa.gov/travel/passenger-volumes" + ("" if y == "cur" else f"/{y}")
        f.write_text(requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60).text, encoding="utf8"); time.sleep(1)
    t = f.read_text(encoding="utf8")
    for d, n in re.findall(r'<td[^>]*>\s*(\d{1,2}/\d{1,2}/\d{4})\s*</td>\s*<td[^>]*>\s*([\d,]+)\s*</td>', t):
        rows.append((pd.Timestamp(d), int(n.replace(",", "")), y))
d = pd.DataFrame(rows, columns=["date", "pax", "page"]).drop_duplicates("date").sort_values("date")
print(d.groupby("page").date.agg(["min", "max", "count"]))
d.to_parquet(ROOT / "data/vendor/settle/tsa_daily.parquet")

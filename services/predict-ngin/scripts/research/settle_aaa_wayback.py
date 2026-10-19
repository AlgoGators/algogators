"""settle: rebuild the AAA US daily regular-gas average from Wayback Machine snapshots of gasprices.aaa.com (free).
Each snapshot gives 'Price as of <date>' with Current / Yesterday / Week ago / Month ago averages (3 dp), and its
capture time tells us when that date's value was already public (an upper bound on AAA's publish time).
Output: data/vendor/settle/aaa_snapshots.parquet (one row per snapshot)."""
import re, time, json
from pathlib import Path
import pandas as pd, requests
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/vendor/settle"
f = OUT / "aaa_snapshots.parquet"
old = pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=["snap"])
have = set(old.snap)
s = requests.Session(); s.headers["User-Agent"] = "research (settle)"
cdx = s.get("http://web.archive.org/cdx/search/cdx", params={"url": "gasprices.aaa.com/", "from": "2023", "to": "2026",
            "output": "json", "fl": "timestamp", "filter": "statuscode:200"}, timeout=120).json()[1:]
snaps = sorted({c[0] for c in cdx})
# keep up to 3 captures per UTC day (earliest, ~middle, latest) to learn publish timing without hammering wayback
by = {}
for t in snaps:
    by.setdefault(t[:8], []).append(t)
pick = []
for d, ts in by.items():
    if d < "20240601":
        continue
    # one capture per UTC day (the first at/after 13:00 UTC, i.e. after the morning update), plus every
    # capture in Sep-Oct 2025 and Jan 2026 to locate the daily update time
    late = [t for t in ts if t[8:] >= "130000"]
    pick.append(late[0] if late else ts[-1])
    if d[:6] in ("202509", "202601"):
        pick += ts
pick = sorted(set(p for p in pick if p not in have))
print("captures", len(snaps), "days", len(by), "to fetch", len(pick), flush=True)
rows = old.to_dict("records")
from concurrent.futures import ThreadPoolExecutor


def one(ts):
    r = None
    for k in range(4):
        try:
            r = s.get(f"http://web.archive.org/web/{ts}id_/https://gasprices.aaa.com/", timeout=60)
            if r.status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(5 * (k + 1))
    if r is None or r.status_code != 200:
        return dict(snap=ts)
    t = r.text
    asof = re.search(r"Price as of\s*(\d{1,2}/\d{1,2}/\d{2})", t)
    nums = {}
    for lab in ["Current Avg.", "Yesterday Avg.", "Week Ago Avg.", "Month Ago Avg.", "Year Ago Avg."]:
        m = re.search(re.escape(lab) + r"\s*</td>\s*<td>\s*\$([\d.]+)", t)
        if not m:
            m = re.search(re.escape(lab) + r"[^$]{0,200}\$([\d.]+)", t)
        nums[lab.split()[0].lower()] = float(m.group(1)) if m else None
    return dict(snap=ts, asof=asof.group(1) if asof else None, **nums)


with ThreadPoolExecutor(3) as ex:
    for i, rec in enumerate(ex.map(one, pick)):
        rows.append(rec)
        if i % 100 == 0:
            pd.DataFrame(rows).to_parquet(f); print(i, rec, flush=True)
pd.DataFrame(rows).to_parquet(f)
print("done", len(rows))

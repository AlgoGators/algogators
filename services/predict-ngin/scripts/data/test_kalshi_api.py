#!/usr/bin/env python3
"""Quick test of Kalshi API to check volume distribution and verify endpoints."""
import sys
import requests
import time

sys.stdout.reconfigure(line_buffering=True)

API = "https://api.elections.kalshi.com/trade-api/v2"
s = requests.Session()
s.headers.update({"User-Agent": "PredictionMarketResearch/1.0"})

print("Testing Kalshi API...")
r = s.get(f"{API}/markets", params={"limit": 3}, timeout=30)
print(f"Status: {r.status_code}")
data = r.json()
markets = data.get("markets", [])
print(f"Got {len(markets)} markets in first page")
for m in markets:
    print(f"  {m.get('ticker')} vol={m.get('volume',0)} status={m.get('status')}")
r.close()

# Scan first N pages for volume distribution
print("\nScanning markets for volume >= 100k...")
found = []
cursor = ""
scanned = 0
MAX_PAGES = 300  # scan up to 300k markets

for page in range(MAX_PAGES):
    params = {"limit": 1000}
    if cursor:
        params["cursor"] = cursor
    try:
        r = s.get(f"{API}/markets", params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(2)
            r.close()
            continue
        if r.status_code != 200:
            print(f"Error: {r.status_code}")
            r.close()
            break
        data = r.json()
        r.close()
    except Exception as e:
        print(f"Exception at page {page}: {e}")
        break

    batch = data.get("markets", [])
    new_cursor = data.get("cursor", "")
    del data

    if not batch:
        print(f"No more markets at page {page}")
        break

    for m in batch:
        v = m.get("volume", 0) or 0
        if v >= 100000:
            found.append({
                "ticker": m.get("ticker", ""),
                "event_ticker": m.get("event_ticker", ""),
                "volume": v,
                "volume_24h": m.get("volume_24h", 0) or 0,
                "status": m.get("status", ""),
                "title": (m.get("title", "") or "")[:60],
            })
    scanned += len(batch)
    del batch
    cursor = new_cursor

    if (page + 1) % 20 == 0:
        print(f"  Page {page+1}: scanned {scanned:,}, found {len(found)} with vol>=100k")

    if not cursor:
        print(f"Reached end at page {page+1}")
        break
    time.sleep(0.05)

print(f"\nResults: {len(found)} markets with volume >= 100,000 in {scanned:,} total markets")
found.sort(key=lambda x: x["volume"], reverse=True)
for m in found[:30]:
    print(f"  {m['ticker']:40s} vol={m['volume']:>12,}  24h={m['volume_24h']:>10,}  {m['status']:8s}  {m['title']}")

s.close()
print("\nDone.")

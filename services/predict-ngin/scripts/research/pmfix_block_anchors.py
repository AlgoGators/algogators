"""Fetch true Polygon block timestamps for the blocks NOT covered by data/vendor/alpha_decay/block_anchors.parquet.

The existing anchor table (alpha_decay namespace, read-only) covers blocks 56,438,000-80,978,000 every 100 blocks.
data/polymarket/trades.parquet starts at block 35,896,869, so this script fetches every STEP-th block from
35,800,000 to 56,438,000 from the free public Polygon RPC (batched eth_getBlockByNumber, 100 per request,
~1 request/s) and writes data/vendor/pmfix/block_anchors_lo.parquet (block, ts). It resumes from partial output.

    venv/Scripts/python.exe scripts/research/pmfix_block_anchors.py [lo] [hi] [step]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "pmfix" / "block_anchors_lo.parquet"
RPC = "https://polygon-bor-rpc.publicnode.com"


def fetch_chunk(chunk: list[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    batch = [{"jsonrpc": "2.0", "method": "eth_getBlockByNumber", "params": [hex(b), False], "id": b} for b in chunk]
    for attempt in range(8):
        try:
            r = requests.post(RPC, json=batch, timeout=60)
            j = r.json()
            if isinstance(j, list):
                for x in j:
                    if x.get("result"):
                        out[int(x["id"])] = int(x["result"]["timestamp"], 16)
                if all(b in out for b in chunk):
                    return out
                batch = [q for q in batch if q["id"] not in out]
        except Exception as e:  # noqa: BLE001
            print("err", repr(e)[:120], flush=True)
        time.sleep(1 + 2 * attempt)
    return out


def main() -> None:
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 35_800_000
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 56_438_000
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 500
    want = list(range(lo, hi + step, step))
    have: dict[int, int] = {}
    if OUT.exists():
        d = pd.read_parquet(OUT)
        have = dict(zip(d.block.astype(int), d.ts.astype(int)))
    todo = [b for b in want if b not in have]
    print(f"{len(want)} wanted, {len(todo)} to fetch", flush=True)
    for i in range(0, len(todo), 100):
        have.update(fetch_chunk(todo[i:i + 100]))
        time.sleep(1.0)
        if (i // 100) % 25 == 0 or i + 100 >= len(todo):
            pd.DataFrame({"block": list(have), "ts": list(have.values())}).sort_values("block").to_parquet(OUT, index=False)
            print(f"{i + 100}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()

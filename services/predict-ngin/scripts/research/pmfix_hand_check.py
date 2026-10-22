"""Pull the raw on-chain record for random Polymarket prints so their YES mapping can be checked by hand.

* 20 random rows of data/polymarket/trades.parquet (seed 20261015): fetch the transaction receipt from the
  public Polygon RPC, find the OrderFilled log whose topic[1] is the row's order_hash and decode
  (makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled, fee) plus maker/taker from topics.
* 10 random Data-API ticks from data/research/<cat>/trades.parquet: fetch the receipt and list every OrderFilled
  log touching the tick's asset with the tick's proxyWallet as maker or taker.

Writes data/vendor/pmfix/hand_check_raw.json (raw records only; expected values are filled in by hand in
tests/unit/fixtures/pmfix_hand_checked_fills.json).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vendor" / "pmfix" / "hand_check_raw.json"
RPC = "https://polygon.drpc.org"  # publicnode refuses archive receipts without a token
CATS = ["Politics", "Economy", "Sports", "Geopolitics", "Tech"]


def receipt(tx: str) -> dict:
    tx = tx if tx.startswith("0x") else "0x" + tx
    for attempt in range(6):
        try:
            r = requests.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt",
                                         "params": [tx]}, timeout=30).json()
            if r.get("result"):
                return r["result"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2 + 2 * attempt)
    return {}


def decode(log: dict) -> dict:
    t = log["topics"]
    words = [int(log["data"][2 + 64 * i: 2 + 64 * (i + 1)], 16) for i in range((len(log["data"]) - 2) // 64)]
    return {
        "order_hash": t[1][2:], "maker": "0x" + t[2][-40:], "taker": "0x" + t[3][-40:],
        "makerAssetId": str(words[0]), "takerAssetId": str(words[1]),
        "makerAmountFilled": words[2], "takerAmountFilled": words[3], "fee": words[4] if len(words) > 4 else None,
        "contract": log["address"], "logIndex": int(log["logIndex"], 16),
    }


def main() -> None:
    rng = np.random.default_rng(20261015)
    n_rows = 293_333_497
    picks = sorted(int(x) for x in rng.choice(n_rows, 20, replace=False))
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(ROOT / "data" / "polymarket" / "trades.parquet")
    starts = np.cumsum([0] + [pf.metadata.row_group(i).num_rows for i in range(pf.metadata.num_row_groups)])
    cols = ["block_number", "transaction_hash", "order_hash", "market_id", "nonusdc_side", "asset_id", "maker",
            "taker", "maker_direction", "taker_direction", "price", "usd_amount", "token_amount"]
    parts = []
    for p in picks:
        gi = int(np.searchsorted(starts, p, side="right") - 1)
        t = pf.read_row_group(gi, columns=cols).slice(p - int(starts[gi]), 1).to_pandas()
        t.insert(0, "file_row_number", p)
        parts.append(t)
    rows = __import__("pandas").concat(parts, ignore_index=True)
    out = {"onchain": [], "data_api": []}
    for r in rows.to_dict("records"):
        rc = receipt(r["transaction_hash"])
        logs = [decode(lg) for lg in rc.get("logs", []) if len(lg.get("topics", [])) == 4
                and lg["topics"][1][2:].lower() == str(r["order_hash"]).lower()]
        r = {k: (int(v) if isinstance(v, (np.integer,)) else (float(v) if isinstance(v, np.floating) else v))
             for k, v in r.items()}
        out["onchain"].append({"file_row": r, "raw_logs": logs})
        time.sleep(0.5)
        print("onchain", r["file_row_number"], len(logs), flush=True)
    for i in range(10):
        cat = CATS[i % len(CATS)]
        import pandas as pd

        f = ROOT / "data" / "research" / cat / "trades.parquet"
        tk = pd.read_parquet(f, columns=["timestamp", "conditionId", "asset", "outcome", "outcomeIndex", "side",
                                         "price", "size", "proxyWallet", "transactionHash"])
        tk = tk[tk.timestamp > 0].reset_index(drop=True)
        r = tk.iloc[int(rng.integers(len(tk)))].to_dict()
        r = {k2: (float(v) if isinstance(v, (np.floating,)) else (int(v) if isinstance(v, np.integer) else v))
             for k2, v in r.items()}
        rc = receipt(r["transactionHash"])
        w = str(r["proxyWallet"]).lower()
        logs = [decode(lg) for lg in rc.get("logs", []) if len(lg.get("topics", [])) == 4 and len(lg["data"]) == 2 + 64 * 5]
        logs = [x for x in logs if r["asset"] in (x["makerAssetId"], x["takerAssetId"]) and w in (x["maker"].lower(), x["taker"].lower())]
        out["data_api"].append({"category": cat, "tick": r, "raw_logs": logs})
        time.sleep(0.5)
        print("data_api", cat, len(logs), flush=True)
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print("wrote", OUT)


if __name__ == "__main__":
    main()

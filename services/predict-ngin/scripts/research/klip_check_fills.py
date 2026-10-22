"""Hand check of YES/NO normalisation: 20 random simulated fills with the print that filled them and the
book row before it. Prints, for each fill: side, our own-side price, the print (YES price, taker side),
and the book's YES best bid / best ask. A YES-bid fill must come from a taker-NO print at YES price <= our bid;
a NO-bid fill (own price c) must come from a taker-YES print at YES price >= 1-c."""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.recorder.writer import BOOK_SCHEMA, TRADE_SCHEMA  # noqa: E402

tag = sys.argv[1] if len(sys.argv) > 1 else "base"
f = pd.read_parquet(ROOT / f"data/vendor/klip/analysis/fills_{tag}.parquet")
f = f[f.q == 100].sample(20, random_state=7)
tk = set(f.ticker)
T = pd.concat([pq.read_table(x, schema=TRADE_SCHEMA).to_pandas() for x in sorted(glob.glob(str(ROOT / "data/live_books/kalshi/2026-10-1[234]/trades-*.parquet")))])
T["market_id"] = T.market_id.astype(str); T = T[T.market_id.isin(tk)]
B = pd.concat([pq.read_table(x, schema=BOOK_SCHEMA, columns=["market_id", "ts_recv_ns", "best_bid", "best_ask"]).to_pandas() for x in sorted(glob.glob(str(ROOT / "data/live_books/kalshi/2026-10-1[234]/books-*.parquet")))])
B["market_id"] = B.market_id.astype(str); B = B[B.market_id.isin(tk)].sort_values("ts_recv_ns")
ok = 0
for r in f.itertuples():
    t = T[(T.market_id == r.ticker) & (T.ts_recv_ns == r.ts)].iloc[0]
    b = B[(B.market_id == r.ticker) & (B.ts_recv_ns < r.ts)].tail(1)
    if r.side == "yes":
        good = str(t.taker_side) == "no" and t.price <= r.price + 1e-9
    else:
        good = str(t.taker_side) == "yes" and t.price >= 1 - r.price - 1e-9
    ok += good
    print(f"{r.ticker:34s} {r.placement:7s} our {r.side:3s} bid {r.price:.2f} | print yes_px {t.price:.3f} taker {t.taker_side:3s} sz {t['size']:.0f} | "
          f"book yes bid/ask {b.best_bid.iloc[0] if len(b) else np.nan:.2f}/{b.best_ask.iloc[0] if len(b) else np.nan:.2f} | {'OK' if good else 'BAD'}")
print(ok, "/ 20 consistent")

"""tailcarry: queue depth Q0 at the best NO bid for tail markets (YES best bid <= 8c) from the live recorder books.

Reads data/live_books/kalshi/<date>/books-*.parquet (read-only). A tail snapshot has 0 < best YES bid <= 0.08; the
NO-bid depth is the YES-ask size at the best level (ask_sz[1]). Per market: median NO-bid depth over tail snapshots.
Per study category: median over markets, floored at 20; categories with < 5 recorder markets fall back to the
pre-registered default 1000 (the mkgap convention). Also reports the 1c-spread share and the median spread.
Output: data/vendor/tailcarry/books_q0.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tailcarry_data as D  # noqa: E402

BOOKS = (D.ROOT / "data/live_books/kalshi").as_posix() + "/*/books-*.parquet"


def main():
    c = duckdb.connect(); c.execute("SET memory_limit='3GB'; SET threads=1; SET enable_progress_bar=false")
    m = c.execute(f"""
      select market_id, regexp_extract(market_id, '^([A-Za-z0-9]+)', 1) series,
             median(ask_sz[1]) no_q, avg((best_ask - best_bid < 0.0105)::int) sp1, median(best_ask - best_bid) spread,
             count(*) n
      from '{BOOKS}' where best_bid > 0 and best_bid <= 0.08 and best_ask < 1 and best_bid < best_ask
            and len(ask_sz) > 0
      group by 1, 2""").df()
    s = D.series_table(c)[["series", "category"]]
    m = m.merge(s, on="series", how="inner")
    f = m.groupby("category").agg(markets=("market_id", "size"), no_q_med=("no_q", "median"),
                                  sp1_share=("sp1", "mean"), spread_med=("spread", "median"))
    f["q0"] = np.where(f.markets >= 5, f.no_q_med.clip(lower=20).round(), 1000.0)
    f.to_csv(D.OUT / "books_q0.csv")
    print(f.round(3).to_string())


if __name__ == "__main__":
    main()

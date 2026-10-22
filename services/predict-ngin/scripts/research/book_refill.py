"""Does the best price come back after a taker order clears it? Refill and drift after depleting orders.

Follow-up to book_walk_cost.py (2026-10-16). If liquidity at the touch refills within seconds and the price does not
run away, an order split over time pays close to the touch on every slice, which beats sweeping a thin book.

Data: data/live_books/{venue}/*/{trades,books,markets}-*.parquet (atlas recorder, read-only).

Taker orders = trades grouped by (market, taker side, exchange timestamp). Buying (Kalshi taker 'yes', Polymarket
'BUY') hits the asks; selling (Kalshi 'no', Polymarket 'SELL') hits the bids. Kalshi trade prices are YES prices.

Pre-trade book: the last stored book for that market strictly before the order (exchange time on Kalshi, receive
time on Polymarket). An order is kept only when its first fill was at that book's best price (the book lines up),
and it is "depleting" when its size >= the size shown at that price. After +h seconds (the last stored book at or
before the order's receive time + h):
    refill  = size now available at a price at least as good as the old touch / size that was there before
    back    = best price is again at least as good as the old touch
    drift_c = how much worse the best price is than the old touch, in cents (the price a later slice would pay)
All depleting orders are kept, and a 10% hash sample of the others (as a comparison group).
Horizons past the market's close are dropped, as are orders with no stored book after the pre-trade one.
Processed one UTC day of files at a time; a horizon that runs past that day's last stored book is dropped.
Books are stored when they change, at most about once a second, so h < 2 s is not resolved.

    uv run python scripts/research/book_refill.py
Writes CSVs to data/research_reports/book_refill_<date>/.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
BOOKS = ROOT / "data/live_books"
HORIZONS = [2, 5, 15, 30, 60, 300]
OUT = ROOT / "data/research_reports" / f"book_refill_{dt.date.today():%Y-%m-%d}"


def q(p) -> str:
    return "'" + str(p).replace("\\", "/") + "'"


def run_day(c: duckdb.DuckDBPyConnection, venue: str, day: Path) -> tuple[int, int, int]:
    """Append this day's per-order, per-horizon rows to `post_all`. Returns (orders, aligned, depleting)."""
    buy = "'yes'" if venue == "kalshi" else "'BUY', 'buy'"
    # Kalshi books carry the exchange time of the last update; Polymarket's are not reliable for this, use receive time
    pre_ts = "ts_exchange_ns" if venue == "kalshi" else "ts_recv_ns"
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE orders AS
        SELECT market_id, taker_side IN ({buy}) AS is_buy,
               ts_exchange_ns, min(ts_recv_ns) AS ts_recv_ns, min({pre_ts}) AS ts_pre,
               sum(size) AS ct, count(*) AS fills,
               CASE WHEN taker_side IN ({buy}) THEN min(price) ELSE max(price) END AS first_px
        FROM read_parquet({q(day / 'trades-*.parquet')}, union_by_name=true)
        WHERE taker_side IS NOT NULL AND size > 0 AND ts_exchange_ns IS NOT NULL
        GROUP BY market_id, taker_side, ts_exchange_ns""")
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE bk AS
        SELECT market_id, ts_recv_ns, {pre_ts} AS ts_pre, best_bid, best_ask, bid_px, bid_sz, ask_px, ask_sz
        FROM read_parquet({q(day / 'books-*.parquet')}, union_by_name=true)
        WHERE market_id IN (SELECT DISTINCT market_id FROM orders) AND NOT coalesce(gap, false)
              AND {pre_ts} IS NOT NULL""")
    c.execute("""
        CREATE OR REPLACE TEMP TABLE ev AS
        SELECT o.*, coalesce(g.grp, '(none)') AS grp, g.close_ns, b.ts_pre AS pre_book_ts,
               CASE WHEN o.is_buy THEN b.best_ask ELSE b.best_bid END AS p0,
               CASE WHEN o.is_buy THEN b.ask_sz[1] ELSE b.bid_sz[1] END AS d0
        FROM orders o ASOF JOIN (SELECT market_id, ts_pre, best_ask, best_bid, ask_sz[1:1] AS ask_sz,
                                        bid_sz[1:1] AS bid_sz FROM bk) b
             ON o.market_id = b.market_id AND o.ts_pre > b.ts_pre
        LEFT JOIN grp g ON g.market_id = o.market_id""")
    c.execute("""
        CREATE OR REPLACE TEMP TABLE ev AS
        SELECT *, ct >= d0 AS depleting FROM ev
        WHERE p0 IS NOT NULL AND abs(first_px - p0) < 0.0005 AND d0 > 0 AND p0 BETWEEN 0.03 AND 0.97""")
    n_orders = c.sql("SELECT count(*) FROM orders").fetchone()[0]
    n_aligned, n_depl = c.sql("SELECT count(*), coalesce(sum(depleting::INT), 0) FROM ev").fetchone()
    # all depleting orders plus a 10% sample of the rest; books only for those markets
    c.execute("""CREATE OR REPLACE TEMP TABLE ev AS
                 SELECT * FROM ev WHERE depleting OR hash(market_id || ts_exchange_ns) % 10 = 0""")
    c.execute("DELETE FROM bk WHERE market_id NOT IN (SELECT DISTINCT market_id FROM ev)")
    day_end = c.sql("SELECT max(ts_recv_ns) FROM bk").fetchone()[0] or 0
    for h in HORIZONS:
        hn = h * 1_000_000_000
        c.execute(f"""
            INSERT INTO post_all
            SELECT e.grp, e.depleting, {h} AS h_s,
                   CASE WHEN e.is_buy
                        THEN list_sum(list_transform(range(1, len(b.ask_px) + 1),
                                      lambda i: CASE WHEN b.ask_px[i] <= e.p0 + 1e-9 THEN b.ask_sz[i] ELSE 0 END))
                        ELSE list_sum(list_transform(range(1, len(b.bid_px) + 1),
                                      lambda i: CASE WHEN b.bid_px[i] >= e.p0 - 1e-9 THEN b.bid_sz[i] ELSE 0 END))
                   END / e.d0 AS refill,
                   100 * CASE WHEN e.is_buy THEN b.best_ask - e.p0 ELSE e.p0 - b.best_bid END AS drift_c
            FROM ev e ASOF JOIN bk b ON e.market_id = b.market_id AND e.ts_recv_ns + {hn}::BIGINT >= b.ts_recv_ns
            -- a book stored after the pre-trade one (else the post state is unseen), market still open at +h,
            -- and +h inside this day's files
            WHERE b.ts_pre > e.pre_book_ts
              AND (e.close_ns IS NULL OR e.close_ns > e.ts_exchange_ns + {hn}::BIGINT)
              AND e.ts_recv_ns + {hn}::BIGINT <= {day_end}""")
    return n_orders, n_aligned, int(n_depl)


AGG = """count(*) AS orders,
         round(100 * avg((coalesce(refill, 0) >= 1)::INT), 1) AS pct_fully_refilled,
         round(100 * avg((coalesce(refill, 0) >= 0.5)::INT), 1) AS pct_half_refilled,
         round(median(coalesce(refill, 0)), 2) AS median_refill,
         round(100 * avg((drift_c <= 0)::INT), 1) AS pct_touch_back,
         round(median(drift_c), 2) AS median_drift_c, round(avg(drift_c), 2) AS mean_drift_c"""


def run(c: duckdb.DuckDBPyConnection, venue: str):
    import pandas as pd
    d = BOOKS / venue
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE grp AS
        SELECT market_id, any_value("group") AS grp, max(close_ns) AS close_ns
        FROM read_parquet({q(d / '*' / 'markets-*.parquet')}, union_by_name=true) GROUP BY 1""")
    c.execute("""CREATE OR REPLACE TEMP TABLE post_all (grp VARCHAR, depleting BOOLEAN, h_s INTEGER,
                                                         refill DOUBLE, drift_c DOUBLE)""")
    tot = [0, 0, 0]
    for day in sorted(p for p in d.iterdir() if p.is_dir() and any(p.glob("books-*.parquet"))):
        n = run_day(c, venue, day)
        tot = [a + b for a, b in zip(tot, n)]
        print(f"   {venue} {day.name}: orders {n[0]:,} aligned {n[1]:,} depleting {n[2]:,}", flush=True)
    by_group = c.sql(f"SELECT '{venue}' AS venue, grp, depleting, h_s, {AGG} FROM post_all "
                     f"GROUP BY ALL ORDER BY grp, depleting, h_s").df()
    overall = c.sql(f"SELECT '{venue}' AS venue, depleting, h_s, {AGG} FROM post_all "
                    f"GROUP BY ALL ORDER BY depleting, h_s").df()
    counts = pd.DataFrame([{"venue": venue, "taker_orders": tot[0], "aligned_orders": tot[1],
                            "depleting_orders": tot[2]}])
    return by_group, overall, counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venues", nargs="*", default=["kalshi", "polymarket"])
    a = ap.parse_args()
    from predict_ngin.shared.duck import connect

    c = connect()  # standard limits, UTC session
    OUT.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    res = {"by_group": [], "overall": [], "counts": []}
    for v in a.venues:
        for k, df in zip(res, run(c, v)):
            res[k].append(df)
            print(f"\n== {v} {k}\n{df.to_string(index=False, max_rows=200)}", flush=True)
    for k, dfs in res.items():
        pd.concat(dfs, ignore_index=True).to_csv(OUT / f"{k}.csv", index=False)


if __name__ == "__main__":
    main()

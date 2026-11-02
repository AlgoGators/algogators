"""How much does walking the visible order book cost? Depth profile and sweep slippage from recorded books.

Question (2026-10-16): is visible liquidity thin at the touch and thicker further away, so that taking a large
size in one go fills at a much worse average price than the best quote?

Data: data/live_books/{kalshi,polymarket}/*/books-*.parquet (atlas recorder, read-only). One snapshot per market
per --every-s seconds (the last one in each slot), books with gap=False, both sides present, not crossed.
Each snapshot gives two walks: buying YES (the ask side) and buying NO (the bid side at 1 - bid).

For each walk and dollar target T (dollars spent):
    vwap = T / contracts bought, slippage = vwap - touch (cents), fee = Kalshi taker 0.07 * sum c p (1 - p)
    unrounded (rounding is "exact" after 2026-05-28; one order). Polymarket rows carry no fee.
A walk that runs out of visible levels before T is "not fillable". When the snapshot holds the recorder's maximum
number of levels (Kalshi 15, Polymarket 10) the book may go deeper than we can see, so that share is reported too.

    uv run python scripts/research/book_walk_cost.py
Writes CSVs to data/research_reports/book_walk_<date>/.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
BOOKS = ROOT / "data/live_books"
TARGETS = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
MAX_LEVELS = {"kalshi": 15, "polymarket": 10}
TAKER_RATE = 0.07
OUT = ROOT / "data/research_reports" / f"book_walk_{dt.date.today():%Y-%m-%d}"


def q(p) -> str:
    return "'" + str(p).replace("\\", "/") + "'"


def build_levels(c: duckdb.DuckDBPyConnection, venue: str, every_s: int) -> None:
    books = q(BOOKS / venue / "*" / "books-*.parquet")
    mk = q(BOOKS / venue / "*" / "markets-*.parquet")
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE snaps AS
        WITH b AS (
            SELECT market_id, ts_recv_ns, bid_px, bid_sz, ask_px, ask_sz, best_bid, best_ask
            FROM read_parquet({books}, union_by_name=true)
            WHERE NOT coalesce(gap, false) AND best_bid IS NOT NULL AND best_ask IS NOT NULL
              AND best_ask > best_bid AND len(ask_px) > 0 AND len(bid_px) > 0)
        SELECT market_id, ts_recv_ns // ({every_s * 1_000_000_000}::BIGINT) AS slot,
               arg_max(bid_px, ts_recv_ns) AS bid_px, arg_max(bid_sz, ts_recv_ns) AS bid_sz,
               arg_max(ask_px, ts_recv_ns) AS ask_px, arg_max(ask_sz, ts_recv_ns) AS ask_sz
        FROM b GROUP BY 1, 2""")
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE grp AS
        SELECT market_id, any_value("group") AS grp FROM read_parquet({mk}, union_by_name=true) GROUP BY 1""")
    # one row per (walk, level): buy YES walks asks at ask_px; buy NO walks bids at 1 - bid_px
    c.execute(f"""
        CREATE OR REPLACE TEMP TABLE lv AS
        WITH s AS (SELECT row_number() OVER () AS sid, * FROM snaps),
        w AS (
            SELECT sid, market_id, 'yes' AS side, len(ask_px) AS nlev,
                   unnest(ask_px) AS px, unnest(ask_sz) AS sz, unnest(range(len(ask_px))) AS k FROM s
            UNION ALL
            SELECT sid, market_id, 'no', len(bid_px),
                   unnest(list_transform(bid_px, x -> 1 - x)), unnest(bid_sz), unnest(range(len(bid_px))) FROM s),
        w2 AS (
            SELECT w.*, first_value(px) OVER (PARTITION BY sid, side ORDER BY k) AS touch,
                   px * sz AS usd, {TAKER_RATE} * sz * px * (1 - px) AS fee
            FROM w WHERE sz > 0)
        SELECT w2.*, coalesce(g.grp, '(none)') AS grp,
               max(nlev) OVER (PARTITION BY market_id) AS cap,
               round((px - touch) * 100) AS dist_c,
               sum(usd) OVER win AS cum_usd, sum(sz) OVER win AS cum_ct, sum(fee) OVER win AS cum_fee,
               sum(usd) OVER (PARTITION BY sid, side) AS tot_usd
        FROM w2 LEFT JOIN grp g USING (market_id)
        WINDOW win AS (PARTITION BY sid, side ORDER BY k ROWS UNBOUNDED PRECEDING)""")


def touch_bucket(col: str = "touch") -> str:
    return (f"CASE WHEN {col} < 0.05 THEN 'a <5c' WHEN {col} < 0.15 THEN 'b 5-15c' WHEN {col} < 0.35 THEN 'c 15-35c' "
            f"WHEN {col} < 0.65 THEN 'd 35-65c' WHEN {col} < 0.85 THEN 'e 65-85c' WHEN {col} < 0.95 THEN 'f 85-95c' "
            f"ELSE 'g >=95c' END")


def analyse(c: duckdb.DuckDBPyConnection, venue: str) -> dict:
    fee_on = 1 if venue == "kalshi" else 0
    out = {}
    # depth profile: where are the visible dollars, by cents from the touch (0 = at the touch)
    out["depth_profile"] = c.sql(f"""
        WITH x AS (SELECT grp, least(dist_c, 10) AS dist_c, usd, tot_usd, sid, side FROM lv)
        SELECT '{venue}' AS venue, grp, dist_c AS cents_from_touch,
               round(100 * sum(usd) / sum(sum(usd)) OVER (PARTITION BY grp), 2) AS pct_of_visible_usd,
               count(DISTINCT (sid, side)) AS walks_with_level
        FROM x GROUP BY grp, dist_c ORDER BY grp, dist_c""").df()
    # concentration per walk: share of visible $ at the touch / within 2c / within 5c, and visible $ in total
    out["concentration"] = c.sql(f"""
        WITH w AS (
            SELECT grp, sid, side, any_value(touch) AS touch, any_value(nlev) AS nlev, any_value(cap) AS cap, any_value(tot_usd) AS tot,
                   sum(usd) FILTER (dist_c = 0) AS at_touch, sum(usd) FILTER (dist_c <= 2) AS w2,
                   sum(usd) FILTER (dist_c <= 5) AS w5
            FROM lv GROUP BY ALL)
        SELECT '{venue}' AS venue, grp, count(*) AS walks,
               round(median(tot)) AS median_visible_usd, round(median(at_touch)) AS median_usd_at_touch,
               round(100 * median(at_touch / tot), 1) AS median_pct_at_touch,
               round(100 * median(w2 / tot), 1) AS median_pct_within_2c,
               round(100 * median(w5 / tot), 1) AS median_pct_within_5c,
               round(100 * avg((nlev >= cap)::INT), 1) AS pct_book_at_depth_cap
        FROM w WHERE touch BETWEEN 0.03 AND 0.97 GROUP BY ALL ORDER BY walks DESC""").df()
    # walk cost for each dollar target
    tg = ", ".join(f"({t})" for t in TARGETS)
    walks = f"""
        WITH t(target) AS (VALUES {tg}),
        f AS (  -- the level where the walk reaches the target
            SELECT lv.grp, lv.sid, lv.side, lv.touch, lv.nlev, t.target,
                   lv.cum_ct - lv.sz + (t.target - (lv.cum_usd - lv.usd)) / lv.px AS ct,
                   lv.cum_fee - lv.fee + {TAKER_RATE} * ((t.target - (lv.cum_usd - lv.usd)) / lv.px) * lv.px * (1 - lv.px)
                       AS fee
            FROM lv JOIN t ON lv.cum_usd - lv.usd < t.target AND lv.cum_usd >= t.target),
        a AS (SELECT DISTINCT grp, sid, side, touch, nlev, cap, tot_usd FROM lv),
        j AS (
            SELECT a.grp, a.sid, a.side, a.touch, a.nlev, a.cap, t.target, f.ct, f.fee
            FROM a CROSS JOIN t LEFT JOIN f ON f.sid = a.sid AND f.side = a.side AND f.target = t.target)
        SELECT *, 100 * (target / ct - touch) AS slip_c, 100 * (target / ct - touch) / touch AS slip_pct,
               100 * ((target + {fee_on} * fee) / ct - touch) AS cost_c
        FROM j WHERE touch BETWEEN 0.03 AND 0.97"""
    c.execute(f"CREATE OR REPLACE TEMP TABLE wk AS {walks}")
    agg = f"""
        count(*) AS walks, round(100 * avg((ct IS NOT NULL)::INT), 1) AS pct_fillable,
        round(100 * avg((ct IS NULL AND nlev >= cap)::INT), 1) AS pct_unfilled_maybe_deeper,
        -- slippage stats are over fillable walks only (deep books), so they flatter large sizes
        round(median(slip_c), 2) AS median_slip_c, round(quantile_cont(slip_c, 0.9), 2) AS p90_slip_c,
        round(median(slip_pct), 2) AS median_slip_pct_of_price, round(median(cost_c), 2) AS median_cost_c_with_fee"""
    # sweep: take everything visible in one order. Average price vs the touch, and how far the last level is
    out["sweep"] = c.sql(f"""
        WITH w AS (
            SELECT grp, sid, side, any_value(touch) AS touch, any_value(nlev) AS nlev, any_value(cap) AS cap,
                   sum(usd) AS usd, sum(sz) AS ct, sum(fee) AS fee, max(dist_c) AS last_c
            FROM lv GROUP BY ALL)
        SELECT '{venue}' AS venue, grp, count(*) AS walks, round(median(usd)) AS median_visible_usd,
               round(quantile_cont(usd, 0.9)) AS p90_visible_usd,
               round(median(100 * (usd / ct - touch)), 2) AS median_sweep_slip_c,
               round(median(100 * (usd / ct - touch) / touch), 2) AS median_sweep_slip_pct,
               round(median(100 * ((usd + {fee_on} * fee) / ct - touch)), 2) AS median_sweep_cost_c_with_fee,
               round(median(last_c), 1) AS median_last_level_c,
               round(100 * avg((nlev >= cap)::INT), 1) AS pct_at_depth_cap
        FROM w WHERE touch BETWEEN 0.03 AND 0.97 GROUP BY ALL ORDER BY walks DESC""").df()
    out["walk_cost_by_group"] = c.sql(
        f"SELECT '{venue}' AS venue, grp, target AS usd, {agg} FROM wk GROUP BY ALL ORDER BY grp, usd").df()
    out["walk_cost_by_price"] = c.sql(
        f"SELECT '{venue}' AS venue, {touch_bucket()} AS touch_band, target AS usd, {agg} FROM wk "
        f"GROUP BY ALL ORDER BY touch_band, usd").df()
    out["walk_cost_all"] = c.sql(
        f"SELECT '{venue}' AS venue, target AS usd, {agg} FROM wk GROUP BY ALL ORDER BY usd").df()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every-s", type=int, default=300)
    ap.add_argument("--venues", nargs="*", default=["kalshi", "polymarket"])
    a = ap.parse_args()
    from predict_ngin.shared.duck import connect

    c = connect()  # standard limits, UTC session
    OUT.mkdir(parents=True, exist_ok=True)
    frames: dict[str, list] = {}
    for v in a.venues:
        build_levels(c, v, a.every_s)
        n = c.sql("SELECT count(*), count(DISTINCT market_id) FROM snaps").fetchone()
        print(f"{v}: {n[0]:,} snapshots, {n[1]:,} markets", flush=True)
        for k, df in analyse(c, v).items():
            frames.setdefault(k, []).append(df)
    import pandas as pd
    for k, dfs in frames.items():
        df = pd.concat(dfs, ignore_index=True)
        df.to_csv(OUT / f"{k}.csv", index=False)
        if k in ("walk_cost_all", "concentration", "sweep"):
            print(f"\n== {k}\n{df.to_string(index=False, max_rows=120)}", flush=True)


if __name__ == "__main__":
    main()

"""whale_pit stage 1: per-month taker trades from the Polymarket lake (point-in-time whale study).

For each lake month 2022-11..2025-12 this writes ``data/research/whale_pit/trades/YYYY-MM.parquet``
with one row per (taker wallet, market, transaction, aggressor side) of at least ``--min-usd`` notional,
on eligible markets only (not crypto up/down, not Mentions, not "say/mention" questions: the MNPI
mention-market rule). Rows carry only information available at the trade's block time ``ts``, plus
two causal reference prices for a follower: the last print in the market at or before ``ts + 60`` and
``ts + 300`` seconds (as-of join on the same month's fills).

Also writes ``markets.parquet`` (eligible markets, YES payoff, ``known_ts`` = time the outcome is
treated as public) once.

    uv run --no-sync python scripts/research/whale_pit_extract.py [--months 2024-01,2024-02] [--force]
"""

from __future__ import annotations

import argparse
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from predict_ngin.shared.duck import connect, sql_path
from predict_ngin.shared.paths import data_root

LAKE = data_root() / "lake" / "polymarket"

# Data end (exclusive). Default 2026-01-01 = the original study; WHALE_PIT_END=2026-10-17 runs the
# 2026 extension into its own folder (data/research/whale_pit/end_2026-10-17), leaving the original intact.
END = os.environ.get("WHALE_PIT_END", "2026-01-01")
DATA_END_TS = int(datetime.fromisoformat(END).replace(tzinfo=timezone.utc).timestamp())
_ORIG = END == "2026-01-01"
OUT = data_root() / "research" / "whale_pit" / ("" if _ORIG else f"end_{END}")
_last_day = datetime.fromtimestamp(DATA_END_TS - 1, timezone.utc)
FIRST, LAST = (2022, 11), (_last_day.year, _last_day.month)  # lake months up to the data end
MNPI_RE = r"(?i)\b(say|says|said|mention|mentions|mentioned|tweet|tweets|post on truth)\b"


def months():
    y, m = FIRST
    while (y, m) <= LAST:
        yield y, m
        y, m = (y + (m == 12), m % 12 + 1)


def build_markets(con) -> Path:
    """Eligible markets with YES payoff and a conservative public-outcome time.

    known_ts = max(last fill in the lake <= 2025-12, scheduled end_date) + 1 day. Markets whose
    last lake fill is in the final 31 days of the data (they may keep trading in 2026) get
    known_ts = +inf for scoring purposes (never used as resolved inside the study window).
    """
    path = OUT / "markets.parquet"
    if path.exists():
        return path
    bars = sql_path(LAKE / "bars_1h" / "**" / "*.parquet")
    con.execute(f"""
        COPY (
        WITH last AS (
            SELECT market_id, max(ts) + 3600 AS last_fill_ts, sum(usd) AS usd_total
            FROM read_parquet({bars}, hive_partitioning=true)
            WHERE ts < {DATA_END_TS} GROUP BY 1),
        m AS (
            SELECT market_id, event_id, category, question, closed, outcome_prices, end_date,
                   try_cast(json_extract_string(replace(outcome_prices, '''', '"'), '$[0]') AS DOUBLE) AS p0,
                   try_cast(json_extract_string(replace(outcome_prices, '''', '"'), '$[1]') AS DOUBLE) AS p1,
                   epoch(try_cast(end_date AS TIMESTAMPTZ))::BIGINT AS end_ts
            FROM read_parquet({sql_path(LAKE / 'markets.parquet')})
            WHERE NOT coalesce(is_updown, false) AND category <> 'Mentions'
              AND NOT regexp_matches(coalesce(question, ''), '{MNPI_RE}'))
        SELECT m.market_id, m.event_id, m.category, m.question, l.last_fill_ts, m.end_ts,
               CASE WHEN m.closed = '1' AND p0 IN (0, 1) AND p1 = 1 - p0 THEN p0 END AS yes_payoff,
               CASE WHEN l.last_fill_ts >= {DATA_END_TS} - 31 * 86400 THEN 9e18
                    ELSE greatest(l.last_fill_ts, coalesce(m.end_ts, 0)) + 86400 END AS known_ts
        FROM m JOIN last l USING (market_id)
        ) TO {sql_path(path)} (FORMAT parquet)""")
    return path


def extract_month(con, y: int, m: int, min_usd: float, force: bool) -> None:
    out = OUT / "trades" / f"{y:04d}-{m:02d}.parquet"
    if out.exists() and not force:
        return
    orig = OUT.parent / "trades" / out.name
    if not _ORIG and (y, m) <= (2025, 12) and orig.exists() and not force:
        # the original study's month: same trades (a market in either universe has pre-2026 bars)
        shutil.copyfile(orig, out)
        print(f"{y}-{m:02d}: reused {orig}", flush=True)
        return
    src = LAKE / "fills" / f"year={y:04d}" / f"month={m:02d}"
    files = sorted(src.glob("*.parquet"))
    size_gb = sum(f.stat().st_size for f in files) / 1e9
    k = max(1, int(size_gb * 3))  # hash chunks so each aggregate stays small
    tmp = []
    t0 = time.time()
    for c in range(k):
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE f AS
            SELECT ts_chain AS ts, transaction_hash AS tx, taker AS w, market_id,
                   yes_price AS p, token_amount AS q, CASE yes_side WHEN 'BUY' THEN 1 ELSE -1 END AS d
            FROM read_parquet({sql_path(src / '*.parquet')})
            WHERE NOT is_taker_order_row AND yes_price BETWEEN 0.001 AND 0.999 AND token_amount > 0
              AND yes_side IN ('BUY', 'SELL') AND NOT coalesce(timestamp_unreliable, false)
              AND ts_chain < {DATA_END_TS}
              AND market_id IN (SELECT market_id FROM mk)
              AND hash(market_id) % {k} = {c}""")
        part = OUT / "trades" / f".{y:04d}-{m:02d}.{c}.parquet"
        con.execute(f"""
            COPY (
            WITH t AS (
                SELECT w, market_id, tx, d, min(ts) AS ts, sum(q) AS q,
                       sum(p * q) / sum(q) AS p_vwap, max(p) AS p_max, min(p) AS p_min,
                       sum(CASE WHEN d = 1 THEN p ELSE 1 - p END * q) AS usd, count(*) AS n_fills
                FROM f GROUP BY ALL
                HAVING sum(CASE WHEN d = 1 THEN p ELSE 1 - p END * q) >= {min_usd}),
            px AS (SELECT market_id, ts, p, d FROM f),
            a60 AS (
                SELECT t.*, px.p AS p_ref60, px.d AS d_ref60
                FROM t ASOF LEFT JOIN px ON t.market_id = px.market_id AND t.ts + 60 >= px.ts)
            SELECT a60.*, px.p AS p_ref300, px.d AS d_ref300
            FROM a60 ASOF LEFT JOIN px ON a60.market_id = px.market_id AND a60.ts + 300 >= px.ts
            ) TO {sql_path(part)} (FORMAT parquet)""")
        tmp.append(part)
    con.execute("DROP TABLE IF EXISTS f")
    con.execute(f"COPY (SELECT * FROM read_parquet([{', '.join(sql_path(p) for p in tmp)}]) ORDER BY ts) "
                f"TO {sql_path(out)} (FORMAT parquet)")
    for p in tmp:
        p.unlink()
    n = con.sql(f"SELECT count(*) FROM read_parquet({sql_path(out)})").fetchone()[0]
    print(f"{y}-{m:02d}: {size_gb:.1f} GB in {k} chunks -> {n:,} trades in {time.time() - t0:.0f}s", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="")
    ap.add_argument("--min-usd", type=float, default=100.0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--memory", default="3GB")
    a = ap.parse_args()
    (OUT / "trades").mkdir(parents=True, exist_ok=True)
    con = connect(memory=a.memory, threads=3)
    mk = build_markets(con)
    con.execute(f"CREATE TEMP TABLE mk AS SELECT market_id FROM read_parquet({sql_path(mk)})")
    want = set(a.months.split(",")) if a.months else None
    for y, m in months():
        if want and f"{y:04d}-{m:02d}" not in want:
            continue
        extract_month(con, y, m, a.min_usd, a.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Query-time benchmark for the Polymarket fill tape: current layout vs the partitioned lake.

Five queries a backtest or study typically runs, each timed twice (second run = warm OS cache). The same queries run on
both layouts and their results must match, so this is also the equivalence check for the lake rewrite.

  q1 one event family over six months (Fed chair nomination markets, Oct 2025 - Mar 2026)
  q2 one market family over all time (crypto up/down markets), volume by month
  q3 full-tape calibration (price band x fill count / volume), no join
  q4 one market's full fill history, in time order
  q5 daily volume by event for March 2026

    uv run python scripts/research/lake_bench.py --layout current
    uv run python scripts/research/lake_bench.py --layout lake
Writes data/research_reports/lake_bench_<layout>.json (timings + result fingerprints).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.data_quality import polymarket_tape as pmt  # noqa: E402

LAKE = ROOT / "data" / "lake" / "polymarket"
MARKET_LISTS = [ROOT / "data/polymarket/hf_20261005/markets_gamma_tail.parquet",
                ROOT / "data/polymarket/hf_20261005/markets.parquet", ROOT / "data/polymarket/markets.parquet"]
T_Q1 = (1759276800, 1774915200)   # 2025-10-01 .. 2026-03-31 UTC
T_Q5 = (1772323200, 1775001600)   # 2026-03-01 .. 2026-04-01 UTC


def q(p) -> str:
    return "'" + str(p).replace("\\", "/") + "'"


def markets_sql() -> str:
    parts = [f"SELECT CAST(id AS VARCHAR) AS market_id, CAST(event_id AS VARCHAR) AS event_id, slug, event_title, {i} AS pri "
             f"FROM read_parquet({q(f)})" for i, f in enumerate(MARKET_LISTS) if f.exists()]
    return (f"SELECT market_id, arg_min(event_id, pri) AS event_id, arg_min(slug, pri) AS slug, "
            f"arg_min(event_title, pri) AS event_title FROM ({' UNION ALL '.join(parts)}) GROUP BY 1")


def fills(layout: str, cols: str) -> str:
    if layout == "current":
        names = [c.strip() for c in cols.split(",")]
        return f"({pmt.clean_trades_sql(names, maker_rows_only=True)})"
    return f"(SELECT {cols} FROM read_parquet({q(LAKE / 'fills' / '**' / '*.parquet')}, hive_partitioning=true) " \
           f"WHERE NOT is_taker_order_row)"


def queries(layout: str, fed_ids: str, top_market: str) -> dict:
    f = lambda cols: fills(layout, cols)  # noqa: E731
    # same market set on both layouts: the lake's is_updown flag (markets.parquet)
    crypto = (f"(SELECT market_id FROM read_parquet({q(LAKE / 'markets.parquet')}) WHERE is_updown)"
              if layout == "current" else None)
    q2_where = f"market_id IN {crypto}" if crypto else "is_updown"
    return {
        "q1": f"SELECT count(*), round(sum(usd_amount), 2) FROM {f('market_id, ts_chain, usd_amount, is_taker_order_row')} "
              f"WHERE market_id IN ({fed_ids}) AND ts_chain >= {T_Q1[0]} AND ts_chain < {T_Q1[1]}",
        "q2": f"SELECT strftime(to_timestamp(ts_chain), '%Y-%m') m, count(*), round(sum(usd_amount), 2) "
              f"FROM {f('market_id, ts_chain, usd_amount, is_taker_order_row' + ('' if crypto else ', is_updown'))} "
              f"WHERE {q2_where} GROUP BY 1 ORDER BY 1",
        "q3": f"SELECT floor(yes_price * 20) b, count(*), round(sum(usd_amount), 2) "
              f"FROM {f('yes_price, usd_amount, is_taker_order_row')} GROUP BY 1 ORDER BY 1",
        "q4": f"SELECT ts_chain, yes_price, usd_amount FROM {f('market_id, ts_chain, yes_price, usd_amount, is_taker_order_row')} "
              f"WHERE market_id = '{top_market}' ORDER BY ts_chain, yes_price, usd_amount",
        "q5": f"SELECT CAST(to_timestamp(ts_chain) AS DATE) d, mk.event_id, count(*), round(sum(usd_amount), 2) "
              f"FROM {f('market_id, ts_chain, usd_amount, is_taker_order_row')} t JOIN mk USING (market_id) "
              f"WHERE ts_chain >= {T_Q5[0]} AND ts_chain < {T_Q5[1]} GROUP BY 1, 2 ORDER BY 1, 2",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", choices=["current", "lake"], required=True)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    from predict_ngin.shared.duck import connect

    c = connect()  # standard limits, UTC session
    c.execute(f"CREATE TEMP TABLE mk AS {markets_sql()}")
    fed = c.sql("SELECT string_agg('''' || market_id || '''', ',') FROM mk "
                "WHERE event_title = 'Who will Trump nominate as Fed Chair?'").fetchone()[0]
    top = "2169995"  # MicroStrategy sells any Bitcoin by May 31, 2026? (top single market in the overview)
    out = {}
    for name, sql in queries(a.layout, fed, top).items():
        if a.only and name not in a.only:
            continue
        times = []
        for _ in range(2):
            t0 = time.perf_counter()
            rows = c.sql(sql).fetchall()
            times.append(round(time.perf_counter() - t0, 2))
        fp = hashlib.sha256(repr(rows).encode()).hexdigest()[:16]
        out[name] = {"seconds": times, "rows": len(rows), "fingerprint": fp, "head": repr(rows[:2])[:200]}
        print(f"{name}: {times} s, {len(rows)} rows, fp {fp}", flush=True)
    dst = ROOT / "data/research_reports" / f"lake_bench_{a.layout}.json"
    dst.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

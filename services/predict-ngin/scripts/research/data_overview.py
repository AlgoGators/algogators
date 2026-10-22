"""Overview of the prediction-market trade data on disk: inventory, monthly activity, what trades, calibration.

Read-only over local parquet (no network). Kalshi = trades_fixed (old monthly tape, < cutover) + trades_fixed_ext
(daily) + trades_fixed_ext/_superseded_rounded_counts for days not yet re-fetched (integer-rounded counts; fine for
trade counts, slightly low contract totals after 2026-03-13). Polymarket = polymarket_clean old + ext, maker rows only.

    uv run python scripts/research/data_overview.py            # all sections
    uv run python scripts/research/data_overview.py --only kalshi_monthly

Writes CSVs to data/research_reports/data_overview_<date>/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.data_quality import polymarket_tape as pmt  # noqa: E402

K_OLD = ROOT / "data/kalshi/trades_fixed"
K_EXT = ROOT / "data/kalshi/trades_fixed_ext"
K_SUP = K_EXT / "_superseded_rounded_counts"
K_CUT = 1769558400  # 2026-01-28 UTC
PM_MARKET_FILES = [ROOT / "data/polymarket/hf_20261005/markets_gamma_tail.parquet",
                   ROOT / "data/polymarket/hf_20261005/markets.parquet", ROOT / "data/polymarket/markets.parquet"]
OUT = ROOT / "data/research_reports" / f"data_overview_{dt.date.today():%Y-%m-%d}"


def q(p) -> str:
    return "'" + str(p).replace("\\", "/") + "'"


def kalshi_sql() -> str:
    cols = "trade_id, ticker, event_ticker, yes_price, no_price, count, taker_side, timestamp_unix"
    old = ", ".join(q(f) for f in sorted(K_OLD.glob("trades_*.parquet")))
    ext = sorted(K_EXT.glob("trades_*.parquet"))
    have = {f.name for f in ext}
    sup = [f for f in sorted(K_SUP.glob("trades_*.parquet")) if f.name not in have]
    newer = ", ".join(q(f) for f in ext + sup)
    return (f"SELECT {cols} FROM read_parquet([{old}]) WHERE timestamp_unix < {K_CUT} UNION ALL "
            f"SELECT {cols} FROM read_parquet([{newer}], union_by_name=true) WHERE timestamp_unix >= {K_CUT}")


def pm_markets_sql() -> str:
    """Newest copy of each Polymarket market across the local market lists (first file wins)."""
    parts = [f"SELECT CAST(id AS VARCHAR) AS market_id, event_title, CAST(closed AS BOOLEAN) AS closed, "
             f"outcome_prices, {i} AS pri FROM read_parquet({q(f)})" for i, f in enumerate(PM_MARKET_FILES) if f.exists()]
    return ("SELECT market_id, arg_min(event_title, pri) AS event_title, arg_min(closed, pri) AS closed, "
            f"arg_min(outcome_prices, pri) AS outcome_prices FROM ({' UNION ALL '.join(parts)}) GROUP BY 1")


def con() -> duckdb.DuckDBPyConnection:
    from predict_ngin.shared.duck import connect

    c = connect()  # standard limits, UTC session
    return c


def save(df, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / f"{name}.csv", index=False)
    print(f"\n== {name}\n{df.to_string(index=False, max_rows=80)}", flush=True)


def kalshi_monthly(c):
    df = c.sql(f"""
        WITH t AS ({kalshi_sql()})
        SELECT strftime(to_timestamp(timestamp_unix), '%Y-%m') AS month,
               count(*) AS trades,
               round(sum(count)) AS contracts,
               round(sum(count * CASE WHEN taker_side='yes' THEN yes_price ELSE no_price END)) AS notional_usd,
               approx_count_distinct(ticker) AS markets,
               approx_count_distinct(split_part(event_ticker, '-', 1)) AS series,
               round(100 * avg(CASE WHEN ticker LIKE 'KXMVE%' THEN 1 ELSE 0 END), 1) AS pct_parlay_trades
        FROM t GROUP BY 1 ORDER BY 1""").df()
    save(df, "kalshi_monthly")


def kalshi_series(c, since="2025-10-01"):
    s = int(dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc).timestamp())
    df = c.sql(f"""
        WITH t AS ({kalshi_sql()})
        SELECT split_part(event_ticker, '-', 1) AS series, count(*) AS trades,
               round(sum(count * CASE WHEN taker_side='yes' THEN yes_price ELSE no_price END)) AS notional_usd,
               approx_count_distinct(ticker) AS markets
        FROM t WHERE timestamp_unix >= {s} GROUP BY 1 ORDER BY notional_usd DESC LIMIT 40""").df()
    df["share_pct"] = (100 * df.notional_usd / df.notional_usd.sum()).round(1)
    save(df, "kalshi_top_series_since_" + since)


def kalshi_calibration(c):
    """Trades since the cutover on settled markets: traded YES price bucket vs. settled YES rate.
    Contract-weighted. Taker return = what a taker buying at that price earned per $ (before fees)."""
    mk = ", ".join(q(f) for f in sorted((K_EXT / "markets").glob("part_*.parquet")))
    df = c.sql(f"""
        WITH m AS (SELECT ticker, any_value(result) AS result FROM read_parquet([{mk}], union_by_name=true)
                   WHERE result IN ('yes','no') GROUP BY 1),
             t AS ({kalshi_sql()})
        SELECT floor(t.yes_price * 20) / 20 AS yes_px_bucket,
               count(*) AS trades, round(sum(count)) AS contracts,
               round(sum(count * yes_price) / sum(count), 4) AS avg_yes_px,
               round(sum(count * (m.result='yes')::INT) / sum(count), 4) AS settled_yes_rate,
               round(sum(count * CASE WHEN taker_side='yes' THEN (m.result='yes')::INT - yes_price
                                       ELSE (m.result='no')::INT - no_price END)
                     / sum(count * CASE WHEN taker_side='yes' THEN yes_price ELSE no_price END), 4)
                   AS taker_return_per_usd
        FROM t JOIN m USING (ticker)
        WHERE t.timestamp_unix >= {K_CUT} AND t.ticker NOT LIKE 'KXMVE%'
        GROUP BY 1 ORDER BY 1""").df()
    save(df, "kalshi_calibration_2026")


def pm_monthly(c):
    df = c.sql(f"""
        WITH t AS ({pmt.clean_trades_sql(['ts_chain', 'market_id', 'usd_amount', 'taker'], maker_rows_only=True)})
        SELECT strftime(to_timestamp(ts_chain), '%Y-%m') AS month, count(*) AS fills,
               round(sum(usd_amount)) AS volume_usd, approx_count_distinct(market_id) AS markets,
               approx_count_distinct(taker) AS taker_wallets
        FROM t GROUP BY 1 ORDER BY 1""").df()
    save(df, "polymarket_monthly")


def pm_top_events(c, since="2025-10-01"):
    s = dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc).timestamp()
    df = c.sql(f"""
        WITH t AS ({pmt.clean_trades_sql(['market_id', 'usd_amount'], start_ts=s, maker_rows_only=True)}),
             v AS (SELECT market_id, sum(usd_amount) AS usd, count(*) AS fills FROM t GROUP BY 1),
             m AS ({pm_markets_sql()})
        SELECT coalesce(m.event_title, '(unknown market ' || v.market_id || ')') AS event,
               round(sum(usd)) AS volume_usd, sum(fills) AS fills, count(*) AS markets
        FROM v LEFT JOIN m USING (market_id) GROUP BY 1 ORDER BY volume_usd DESC LIMIT 30""").df()
    save(df, "polymarket_top_events_since_" + since)


def pm_calibration(c):
    """Resolved binary markets (outcome_prices exactly 1/0): outcome-0 price bucket vs. outcome-0 win rate,
    USD-weighted. yes_price in the clean tape is the outcome-0 (answer1) price."""
    df = c.sql(f"""
        WITH m AS (SELECT market_id,
                          CASE WHEN outcome_prices LIKE '[''1'', ''0'']' THEN 1
                               WHEN outcome_prices LIKE '[''0'', ''1'']' THEN 0 END AS win0
                   FROM ({pm_markets_sql()}) WHERE closed),
             t AS ({pmt.clean_trades_sql(['market_id', 'yes_price', 'usd_amount'], maker_rows_only=True)})
        SELECT floor(yes_price * 20) / 20 AS px_bucket, count(*) AS fills, round(sum(usd_amount)) AS volume_usd,
               round(sum(usd_amount * yes_price) / sum(usd_amount), 4) AS avg_px,
               round(sum(usd_amount * win0) / sum(usd_amount), 4) AS win_rate
        FROM t JOIN m USING (market_id) WHERE m.win0 IS NOT NULL AND yes_price > 0 AND yes_price < 1
        GROUP BY 1 ORDER BY 1""").df()
    save(df, "polymarket_calibration")


SECTIONS = {"kalshi_monthly": kalshi_monthly, "kalshi_series": kalshi_series,
            "kalshi_calibration": kalshi_calibration, "pm_monthly": pm_monthly,
            "pm_top_events": pm_top_events, "pm_calibration": pm_calibration}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", choices=list(SECTIONS))
    a = ap.parse_args()
    c = con()
    for name in a.only or SECTIONS:
        t0 = time.time()
        SECTIONS[name](c)
        print(f"   ({name}: {time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()

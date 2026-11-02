"""Build the Kalshi part of the data lake from the original tape files.

Layout (``<data>/lake/kalshi/``):

* ``markets.parquet`` - one row per market seen: ticker, event, series, category, title, result,
  times
* ``trades/year=YYYY/month=MM/part-NN.parquet`` - every trade of the legacy tape (all columns incl.
  ``count_fp``) plus ``series`` / ``category``, sorted by (category, series, event_ticker, ticker,
  timestamp_unix)
* ``bars_1h/``, ``bars_1m/`` - per-market YES-price bars (OHLC, VWAP by contracts, trades,
  contracts, notional, taker yes / no contracts), rebuilt with each month
* ``trades_state.json`` - per-month build record; ``_complete`` once every month is built **and**
  the source has no missing days since the cutover (a lake with holes is never served)

Each month is checked before it is swapped in: row count, sum(count_fp) and sum(yes_price) equal the
source, no row outside the month, unique trade ids, and :data:`contracts.KALSHI_TRADES`. Run only
after the tape refresh is complete and verified (``scripts/data/kalshi_tape_parallel.py verify``).
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import shutil
import time
from pathlib import Path
from typing import List, Optional

from ..markets import kalshi_category as kc
from ..shared.duck import connect, sql_path
from ..shared.paths import data_root
from ..tapes import kalshi as tape
from . import contracts, manifest
from ._swap import reconciles as _reconciles
from ._swap import swap_in

MAX_ROWS = 40_000_000
ROW_GROUP = 1_000_000
BAR_SIZES = {"1h": 3600, "1m": 60}
SERIES_SQL = "split_part(m.event_ticker, '-', 1)"


def lake() -> Path:
    return data_root() / "lake" / "kalshi"


def series_table() -> Path:
    """series -> Kalshi category (written by scripts/research/kalshi_flb_build.py from
    GET /series)."""
    return data_root() / "vendor" / "kalshi_research" / "series.parquet"


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def _con():
    return connect(temp_max="40GiB")


def _src(cols=None, start_ts=None, end_ts=None) -> str:
    return tape.tape_sql(
        cols or tape.ALL_COLUMNS, start_ts=start_ts, end_ts=end_ts, source="legacy"
    )


def _series_sql() -> str:
    if series_table().exists():
        return (
            f"SELECT series_ticker AS series, any_value(category) AS category "
            f"FROM read_parquet({sql_path(series_table())}) GROUP BY 1"
        )
    log(f"warning: {series_table()} missing; categories are Parlay / Other only")
    return "SELECT CAST(NULL AS VARCHAR) AS series, CAST(NULL AS VARCHAR) AS category WHERE FALSE"


# ------------------------------------------------------------------------------------------ markets
def build_markets() -> int:
    c = _con()
    d = data_root()
    parts = []
    ext_mk = sorted((d / "kalshi" / "trades_fixed_ext" / "markets").glob("part_*.parquet"))
    if ext_mk:  # newest: every market seen since the cutover, latest fetch wins
        parts.append(
            "SELECT ticker, event_ticker, title, result, status,"
            "\n                            "
            "epoch(TRY_CAST(nullif(close_time, '') AS TIMESTAMPTZ))::BIGINT AS close_ts,"
            "\n                            "
            "CAST(settlement_ts AS VARCHAR) AS settlement_ts, 0 AS pri"
            "\n                         "
            f"FROM read_parquet([{', '.join(sql_path(f) for f in ext_mk)}], union_by_name=true)"
            "\n                         "
            "QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY fetched_at DESC) = 1"
        )
    meta = d / "vendor" / "kalshi_research" / "meta.parquet"
    if meta.exists():  # settled markets of the old tape (kalshi_flb_build)
        parts.append(
            "SELECT ticker, event_ticker, CAST(NULL AS VARCHAR) AS title, result,"
            "\n                            "
            "CAST(NULL AS VARCHAR) AS status, TRY_CAST(close_ts AS BIGINT) AS close_ts,"
            "\n                            "
            "CAST(settle_ts AS VARCHAR) AS settlement_ts, 1 AS pri "
            f"FROM read_parquet({sql_path(meta)})"
        )
    old = d / "kalshi" / "markets.parquet"
    if old.exists():
        parts.append(
            "SELECT ticker, event_ticker, title, result, status,"
            "\n                            "
            "epoch(TRY_CAST(nullif(close_time, '') AS TIMESTAMPTZ))::BIGINT AS close_ts,"
            "\n                            "
            "CAST(NULL AS VARCHAR) AS settlement_ts, 2 AS pri "
            f"FROM read_parquet({sql_path(old)})"
        )
    if not parts:
        raise SystemExit("no Kalshi market metadata found")
    c.execute(
        f"CREATE TEMP TABLE m0 AS SELECT * FROM ({' UNION ALL '.join(parts)}) "
        f"QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY pri) = 1"
    )
    tmp = lake() / "markets.parquet.tmp"
    lake().mkdir(parents=True, exist_ok=True)
    c.execute(
        "COPY (SELECT m.ticker, m.event_ticker, split_part(m.event_ticker, '-', 1) AS series,"
        "\n                               "
        f"{kc.category_case_sql(SERIES_SQL)} AS category,"
        "\n                               "
        "m.title, m.result, m.status, m.close_ts, m.settlement_ts, "
        f"{kc.VERSION} AS category_version"
        "\n                        "
        f"FROM m0 m LEFT JOIN ({_series_sql()}) sc "
        "ON sc.series = split_part(m.event_ticker, '-', 1)"
        "\n                        "
        "ORDER BY category, series, event_ticker, ticker)"
        "\n                  "
        f"TO {sql_path(tmp)} (FORMAT parquet, COMPRESSION zstd)"
    )
    n, by = c.sql(
        f"SELECT count(*), histogram(category) FROM read_parquet({sql_path(tmp)})"
    ).fetchone()
    dup = c.sql(
        f"SELECT count(*) - count(DISTINCT ticker) FROM read_parquet({sql_path(tmp)})"
    ).fetchone()[0]
    if dup:
        raise SystemExit(f"markets: {dup} duplicate tickers; left in {tmp}")
    tmp.replace(lake() / "markets.parquet")
    manifest.update(
        "kalshi/markets", rows=int(n), by_category=dict(by), category_version=kc.VERSION
    )
    log(f"markets: {n:,} rows")
    return int(n)


# ------------------------------------------------------------------------------------------- trades
def month_bounds(ym: str) -> tuple[int, int]:
    y, m = map(int, ym.split("-"))
    return calendar.timegm((y, m, 1, 0, 0, 0)), calendar.timegm(
        (y + (m == 12), m % 12 + 1, 1, 0, 0, 0)
    )


def source_months(c) -> List[str]:
    return [
        r[0]
        for r in c.sql(
            f"SELECT DISTINCT strftime(to_timestamp(timestamp_unix), '%Y-%m') "
            f"FROM ({_src(['timestamp_unix'])}) ORDER BY 1"
        ).fetchall()
    ]


def _day_chunks(c, lo: int, hi: int) -> List[tuple[int, int]]:
    days = c.sql(
        f"SELECT timestamp_unix // 86400, count(*) FROM ({_src(['timestamp_unix'], lo, hi)}) "
        f"GROUP BY 1 ORDER BY 1"
    ).fetchall()
    out, start, acc = [], lo, 0
    for d, n in days:
        if acc and acc + n > MAX_ROWS:
            out.append((start, int(d) * 86400))
            start, acc = int(d) * 86400, 0
        acc += n
    out.append((start, hi))
    return out


def build_month(c, ym: str) -> dict:
    lo, hi = month_bounds(ym)
    y, m = ym.split("-")
    dst = lake() / "trades" / f"year={y}" / f"month={m}"
    tmp = dst.with_name(dst.name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    cols = ", ".join(f's."{x}"' for x in tape.ALL_COLUMNS)
    chunks = _day_chunks(c, lo, hi)
    for i, (a, b) in enumerate(chunks):
        c.execute(f"""
            COPY (SELECT {cols}, split_part(s.event_ticker, '-', 1) AS series,
                         {kc.category_case_sql("split_part(s.event_ticker, '-', 1)")} AS category
                  FROM ({_src(None, a, b)}) s
                  LEFT JOIN ({_series_sql()}) sc ON sc.series = split_part(s.event_ticker, '-', 1)
                  ORDER BY category, series, event_ticker, ticker, timestamp_unix)
            TO {sql_path(tmp / f"part-{i:02d}.parquet")}
            (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP})""")
    built = f"SELECT * FROM read_parquet({sql_path(tmp / '*.parquet')})"
    chk = "count(*), sum(count_fp), sum(yes_price)"
    want = c.sql(f"SELECT {chk} FROM ({_src(['count_fp', 'yes_price'], lo, hi)})").fetchone()
    got = c.sql(f"SELECT {chk} FROM ({built})").fetchone()
    outside = c.sql(
        f"SELECT count(*) FROM ({built}) WHERE timestamp_unix < {lo} OR timestamp_unix >= {hi}"
    ).fetchone()[0]
    bad = contracts.failed(contracts.KALSHI_TRADES.run(c, built))
    if not _reconciles(want, got) or outside or bad:
        raise SystemExit(
            f"{ym}: check failed (source {want}, lake {got}, outside {outside}, contract {bad}); "
            f"left in {tmp}"
        )
    swap_in(tmp, dst)
    return {
        "rows": want[0],
        "contracts": want[1],
        "files": len(chunks),
        "bars": build_bars_month(c, ym),
    }


def build_bars_month(c, ym: str) -> dict:
    """Per-market YES-price bars for one built month (bars exist only where there were trades)."""
    y, m = ym.split("-")
    src = sql_path(lake() / "trades" / f"year={y}" / f"month={m}" / "*.parquet")
    out = {}
    for name, sec in BAR_SIZES.items():
        dst = lake() / f"bars_{name}" / f"year={y}" / f"month={m}"
        tmp = dst.with_name(dst.name + ".building")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        c.execute(
            "COPY ("
            "\n            "
            "SELECT category, series, event_ticker, ticker, "
            f"(timestamp_unix // {sec}) * {sec} AS ts,"
            "\n                   "
            "arg_min(yes_price, timestamp_unix) AS open, max(yes_price) AS high, "
            "min(yes_price) AS low,"
            "\n                   "
            "arg_max(yes_price, timestamp_unix) AS close,"
            "\n                   "
            "sum(yes_price * count_fp) / nullif(sum(count_fp), 0) AS vwap,"
            "\n                   "
            "count(*) AS trades, sum(count_fp) AS contracts,"
            "\n                   "
            "sum(count_fp * CASE WHEN taker_side = 'yes' THEN yes_price ELSE no_price END) "
            "AS notional,"
            "\n                   "
            "sum(count_fp) FILTER (WHERE taker_side = 'yes') AS taker_yes_contracts,"
            "\n                   "
            "sum(count_fp) FILTER (WHERE taker_side = 'no') AS taker_no_contracts"
            "\n            "
            f"FROM read_parquet({src}) GROUP BY category, series, event_ticker, ticker, 5"
            "\n            "
            "ORDER BY category, series, event_ticker, ticker, ts)"
            "\n            "
            f"TO {sql_path(tmp / 'part-00.parquet')} "
            f"(FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP})"
        )
        want = c.sql(f"SELECT count(*), sum(count_fp) FROM read_parquet({src})").fetchone()
        bar_files = sql_path(tmp / "*.parquet")
        got = c.sql(
            f"SELECT sum(trades), sum(contracts), count(*) FROM read_parquet({bar_files})"
        ).fetchone()
        ok = (want[0] or 0) == (got[0] or 0) and abs((want[1] or 0) - (got[1] or 0)) <= 1e-9 * max(
            1.0, want[1] or 0
        )
        if not ok:
            raise SystemExit(f"{ym} bars_{name}: {got[:2]} do not add up to {want}; left in {tmp}")
        swap_in(tmp, dst)
        out[name] = int(got[2])
    return out


def build_trades(months: Optional[List[str]] = None, rebuild: bool = False) -> None:
    c = _con()  # categories come from the series table, so the market table is not needed here
    lake().mkdir(parents=True, exist_ok=True)
    state_f = lake() / "trades_state.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    all_m = source_months(c)
    latest = all_m[-1]
    for ym in months or all_m:
        if ym in state and not rebuild and ym < latest:
            continue
        t0 = time.time()
        st = build_month(c, ym)
        state[ym] = {
            **st,
            "built": dt.datetime.now().isoformat(timespec="seconds"),
            "category_version": kc.VERSION,
        }
        gaps = tape.missing_days()
        state["_missing_days"] = len(gaps)
        state["_complete"] = all(x in state for x in all_m) and not gaps
        state_f.write_text(json.dumps(state, indent=1, sort_keys=True))
        log(f"{ym}: {st['rows']:,} trades in {st['files']} file(s), {time.time() - t0:.0f}s")
    built = sorted(k for k in state if not k.startswith("_"))
    manifest.update(
        "kalshi/trades",
        months=len(built),
        first=built[0],
        last=built[-1],
        rows=sum(state[k]["rows"] for k in built),
        complete=state.get("_complete", False),
        missing_days=state.get("_missing_days"),
        category_version=kc.VERSION,
    )

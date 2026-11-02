"""Build the Polymarket part of the data lake from the original fill files.

Layout (``<data>/lake/polymarket/``):

* ``markets.parquet`` - one row per market: ids, question, event, tags, ``category``, ``is_updown``
* ``fills/year=YYYY/month=MM/part-NN.parquet`` - every row of the legacy fill tape plus
  ``category`` / ``is_updown`` (and ``event_id`` filled for old rows), sorted by (category,
  event_id, market_id, ts_chain) inside each file; files hold at most :data:`MAX_ROWS` rows (big
  months are split by day ranges)
* ``bars_1h/``, ``bars_1m/`` (same month folders) - per-market OHLC / VWAP / volume bars from
  maker fills, rebuilt with each fill month (:func:`build_bars_month`)
* ``fills_state.json`` - per-month build record; ``_complete`` once every source month is built

Each month is written to a temporary folder, checked (row count, sum of usd_amount and yes_price
equal to the source, no row outside the month, :data:`contracts.POLYMARKET_FILLS`), and only then
swapped in. Built months are skipped later unless ``rebuild``; months at or after the source's last
month, or the month where the source ended at the previous build, are always rebuilt (they grow).
Months before ``_sources_retired.before_month`` in the state file are never rebuilt: their original
files were deleted once the lake was verified row for row, so the lake is the only copy.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import shutil
import time
from pathlib import Path
from typing import List, Optional

from ..markets import polymarket_category as pc
from ..shared.duck import connect, sql_path
from ..shared.paths import data_root
from ..tapes import polymarket as tape
from . import contracts, manifest
from ._swap import reconciles as _reconciles
from ._swap import swap_in

# 20M rows per file: sorting a 39M-row chunk of 2026-01 (wide maker/taker/hash columns) needed more
# than the 10 GiB temp cap (DuckDB segfaulted at the limit); 20M keeps the spill near 5 GiB.
MAX_ROWS = 20_000_000
ROW_GROUP = 1_000_000


def lake() -> Path:
    return data_root() / "lake" / "polymarket"


def market_lists() -> List[Path]:
    snap = tape.hf_snapshot_dir()
    return [
        snap / "markets_gamma_tail.parquet",
        snap / "markets.parquet",
        data_root() / "polymarket" / "markets.parquet",
    ]


def event_tags_path() -> Path:
    return data_root() / "polymarket_clean" / "event_tags.parquet"


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def _con():
    return connect(temp_max="40GiB")  # month sorts spill well past the 10 GiB default


def _src(cols=None, start_ts=None, end_ts=None) -> str:
    return tape.clean_trades_sql(cols, start_ts=start_ts, end_ts=end_ts, source="legacy")


# ------------------------------------------------------------------------------------------ markets
MARKET_COLS = [
    "id",
    "question",
    "slug",
    "condition_id",
    "token1",
    "token2",
    "answer1",
    "answer2",
    "closed",
    "outcome_prices",
    "volume",
    "event_id",
    "event_slug",
    "event_title",
    "created_at",
    "end_date",
    "neg_risk",
]


def build_markets() -> int:
    import pandas as pd

    c = _con()
    # newest list first; an older list only adds markets the newer ones lack
    c.execute("CREATE TEMP TABLE m0 (" + ", ".join(f"{x} VARCHAR" for x in MARKET_COLS) + ")")
    for f in market_lists():
        if not f.exists():
            continue
        have = {
            r[0] for r in c.sql(f"DESCRIBE SELECT * FROM read_parquet({sql_path(f)})").fetchall()
        }
        sel = ", ".join(
            f"CAST({x} AS VARCHAR) AS {x}" if x in have else f"CAST(NULL AS VARCHAR) AS {x}"
            for x in MARKET_COLS
        )
        c.execute(
            f"INSERT INTO m0 SELECT {sel} FROM read_parquet({sql_path(f)}) "
            f"WHERE CAST(id AS VARCHAR) NOT IN (SELECT id FROM m0) "
            f"QUALIFY row_number() OVER (PARTITION BY id) = 1"
        )
    c.execute("ALTER TABLE m0 RENAME COLUMN id TO market_id")
    if event_tags_path().exists():
        c.execute(
            f"CREATE TEMP TABLE ev AS SELECT event_id, tag_labels, tag_slugs, category_raw "
            f"FROM read_parquet({sql_path(event_tags_path())})"
        )
    else:
        log(
            f"warning: {event_tags_path()} missing "
            "(scripts/data/fetch_pm_event_tags.py); slug rules only"
        )
        c.execute(
            "CREATE TEMP TABLE ev (event_id VARCHAR, tag_labels VARCHAR[], tag_slugs VARCHAR[], "
            "category_raw VARCHAR)"
        )
    df = c.sql(
        "SELECT m0.*, ev.tag_labels, ev.tag_slugs, ev.category_raw "
        "FROM m0 LEFT JOIN ev USING (event_id)"
    ).df()

    def as_list(v):
        try:
            return [] if v is None or v is pd.NA or isinstance(v, float) else list(v)
        except TypeError:
            return []

    df["category"] = [
        pc.category_of(as_list(a), as_list(b), r, (s if isinstance(s, str) else e))
        for a, b, r, s, e in zip(
            df.tag_labels, df.tag_slugs, df.category_raw, df.slug, df.event_slug, strict=False
        )
    ]
    df["is_updown"] = [
        pc.is_updown(as_list(a), (s if isinstance(s, str) else e))
        for a, s, e in zip(df.tag_labels, df.slug, df.event_slug, strict=False)
    ]
    df["category_version"] = pc.VERSION
    lake().mkdir(parents=True, exist_ok=True)
    c.register("mdf", df)
    tmp = lake() / "markets.parquet.tmp"
    c.execute(
        f"COPY (SELECT * FROM mdf ORDER BY category, event_id, market_id) TO {sql_path(tmp)} "
        f"(FORMAT parquet, COMPRESSION zstd)"
    )
    res = contracts.POLYMARKET_MARKETS.run(c, f"SELECT * FROM read_parquet({sql_path(tmp)})")
    if contracts.failed(res):
        raise SystemExit(f"markets contract failed: {contracts.failed(res)}")
    tmp.replace(lake() / "markets.parquet")
    by_cat = df.category.value_counts().to_dict()
    manifest.update(
        "polymarket/markets",
        rows=len(df),
        by_category=by_cat,
        category_version=pc.VERSION,
        tagged_events=bool(event_tags_path().exists()),
    )
    log(f"markets: {len(df):,} rows; {by_cat}")
    return len(df)


# -------------------------------------------------------------------------------------------- fills
def month_bounds(ym: str) -> tuple[int, int]:
    y, m = map(int, ym.split("-"))
    return calendar.timegm((y, m, 1, 0, 0, 0)), calendar.timegm(
        (y + (m == 12), m % 12 + 1, 1, 0, 0, 0)
    )


def source_months(c) -> List[str]:
    return [
        r[0]
        for r in c.sql(
            f"SELECT DISTINCT strftime(to_timestamp(ts_chain), '%Y-%m') FROM "
            f"({_src(['ts_chain'])}) ORDER BY 1"
        ).fetchall()
    ]


def _day_chunks(c, lo: int, hi: int) -> List[tuple[int, int]]:
    days = c.sql(
        f"SELECT floor(ts_chain / 86400)::BIGINT, count(*) FROM ({_src(['ts_chain'], lo, hi)}) "
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
    dst = lake() / "fills" / f"year={y}" / f"month={m}"
    tmp = dst.with_name(dst.name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    src_cols = ", ".join(f's."{x}"' for x in tape.ALL_COLUMNS if x != "event_id")
    mk = (
        "(SELECT market_id, event_id, category, is_updown FROM "
        f"read_parquet({sql_path(lake() / 'markets.parquet')}))"
    )
    chunks = _day_chunks(c, lo, hi)
    for i, (a, b) in enumerate(chunks):
        c.execute(
            f"""
            COPY (SELECT {src_cols}, coalesce(s.event_id, mk.event_id) AS event_id,
                         coalesce(mk.category, 'Other') AS category, """
            f"""coalesce(mk.is_updown, false) AS is_updown
                  FROM ({_src(None, a, b)}) s LEFT JOIN {mk} mk ON mk.market_id = s.market_id
                  ORDER BY category, event_id, market_id, ts_chain)
            TO {sql_path(tmp / f"part-{i:02d}.parquet")} (FORMAT parquet, COMPRESSION zstd, """
            f"""ROW_GROUP_SIZE {ROW_GROUP})"""
        )
    built = f"SELECT * FROM read_parquet({sql_path(tmp / '*.parquet')})"
    chk = "count(*), sum(usd_amount), sum(yes_price)"
    want = c.sql(f"SELECT {chk} FROM ({_src(['usd_amount', 'yes_price'], lo, hi)})").fetchone()
    got = c.sql(f"SELECT {chk} FROM ({built})").fetchone()
    outside = c.sql(
        f"SELECT count(*) FROM ({built}) WHERE ts_chain < {lo} OR ts_chain >= {hi}"
    ).fetchone()[0]
    bad = contracts.failed(contracts.POLYMARKET_FILLS.run(c, built))
    if not _reconciles(want, got) or outside or bad:
        raise SystemExit(
            f"{ym}: check failed (source {want}, lake {got}, outside {outside}, contract {bad}); "
            f"left in {tmp}"
        )
    swap_in(tmp, dst)
    return {"rows": want[0], "usd": want[1], "files": len(chunks)}


# ------------------------------------------------------------------------------------------ bars
BAR_SIZES = {"1h": 3600, "1m": 60}


def build_bars_month(c, ym: str) -> dict:
    """Per-market OHLC bars of the outcome-0 (YES) price for one built month, from maker fills.

    ``bars_<size>/year=YYYY/month=MM/part-00.parquet``; columns: category, event_id, market_id,
    is_updown, ts (bar start, UTC seconds), open/high/low/close (yes_price by fill time), vwap
    (token-weighted), fills, usd, tokens, buy_usd / sell_usd (taker side of the YES leg), sorted
    like the fills. Bars exist only where there were fills (no forward fill). Each bar file must
    add up to the month's maker fills (count exactly, USD within 1e-9 relative).
    """
    y, m = ym.split("-")
    src = lake() / "fills" / f"year={y}" / f"month={m}"
    out = {}
    for name, sec in BAR_SIZES.items():
        dst = lake() / f"bars_{name}" / f"year={y}" / f"month={m}"
        tmp = dst.with_name(dst.name + ".building")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        c.execute(
            f"""COPY (
                SELECT category, event_id, market_id, any_value(is_updown) AS is_updown,
                       CAST(floor(ts_chain / {sec}) * {sec} AS BIGINT) AS ts,
                       arg_min(yes_price, ts_chain) AS open, max(yes_price) AS high,
                       min(yes_price) AS low, arg_max(yes_price, ts_chain) AS close,
                       sum(yes_price * token_amount) / nullif(sum(token_amount), 0) AS vwap,
                       count(*) AS fills, sum(usd_amount) AS usd, sum(token_amount) AS tokens,
                       sum(usd_amount) FILTER (WHERE yes_side = 'BUY') AS buy_usd,
                       sum(usd_amount) FILTER (WHERE yes_side = 'SELL') AS sell_usd
                FROM read_parquet({sql_path(src / "*.parquet")})
                WHERE NOT is_taker_order_row
                GROUP BY category, event_id, market_id, 5
                ORDER BY category, event_id, market_id, ts)
            TO {sql_path(tmp / "part-00.parquet")}
            (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP})"""
        )
        src_files = sql_path(src / "*.parquet")
        want = c.sql(
            f"SELECT count(*), sum(usd_amount) FROM read_parquet({src_files}) "
            f"WHERE NOT is_taker_order_row"
        ).fetchone()
        bar_files = sql_path(tmp / "*.parquet")
        got = c.sql(
            f"SELECT sum(fills), sum(usd), count(*) FROM read_parquet({bar_files})"
        ).fetchone()
        n_ok = (want[0] or 0) == (got[0] or 0)
        usd_ok = abs((want[1] or 0) - (got[1] or 0)) <= 1e-9 * max(1.0, abs(want[1] or 0))
        if not (n_ok and usd_ok):
            raise SystemExit(
                f"{ym} bars_{name}: fills/usd {got[:2]} do not add up to {want}; left in {tmp}"
            )
        swap_in(tmp, dst)
        out[name] = int(got[2])
    return out


def build_fills(months: Optional[List[str]] = None, rebuild: bool = False) -> None:
    if not (lake() / "markets.parquet").exists():
        raise SystemExit(
            "build the market table first (predict-ngin data build polymarket --markets)"
        )
    c = _con()
    state_f = lake() / "fills_state.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    all_m = source_months(c)
    latest = all_m[-1]
    # the month the source ended in at the last build may have grown since (its last days arrive
    # after the next month has started), so it is rebuilt too, not only the latest month
    prev_end = state.get("_source_end_ts")
    grown = (
        dt.datetime.fromtimestamp(prev_end - 1, dt.timezone.utc).strftime("%Y-%m")
        if prev_end
        else latest
    )
    # months whose original files were deleted after verification (``_sources_retired``) exist
    # only in the lake: rebuilding them from what is left would silently drop rows
    retired = (state.get("_sources_retired") or {}).get("before_month")
    for ym in months or all_m:
        if retired and ym < retired:
            if ym not in state or rebuild or months:
                log(f"{ym}: skipped, its source files were retired (lake is the only copy)")
            continue
        if ym in state and not rebuild and ym < min(latest, grown):
            continue
        t0 = time.time()
        # a fresh connection per month: one long-lived connection crashed DuckDB 1.5.6 (access
        # violation) after ~15 large COPYs, while the same month built fine in a new process
        c.close()
        c = _con()
        st = build_month(c, ym)
        st["bars"] = build_bars_month(c, ym)
        state[ym] = {
            **st,
            "built": dt.datetime.now().isoformat(timespec="seconds"),
            "category_version": pc.VERSION,
        }
        state["_complete"] = all(m in state for m in all_m)
        state["_source_end_ts"] = tape.tape_end_ts()
        state_f.write_text(json.dumps(state, indent=1, sort_keys=True))
        log(f"{ym}: {st['rows']:,} rows in {st['files']} file(s), {time.time() - t0:.0f}s")
    months_built = sorted(k for k in state if not k.startswith("_"))
    manifest.update(
        "polymarket/fills",
        months=len(months_built),
        first=months_built[0],
        last=months_built[-1],
        rows=sum(state[k]["rows"] for k in months_built),
        complete=state.get("_complete", False),
        category_version=pc.VERSION,
        source_end_ts=state.get("_source_end_ts"),
    )

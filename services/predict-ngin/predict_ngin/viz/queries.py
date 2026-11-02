"""SQL builders for the data explorer: pure functions, no Streamlit, no file access.

Every builder takes its inputs as *sources*: a view name (``polymarket_bars_1h``) or a
parenthesised subquery (``(SELECT ... )``) that :mod:`predict_ngin.viz.sources` builds with the
time range already applied, so only the months or files in range are opened. Builders add their
own time and market filters on top, so the same SQL runs against the catalog views, the bounded
subqueries and the small in-memory tables the unit tests create.

Conventions shared with the lake (docs/DATA_LAKE.md):

* times are UTC seconds (``ts``, ``ts_chain``, ``timestamp_unix``) or nanoseconds (recorder
  ``ts_recv_ns``); ranges are half-open ``[start, end)``;
* prices are the YES price in [0, 1];
* Polymarket volume is ``usd_amount`` from maker fills (``usd`` in the bars); Kalshi notional is
  what takers paid: ``count_fp * (yes_price if taker_side = 'yes' else no_price)``, the
  definition the Kalshi bars use.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

# YES outcome of a Polymarket market from ``outcome_prices`` ("['1', '0']", '["0", "1"]', ...):
# the first number. Resolved markets have it at 0 or 1 (within half a cent).
PM_FIRST_OUTCOME_PRICE = (
    "try_cast(regexp_extract(outcome_prices, '^\\[\\s*[''\"]?([0-9.eE+-]+)', 1) AS DOUBLE)"
)


# ------------------------------------------------------------------------------------- helpers


def kalshi_notional(alias: str = "") -> str:
    """Taker-paid notional of one Kalshi trade (matches ``notional`` in kalshi_bars_*)."""
    a = f"{alias}." if alias else ""
    return (
        f"coalesce({a}count_fp, {a}count) * "
        f"CASE WHEN {a}taker_side = 'yes' THEN {a}yes_price ELSE {a}no_price END"
    )


def lit(value: str) -> str:
    """A SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def lit_list(values: Iterable[str]) -> str:
    """``('a', 'b')`` for an IN clause; ``(NULL)`` when empty, which matches nothing."""
    vals = [lit(v) for v in values]
    return "(" + (", ".join(vals) if vals else "NULL") + ")"


def like_pattern(text: str) -> str:
    """A case-insensitive substring pattern with LIKE wildcards in ``text`` escaped."""
    esc = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return lit(f"%{esc}%")


def ts_range(col: str, start: Optional[float], end: Optional[float]) -> str:
    """``col >= start AND col < end`` (either bound optional); ``TRUE`` when both are None."""
    parts = []
    if start is not None:
        parts.append(f"{col} >= {int(start)}")
    if end is not None:
        parts.append(f"{col} < {int(end)}")
    return " AND ".join(parts) or "TRUE"


def month_partition_filter(start: Optional[float], end: Optional[float]) -> str:
    """Filter on the hive ``year`` / ``month`` columns of lake bar files, so DuckDB skips whole
    month folders. Pass it only for sources with those columns (the ``*_bars_*`` views)."""
    import datetime as dt

    def ym(ts: float) -> int:
        d = dt.datetime.fromtimestamp(int(ts), dt.timezone.utc)
        return d.year * 100 + d.month

    expr = "(year * 100 + CAST(month AS INTEGER))"
    parts = []
    if start is not None:
        parts.append(f"{expr} >= {ym(start)}")
    if end is not None:
        parts.append(f"{expr} <= {ym(end - 1)}")
    return " AND ".join(parts) or "TRUE"


def _and(*clauses: str) -> str:
    return " AND ".join(f"({c})" for c in clauses if c and c != "TRUE") or "TRUE"


# ------------------------------------------------------------------------------------ overview
def pm_monthly_volume_from_bars(
    bars: str,
    start: Optional[float],
    end: Optional[float],
    exclude_updown: bool = False,
    partition_filter: bool = True,
) -> str:
    """Polymarket USD volume and fill count per month and category, from hourly bars."""
    where = _and(
        ts_range("ts", start, end),
        month_partition_filter(start, end) if partition_filter else "TRUE",
        "NOT coalesce(is_updown, FALSE)" if exclude_updown else "TRUE",
    )
    return (
        "SELECT strftime(to_timestamp(ts), '%Y-%m') AS month, "
        "coalesce(category, 'Unknown') AS category, sum(usd) AS usd, sum(fills) AS fills "
        f"FROM {bars} WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2"
    )


def pm_monthly_volume_from_fills(
    fills: str,
    markets: str,
    start: Optional[float],
    end: Optional[float],
    exclude_updown: bool = False,
) -> str:
    """The same as :func:`pm_monthly_volume_from_bars` from maker fills (slower: one row per
    fill). Category and the up/down flag come from the market table, so this works on the
    legacy fill layout that lacks them."""
    where = _and(
        ts_range("f.ts_chain", start, end),
        "NOT coalesce(m.is_updown, FALSE)" if exclude_updown else "TRUE",
    )
    return (
        "SELECT strftime(to_timestamp(f.ts_chain), '%Y-%m') AS month, "
        "coalesce(m.category, 'Unknown') AS category, sum(f.usd_amount) AS usd, "
        "count(*) AS fills "
        f"FROM {fills} f LEFT JOIN {markets} m ON m.market_id = f.market_id "
        f"WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2"
    )


def kalshi_monthly_volume(
    trades: str,
    start: Optional[float],
    end: Optional[float],
    lake_markets: Optional[str] = None,
) -> str:
    """Kalshi taker-paid notional (USD), contracts and trades per month and group.

    The group is the category from ``kalshi_lake_markets`` when given, else the series (the
    event ticker up to the first ``-``).
    """
    where = ts_range("t.timestamp_unix", start, end)
    notional = kalshi_notional("t")
    if lake_markets:
        group = "coalesce(m.category, 'Unknown')"
        join = f"LEFT JOIN {lake_markets} m ON m.ticker = t.ticker"
    else:
        group = "split_part(t.event_ticker, '-', 1)"
        join = ""
    return (
        "SELECT strftime(to_timestamp(t.timestamp_unix), '%Y-%m') AS month, "
        f"{group} AS category, sum({notional}) AS usd, "
        "sum(coalesce(t.count_fp, t.count)) AS contracts, count(*) AS trades "
        f"FROM {trades} t {join} WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2"
    )


# ------------------------------------------------------------------------------ market explorer
def pm_search(markets: str, text: str, limit: int = 50, exclude_updown: bool = False) -> str:
    """Polymarket markets whose question, slug, event title or id match ``text``, largest
    reported volume first."""
    pat = like_pattern(text.strip())
    match = (
        f"question ILIKE {pat} ESCAPE '\\' OR slug ILIKE {pat} ESCAPE '\\' "
        f"OR event_title ILIKE {pat} ESCAPE '\\' OR market_id = {lit(text.strip())}"
    )
    where = _and(match, "NOT coalesce(is_updown, FALSE)" if exclude_updown else "TRUE")
    return (
        "SELECT market_id, question, event_title, category, is_updown, closed, outcome_prices, "
        "try_cast(volume AS DOUBLE) AS volume, created_at, end_date, event_id "
        f"FROM {markets} WHERE {where} "
        f"ORDER BY try_cast(volume AS DOUBLE) DESC NULLS LAST LIMIT {int(limit)}"
    )


def kalshi_search(markets: str, text: str, limit: int = 50, has_category: bool = False) -> str:
    """Kalshi markets whose ticker, event ticker or title match ``text``.

    ``markets`` is ``kalshi_lake_markets`` (``has_category=True``: every ticker in the tape) or
    ``kalshi_markets`` (markets seen since 2026-01-28).
    """
    pat = like_pattern(text.strip())
    cat = "category" if has_category else "CAST(NULL AS VARCHAR) AS category"
    return (
        f"SELECT ticker, event_ticker, title, {cat}, status, result "
        f"FROM {markets} WHERE ticker ILIKE {pat} ESCAPE '\\' "
        f"OR event_ticker ILIKE {pat} ESCAPE '\\' OR title ILIKE {pat} ESCAPE '\\' "
        f"ORDER BY (ticker = {lit(text.strip().upper())}) DESC, ticker LIMIT {int(limit)}"
    )


def bars_series(
    bars: str,
    id_col: str,
    market_id: str,
    start: Optional[float],
    end: Optional[float],
    volume_col: str,
    extra_filters: Sequence[str] = (),
    partition_filter: bool = True,
) -> str:
    """One market's bars in range: ts, open, high, low, close, vwap, volume.

    ``extra_filters`` lets callers add sort-key filters (category, event) that let DuckDB skip
    row groups, since bar files are sorted by category, event, market, time.
    """
    where = _and(
        f"{id_col} = {lit(market_id)}",
        ts_range("ts", start, end),
        month_partition_filter(start, end) if partition_filter else "TRUE",
        *extra_filters,
    )
    return (
        "SELECT ts, open, high, low, close, vwap, "
        f"{volume_col} AS volume FROM {bars} WHERE {where} ORDER BY ts"
    )


def trades_series(
    trades: str,
    id_col: str,
    market_id: str,
    ts_col: str,
    price_col: str,
    volume_expr: str,
    start: Optional[float],
    end: Optional[float],
    bucket_s: int = 3600,
) -> str:
    """Bars built on the fly from one market's trades, for when no bar files cover the range."""
    b = int(bucket_s)
    where = _and(f"{id_col} = {lit(market_id)}", ts_range(ts_col, start, end))
    return (
        f"SELECT (CAST({ts_col} AS BIGINT) // {b}) * {b} AS ts, "
        f"arg_min({price_col}, {ts_col}) AS open, max({price_col}) AS high, "
        f"min({price_col}) AS low, arg_max({price_col}, {ts_col}) AS close, "
        f"sum({price_col} * ({volume_expr})) / nullif(sum({volume_expr}), 0) AS vwap, "
        f"sum({volume_expr}) AS volume "
        f"FROM {trades} WHERE {where} GROUP BY 1 ORDER BY 1"
    )


def recent_trades(
    trades: str,
    id_col: str,
    market_id: str,
    ts_col: str,
    columns: Sequence[str],
    start: Optional[float],
    end: Optional[float],
    limit: int = 200,
) -> str:
    """The latest ``limit`` trades of one market in range, newest first."""
    where = _and(f"{id_col} = {lit(market_id)}", ts_range(ts_col, start, end))
    cols = ", ".join(columns)
    return (
        f"SELECT to_timestamp({ts_col}) AS time, {cols} FROM {trades} WHERE {where} "
        f"ORDER BY {ts_col} DESC LIMIT {int(limit)}"
    )


# ---------------------------------------------------------------------------------- calibration
def pm_outcomes(markets: str, categories: Sequence[str] = (), exclude_updown: bool = True) -> str:
    """Resolved Polymarket markets: market_id, category, yes_won (1/0).

    A market counts as resolved when it is closed and its first outcome price is within half a
    cent of 0 or 1.
    """
    where = _and(
        "lower(CAST(closed AS VARCHAR)) IN ('1', 'true')",
        f"{PM_FIRST_OUTCOME_PRICE} >= 0.995 OR {PM_FIRST_OUTCOME_PRICE} <= 0.005",
        f"category IN {lit_list(categories)}" if categories else "TRUE",
        "NOT coalesce(is_updown, FALSE)" if exclude_updown else "TRUE",
    )
    return (
        "SELECT market_id, category, "
        f"CASE WHEN {PM_FIRST_OUTCOME_PRICE} >= 0.5 THEN 1 ELSE 0 END AS yes_won "
        f"FROM {markets} WHERE {where}"
    )


def kalshi_outcomes(
    markets: str,
    series_prefix: str = "",
    categories: Sequence[str] = (),
    has_category: bool = False,
) -> str:
    """Settled binary Kalshi markets: market_id (ticker), category, yes_won (1/0)."""
    cat = "category" if has_category else "split_part(event_ticker, '-', 1)"
    where = _and(
        "lower(result) IN ('yes', 'no')",
        f"ticker LIKE {lit(series_prefix.strip().upper() + '%')}" if series_prefix else "TRUE",
        f"{cat} IN {lit_list(categories)}" if categories else "TRUE",
    )
    return (
        f"SELECT ticker AS market_id, {cat} AS category, "
        "CASE WHEN lower(result) = 'yes' THEN 1 ELSE 0 END AS yes_won "
        f"FROM {markets} WHERE {where}"
    )


def pm_price_obs_from_bars(
    bars: str,
    start: Optional[float],
    end: Optional[float],
    exclude_updown: bool = True,
    partition_filter: bool = True,
) -> str:
    """(market_id, ts, price, usd) observations from Polymarket bars: the bar close."""
    where = _and(
        ts_range("ts", start, end),
        month_partition_filter(start, end) if partition_filter else "TRUE",
        "NOT coalesce(is_updown, FALSE)" if exclude_updown else "TRUE",
    )
    return f"SELECT market_id, ts, close AS price, usd FROM {bars} WHERE {where}"


def pm_price_obs_from_fills(fills: str, start: Optional[float], end: Optional[float]) -> str:
    """(market_id, ts, price, usd) observations from Polymarket maker fills."""
    return (
        "SELECT market_id, CAST(ts_chain AS BIGINT) AS ts, yes_price AS price, "
        f"usd_amount AS usd FROM {fills} WHERE {ts_range('ts_chain', start, end)}"
    )


def kalshi_price_obs(trades: str, start: Optional[float], end: Optional[float]) -> str:
    """(market_id, ts, price, usd) observations from Kalshi trades."""
    return (
        "SELECT ticker AS market_id, timestamp_unix AS ts, yes_price AS price, "
        f"{kalshi_notional()} AS usd FROM {trades} "
        f"WHERE {ts_range('timestamp_unix', start, end)}"
    )


def kalshi_price_obs_from_bars(
    bars: str, start: Optional[float], end: Optional[float], partition_filter: bool = True
) -> str:
    """(market_id, ts, price, usd) observations from Kalshi bars."""
    where = _and(
        ts_range("ts", start, end),
        month_partition_filter(start, end) if partition_filter else "TRUE",
    )
    return (
        f"SELECT ticker AS market_id, ts, close AS price, notional AS usd FROM {bars} WHERE {where}"
    )


def calibration(
    obs: str,
    outcomes: str,
    horizon_s: int = 86400,
    band_width: float = 0.05,
    min_market_usd: float = 0.0,
) -> str:
    """Price band vs YES win rate, one observation per resolved market.

    For each market the observation is its last price at least ``horizon_s`` before its last
    observed trade in the window (a proxy for resolution time), so near-certain prices just
    before settlement do not dominate. Markets whose window volume is below ``min_market_usd``
    are skipped. Returns band_lo, band_hi, avg_price, win_rate, markets, usd and a 95% Wilson
    interval (ci_lo, ci_hi).
    """
    w = float(band_width)
    h = int(horizon_s)
    z = 1.959963984540054  # two-sided 95%
    z2 = z * z
    return f"""
WITH o AS ({obs}),
r AS ({outcomes}),
j AS (SELECT o.market_id, o.ts, o.price, o.usd, r.yes_won
      FROM o JOIN r ON r.market_id = o.market_id
      WHERE o.price BETWEEN 0 AND 1),
mk AS (SELECT market_id, max(ts) AS last_ts, sum(usd) AS usd FROM j GROUP BY 1),
pick AS (SELECT j.market_id, arg_max(j.price, j.ts) AS price, any_value(j.yes_won) AS yes_won,
                any_value(mk.usd) AS usd
         FROM j JOIN mk ON mk.market_id = j.market_id
         WHERE j.ts <= mk.last_ts - {h} AND coalesce(mk.usd, 0) >= {float(min_market_usd)}
         GROUP BY 1),
b AS (SELECT least(floor(price / {w}), {int(round(1 / w)) - 1}) AS band, price, yes_won, usd
      FROM pick),
g AS (SELECT band, avg(price) AS avg_price, avg(yes_won) AS win_rate, count(*) AS markets,
             sum(usd) AS usd FROM b GROUP BY 1)
SELECT band * {w} AS band_lo, (band + 1) * {w} AS band_hi, avg_price, win_rate, markets, usd,
       (win_rate + {z2 / 2} / markets - {z} * sqrt(win_rate * (1 - win_rate) / markets
         + {z2 / 4} / (markets * markets))) / (1 + {z2} / markets) AS ci_lo,
       (win_rate + {z2 / 2} / markets + {z} * sqrt(win_rate * (1 - win_rate) / markets
         + {z2 / 4} / (markets * markets))) / (1 + {z2} / markets) AS ci_hi
FROM g ORDER BY band
"""


# ------------------------------------------------------------------------------- order books
def book_markets(books: str, limit: int = 500) -> str:
    """Markets in a recorder book source with snapshot counts, busiest first."""
    return (
        "SELECT market_id, outcome, any_value(pair) AS pair, count(*) AS snapshots, "
        "min(ts_recv_ns) AS first_ns, max(ts_recv_ns) AS last_ns "
        f"FROM {books} GROUP BY 1, 2 ORDER BY snapshots DESC LIMIT {int(limit)}"
    )


def _book_key(market_id: str, outcome: Optional[str]) -> str:
    return _and(
        f"market_id = {lit(market_id)}",
        f"outcome = {lit(outcome)}" if outcome else "TRUE",
    )


def book_top_series(
    books: str,
    market_id: str,
    outcome: Optional[str],
    start_ns: int,
    end_ns: int,
    max_points: int = 3000,
) -> str:
    """Best bid / ask / mid over ``[start_ns, end_ns)``, downsampled to at most ``max_points``
    (the last snapshot in each step)."""
    step = max(1, (int(end_ns) - int(start_ns)) // max(1, int(max_points)))
    where = _and(_book_key(market_id, outcome), ts_range("ts_recv_ns", start_ns, end_ns))
    return (
        f"SELECT max(ts_recv_ns) AS ts_ns, arg_max(best_bid, ts_recv_ns) AS best_bid, "
        "arg_max(best_ask, ts_recv_ns) AS best_ask, "
        "(arg_max(best_bid, ts_recv_ns) + arg_max(best_ask, ts_recv_ns)) / 2 AS mid, "
        "count(*) AS snapshots "
        f"FROM {books} WHERE {where} GROUP BY ts_recv_ns // {step} ORDER BY 1"
    )


def book_snapshot_at(books: str, market_id: str, outcome: Optional[str], t_ns: int) -> str:
    """The last snapshot at or before ``t_ns``: one row with the ladders as lists."""
    where = _and(_book_key(market_id, outcome), f"ts_recv_ns <= {int(t_ns)}")
    return (
        "SELECT ts_recv_ns, best_bid, best_ask, bid_px, bid_sz, ask_px, ask_sz "
        f"FROM {books} WHERE {where} ORDER BY ts_recv_ns DESC LIMIT 1"
    )


def live_trades(
    trades: str,
    market_id: str,
    outcome: Optional[str],
    start_ns: int,
    end_ns: int,
    limit: int = 5000,
) -> str:
    """Recorder trades of one market in ``[start_ns, end_ns)``, oldest first."""
    where = _and(_book_key(market_id, outcome), ts_range("ts_recv_ns", start_ns, end_ns))
    return (
        "SELECT ts_recv_ns, price, size, taker_side "
        f"FROM {trades} WHERE {where} ORDER BY ts_recv_ns LIMIT {int(limit)}"
    )

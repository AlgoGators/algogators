"""Fill the continuous rows the vendor did not deliver, from the per-contract table.

trade-ngin grids every per-contract series on the <ROOT>.v.0 dates of futures_data.ohlcv_1d,
and its optimiser builds one covariance on the dates every symbol shares, so a .v.0 row the
vendor never delivered removes that day's real contract bars from every run. After each day's
pulls, this finds, for every continuous symbol over a short lookback, the UTC dates on which
its source root has bars in futures_data.contract_ohlcv_1d while neither futures_data.ohlcv_1d
nor futures_data.ohlcv_1d_raw holds the symbol, and inserts one contract's bar into both:

- The source root is the root of the contract named by the symbol's nearest earlier .v.0 row
  (ohlcv_1d_raw.instrument_id, matched on the instrument id AND the date: ids are reused
  across years). MES.v.0 therefore reads ES before 2026-02-22 and MES after, as its rows do.
- Bracket: when the nearest .v.0 rows before and after the date name the same contract and it
  has a bar on the date, that contract.
- Carry: otherwise, when the contract the nearest earlier .v.0 row names has a bar on the date,
  that contract. A filled day then never shows trade-ngin's roll rule a vendor switch the vendor
  did not make: the switch stays on the next real row.
- Otherwise the volume rule. On a weekday: the busiest contract of the previous weekday the
  root has bars (ties: the earlier expiration, then the lower instrument id), or, when that
  one has no bar on the date, the busiest of the date itself. On a Saturday or Sunday: the
  previous weekday's pick, or the busiest of the date itself.

The row is that contract's bar exactly, in both tables, with the rtype and publisher_id of the
symbol's nearest earlier raw row. The raw row carries the contract's instrument id for the
bracket and the carry, and NULL for the volume rule: the vendor's contract on that day is not
known, and trade-ngin's roll rule reads a NULL id as no vendor information, so a filled day
never shows it a vendor switch the vendor did not make. Insert only: a (symbol, UTC
date) either table holds is never written, so the vendor's own bar, when a later re-pull
fetches it, replaces the filled one through the pull's upsert. Saturdays are never filled
(HD 2026-10-28: only crypto trades them, and those are not holes). Every inserted row is logged
as one CONTINUOUS_FILL line. The rule is trade-ngin's DFILL rule (2026-10-28), which picks
Databento's own .v.0 contract on every hole date HD checked (42 of 42).
"""

import logging
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Days before bar date D whose holes each run fills (the pulls re-fetch D-5 .. D).
LOOKBACK_DAYS = 10
# Days read before the window: the nearest earlier .v.0 row and the previous weekday's bars.
CONTEXT_DAYS = 45

BRACKET = "B_bracket"
CARRY = "C_carry_previous_vendor"
PREV_WEEKDAY = "V_prev_weekday_busiest"
WEEKEND = "V_weekend_prev_weekday_pick"
FALLBACK = "V_fallback_same_day_busiest"
FIRST_DATE = "V_first_date_same_day_busiest"


@dataclass(frozen=True)
class ContractBar:
    """One row of futures_data.contract_ohlcv_1d."""

    root: str
    day: date
    instrument_id: str
    raw_symbol: str
    expiration: date | None
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class VendorRow:
    """One date of a continuous symbol in either table, with the raw row's fields (or None)."""

    day: date
    instrument_id: str | None
    rtype: str | None
    publisher_id: str | None


@dataclass(frozen=True)
class FilledRow:
    symbol: str
    day: date
    bar: ContractBar
    branch: str
    rtype: str | None
    publisher_id: str | None


def busiest(bars: list[ContractBar]) -> ContractBar:
    """The day's busiest contract; ties: the earlier expiration (none last), then the lower id."""
    return min(
        bars,
        key=lambda b: (-b.volume, b.expiration is None, b.expiration or date.max, b.instrument_id),
    )


def _previous_weekday(days: list[date], day: date) -> date | None:
    """The latest weekday in sorted `days` strictly before `day`."""
    i = bisect_left(days, day) - 1
    while i >= 0:
        if days[i].isoweekday() < 6:
            return days[i]
        i -= 1
    return None


def _weekday_pick(by_day: dict[date, dict[str, ContractBar]], days: list[date], day: date):
    today = by_day.get(day)
    if not today:
        return None, ""
    prev = _previous_weekday(days, day)
    if prev is None:
        return busiest(list(today.values())), FIRST_DATE
    choice = busiest(list(by_day[prev].values()))
    if choice.instrument_id in today:
        return today[choice.instrument_id], PREV_WEEKDAY
    return busiest(list(today.values())), FALLBACK


def volume_pick(by_day: dict[date, dict[str, ContractBar]], day: date):
    """The volume rule on `day` for one root: (bar, branch), or (None, "") if it has no bar.
    `by_day` maps each date the root has bars to {instrument_id: bar}."""
    days = sorted(by_day)
    if day.isoweekday() < 6:
        return _weekday_pick(by_day, days, day)
    today = by_day.get(day)
    if not today:
        return None, ""
    prev = _previous_weekday(days, day)
    if prev is None:
        return busiest(list(today.values())), FIRST_DATE
    choice, _ = _weekday_pick(by_day, days, prev)
    if choice is not None and choice.instrument_id in today:
        return today[choice.instrument_id], WEEKEND
    return busiest(list(today.values())), FALLBACK


def pick_contract(
    by_day: dict[date, dict[str, ContractBar]],
    day: date,
    before_id: str | None,
    after_id: str | None,
):
    """The contract filling `day`: the bracket when both neighbours agree, else the earlier
    neighbour's contract when it has a bar on the date, else the volume rule."""
    today = by_day.get(day) or {}
    if before_id is not None and before_id in today:
        return today[before_id], BRACKET if before_id == after_id else CARRY
    return volume_pick(by_day, day)


def find_fills(
    vendor_rows: dict[str, list[VendorRow]],
    bars: list[ContractBar],
    start: date,
    end: date,
) -> list[FilledRow]:
    """
    The rows to insert for the dates `start` .. `end`. `vendor_rows` holds every date each
    continuous symbol has in either table (sorted, with the context before and after the
    window), `bars` the contract rows of the same span.
    """
    by_root: dict[str, dict[date, dict[str, ContractBar]]] = defaultdict(dict)
    by_key: dict[tuple[str, date], ContractBar] = {}
    for b in bars:
        by_root[b.root].setdefault(b.day, {})[b.instrument_id] = b
        by_key[(b.instrument_id, b.day)] = b
    fills = []
    for symbol in sorted(vendor_rows):
        rows = vendor_rows[symbol]
        have = {r.day for r in rows}
        dates = [r.day for r in rows]
        roots_seen = {
            by_key[(r.instrument_id, r.day)].root
            for r in rows
            if r.instrument_id is not None and (r.instrument_id, r.day) in by_key
        }
        candidates = sorted(
            {d for root in roots_seen for d in by_root[root] if start <= d <= end}
            - have
        )
        for day in candidates:
            if day.isoweekday() == 6:
                continue
            i = bisect_left(dates, day)
            # The source root: the nearest earlier row whose vendor contract is known.
            source = None
            j = i - 1
            while j >= 0:
                r = rows[j]
                key = (r.instrument_id, r.day)
                if r.instrument_id is not None and key in by_key:
                    source = by_key[key].root
                    break
                j -= 1
            if source is None or day not in by_root[source]:
                continue
            before = rows[i - 1] if i > 0 else None
            after = rows[i] if i < len(rows) else None
            bar, branch = pick_contract(
                by_root[source],
                day,
                before.instrument_id if before else None,
                after.instrument_id if after else None,
            )
            if bar is None:
                continue
            fills.append(
                FilledRow(
                    symbol,
                    day,
                    bar,
                    branch,
                    before.rtype if before else None,
                    before.publisher_id if before else None,
                )
            )
    return fills


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


READ_VENDOR_ROWS = """
SELECT symbol, d, max(instrument_id), max(rtype), max(publisher_id) FROM (
  SELECT r.symbol, (r.ts_event AT TIME ZONE 'UTC')::date AS d, r.instrument_id, r.rtype, r.publisher_id
  FROM futures_data.ohlcv_1d_raw r
  WHERE r.symbol LIKE '%%.v.0' AND r.ts_event >= %(lo)s AND r.ts_event < %(hi)s
  UNION ALL
  SELECT o.symbol, (o.time AT TIME ZONE 'UTC')::date, NULL, NULL, NULL
  FROM futures_data.ohlcv_1d o
  WHERE o.symbol LIKE '%%.v.0' AND o.time >= %(lo)s AND o.time < %(hi)s
) t GROUP BY symbol, d ORDER BY symbol, d
"""

READ_CONTRACT_BARS = """
SELECT root, date, instrument_id, raw_symbol, expiration, open, high, low, close, volume
FROM futures_data.contract_ohlcv_1d WHERE date BETWEEN %(lo)s AND %(hi)s
"""

INSERT_CONTINUOUS = """
INSERT INTO futures_data.ohlcv_1d (time, symbol, volume, open, high, low, close)
SELECT %(ts)s, %(symbol)s, %(volume)s, %(open)s, %(high)s, %(low)s, %(close)s
WHERE NOT EXISTS (SELECT 1 FROM futures_data.ohlcv_1d WHERE symbol = %(symbol)s
                  AND time >= %(ts)s AND time < %(ts_next)s)
"""

INSERT_RAW = """
INSERT INTO futures_data.ohlcv_1d_raw
  (ts_event, symbol, open, high, low, close, volume, rtype, publisher_id, instrument_id)
SELECT %(ts)s, %(symbol)s, %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s, %(rtype)s,
       %(publisher_id)s, %(instrument_id)s
WHERE NOT EXISTS (SELECT 1 FROM futures_data.ohlcv_1d_raw WHERE symbol = %(symbol)s
                  AND ts_event >= %(ts)s AND ts_event < %(ts_next)s)
"""


def fill_continuous_holes(
    connection: Any, start: date, end: date, context_days: int = CONTEXT_DAYS
) -> list[FilledRow]:
    """
    Find and insert the missing continuous rows for `start` .. `end` in one transaction, with
    both continuous tables locked against other writers while it reads and writes. Returns
    the rows inserted (each logged as one CONTINUOUS_FILL line).
    """
    lo, hi = start - timedelta(days=context_days), end + timedelta(days=context_days)
    with connection.cursor() as cur:
        cur.execute("BEGIN")
        try:
            cur.execute(
                "LOCK TABLE futures_data.ohlcv_1d, futures_data.ohlcv_1d_raw "
                "IN SHARE ROW EXCLUSIVE MODE"
            )
            cur.execute(READ_VENDOR_ROWS, {"lo": _utc_midnight(lo), "hi": _utc_midnight(hi)})
            vendor_rows: dict[str, list[VendorRow]] = defaultdict(list)
            for symbol, d, iid, rtype, publisher in cur.fetchall():
                vendor_rows[symbol].append(VendorRow(d, iid, rtype, publisher))
            cur.execute(READ_CONTRACT_BARS, {"lo": lo, "hi": end})
            bars = [
                ContractBar(r[0], r[1], str(r[2]), r[3], r[4], *map(float, r[5:9]), int(r[9]))
                for r in cur.fetchall()
            ]
            fills = find_fills(vendor_rows, bars, start, end)
            inserted = []
            for f in fills:
                b = f.bar
                params = {
                    "ts": _utc_midnight(f.day),
                    "ts_next": _utc_midnight(f.day + timedelta(days=1)),
                    "symbol": f.symbol,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "rtype": f.rtype,
                    "publisher_id": f.publisher_id,
                    "instrument_id": b.instrument_id if f.branch in (BRACKET, CARRY) else None,
                }
                cur.execute(INSERT_CONTINUOUS, params)
                n_continuous = cur.rowcount
                cur.execute(INSERT_RAW, params)
                if n_continuous != 1 or cur.rowcount != 1:
                    raise RuntimeError(
                        f"continuous fill {f.symbol} {f.day}: inserted {n_continuous} / "
                        f"{cur.rowcount} rows, expected 1 / 1"
                    )
                inserted.append(f)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    for f in inserted:
        logger.warning(
            "CONTINUOUS_FILL %s %s from %s (instrument_id %s, %s, volume %d)",
            f.symbol,
            f.day.isoformat(),
            f.bar.raw_symbol,
            f.bar.instrument_id,
            f.branch,
            f.bar.volume,
        )
    return inserted


def run_continuous_fill(
    config: dict[str, Any], bar_date: date, days: int = LOOKBACK_DAYS
) -> dict[str, Any]:
    """Fill the holes of D-days .. D; a JSON-able summary of what was inserted."""
    from data_ngin.infrastructure.repository.ohlcv_repository import OhlcvRepository

    repository = OhlcvRepository(config)
    repository.connect()
    try:
        filled = fill_continuous_holes(
            repository.connection, bar_date - timedelta(days=days), bar_date
        )
    finally:
        repository.close()
    if not filled:
        logger.info("Continuous fill %s: no missing .v.0 row in the last %d days", bar_date, days)
    return {
        "bar_date": bar_date.isoformat(),
        "filled": [
            {
                "symbol": f.symbol,
                "date": f.day.isoformat(),
                "instrument_id": f.bar.instrument_id,
                "raw_symbol": f.bar.raw_symbol,
                "branch": f.branch,
            }
            for f in filled
        ],
    }

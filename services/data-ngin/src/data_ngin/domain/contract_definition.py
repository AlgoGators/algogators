"""
Contract definition parsing and expiration calculation (AlgoGators/algogators#37).
Provides extraction of expiration dates from Databento .definition.dbn.zst records,
with CME product-specific fallback expiration date calculation.
"""

import calendar
import logging
import os
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import databento as db
import pandas as pd

from data_ngin.domain.contract_filter import is_outright_contract

logger = logging.getLogger(__name__)

MONTH_CODE_TO_MONTH = {
    "F": 1,
    "G": 2,
    "H": 3,
    "J": 4,
    "K": 5,
    "M": 6,
    "N": 7,
    "Q": 8,
    "U": 9,
    "V": 10,
    "X": 11,
    "Z": 12,
}


def get_third_friday(year: int, month: int) -> date:
    """Calculate the 3rd Friday of a given year and month."""
    c = calendar.monthcalendar(year, month)
    fridays = [week[4] for week in c if week[4] != 0]
    return date(year, month, fridays[2])


def get_cme_expiration_fallback(root: str, month_code: str, year: int) -> date:
    """
    Compute fallback expiration date according to CME specifications when
    vendor definition record is unavailable.
    """
    month = MONTH_CODE_TO_MONTH.get(month_code.upper())
    if not month:
        raise ValueError(f"Invalid contract month code: {month_code}")

    root_upper = root.upper()

    # Equity Index Futures (ES, NQ, YM, RTY, MES, MNQ, MYM, M2K)
    if root_upper in {"ES", "NQ", "YM", "RTY", "MES", "MNQ", "MYM", "M2K"}:
        return get_third_friday(year, month)

    # Energy Futures: CL, RB, HO (3 business days prior to 25th calendar day of prior month)
    if root_upper in {"CL", "RB", "HO"}:
        prior_month = 12 if month == 1 else month - 1
        prior_year = year - 1 if month == 1 else year
        base = date(prior_year, prior_month, 25)
        exp = base - timedelta(days=5 if base.weekday() == 0 else 3)
        return exp

    # Natural Gas (NG): 3 business days prior to the first day of delivery month
    if root_upper == "NG":
        first_day = date(year, month, 1)
        exp = first_day - timedelta(days=3 if first_day.weekday() >= 3 else 5)
        return exp

    # Default for all other commodities/financials: 3rd Friday of contract month
    return get_third_friday(year, month)


# Definition records are matched to a contract on (instrument_id, raw_symbol), never on the
# id alone: the vendor reuses an instrument id for unrelated contracts years apart (2017 was
# 6BX0 in 2020 and GCQ4 in 2023-24), and raw symbols repeat every ten years (GCZ6 is both the
# 2016 and the 2026 contract). Spreads and other non-outright records are left out.
DefinitionKey = tuple[str, str]


@dataclass(frozen=True)
class DefinitionRecord:
    """One expiration seen for a (instrument_id, raw_symbol), with when it was published."""

    expiration: date
    first_seen: date
    last_seen: date


DefinitionIndex = dict[DefinitionKey, list[DefinitionRecord]]


def _add(index: DefinitionIndex, key: DefinitionKey, exp: date, seen: date) -> None:
    records = index.setdefault(key, [])
    for i, rec in enumerate(records):
        if rec.expiration == exp:
            records[i] = DefinitionRecord(exp, min(rec.first_seen, seen), max(rec.last_seen, seen))
            return
    records.append(DefinitionRecord(exp, seen, seen))


def _to_date(value: Any) -> date | None:
    if value is None or (not isinstance(value, (date, datetime)) and pd.isna(value)):
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        ts = pd.Timestamp(value)
        return (ts.tz_convert(UTC) if ts.tzinfo else ts).date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except (ValueError, TypeError):
        return None


def extract_definitions_from_df(
    df: pd.DataFrame, index: DefinitionIndex | None = None
) -> DefinitionIndex:
    """
    Index outright-future definition records of a Databento definition DataFrame by
    (instrument_id, raw_symbol). Rows need instrument_id, raw_symbol and expiration;
    instrument_class (kept when 'F') and ts_recv (publication date) are used when present.
    """
    index = {} if index is None else index
    needed = {"instrument_id", "raw_symbol", "expiration"}
    if df.empty or not needed.issubset(df.columns):
        return index
    frame = df.reset_index() if "ts_recv" not in df.columns and df.index.name == "ts_recv" else df
    for row in frame.itertuples(index=False):
        cls = getattr(row, "instrument_class", "F")
        if str(cls) != "F":
            continue
        symbol = str(row.raw_symbol)
        if not is_outright_contract(symbol):
            continue
        exp = _to_date(row.expiration)
        if exp is None:
            continue
        seen = _to_date(getattr(row, "ts_recv", None)) or exp
        _add(index, (str(row.instrument_id), symbol), exp, seen)
    return index


def extract_definitions_from_dbn_bytes(
    raw_bytes: bytes, index: DefinitionIndex | None = None
) -> DefinitionIndex:
    """Index the outright-future records of one DBN definition file (see extract_definitions_from_df)."""
    index = {} if index is None else index
    arr = db.DBNStore.from_bytes(raw_bytes).to_ndarray()
    if not len(arr):
        return index
    keep = arr["instrument_class"] == b"F"
    if "user_defined_instrument" in (arr.dtype.names or ()):
        keep &= arr["user_defined_instrument"] != b"Y"
    for rec in arr[keep]:
        exp_ns = int(rec["expiration"])
        if exp_ns <= 0 or exp_ns >= 2**63:
            continue
        symbol = (
            rec["raw_symbol"].decode()
            if isinstance(rec["raw_symbol"], bytes)
            else str(rec["raw_symbol"])
        )
        if not is_outright_contract(symbol):
            continue
        exp = datetime.fromtimestamp(exp_ns / 1e9, UTC).date()
        seen = datetime.fromtimestamp(int(rec["ts_recv"]) / 1e9, UTC).date()
        _add(index, (str(rec["instrument_id"]), symbol), exp, seen)
    return index


def extract_definitions_from_tar(
    tar_path: str, index: DefinitionIndex | None = None
) -> DefinitionIndex:
    """
    Stream and index all .definition.dbn.zst records from a tar archive.
    """
    index = {} if index is None else index
    if not os.path.exists(tar_path):
        logger.warning("Tar archive not found: %s", tar_path)
        return index

    with tarfile.open(tar_path, "r") as tar:
        for m in tar.getmembers():
            if not m.name.endswith(".definition.dbn.zst"):
                continue
            f = tar.extractfile(m)
            if f is None:
                continue
            extract_definitions_from_dbn_bytes(f.read(), index)
    return index


def build_definition_map(tar_paths: list[str]) -> DefinitionIndex:
    """
    Build one (instrument_id, raw_symbol) index across several archives.
    """
    index: DefinitionIndex = {}
    for p in tar_paths:
        extract_definitions_from_tar(p, index)
    return index


def lookup_expiration(
    index: DefinitionIndex, instrument_id: str, raw_symbol: str, last_bar: date
) -> date | None:
    """
    The expiration of the contract that (instrument_id, raw_symbol) names on a bar dated
    `last_bar` (for a whole contract: its last bar). Only definitions expiring on or after
    that bar fit; if the same id and symbol name contracts a decade apart, the nearest
    contract month wins; if the exchange moved the date of that contract, the
    latest-published definition wins, so every bar of a contract gets the same date.
    None when nothing fits: the caller must not guess.
    """
    fits = [r for r in index.get((str(instrument_id), raw_symbol), []) if r.expiration >= last_bar]
    if not fits:
        return None
    month = min((r.expiration.year, r.expiration.month) for r in fits)
    same = [r for r in fits if (r.expiration.year, r.expiration.month) == month]
    return max(same, key=lambda r: (r.last_seen, r.expiration)).expiration


def contract_expiration_spans(
    rows: Iterable[tuple[str, str, date]], index: DefinitionIndex
) -> list[tuple[str, str, date, date, date | None]]:
    """
    Group stored bars (instrument_id, raw_symbol, date) into runs that get one expiration:
    [(instrument_id, raw_symbol, first_date, last_date, expiration or None)]. Every bar is
    resolved by lookup_expiration, so two contracts that share an id and a symbol a decade
    apart come out as two runs.
    """
    by_key: dict[DefinitionKey, list[date]] = {}
    for inst_id, symbol, day in rows:
        by_key.setdefault((str(inst_id), symbol), []).append(day)
    spans: list[tuple[str, str, date, date, date | None]] = []
    for (inst_id, symbol), days in sorted(by_key.items()):
        days.sort()
        start = days[0]
        current = lookup_expiration(index, inst_id, symbol, days[0])
        prev = days[0]
        for day in days[1:]:
            exp = lookup_expiration(index, inst_id, symbol, day)
            if exp != current:
                spans.append((inst_id, symbol, start, prev, current))
                start, current = day, exp
            prev = day
        spans.append((inst_id, symbol, start, prev, current))
    return spans


def resolve_contract_expiration(
    root: str,
    month_code: str,
    year_str: str,
    bar_date: date,
) -> date | None:
    """
    Resolve outright contract expiration date using year string and CME specifications.
    """
    if len(year_str) == 1:
        decade = (bar_date.year // 10) * 10
        full_year = decade + int(year_str)
        if full_year < bar_date.year - 1:
            full_year += 10
        elif full_year > bar_date.year + 8:
            full_year -= 10
    elif len(year_str) == 2:
        full_year = 2000 + int(year_str)
    else:
        full_year = int(year_str)
    return get_cme_expiration_fallback(root, month_code, full_year)

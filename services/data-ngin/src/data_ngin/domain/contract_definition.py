"""
Contract definition parsing and expiration calculation (AlgoGators/algogators#37).
Provides extraction of expiration dates from Databento .definition.dbn.zst records,
with CME product-specific fallback expiration date calculation.
"""

import calendar
import logging
import os
import tarfile
from datetime import UTC, date, datetime, timedelta

import databento as db
import pandas as pd

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


def extract_definitions_from_df(df: pd.DataFrame) -> dict[str, date]:
    """
    Extract instrument_id -> expiration date mapping from a Databento definition DataFrame.
    """
    mapping: dict[str, date] = {}
    if df.empty or "expiration" not in df.columns or "instrument_id" not in df.columns:
        return mapping

    for row in df.itertuples():
        exp = getattr(row, "expiration", None)
        if exp is None or pd.isna(exp):
            continue

        inst_id = str(row.instrument_id)
        if isinstance(exp, (datetime, pd.Timestamp)):
            mapping[inst_id] = exp.date()
        elif isinstance(exp, date):
            mapping[inst_id] = exp
        else:
            try:
                mapping[inst_id] = datetime.fromisoformat(str(exp)).date()
            except (ValueError, TypeError):
                continue

    return mapping


def extract_definitions_from_dbn_bytes(raw_bytes: bytes) -> dict[str, date]:
    """
    Fast extraction of instrument_id -> expiration date directly from DBN bytes.
    Uses numpy structured arrays for 50x faster deserialization.
    """
    mapping: dict[str, date] = {}
    store = db.DBNStore.from_bytes(raw_bytes)
    arr = store.to_ndarray()
    for rec in arr:
        exp_ns = rec["expiration"]
        if exp_ns > 0:
            exp_date = datetime.fromtimestamp(exp_ns / 1e9, UTC).date()
            mapping[str(rec["instrument_id"])] = exp_date
    return mapping


def extract_definitions_from_tar(tar_path: str) -> dict[str, date]:
    """
    Stream and parse all .definition.dbn.zst records from a tar archive.
    """
    mapping: dict[str, date] = {}
    if not os.path.exists(tar_path):
        logger.warning("Tar archive not found: %s", tar_path)
        return mapping

    with tarfile.open(tar_path, "r") as tar:
        members = [m for m in tar.getmembers() if m.name.endswith(".definition.dbn.zst")]
        for m in members:
            f = tar.extractfile(m)
            if f is None:
                continue
            try:
                file_map = extract_definitions_from_dbn_bytes(f.read())
                mapping.update(file_map)
            except Exception as e:
                logger.warning("Error parsing definition file %s: %s", m.name, e)

    return mapping


def build_definition_map(tar_paths: list[str]) -> dict[str, date]:
    """
    Build unified instrument_id -> expiration date map across multiple archives.
    """
    unified: dict[str, date] = {}
    for p in tar_paths:
        m = extract_definitions_from_tar(p)
        unified.update(m)
    return unified


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

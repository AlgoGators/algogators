"""Which futures contracts traded during a given UTC calendar day.

Databento's ohlcv-1d bars for GLBX.MDP3 are UTC-calendar-day bars: a contract
has a bar for day D when it traded at some point between D 00:00 UTC and D+1
00:00 UTC. So the question asked here is not "is D a trading session?" but "does
any session of this product's calendar overlap UTC day D?". That is what makes:

- Sunday count for the CME Globex products whose week opens Sunday at 17:00 CT
  (22:00/23:00 UTC Sunday): FX, equity index, rates, energy, metals, crypto.
- Sunday not count for grains (week opens 19:00 CT = 00:00/01:00 UTC Monday) or
  livestock (day session only).
- Saturday not count, except for CME crypto futures, which trade 24/7 since
  2026-06-13 (see CRYPTO_24_7_FROM).
- A holiday count for a product that trades an abbreviated session that day
  (e.g. equity index futures on MLK Day), and not count for a product that is
  closed (e.g. grains on MLK Day).

Calendars come from pandas_market_calendars, one per product group. A contract
whose exchange or product group cannot be mapped is treated as expected (fail
safe: an unexpected missing bar is reported, never silently ignored).
"""

import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from typing import Any

logger = logging.getLogger(__name__)

# The CME Group venues that clear on CME Globex. Every contract in the futures
# contract CSVs is on one of these (dataSet GLOBEX).
CME_GROUP_EXCHANGES = frozenset({"CME", "CBOT", "NYMEX", "COMEX"})

CRYPTO_CALENDAR = "CME Globex Crypto"
# CME crypto futures (MBT, BTC, MET, ETH, ...) moved to 24/7 trading. The
# first UTC Saturday with a GLBX.MDP3 MBT bar in new_algo_data is 2026-06-13;
# every Saturday since has one, and no Saturday before does. From this date
# every UTC day is a trading day for them. pandas_market_calendars 5.4.0 still
# models the old Sunday-Friday week and knows no 24/7 holiday closures, so the
# rule is applied here rather than taken from the library.
CRYPTO_24_7_FROM = date(2026, 6, 13)

# Contract CSV `underlying` -> pandas_market_calendars calendar name.
CALENDAR_BY_UNDERLYING: dict[str, str] = {
    "FX": "CMEGlobex_FX",
    "Equity": "CME_Equity",
    "Bond": "CME_Rate",
    "OilGas": "CMEGlobex_Energy",
    "Metals": "CMEGlobex_Metals",
    "Crypto": CRYPTO_CALENDAR,
}

# `underlying` "Ags" covers two product groups with different hours.
LIVESTOCK_SYMBOLS = frozenset({"GF", "HE", "LE"})
GRAINS_CALENDAR = "CMEGlobex_Grains"
LIVESTOCK_CALENDAR = "CMEGlobex_Livestock"


def calendar_name_for(contract: dict[str, Any]) -> str | None:
    """
    The calendar for one contract-CSV row (needs `dataSymbol`, `exchange` and
    `underlying`), or None when it cannot be mapped.
    """
    exchange = str(contract.get("exchange") or "").strip().upper()
    underlying = str(contract.get("underlying") or "").strip()
    symbol = str(contract.get("dataSymbol") or "").strip()
    if exchange not in CME_GROUP_EXCHANGES:
        return None
    if underlying == "Ags":
        return LIVESTOCK_CALENDAR if symbol in LIVESTOCK_SYMBOLS else GRAINS_CALENDAR
    return CALENDAR_BY_UNDERLYING.get(underlying)


@cache
def _calendar(name: str) -> Any:
    import pandas_market_calendars as mcal

    return mcal.get_calendar(name)


def traded_on_utc_day(calendar_name: str, day: date) -> bool:
    """True when any session of `calendar_name` overlaps UTC day `day`."""
    if calendar_name == CRYPTO_CALENDAR and day >= CRYPTO_24_7_FROM:
        return True
    import pandas as pd

    day_start = pd.Timestamp(datetime.combine(day, time.min, tzinfo=UTC))
    day_end = pd.Timestamp(datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC))
    # Sessions are labelled by their close date and can open the evening
    # before, so look a few days either side.
    schedule = _calendar(calendar_name).schedule(
        start_date=day - timedelta(days=3), end_date=day + timedelta(days=3)
    )
    if schedule.empty:
        return False
    overlaps = (schedule["market_open"] < day_end) & (schedule["market_close"] > day_start)
    return bool(overlaps.any())


def contract_traded_on(contract: dict[str, Any], day: date) -> bool:
    """
    Whether this contract is expected to have a bar for UTC day `day`. An
    unmappable contract is expected (and logged), so it can never drop out of
    the expected set unnoticed.
    """
    name = calendar_name_for(contract)
    if name is None:
        logger.warning(
            "No exchange calendar for %s (exchange=%r, underlying=%r); treating it as traded on %s",
            contract.get("dataSymbol"),
            contract.get("exchange"),
            contract.get("underlying"),
            day,
        )
        return True
    return traded_on_utc_day(name, day)


def expected_contracts(contracts: Iterable[dict[str, Any]], day: date) -> list[dict[str, Any]]:
    """The contracts expected to have a bar for UTC day `day`, in input order."""
    return [c for c in contracts if contract_traded_on(c, day)]

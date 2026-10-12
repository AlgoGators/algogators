"""After-close futures ingestion: pull UTC day D's daily bars, plus any gaps.

The futures DAGs run at 20:00 America/New_York. Databento's GLBX.MDP3 ohlcv-1d
bars are UTC-calendar-day bars, and at 20:00 New York time (00:00 UTC in EDT,
01:00 UTC in EST) the UTC day matching the New York date D has just ended. So a
run pulls bar date D:

1. Load the contract CSV and work out, per contract, which of the days
   D-5 .. D its exchange traded on (see trading_calendar). The contracts that
   traded on D are D's expected set. If none did (a Saturday, Christmas), the
   run does nothing.
2. Read which of those (contract, day) bars the target table already holds and
   pull only the missing ones: one Databento range per contract, from its
   earliest missing day to its latest. Writes are INSERT ... ON CONFLICT DO
   NOTHING on (time, symbol), so re-pulling a bar that is already there is a
   no-op.
3. Re-read D's bars and report which expected contracts are still missing.

Heavy imports stay inside the functions (see pipeline_tasks).
"""

import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from data_ngin.application.trading_calendar import expected_contracts

logger = logging.getLogger(__name__)

DATASET = "futures_ohlcv_1d"
BACKFILL_DAYS = 5

CONTRACT_COLUMNS = ("dataSymbol", "instrumentType", "exchange", "underlying")


def bar_date_for_run(reference: datetime) -> date:
    """
    The bar date a run at `reference` pulls: the most recent UTC calendar day
    that has fully ended by then. For the 20:00 America/New_York schedule this
    is the run's New York calendar date, in both EDT (20:00 = 00:00 UTC next
    day) and EST (20:00 = 01:00 UTC next day). An earlier manual run gets the
    day before, never a UTC day still in progress. A naive `reference` is UTC.
    """
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return reference.astimezone(UTC).date() - timedelta(days=1)


def storage_symbol(config: dict[str, Any], data_symbol: str) -> str:
    """The symbol a contract's bars are stored under, e.g. ES -> MES.v.0."""
    from data_ngin.domain.services import SymbolRemapper

    provider = config["provider"]
    base = SymbolRemapper(config.get("symbol_remap")).remap(data_symbol)
    return f"{base}.{provider['roll_type']}.{provider['contract_type']}"


def load_contracts(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The futures rows of the pipeline's contract CSV, with exchange/underlying."""
    import pandas as pd

    from data_ngin.infrastructure.loader.csv_loader import resolve_contract_path

    path = resolve_contract_path(config["loader"]["file_path"])
    # utf-8-sig: contract_valid.csv starts with byte-order marks.
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    frame.columns = [c.strip().lstrip("﻿") for c in frame.columns]
    for column in CONTRACT_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    asset = str(config["provider"]["asset"]).upper()
    contracts = []
    for row in frame[list(CONTRACT_COLUMNS)].to_dict(orient="records"):
        symbol = (row["dataSymbol"] or "").strip() if isinstance(row["dataSymbol"], str) else ""
        if not symbol:
            continue
        instrument = str(row["instrumentType"] or "").strip().upper()
        if instrument != asset:
            continue
        contracts.append({**row, "dataSymbol": symbol, "instrumentType": instrument})
    return contracts


def backfill_days(bar_date: date, days: int = BACKFILL_DAYS) -> list[date]:
    """D-days .. D, oldest first."""
    return [bar_date - timedelta(days=offset) for offset in range(days, -1, -1)]


def find_missing_bars(
    expected_by_day: dict[date, list[dict[str, Any]]],
    present: set[tuple[str, date]],
    to_storage: Any,
) -> dict[str, list[date]]:
    """
    {dataSymbol: [missing days, ascending]} for every (contract, day) that the
    calendar expects and `present` (storage symbol, day) pairs lack.
    """
    missing: dict[str, list[date]] = {}
    for day in sorted(expected_by_day):
        for contract in expected_by_day[day]:
            symbol = contract["dataSymbol"]
            if (to_storage(symbol), day) not in present:
                missing.setdefault(symbol, []).append(day)
    return missing


def readiness_row(
    expected_symbols: Iterable[str],
    present_symbols: Iterable[str],
    bar_date: date,
    run_id: str | None,
    failed: bool = False,
) -> dict[str, Any]:
    """
    The ops.data_ready row for one bar date. 'complete' when every expected
    contract has its bar, else 'partial' -- or 'failed' when the pull itself
    failed and bars are still missing.
    """
    expected = sorted(set(expected_symbols))
    present = set(present_symbols)
    missing = [s for s in expected if s not in present]
    if not missing:
        status = "complete"
    elif failed:
        status = "failed"
    else:
        status = "partial"
    return {
        "dataset": DATASET,
        "bar_date": bar_date,
        "expected": len(expected),
        "loaded": len(expected) - len(missing),
        "missing": missing,
        "status": status,
        "run_id": run_id,
        "finished_at": datetime.now(UTC),
    }


def _present_bars(
    config: dict[str, Any], symbols: list[str], start: date, end: date
) -> set[tuple[str, date]]:
    from data_ngin.infrastructure.repository.ohlcv_repository import OhlcvRepository

    repository = OhlcvRepository(config)
    try:
        return repository.get_bar_dates(symbols, start, end)
    finally:
        repository.close()


def run_close_pipeline(
    config: dict[str, Any], bar_date: date, days: int = BACKFILL_DAYS
) -> dict[str, Any]:
    """
    Pull bar date `bar_date` and fill gaps in the `days` before it. Returns a
    JSON-able summary: {"bar_date", "skipped", "expected", "missing"} where
    expected/missing are D's storage symbols.

    Raises:
        RuntimeError: If any contract's fetch/insert failed (after the others
            finished; see Orchestrator.process).
    """
    import asyncio

    from data_ngin.application.orchestrator import Orchestrator

    contracts = load_contracts(config)
    expected_today = expected_contracts(contracts, bar_date)
    if not expected_today:
        logger.info("No contract traded on %s (UTC); nothing to pull.", bar_date)
        return {"bar_date": bar_date.isoformat(), "skipped": True, "expected": [], "missing": []}

    def to_storage(symbol: str) -> str:
        return storage_symbol(config, symbol)

    window = backfill_days(bar_date, days)
    expected_by_day = {
        day: (expected_today if day == bar_date else expected_contracts(contracts, day))
        for day in window
    }
    all_symbols = sorted({to_storage(c["dataSymbol"]) for c in contracts})
    present = _present_bars(config, all_symbols, window[0], bar_date)
    missing = find_missing_bars(expected_by_day, present, to_storage)

    by_symbol = {c["dataSymbol"]: c for c in contracts}
    jobs = [
        (
            {"dataSymbol": symbol, "instrumentType": by_symbol[symbol]["instrumentType"]},
            min(days_missing).isoformat(),
            max(days_missing).isoformat(),
        )
        for symbol, days_missing in missing.items()
    ]
    backfill = {s: [d.isoformat() for d in ds if d != bar_date] for s, ds in missing.items()}
    backfill = {s: ds for s, ds in backfill.items() if ds}
    if backfill:
        logger.warning("Backfilling missing bars before %s: %s", bar_date, backfill)
    logger.info(
        "Bar date %s: %d expected contract(s), pulling %d contract(s)",
        bar_date,
        len(expected_today),
        len(jobs),
    )
    if jobs:
        asyncio.run(Orchestrator(config=config).process(jobs))

    expected_symbols = [to_storage(c["dataSymbol"]) for c in expected_today]
    present_today = {
        symbol for symbol, _day in _present_bars(config, expected_symbols, bar_date, bar_date)
    }
    still_missing = sorted(s for s in expected_symbols if s not in present_today)
    if still_missing:
        logger.warning("Bar date %s still missing after the pull: %s", bar_date, still_missing)
    return {
        "bar_date": bar_date.isoformat(),
        "skipped": False,
        "expected": sorted(expected_symbols),
        "missing": still_missing,
    }


def write_readiness(
    config: dict[str, Any], bar_date: date, run_id: str | None, failed: bool = False
) -> dict[str, Any] | None:
    """
    Compute and upsert D's ops.data_ready row from what the target table holds
    now. Writes nothing (returns None) when no contract traded on D.
    """
    from data_ngin.infrastructure.repository.readiness_repository import ReadinessRepository

    expected_today = expected_contracts(load_contracts(config), bar_date)
    if not expected_today:
        logger.info("No contract traded on %s (UTC); no readiness row.", bar_date)
        return None
    expected_symbols = [storage_symbol(config, c["dataSymbol"]) for c in expected_today]
    present = {s for s, _d in _present_bars(config, expected_symbols, bar_date, bar_date)}
    row = readiness_row(expected_symbols, present, bar_date, run_id, failed=failed)
    repository = ReadinessRepository(config)
    try:
        repository.upsert_readiness(row)
    finally:
        repository.close()
    return row


def should_retry_for_missing(missing: list[str], try_number: int, max_tries: int) -> bool:
    """
    Whether a run that finished with D's bars still missing should fail so
    Airflow retries it. Databento can lag right after 00:00 UTC, so retry while
    retries remain; the last try succeeds and the readiness row says 'partial'.
    Airflow's try_number starts at 1 and max_tries equals the task's retries.
    """
    return bool(missing) and try_number <= max_tries

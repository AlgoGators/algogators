"""Daily futures ingestion: pull UTC day D's daily bars, plus any gaps.

The futures DAGs run at 04:30 America/New_York. Databento's GLBX.MDP3 ohlcv-1d
bars are UTC-calendar-day bars, and AlgoGators' historical plan publishes them
about 8 hours after the UTC day ends (around 08:00 UTC on D+1). A run at 04:30
New York time (08:30 UTC in EDT, 09:30 UTC in EST) pulls bar date D, the UTC
day that ended before it -- yesterday's New York date:

1. Load the contract CSV and work out, per contract, which of the days
   D-5 .. D its exchange traded on (see trading_calendar). The contracts that
   traded on D are D's expected set. If none did (a Saturday, Christmas), the
   run does nothing.
2. Log any expected (contract, day) bar the target table lacks (gap backfill),
   wait for Databento to publish bar D (wait_for_bar_date), then re-pull every
   expected bar in the window that Databento has published: one range per
   contract. Writes are INSERT ... ON CONFLICT DO UPDATE on (time, symbol) /
   (ts_event, symbol), so a provisional or partial bar is corrected by a later
   run. (Back-adjustment is a no-op here: every fetched row carries the same
   continuous symbol, so no roll is ever detected within a window.)
3. Re-read D's bars and report which expected contracts are still missing.

Heavy imports stay inside the functions (see pipeline_tasks).
"""

import logging
import os
import time as time_module
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from data_ngin.application.trading_calendar import expected_contracts

logger = logging.getLogger(__name__)

DATASET = "futures_ohlcv_1d"
CONTRACT_DATASET = "futures_contract_ohlcv_1d"
BACKFILL_DAYS = 5
DEFAULT_AVAILABILITY_WAIT_MINUTES = 60.0
AVAILABILITY_POLL_SECONDS = 300.0

CONTRACT_COLUMNS = ("dataSymbol", "instrumentType", "exchange", "underlying")

CONTRACT_ROOTS: tuple[str, ...] = (
    "6A",
    "6B",
    "6C",
    "6E",
    "6J",
    "6L",
    "6M",
    "6N",
    "6S",
    "CL",
    "ES",
    "GC",
    "GF",
    "HE",
    "HG",
    "HO",
    "KE",
    "LE",
    "M2K",
    "MBT",
    "MES",
    "MNQ",
    "MYM",
    "NG",
    "NQ",
    "PL",
    "RB",
    "RTY",
    "SI",
    "UB",
    "YM",
    "ZC",
    "ZF",
    "ZL",
    "ZM",
    "ZN",
    "ZR",
    "ZS",
    "ZT",
    "ZW",
)


def bar_date_for_run(reference: datetime) -> date:
    """
    The bar date a run at `reference` pulls: the most recent UTC calendar day
    that has fully ended by then. For the 04:30 America/New_York schedule this
    is the New York date of the day before the run, in both EDT (04:30 = 08:30
    UTC) and EST (04:30 = 09:30 UTC). Never a UTC day still in progress. A
    naive `reference` is UTC.
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


def repull_ranges(
    expected_by_day: dict[date, list[dict[str, Any]]],
) -> dict[str, tuple[date, date]]:
    """
    {dataSymbol: (first, last)} expected day in the window, per contract. Every
    expected bar in the window is re-pulled, not only missing ones, so a bar
    first fetched provisionally is overwritten by the vendor's later values.
    """
    ranges: dict[str, tuple[date, date]] = {}
    for day in sorted(expected_by_day):
        for contract in expected_by_day[day]:
            symbol = contract["dataSymbol"]
            first = ranges.get(symbol, (day, day))[0]
            ranges[symbol] = (first, day)
    return ranges


def availability_wait_seconds() -> float:
    """Longest a run waits for Databento to publish bar D (env, minutes)."""
    value = os.getenv("DATABENTO_AVAILABILITY_WAIT_MINUTES", "").strip()
    return 60 * (float(value) if value else DEFAULT_AVAILABILITY_WAIT_MINUTES)


def wait_for_bar_date(
    availability: Callable[[], tuple[date, datetime]],
    bar_date: date,
    max_wait_seconds: float,
    poll_seconds: float = AVAILABILITY_POLL_SECONDS,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time_module.sleep,
) -> date:
    """
    Poll Databento's dataset range until bar `bar_date` is published or the
    wait budget runs out, and return the last published daily bar date.

    Bar D is published once the dataset's intraday `end` passes D+1 00:00 UTC.
    The dataset end trails the clock by a fixed delay (8 hours for AlgoGators'
    licence, measured 2026-10-12), so `D+1 00:00 - dataset end` is how long the
    wait would take. When that is longer than the remaining budget, return at
    once instead of sleeping for nothing.
    """
    deadline = now() + timedelta(seconds=max_wait_seconds)
    bar_end = datetime.combine(bar_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    while True:
        published_through, dataset_end = availability()
        if published_through >= bar_date:
            return published_through
        eta = bar_end - dataset_end
        if now() + eta > deadline:
            logger.error(
                "Databento has daily bars only through %s (data up to %s); bar %s needs "
                "about %s more, past this run's %.0f-minute wait. Pulling what is published.",
                published_through,
                dataset_end,
                bar_date,
                eta,
                max_wait_seconds / 60,
            )
            return published_through
        logger.info(
            "Waiting for Databento to publish bar %s (data up to %s)", bar_date, dataset_end
        )
        sleep(min(poll_seconds, max(1.0, eta.total_seconds())))


def _availability_of(orchestrator: Any) -> Callable[[], tuple[date, datetime]] | None:
    fetcher = getattr(orchestrator, "fetcher", None)
    check = getattr(fetcher, "daily_availability", None)
    return check if callable(check) else None


def run_close_pipeline(
    config: dict[str, Any], bar_date: date, days: int = BACKFILL_DAYS
) -> dict[str, Any]:
    """
    Pull bar date `bar_date` and re-pull the `days` before it. Returns a
    JSON-able summary: {"bar_date", "skipped", "expected", "missing",
    "published_through"} where expected/missing are D's storage symbols and
    published_through is the last daily bar Databento had published.

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
        return {
            "bar_date": bar_date.isoformat(),
            "skipped": True,
            "expected": [],
            "missing": [],
            "published_through": None,
        }

    def to_storage(symbol: str) -> str:
        return storage_symbol(config, symbol)

    window = backfill_days(bar_date, days)
    expected_by_day = {
        day: (expected_today if day == bar_date else expected_contracts(contracts, day))
        for day in window
    }
    all_symbols = sorted({to_storage(c["dataSymbol"]) for c in contracts})
    present = _present_bars(config, all_symbols, window[0], bar_date)
    backfill = {
        s: [d.isoformat() for d in ds if d != bar_date]
        for s, ds in find_missing_bars(expected_by_day, present, to_storage).items()
    }
    backfill = {s: ds for s, ds in backfill.items() if ds}
    if backfill:
        logger.warning("Backfilling missing bars before %s: %s", bar_date, backfill)

    orchestrator = Orchestrator(config=config)
    availability = _availability_of(orchestrator)
    published_through = bar_date
    if availability is not None:
        published_through = wait_for_bar_date(availability, bar_date, availability_wait_seconds())

    # Never ask for a day Databento has not published: the request fails with
    # 422 data_end_after_available_end.
    by_symbol = {c["dataSymbol"]: c for c in contracts}
    jobs = [
        (
            {"dataSymbol": symbol, "instrumentType": by_symbol[symbol]["instrumentType"]},
            first.isoformat(),
            min(last, published_through).isoformat(),
        )
        for symbol, (first, last) in repull_ranges(expected_by_day).items()
        if first <= published_through
    ]
    logger.info(
        "Bar date %s: %d expected contract(s), re-pulling %d contract(s) through %s",
        bar_date,
        len(expected_today),
        len(jobs),
        min(bar_date, published_through),
    )
    if jobs:
        asyncio.run(orchestrator.process(jobs, update_existing=True))

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
        "published_through": published_through.isoformat(),
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


def write_contract_readiness(
    config: dict[str, Any],
    bar_date: date,
    run_id: str | None,
    failed: bool = False,
    expected: int | None = None,
    loaded: int | None = None,
    missing: list[str] | None = None,
) -> dict[str, Any]:
    """
    Upsert ops.data_ready row for dataset 'futures_contract_ohlcv_1d'.
    """
    from data_ngin.infrastructure.repository.readiness_repository import ReadinessRepository

    exp_count = expected if expected is not None else 0
    load_count = loaded if loaded is not None else 0
    miss_list = missing if missing is not None else []

    if not miss_list and exp_count == load_count and exp_count > 0:
        status = "complete"
    elif failed:
        status = "failed"
    else:
        status = "partial"

    row = {
        "dataset": CONTRACT_DATASET,
        "bar_date": bar_date,
        "expected": exp_count,
        "loaded": load_count,
        "missing": miss_list,
        "status": status,
        "run_id": run_id,
        "finished_at": datetime.now(UTC),
    }

    repository = ReadinessRepository(config)
    try:
        repository.upsert_readiness(row)
    finally:
        repository.close()
    return row


def should_retry_for_missing(
    missing: list[str], try_number: int, max_tries: int, published: bool = True
) -> bool:
    """
    Whether a run that finished with D's bars still missing should fail so
    Airflow retries it: only while retries remain (the last try succeeds and
    the readiness row says 'partial'), and only when Databento had published
    bar D. If it had not, the run already waited as long as it usefully could
    (wait_for_bar_date), and a retry would only repeat that.
    Airflow's try_number starts at 1 and max_tries equals the task's retries.
    """
    return bool(missing) and published and try_number <= max_tries


def run_contract_close_pipeline(
    config: dict[str, Any],
    bar_date: date,
    roots: Iterable[str] | None = None,
) -> dict[str, Any]:
    """
    Fetch and ingest daily per-contract bars for the 40 roots for bar date `bar_date`.
    Evaluates loaded vs missing roots and returns a readiness summary.
    """
    import databento as db

    from data_ngin.domain.contract_definition import (
        extract_definitions_from_df,
        lookup_expiration,
    )
    from data_ngin.domain.contract_filter import is_outright_contract, parse_contract_symbol
    from data_ngin.infrastructure.repository.ohlcv_repository import OhlcvRepository

    target_roots = sorted(set(roots) if roots is not None else set(CONTRACT_ROOTS))

    contracts = load_contracts(config)
    expected_today = expected_contracts(contracts, bar_date)
    if not expected_today:
        logger.info("No contract traded on %s (UTC); skipping per-contract pull.", bar_date)
        return {
            "bar_date": bar_date.isoformat(),
            "skipped": True,
            "expected": len(target_roots),
            "loaded": 0,
            "missing": [],
            "status": "complete",
        }

    api_key = os.getenv("DATABENTO_API_KEY")
    repository = OhlcvRepository(config)
    repository.connect()

    if api_key:
        client = db.Historical(api_key)
        start_str = bar_date.isoformat()
        end_str = (bar_date + timedelta(days=1)).isoformat()
        for root in target_roots:
            try:
                parent_sym = f"{root}.FUT"
                data = client.timeseries.get_range(
                    dataset="GLBX.MDP3",
                    symbols=parent_sym,
                    schema="ohlcv-1d",
                    start=start_str,
                    end=end_str,
                    stype_in="parent",
                    stype_out="instrument_id",
                )
                df = data.to_df()
                if df.empty:
                    continue
                df = df.reset_index()
                # Expiration comes from the same day's definition records, matched on
                # (instrument_id, raw_symbol); never from a calendar rule.
                definitions = extract_definitions_from_df(
                    client.timeseries.get_range(
                        dataset="GLBX.MDP3",
                        symbols=parent_sym,
                        schema="definition",
                        start=start_str,
                        end=end_str,
                        stype_in="parent",
                        stype_out="instrument_id",
                    )
                    .to_df()
                    .reset_index()
                )
                records = []
                for row in df.itertuples(index=False):
                    raw_sym = getattr(row, "symbol", None)
                    if not raw_sym or not is_outright_contract(str(raw_sym)):
                        continue
                    try:
                        sym_root, _month_code, _year_str = parse_contract_symbol(str(raw_sym))
                    except ValueError:
                        continue
                    inst_id = str(row.instrument_id)
                    exp = lookup_expiration(definitions, inst_id, str(raw_sym), bar_date)
                    if exp is None:
                        logger.warning(
                            "No definition for %s (%s) on %s; stored without expiration",
                            raw_sym,
                            inst_id,
                            bar_date,
                        )
                    records.append(
                        {
                            "instrument_id": inst_id,
                            "date": bar_date,
                            "root": sym_root,
                            "raw_symbol": str(raw_sym),
                            "expiration": exp,
                            "open": float(row.open),
                            "high": float(row.high),
                            "low": float(row.low),
                            "close": float(row.close),
                            "volume": int(row.volume),
                        }
                    )
                if records:
                    repository.insert_contract_bars(records)
            except Exception as e:
                logger.warning(
                    "Failed to fetch contract bars for root %s on %s: %s", root, bar_date, e
                )

    try:
        present_roots = repository.get_contract_roots_for_date(bar_date, target_roots)
    finally:
        repository.close()

    missing = sorted(set(target_roots) - present_roots)
    loaded = len(present_roots)
    expected = len(target_roots)

    if not missing and loaded == expected:
        status = "complete"
    elif loaded > 0:
        status = "partial"
    else:
        status = "failed"

    return {
        "bar_date": bar_date.isoformat(),
        "skipped": False,
        "expected": expected,
        "loaded": loaded,
        "missing": missing,
        "status": status,
    }


def should_retry_contract_run(result: dict[str, Any], try_number: int, max_tries: int) -> bool:
    """
    Whether a per-contract run with missing roots should fail so Airflow retries it:
    only while retries remain.
    """
    return bool(result.get("missing")) and try_number <= max_tries

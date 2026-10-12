"""Task bodies for the Airflow DAGs in services/data-ngin/dags/.

Kept out of the DAG files, and free of Airflow imports, so they can be tested
without Airflow and so the DAG files stay cheap to parse: the dag-processor
re-parses every file continuously, and on a small shared host importing pandas,
databento and psycopg2 at parse time is what trips
AIRFLOW__CORE__DAGBAG_IMPORT_TIMEOUT. Heavy imports happen inside the functions,
at task run time.
"""

import logging
import os
from datetime import UTC, date, datetime
from typing import Any

PACKAGE_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")


def config_path(config_name: str) -> str:
    """
    Path of a pipeline config. Configs ship inside the data_ngin package, so
    the default works in any image that installs it. DATA_NGIN_CONFIG_DIR
    points at a different directory (e.g. a mounted override) without a rebuild.
    """
    return os.path.join(os.getenv("DATA_NGIN_CONFIG_DIR") or PACKAGE_CONFIG_DIR, config_name)


def run_pipeline(config_name: str, run_type: str = "scheduled") -> None:
    """
    Run one pipeline end to end: resolve symbols and the date range, then
    fetch, clean and insert every symbol. Raises if any symbol failed, after
    letting the rest finish (see Orchestrator.run).

    One task per pipeline rather than one per symbol: the Tiingo universe is
    570+ symbols, and a task process per symbol on the shared box would run for
    hours. In-process asyncio concurrency is also what TiingoFetcher's key
    rotation and request throttling are built around.
    """
    import asyncio

    from data_ngin.application.orchestrator import Orchestrator
    from data_ngin.utils.dynamic_loader import load_config

    path = config_path(config_name)
    logging.info(f"Running pipeline {config_name}, type={run_type}, config={path}")
    orchestrator = Orchestrator(config=load_config(path))
    asyncio.run(orchestrator.run())
    logging.info(f"Pipeline {config_name} completed successfully.")


def bar_date_from_context(context: Any) -> date:
    """
    The bar date a futures DAG run pulls (see close_pipeline.bar_date_for_run).

    The reference time is the run's data_interval_end: for a scheduled run of a
    cron DAG that is the scheduled 20:00 America/New_York tick under either
    Airflow timetable (CronTriggerTimetable: logical_date == data_interval_end ==
    the tick; CronDataIntervalTimetable: the interval ends at the tick), and it
    does not move when the task is retried later. A manual run can pin the date
    with conf {"bar_date": "YYYY-MM-DD"}; it must be a UTC day that has ended.
    """
    from data_ngin.application.close_pipeline import bar_date_for_run

    dag_run = context.get("dag_run")
    conf = (getattr(dag_run, "conf", None) or {}) if dag_run else {}
    latest_complete = bar_date_for_run(datetime.now(UTC))
    if conf.get("bar_date"):
        pinned = date.fromisoformat(str(conf["bar_date"]))
        if pinned > latest_complete:
            raise ValueError(
                f"conf bar_date {pinned} is not a completed UTC day yet "
                f"(latest is {latest_complete})"
            )
        return pinned
    reference = (
        context.get("data_interval_end")
        or context.get("logical_date")
        or getattr(dag_run, "run_after", None)
    )
    if reference is None:
        return latest_complete
    return bar_date_for_run(reference)


def run_id_from_context(context: Any) -> str | None:
    dag_run = context.get("dag_run")
    return context.get("run_id") or getattr(dag_run, "run_id", None)


def run_futures_close(config_name: str, bar_date: date) -> dict[str, Any]:
    """Pull bar date `bar_date` (plus gap backfill) for one futures pipeline."""
    from data_ngin.application.close_pipeline import run_close_pipeline
    from data_ngin.utils.dynamic_loader import load_config

    path = config_path(config_name)
    logging.info(f"Running after-close pipeline {config_name} for {bar_date}, config={path}")
    return run_close_pipeline(load_config(path), bar_date)


def write_futures_readiness(
    config_name: str, bar_date: date, run_id: str | None, failed: bool = False
) -> dict[str, Any] | None:
    """Upsert ops.data_ready for `bar_date` from the pipeline's target table."""
    from data_ngin.application.close_pipeline import write_readiness
    from data_ngin.utils.dynamic_loader import load_config

    return write_readiness(load_config(config_path(config_name)), bar_date, run_id, failed=failed)


def should_retry_run(result: dict[str, Any], try_number: int, max_tries: int) -> bool:
    """Whether a futures run's result should fail the try so Airflow retries it
    (see close_pipeline.should_retry_for_missing)."""
    from data_ngin.application.close_pipeline import should_retry_for_missing

    published_through = result.get("published_through")
    published = published_through is None or published_through >= result["bar_date"]
    return should_retry_for_missing(result["missing"], try_number, max_tries, published)

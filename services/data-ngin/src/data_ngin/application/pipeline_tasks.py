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

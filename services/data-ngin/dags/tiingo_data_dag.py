from datetime import datetime, timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task
from data_ngin.application import pipeline_tasks
from data_ngin.ops.dag_failure_notifier import notify_dag_failure

# Only light imports at module level: the dag-processor re-parses this file
# continuously. pandas/databento/psycopg2 load inside the tasks, at run time
# (see data_ngin.application.pipeline_tasks).

CONFIG_NAME = "config_tiingo.yaml"

local_tz = pendulum.timezone("America/New_York")

default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,  # no SMTP on the box; failures open a GitHub issue
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": notify_dag_failure,
}


@task
def run_tiingo_pipeline() -> None:
    dag_run = get_current_context().get("dag_run")
    conf = (dag_run.conf if dag_run else None) or {}
    pipeline_tasks.run_pipeline(CONFIG_NAME, run_type=conf.get("run_type", "scheduled"))


@task(trigger_rule="all_done")
def staleness_check() -> None:
    # all_done: runs (and only warns) even when the pipeline task failed.
    pipeline_tasks.check_staleness(CONFIG_NAME)


@dag(
    dag_id="tiingo_data_dag",
    default_args=default_args,
    description="Daily Tiingo equity OHLCV ingestion into new_algo_data",
    schedule="15 7 * * 1-5",
    # Weekdays 07:15 ET, staggered after the Databento runs to ease memory
    # pressure on the t2.micro.
    start_date=datetime(2024, 12, 1, tzinfo=local_tz),
    catchup=False,
    tags=["tiingo", "equity", "data_pipeline"],
    max_active_runs=1,
)
def tiingo_data_dag():
    run_tiingo_pipeline() >> staleness_check()


tiingo_data_dag()

from datetime import datetime, timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task
from data_ngin.application import pipeline_tasks
from data_ngin.ops.dag_failure_notifier import notify_dag_failure

# Only light imports at module level: the dag-processor re-parses this file
# continuously. pandas/databento/psycopg2 load inside the tasks, at run time
# (see data_ngin.application.pipeline_tasks).

CONFIG_NAME = "new_config.yaml"

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
def run_pipeline() -> None:
    dag_run = get_current_context().get("dag_run")
    conf = (dag_run.conf if dag_run else None) or {}
    pipeline_tasks.run_pipeline(CONFIG_NAME, run_type=conf.get("run_type", "scheduled"))


@task(trigger_rule="all_done")
def staleness_check() -> None:
    # all_done: runs (and only warns) even when the pipeline task failed.
    pipeline_tasks.check_staleness(CONFIG_NAME)


@dag(
    dag_id="new_data_pipeline_dag",
    default_args=default_args,
    description="Daily Databento futures ingestion (full contract list) into new_algo_data",
    schedule="5 7 * * *",  # 07:05 ET daily, after data_pipeline_dag
    start_date=datetime(2024, 12, 1, tzinfo=local_tz),
    catchup=False,
    tags=["new_data_pipeline"],
    max_active_runs=1,
)
def new_data_pipeline_dag():
    run_pipeline() >> staleness_check()


new_data_pipeline_dag()

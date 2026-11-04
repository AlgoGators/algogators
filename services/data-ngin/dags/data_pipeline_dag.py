from datetime import datetime, timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task
from data_ngin.application import pipeline_tasks
from data_ngin.ops.dag_failure_notifier import notify_dag_failure

# Only light imports at module level: the dag-processor re-parses this file
# continuously. pandas/databento/psycopg2 load inside the tasks, at run time
# (see data_ngin.application.pipeline_tasks).

CONFIG_NAME = "config.yaml"

local_tz = pendulum.timezone("America/New_York")
RETRIES = 3

default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,  # no SMTP on the box; failures open a GitHub issue
    "email_on_retry": False,
    # Retries cover fetch/DB errors and bars missing after Databento said D
    # was published; waiting for publication happens inside the task.
    "retries": RETRIES,
    "retry_delay": timedelta(minutes=15),
    "on_failure_callback": notify_dag_failure,
}


@task
def run_pipeline() -> dict:
    context = get_current_context()
    bar_date = pipeline_tasks.bar_date_from_context(context)
    result = pipeline_tasks.run_futures_close(CONFIG_NAME, bar_date)
    ti = context["ti"]
    if pipeline_tasks.should_retry_run(result, ti.try_number, getattr(ti, "max_tries", RETRIES)):
        raise RuntimeError(f"{bar_date}: bars still missing for {result['missing']}; retrying")
    return result


@dag(
    dag_id="data_pipeline_dag",
    default_args=default_args,
    description="Retired: the old algo_data futures ingest. Manual runs only (#185).",
    # No schedule since #185: production reads new_algo_data, which
    # new_data_pipeline_dag feeds. The DAG stays loadable for a manual run or a
    # rollback until the old database is retired (#202).
    schedule=None,
    start_date=datetime(2024, 12, 1, tzinfo=local_tz),
    catchup=False,
    tags=["data_pipeline"],
    max_active_runs=1,
)
def data_pipeline_dag():
    # The pipeline task is the only task, so a failed run_* is a failed DAG
    # run (and a GitHub issue via on_failure_callback). A trailing
    # always-run task (a trigger rule that ignores upstream failures) would
    # turn that into a green run.
    run_pipeline()


data_pipeline_dag()

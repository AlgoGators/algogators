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
    # 20:00 ET is 00:00 UTC in EDT, right as Databento's UTC day closes: a
    # retry 15 minutes later picks up bars that were not published yet.
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
    if pipeline_tasks.should_retry_for_missing(
        result["missing"], ti.try_number, getattr(ti, "max_tries", RETRIES)
    ):
        raise RuntimeError(f"{bar_date}: bars still missing for {result['missing']}; retrying")
    return result


@dag(
    dag_id="data_pipeline_dag",
    default_args=default_args,
    description="After-close Databento futures ingestion into algo_data",
    # Interim fixed 20:00 ET schedule, every day: futures trade Sunday evening
    # through Friday, and the trading calendar decides per contract whether
    # UTC day D had a bar (Saturday pulls nothing). Event/request-driven
    # orchestration is tracked in https://github.com/AlgoGators/trade-ngin/issues/191
    # (transport: https://github.com/AlgoGators/trade-ngin/issues/192).
    schedule="0 20 * * *",
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

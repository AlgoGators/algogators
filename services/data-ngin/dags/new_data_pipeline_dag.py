from datetime import datetime, timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task
from data_ngin.application import pipeline_tasks
from data_ngin.ops.dag_failure_notifier import notify_dag_failure
from data_ngin.ops.readiness_failure import make_readiness_failure_callback

# Only light imports at module level: the dag-processor re-parses this file
# continuously. pandas/databento/psycopg2 load inside the tasks, at run time
# (see data_ngin.application.pipeline_tasks).

CONFIG_NAME = "new_config.yaml"

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
    # A run that fails for good still records ops.data_ready status 'failed',
    # so consumers waiting on bar date D are not left waiting.
    "on_failure_callback": [notify_dag_failure, make_readiness_failure_callback(CONFIG_NAME)],
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


@task
def write_readiness(result: dict) -> None:
    # ops.data_ready (new_algo_data) is created by a trade-ngin migration;
    # data-ngin only upserts into it. Nothing is written for a bar date on
    # which no contract traded (result["skipped"]).
    if result["skipped"]:
        return
    context = get_current_context()
    pipeline_tasks.write_futures_readiness(
        CONFIG_NAME,
        pipeline_tasks.bar_date_from_context(context),
        pipeline_tasks.run_id_from_context(context),
    )


@dag(
    dag_id="new_data_pipeline_dag",
    default_args=default_args,
    description="After-close Databento futures ingestion (full contract list) into new_algo_data",
    # Interim fixed 20:00 ET schedule, every day: futures trade Sunday evening
    # through Friday, and the trading calendar decides per contract whether
    # UTC day D had a bar (Saturday pulls nothing). Event/request-driven
    # orchestration is tracked in https://github.com/AlgoGators/trade-ngin/issues/191
    # (transport: https://github.com/AlgoGators/trade-ngin/issues/192).
    schedule="0 20 * * *",
    start_date=datetime(2024, 12, 1, tzinfo=local_tz),
    catchup=False,
    tags=["new_data_pipeline"],
    max_active_runs=1,
)
def new_data_pipeline_dag():
    # write_readiness is the leaf and runs only when the pull succeeded, so a
    # failed pull is a failed DAG run (and a GitHub issue plus a 'failed'
    # readiness row via on_failure_callback). A trailing always-run task (a
    # trigger rule that ignores upstream failures) would turn that into a
    # green run.
    write_readiness(run_pipeline())


new_data_pipeline_dag()

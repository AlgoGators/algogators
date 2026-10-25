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
    # Retries cover fetch/DB errors and bars missing after Databento said D
    # was published; waiting for publication happens inside the task.
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
    if pipeline_tasks.should_retry_run(result, ti.try_number, getattr(ti, "max_tries", RETRIES)):
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


@task
def run_contract_pipeline() -> dict:
    context = get_current_context()
    bar_date = pipeline_tasks.bar_date_from_context(context)
    result = pipeline_tasks.run_contract_close(CONFIG_NAME, bar_date)
    ti = context["ti"]
    if pipeline_tasks.should_retry_contract_run(
        result, ti.try_number, getattr(ti, "max_tries", RETRIES)
    ):
        raise RuntimeError(
            f"{bar_date}: contract bars still missing for {result['missing']}; retrying"
        )
    if pipeline_tasks.contract_run_failed(result):
        # The failure callback writes the 'failed' row with the counts from the table.
        raise RuntimeError(f"{bar_date}: no contract bars loaded for {result['missing']}")
    return result


@task
def write_contract_readiness(result: dict) -> None:
    """
    Upsert ops.data_ready for dataset 'futures_contract_ohlcv_1d' (AlgoGators/algogators#37).
    """
    if result["skipped"]:
        return
    context = get_current_context()
    bar_date = pipeline_tasks.bar_date_from_context(context)
    run_id = pipeline_tasks.run_id_from_context(context)
    expected_count = result.get("expected", 40)
    missing_list = result.get("missing", [])
    loaded_count = result.get("loaded", 0)
    pipeline_tasks.write_contract_futures_readiness(
        CONFIG_NAME,
        bar_date,
        run_id,
        expected=expected_count,
        loaded=loaded_count,
        missing=missing_list,
        failed=(result.get("status") == "failed"),
    )


@dag(
    dag_id="new_data_pipeline_dag",
    default_args=default_args,
    description="Daily Databento futures ingestion (full contract list) into new_algo_data",
    # Interim fixed 04:30 ET schedule, every day, pulling the UTC day that
    # ended before the run (yesterday's New York date). Fixed at 04:30 because
    # our Databento historical plan lags real time by ~8 hours: bar D is
    # published around 08:00 UTC on D+1 (03:00/04:00 ET). The trading calendar
    # decides per contract whether UTC day D had a bar. Event/request-driven
    # orchestration is tracked in https://github.com/AlgoGators/trade-ngin/issues/191
    # (transport: https://github.com/AlgoGators/trade-ngin/issues/192).
    schedule="30 4 * * *",
    start_date=datetime(2024, 12, 1, tzinfo=local_tz),
    catchup=False,
    tags=["new_data_pipeline"],
    max_active_runs=1,
)
def new_data_pipeline_dag():
    # Continuous pull and readiness
    pipeline_result = run_pipeline()
    write_readiness(pipeline_result)

    # Per-contract daily pull runs after continuous close
    contract_result = run_contract_pipeline()
    pipeline_result >> contract_result
    write_contract_readiness(contract_result)


new_data_pipeline_dag()

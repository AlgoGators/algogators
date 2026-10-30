"""Airflow on_failure_callback that records a failed run in ops.data_ready.

When new_data_pipeline_dag's pull (or its readiness task) fails for good, the
readiness task never writes D's row, and consumers waiting on it would wait
forever. This callback writes it instead: status 'failed' with the bars still
missing, unless every expected bar is in fact present (then 'complete').

It marks 'failed' only the rows of the pull that failed. The task order is the
continuous pull, the per-contract pull, the continuous fill, then both readiness
tasks, so a task that fails after the continuous pull succeeded leaves that pull's
readiness task unrun: the callback then writes the continuous row from what the
table holds (complete or partial, never 'failed'), so consumers are not left
waiting. The continuous pull failing writes both rows as failed, since nothing
after it runs. A failed fill writes both rows from the tables (both pulls
succeeded); the failure notifier still opens the issue.

Like dag_failure_notifier it never raises: a second exception would mask the
task's real failure.
"""

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# The DAG's tasks, by what their failure means for the two readiness rows.
CONTINUOUS_ONLY_TASKS = frozenset(
    {"write_readiness"}
)  # continuous row failed; contract row left alone
CONTRACT_ONLY_TASKS = frozenset({"write_contract_readiness"})  # contract row failed only
CONTRACT_PULL_TASKS = frozenset(
    {"run_contract_pipeline"}
)  # contract row failed; continuous row written
AFTER_PULLS_TASKS = frozenset(
    {"fill_continuous_holes"}
)  # both rows written from the tables, not failed


def make_readiness_failure_callback(config_name: str) -> Callable[[Any], None]:
    def mark_readiness_failed(context: Any) -> None:
        try:
            from data_ngin.application import pipeline_tasks

            bar_date = pipeline_tasks.bar_date_from_context(context)
            run_id = pipeline_tasks.run_id_from_context(context)
            task_id = getattr(context.get("task_instance"), "task_id", None)
            # The continuous pull succeeded for these tasks: its row is written, not failed.
            continuous_ok = task_id in CONTRACT_PULL_TASKS | AFTER_PULLS_TASKS
            contract_ok = task_id in AFTER_PULLS_TASKS
            if task_id not in CONTRACT_ONLY_TASKS:
                pipeline_tasks.write_futures_readiness(
                    config_name, bar_date, run_id, failed=not continuous_ok
                )
            if task_id not in CONTINUOUS_ONLY_TASKS:
                pipeline_tasks.write_contract_futures_readiness(
                    config_name, bar_date, run_id, failed=not contract_ok
                )
        except Exception:
            logger.exception("Could not write the failed readiness row (non-fatal)")

    return mark_readiness_failed

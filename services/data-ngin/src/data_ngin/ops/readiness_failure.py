"""Airflow on_failure_callback that records a failed run in ops.data_ready.

When new_data_pipeline_dag's pull (or its readiness task) fails for good, the
readiness task never writes D's row, and consumers waiting on it would wait
forever. This callback writes it instead: status 'failed' with the bars still
missing, unless every expected bar is in fact present (then 'complete').

It writes only the rows of the pull that failed. A failed per-contract task must
not rewrite the continuous row: a continuous pull that loaded all but one symbol
('partial') would become 'failed', and the desk would alert every symbol as
missing. The continuous pull failing writes both rows, since the per-contract
pull runs after it and so never runs.

Like dag_failure_notifier it never raises: a second exception would mask the
task's real failure.
"""

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# The DAG's tasks whose failure leaves only one dataset's row unwritten.
CONTINUOUS_ONLY_TASKS = frozenset({"write_readiness"})
CONTRACT_ONLY_TASKS = frozenset({"run_contract_pipeline", "write_contract_readiness"})


def make_readiness_failure_callback(config_name: str) -> Callable[[Any], None]:
    def mark_readiness_failed(context: Any) -> None:
        try:
            from data_ngin.application import pipeline_tasks

            bar_date = pipeline_tasks.bar_date_from_context(context)
            run_id = pipeline_tasks.run_id_from_context(context)
            task_id = getattr(context.get("task_instance"), "task_id", None)
            if task_id not in CONTRACT_ONLY_TASKS:
                pipeline_tasks.write_futures_readiness(config_name, bar_date, run_id, failed=True)
            if task_id not in CONTINUOUS_ONLY_TASKS:
                pipeline_tasks.write_contract_futures_readiness(
                    config_name, bar_date, run_id, failed=True
                )
        except Exception:
            logger.exception("Could not write the failed readiness row (non-fatal)")

    return mark_readiness_failed

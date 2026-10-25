"""Airflow on_failure_callback that records a failed run in ops.data_ready.

When new_data_pipeline_dag's pull (or its readiness task) fails for good, the
readiness task never writes D's row, and consumers waiting on it would wait
forever. This callback writes it instead: status 'failed' with the bars still
missing, unless every expected bar is in fact present (then 'complete').

Like dag_failure_notifier it never raises: a second exception would mask the
task's real failure.
"""

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def make_readiness_failure_callback(config_name: str) -> Callable[[Any], None]:
    def mark_readiness_failed(context: Any) -> None:
        try:
            from data_ngin.application import pipeline_tasks

            bar_date = pipeline_tasks.bar_date_from_context(context)
            run_id = pipeline_tasks.run_id_from_context(context)
            pipeline_tasks.write_futures_readiness(config_name, bar_date, run_id, failed=True)
            pipeline_tasks.write_contract_futures_readiness(
                config_name, bar_date, run_id, failed=True
            )
        except Exception:
            logger.exception("Could not write the failed readiness row (non-fatal)")

    return mark_readiness_failed

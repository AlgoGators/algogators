"""Airflow on_failure_callback that files or updates a GitHub issue per failing DAG.

See github_issues for configuration. A missing token, or a GitHub API error, is
logged and swallowed: the callback must never raise a second exception that
masks the task's real failure.
"""

import logging

from data_ngin.ops.github_issues import file_or_update_issue, github_settings

logger = logging.getLogger(__name__)

ISSUE_LABEL = "dag-failure"


def notify_dag_failure(context) -> None:
    """Airflow on_failure_callback signature: takes the task-instance context dict."""
    dag = context.get("dag")
    dag_id = dag.dag_id if dag else "<unknown>"

    token, repo = github_settings()
    if not token:
        logger.warning(
            "GITHUB_TOKEN not set; skipping DAG-failure GitHub issue for dag_id=%s", dag_id
        )
        return

    task_instance = context.get("task_instance")
    # logical_date is the Airflow 3 name; execution_date is the Airflow 2 name.
    run = context.get("logical_date") or context.get("execution_date")
    body = (
        f"**DAG:** `{dag_id}`\n"
        f"**Task:** `{getattr(task_instance, 'task_id', '<unknown>')}`\n"
        f"**Run:** `{run}`\n"
        f"**Exception:**\n```\n{context.get('exception')}\n```\n"
        f"**Logs:** {getattr(task_instance, 'log_url', '<unavailable>')}\n"
    )

    try:
        number = file_or_update_issue(
            repo=repo,
            token=token,
            label=ISSUE_LABEL,
            title=f"[{ISSUE_LABEL}] {dag_id} failed",
            title_query=dag_id,
            body=body,
        )
        logger.info("Recorded %s failure on issue #%s", dag_id, number)
    except Exception:
        logger.exception("Failed to file/update GitHub issue for %s failure (non-fatal)", dag_id)

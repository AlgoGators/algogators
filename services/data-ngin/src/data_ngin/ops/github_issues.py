"""File-or-update a labelled GitHub issue: the alert channel for data-ngin.

The deployment has no SMTP, so Airflow's email_on_failure goes nowhere. DAG
failures (dag_failure_notifier) and stale data (check_data_freshness) open an
issue instead. A repeat alert comments on the open issue with the same label
and title rather than opening a duplicate.

Configuration comes from the environment:
  GITHUB_TOKEN  a token with Issues read/write on GITHUB_REPO. Unset means
                alerts are skipped with a warning.
  GITHUB_REPO   "owner/repo". Defaults to AlgoGators/algogators.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

DEFAULT_REPO = "AlgoGators/algogators"
_API = "https://api.github.com"
_TIMEOUT_SECONDS = 10


def github_settings() -> tuple[str | None, str]:
    """(token, repo) from the environment; token is None when unset or blank."""
    token = os.environ.get("GITHUB_TOKEN", "").strip() or None
    repo = os.environ.get("GITHUB_REPO", "").strip() or DEFAULT_REPO
    return token, repo


def file_or_update_issue(
    repo: str, token: str, label: str, title: str, title_query: str, body: str
) -> int:
    """
    Comment on the open issue labelled `label` whose title contains
    `title_query`, or open a new one. Returns the issue number.

    Raises:
        requests.HTTPError: if the GitHub API rejects a call.
    """
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    query = f'repo:{repo} is:issue is:open label:{label} in:title "{title_query}"'
    resp = requests.get(
        f"{_API}/search/issues", params={"q": query}, headers=headers, timeout=_TIMEOUT_SECONDS
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])

    if items:
        number = items[0]["number"]
        resp = requests.post(
            f"{_API}/repos/{repo}/issues/{number}/comments",
            json={"body": body},
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return number

    resp = requests.post(
        f"{_API}/repos/{repo}/issues",
        json={"title": title, "body": body, "labels": [label]},
        headers=headers,
        timeout=_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()["number"]

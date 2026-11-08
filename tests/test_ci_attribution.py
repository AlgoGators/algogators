"""Tests for platform/ci/check_attribution.py, the no-AI-attribution check (#179).

The attribution samples are the forms found in this repository's PR descriptions
and in trade-ngin's history (``Co-Authored-By: Claude ...``, ``Claude-Session:``,
the claude.ai/code session link, the Claude Code footer), plus a co-author line
for each tool #179 names. Addresses use reserved example domains: the
repository is public.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "platform" / "ci"))

from check_attribution import find, main  # noqa: E402

AI_COAUTHORS = [
    "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.example>",
    "Co-authored-by: Claude Opus 5.5 <noreply@anthropic.example>",
    "Co-Authored-By: Claude <noreply@example.com>",
    "Co-authored-by: Assistant <noreply@anthropic.example>",
    "Co-authored-by: Copilot <175728472+Copilot@users.noreply.example.com>",
    "Co-authored-by: copilot-swe-agent[bot] <198982749+Copilot@users.noreply.example.com>",
    "Co-authored-by: OpenAI Codex <codex@example.com>",
    "Co-authored-by: ChatGPT <noreply@example.com>",
    "Co-authored-by: Cursor Agent <cursoragent@example.com>",
    "Co-authored-by: gemini-code-assist[bot] <bot@example.com>",
    "Co-authored-by: devin-ai-integration[bot] <bot@example.com>",
    "  CO-AUTHORED-BY : claude_code <noreply@example.com>",
]

OTHER_ATTRIBUTION = [
    "Claude-Session: https://claude.ai/code/session_00example",
    "claude-session: 00example",
    "https://claude.ai/code/session_00example",
    "Details: [the session](https://claude.ai/code/session_00example)",
    "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    "_Generated with Cursor_",
    "> Generated with ChatGPT",
]

HUMAN_COAUTHORS = [
    "Co-authored-by: Jane Doe <jane.doe@example.com>",
    "Co-authored-by: jdoe <12345+jdoe@users.noreply.example.com>",
    "Co-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.example.com>",
    "Co-authored-by: Devinder Kaur <dkaur@example.com>",
    "Co-authored-by: Claudette Moreau <cmoreau@example.com>",
]

MENTIONS = [
    # The rule in prose, as AGENTS.md and the PR template state it.
    'No AI attribution: no AI `Co-authored-by`, no `Claude-Session:`, no `claude.ai/code` link, no "Generated with" footer.',
    # How real PR descriptions and commit messages here use the same words.
    "the other authors become `Co-authored-by` lines, and the merge commit credits them",
    "- `uv.lock` regenerated with both dependencies removed",
    "The committed baselines were generated with the biased update in them.",
    "- Baseline regenerated with new benchmark names",
    # A trailer or a footer is a line of its own; mid-sentence it is a mention.
    "Older commits still end in Co-authored-by: Claude lines; rewriting them is out of scope.",
    "Nothing in this diff was generated with Copilot.",
    # Generated, but not by an AI tool.
    "Generated with `just contracts`.",
]


@pytest.fixture(autouse=True)
def _plain_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Print findings as plain lines, wherever the tests run.

    CI sets GITHUB_ACTIONS=true, which turns findings into ``::error::`` workflow
    commands. A failing test prints what it captured, so the runner would show its
    sample findings as annotations on whichever PR ran the tests.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.mark.parametrize("line", [*AI_COAUTHORS, *OTHER_ATTRIBUTION])
def test_attribution_is_found_on_its_line(line: str) -> None:
    message = f"fix: a change\n\nWhy it was needed.\n\n{line}\n"
    [finding] = find(message, "commit 1a2b3c4d")
    assert (finding.where, finding.line, finding.text) == ("commit 1a2b3c4d", 5, line.strip())


@pytest.mark.parametrize("line", [*HUMAN_COAUTHORS, *MENTIONS])
def test_human_coauthors_and_mentions_pass(line: str) -> None:
    assert find(f"fix: a change\n\n{line}\n", "commit 1a2b3c4d") == []


def test_a_session_line_is_one_finding_naming_both_rules() -> None:
    [finding] = find("Claude-Session: https://claude.ai/code/session_00example", "commit 1a2b3c4d")
    assert finding.what == "a Claude-Session line, a claude.ai/code link"


def test_the_pr_template_passes() -> None:
    """Every description starts as the template, and the template states the rule."""
    template = (REPO_ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert "Co-authored-by" in template
    assert "Generated with" in template
    assert find(template, "the PR template") == []


def test_the_workflow_checks_every_pull_request() -> None:
    """A path or branch filter would skip pull requests whose text still needs checking."""
    workflow = REPO_ROOT / ".github" / "workflows" / "meta-attribution.yml"
    text = workflow.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    on = data.get("on") or data.get(True)  # PyYAML reads a bare `on:` key as True
    assert on == {"pull_request": {"types": ["opened", "edited", "synchronize", "reopened"]}}
    assert "platform/ci/check_attribution.py" in text


# ---------------------------------------------------------------------------
# The command line, fed as meta-attribution.yml feeds it: the event payload and
# one JSON line per commit.
# ---------------------------------------------------------------------------

# A description edited in the browser is stored with CRLF line endings.
CLEAN_BODY = "## What and why\r\n\r\nAdds the attribution check.\r\n"
CLEAN_COMMIT = "ci: add the attribution check\n\nIt runs on every pull request.\n"
AI_TRAILER = "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.example>"
HUMAN_TRAILER = "Co-authored-by: Jane Doe <jane.doe@example.com>"


def check(tmp_path: Path, body: str | None, *messages: str, listed: int | None = None) -> int:
    """Run the check on one pull request. Commit n's sha is n repeated, e.g. 2222..."""
    event = tmp_path / "event.json"
    pull = {"body": body, "commits": len(messages) if listed is None else listed}
    event.write_text(json.dumps({"pull_request": pull}), encoding="utf-8")
    commits = tmp_path / "commits.jsonl"
    lines = [json.dumps({"sha": str(n) * 40, "message": m}) for n, m in enumerate(messages, 1)]
    commits.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return main(["--event", str(event), "--commits", str(commits)])


def test_a_description_with_an_ai_coauthor_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(tmp_path, CLEAN_BODY + AI_TRAILER + "\r\n", CLEAN_COMMIT) == 1
    out = capsys.readouterr().out
    assert "the PR description, line 4: a Co-authored-by line naming an AI tool" in out


def test_a_commit_with_an_ai_coauthor_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(tmp_path, CLEAN_BODY, CLEAN_COMMIT, CLEAN_COMMIT + "\n" + AI_TRAILER) == 1
    out = capsys.readouterr().out
    assert "commit 22222222, line 5: a Co-authored-by line naming an AI tool" in out
    assert "commit 11111111" not in out


def test_a_clean_description_and_commits_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(tmp_path, CLEAN_BODY, CLEAN_COMMIT, CLEAN_COMMIT) == 0
    out = capsys.readouterr().out
    assert out == "No AI attribution in the PR description, commit 11111111, commit 22222222.\n"


def test_a_human_coauthor_passes(tmp_path: Path) -> None:
    body = CLEAN_BODY + HUMAN_TRAILER + "\r\n"
    assert check(tmp_path, body, CLEAN_COMMIT + "\n" + HUMAN_TRAILER) == 0


def test_an_empty_description_passes(tmp_path: Path) -> None:
    assert check(tmp_path, None, CLEAN_COMMIT) == 0


def test_commits_github_did_not_list_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(tmp_path, CLEAN_BODY, CLEAN_COMMIT, listed=300) == 1
    assert "GitHub listed 1 of this PR's 300 commits" in capsys.readouterr().out


def test_in_actions_each_finding_is_an_error_annotation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    check(tmp_path, CLEAN_BODY + AI_TRAILER + "\r\n", CLEAN_COMMIT)
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("::error::the PR description, line 4: a Co-authored-by line")


def test_standard_input_is_checked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    piped = io.TextIOWrapper(io.BytesIO((CLEAN_COMMIT + "\n" + AI_TRAILER).encode("utf-8")))
    monkeypatch.setattr(sys, "stdin", piped)
    assert main(["-"]) == 1
    assert "standard input, line 5" in capsys.readouterr().out

"""Fail a pull request whose description or commit messages carry AI attribution.

The rule (#179, decision 0001): no ``Co-authored-by`` line naming an AI tool, no
``Claude-Session:`` line, no claude.ai/code link, no "Generated with" footer.
Human co-authors stay.

Each pattern matches the attribution's form, not a mention of the rule: the PR
template states all four in prose, and every description starts as the
template. So a trailer must start its line, a link needs its ``https://``, and a
"Generated with" line must start with those words and name an AI tool.

``meta-attribution.yml`` runs it on every pull request::

    gh api --paginate "repos/$REPO/pulls/$PR/commits" --jq '.[] | {sha, message: .commit.message} | tojson' > commits.jsonl
    python3 platform/ci/check_attribution.py --event "$GITHUB_EVENT_PATH" --commits commits.jsonl

Check a branch before pushing it, or any text file, the same way::

    git log --format=%B origin/main..HEAD | python3 platform/ci/check_attribution.py -

Standard library only, so CI runs it without installing anything. Exits 1 on a
match, and when GitHub listed fewer commits than the pull request has.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The AI tools #179 names. Each matches as a whole word, so ``Devinder`` and
#: ``Claudette`` pass while ``devin-ai-integration[bot]`` and ``+Copilot@`` match.
#: A person who shares one of these names (a human Claude or Devin) is flagged too.
AI_TOOLS = ("claude", "anthropic", "copilot", "codex", "chatgpt", "cursor", "gemini", "devin")

_TOOL = rf"(?<![a-z0-9])(?:{'|'.join(AI_TOOLS)})(?![a-z0-9])"

#: What each rule finds, and its pattern, applied to one line at a time.
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "a Co-authored-by line naming an AI tool",
        re.compile(rf"^[ \t]*co-authored-by[ \t]*:.*{_TOOL}", re.IGNORECASE),
    ),
    ("a Claude-Session line", re.compile(r"^[ \t]*claude-session[ \t]*:", re.IGNORECASE)),
    ("a claude.ai/code link", re.compile(r"https?://(?:www\.)?claude\.ai/code\b", re.IGNORECASE)),
    (
        'a "Generated with" footer naming an AI tool',
        re.compile(rf"^[^a-z0-9]*generated with\b.*{_TOOL}", re.IGNORECASE),
    ),
)


@dataclass(frozen=True)
class Finding:
    where: str  # "the PR description", "commit 1a2b3c4d"
    line: int  # 1-based
    what: str
    text: str

    def __str__(self) -> str:
        shown = self.text if len(self.text) <= 100 else self.text[:97] + "..."
        return f"{self.where}, line {self.line}: {self.what}: {shown}"


def find(text: str, where: str) -> list[Finding]:
    """One finding per line of ``text`` that carries AI attribution."""
    found: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        what = [name for name, pattern in RULES if pattern.search(line)]
        if what:
            found.append(Finding(where, number, ", ".join(what), line.strip()))
    return found


def read_commits(path: Path) -> list[tuple[str, str]]:
    """``(label, message)`` for each JSON line ``{"sha": ..., "message": ...}``."""
    commits: list[tuple[str, str]] = []
    # split("\n"), not splitlines(): a message may hold a raw U+0085 or U+2028,
    # which JSON does not escape and splitlines() would split on.
    for raw in path.read_text(encoding="utf-8").split("\n"):
        if raw.strip():
            commit = json.loads(raw)
            commits.append((f"commit {commit['sha'][:8]}", commit["message"] or ""))
    return commits


def report(message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # A workflow command, so the error shows on the pull request; escaped as GitHub requires.
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::{escaped}")
    else:
        print(message)


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        # A Windows pipe (Git Bash) encodes with the code page; an emoji footer would crash it.
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("files", nargs="*", help="text files to check, '-' for standard input")
    parser.add_argument(
        "--event", type=Path, help="a pull_request event payload; its description is checked"
    )
    parser.add_argument(
        "--commits", type=Path, help='the PR\'s commits, one {"sha", "message"} JSON per line'
    )
    args = parser.parse_args(argv)
    if not (args.event or args.commits or args.files):
        parser.error("nothing to check: give --event, --commits or a file")

    texts: list[tuple[str, str]] = []
    problems: list[str] = []
    pull: dict[str, Any] = {}
    if args.event:
        pull = json.loads(args.event.read_text(encoding="utf-8"))["pull_request"]
        texts.append(("the PR description", pull.get("body") or ""))
    if args.commits:
        commits = read_commits(args.commits)
        texts += commits
        listed = pull.get("commits")
        if isinstance(listed, int) and len(commits) < listed:
            problems.append(
                f"GitHub listed {len(commits)} of this PR's {listed} commits (it lists at most "
                "250), so the rest went unchecked. Check them locally: git log --format=%B "
                "<base>..<head> | python3 platform/ci/check_attribution.py -"
            )
    for name in args.files:
        if name == "-":
            stdin = sys.stdin.buffer.read().decode("utf-8", errors="replace")
            texts.append(("standard input", stdin))
        else:
            texts.append((name, Path(name).read_text(encoding="utf-8")))

    findings = [finding for where, text in texts for finding in find(text, where)]
    for message in [*map(str, findings), *problems]:
        report(message)
    if findings:
        print(
            "\nRemove the AI attribution: edit the PR description, and rewrite the commits "
            "(git rebase -i, git commit --amend), then git push --force-with-lease. "
            "Human Co-authored-by lines are fine. The rule: AlgoGators/algogators#179."
        )
    if findings or problems:
        return 1
    print(f"No AI attribution in {', '.join(where for where, _ in texts)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

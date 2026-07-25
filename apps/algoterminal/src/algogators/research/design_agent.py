"""Design stage: turn a plain-English instruction into an edit of a specific
strategy.py, by invoking the `claude` CLI (Claude Code) headlessly.

This runs `claude -p` scoped to a single research record's directory, with
tool access restricted to reading/editing files (no Bash, no network) so the
agent can only touch that one strategy file. It never runs inside the
AlgoGators repo itself — `cwd` is the research record's own directory,
which doesn't contain the rest of the codebase.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from algogators.research.storage import ResearchRecord

REQUIRED_FUNCTIONS = ("generate_signals", "size_positions", "apply_risk_rules")

_TIMEOUT_SECONDS = 300


def build_prompt(record: ResearchRecord, instruction: str) -> str:
    hyp = record.load_hypothesis()
    required = "\n".join(f"  - {name}(...)" for name in REQUIRED_FUNCTIONS)
    return (
        f"You are editing exactly one file: {record.strategy_path}\n\n"
        "This file is a trading strategy for the AlgoGators research framework. "
        "The backtest engine imports it and looks up these exact top-level function "
        "names via hasattr — renaming, removing, or changing the signature of any of "
        f"them breaks the strategy:\n{required}\n\n"
        "Do not rename these functions, change their signatures, or remove them. "
        "Do not add a `__main__` block or CLI wrapper. "
        f"Do not create, edit, or delete any file other than {record.strategy_path}.\n\n"
        "Strategy context:\n"
        f"- Title: {hyp.title}\n"
        f"- Thesis: {hyp.thesis}\n"
        f"- Universe: {hyp.universe} ({', '.join(hyp.symbols)})\n"
        f"- Expected edge: {hyp.expected_edge}\n\n"
        f"User's instruction for what this strategy should do:\n{instruction}\n\n"
        f"Edit {record.strategy_path} now to implement this."
    )


def run_claude_edit(record: ResearchRecord, instruction: str) -> tuple[bool, str]:
    """Invoke `claude -p` to edit `record.strategy_path`. Returns (ok, message)."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return False, "`claude` CLI not found on PATH — install Claude Code first."

    prompt = build_prompt(record, instruction)

    try:
        result = subprocess.run(
            [
                claude_bin,
                "-p",
                prompt,
                "--allowedTools",
                "Read,Edit",
                "--permission-mode",
                "acceptEdits",
                "--output-format",
                "json",
                "--max-turns",
                "8",
            ],
            cwd=str(record.path),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"claude -p timed out after {_TIMEOUT_SECONDS}s."
    except OSError as e:
        return False, f"Failed to launch claude: {e}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:2000]
        return False, f"claude exited {result.returncode}: {detail}"

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return True, "claude ran, but its output wasn't JSON — check the file for changes."

    return True, str(payload.get("result") or "Done.")

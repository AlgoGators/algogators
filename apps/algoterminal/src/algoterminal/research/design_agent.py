"""Design stage: turn a plain-English instruction into an edit of a specific
strategy.py, by invoking either the `claude` (Claude Code) or `codex` (Codex
CLI) coding agent headlessly — whichever the user picks in the Design tab.

Both run scoped to a single research record's directory (`cwd`/`--cd` is the
record's own directory, which doesn't contain the rest of the codebase) with
tool access restricted to reading/editing files where each CLI supports that
(no Bash, no network for Claude; a workspace-write sandbox for Codex).

Both CLIs are invoked exactly as found on PATH, so whichever auth each is
logged in with (subscription vs. API key) is whatever gets used — this
module doesn't distinguish the two.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from enum import StrEnum

from algoterminal.research.storage import ResearchRecord

REQUIRED_FUNCTIONS = ("generate_signals", "size_positions", "apply_risk_rules")

_TIMEOUT_SECONDS = 300


class Engine(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"


ENGINE_LABELS = {
    Engine.CLAUDE: "Claude",
    Engine.CODEX: "Codex",
}

_ENGINE_BINARIES = {
    Engine.CLAUDE: "claude",
    Engine.CODEX: "codex",
}


def available_engines() -> list[Engine]:
    """Engines whose CLI is actually installed and on PATH."""
    return [engine for engine, binary in _ENGINE_BINARIES.items() if shutil.which(binary)]


def build_prompt(record: ResearchRecord, instruction: str) -> str:
    hyp = record.load_hypothesis()
    required = "\n".join(f"  - {name}(...)" for name in REQUIRED_FUNCTIONS)
    return (
        f"You are editing exactly one file: {record.strategy_path}\n\n"
        "This file is a trading strategy for the AlgoTerminal research framework. "
        "The backtest engine imports it and looks up these exact top-level function "
        "names via hasattr — renaming, removing, or changing the signature of any of "
        f"them breaks the strategy:\n{required}\n\n"
        "Do not rename these functions, change their signatures, or remove them. "
        "Do not add a `__main__` block or CLI wrapper. "
        f"Do not create, edit, or delete any file other than {record.strategy_path}.\n\n"
        "You do not have shell/execution access in this session. Do not attempt to run, import, "
        "compile, or syntax-check the file — those attempts will just be denied and waste your "
        "turns. Trust your own edit, verify it by re-reading the file if you want, and then stop; "
        "the file's syntax is checked automatically after you finish.\n\n"
        "Strategy context:\n"
        f"- Title: {hyp.title}\n"
        f"- Thesis: {hyp.thesis}\n"
        f"- Universe: {hyp.universe} ({', '.join(hyp.symbols)})\n"
        f"- Expected edge: {hyp.expected_edge}\n\n"
        f"User's instruction for what this strategy should do:\n{instruction}\n\n"
        f"Edit {record.strategy_path} now to implement this."
    )


def run_edit(engine: Engine, record: ResearchRecord, instruction: str) -> tuple[bool, str]:
    """Invoke the chosen coding agent to edit `record.strategy_path`. Returns (ok, message)."""
    if engine is Engine.CLAUDE:
        ok, message = _run_claude_edit(record, instruction)
    elif engine is Engine.CODEX:
        ok, message = _run_codex_edit(record, instruction)
    else:
        raise ValueError(f"unknown engine: {engine!r}")
    return _check_syntax(record, ok, message)


def _check_syntax(record: ResearchRecord, ok: bool, message: str) -> tuple[bool, str]:
    """Independently verify strategy.py still parses after the agent runs.

    Both CLIs are denied shell access, so neither can compile-check its own
    edit — Claude in particular tends to try anyway (running `python -c
    "import ast; ..."` or similar via Bash), get denied, and burn through its
    turn budget re-attempting, which is what actually produces the confusing
    "ran out of turns" failures. We can give the same assurance directly,
    for free, without granting either agent any execution capability.
    """
    if not record.strategy_path.exists():
        return ok, message
    try:
        source = record.strategy_path.read_text(encoding="utf-8")
    except OSError:
        return ok, message
    try:
        ast.parse(source, filename=str(record.strategy_path))
    except SyntaxError as e:
        return False, f"{message}\n\nstrategy.py has a syntax error after this edit: {e}"
    if not ok:
        return ok, f"{message}\n\n(strategy.py's syntax checks out, for what that's worth.)"
    return ok, message


def _run_claude_edit(record: ResearchRecord, instruction: str) -> tuple[bool, str]:
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
                "Read,Edit,Grep,Glob",
                "--permission-mode",
                "acceptEdits",
                "--output-format",
                "json",
                "--max-turns",
                "15",
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

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = None

    if result.returncode != 0:
        if payload is not None:
            return False, _describe_claude_error(payload)
        detail = (result.stderr or result.stdout or "").strip()[:2000]
        return False, f"claude exited {result.returncode}: {detail}"

    if payload is None:
        return True, "claude ran, but its output wasn't JSON — check the file for changes."

    return True, str(payload.get("result") or "Done.")


def _describe_claude_error(payload: dict) -> str:
    """Turn a non-zero-exit `claude -p --output-format json` payload into a
    plain message. A `result` field means Claude reported the error itself;
    without one, it most likely ran out of `--max-turns` mid tool-call (e.g.
    re-checking its own work) rather than actually failing the edit, so say
    that plainly instead of dumping the raw JSON."""
    result_text = payload.get("result")
    if result_text:
        return f"claude reported an error: {result_text}"

    stop_reason = payload.get("stop_reason")
    num_turns = payload.get("num_turns")
    if stop_reason == "tool_use":
        return (
            f"Claude ran out of turns ({num_turns} used) while still trying to use a tool — likely "
            "double-checking its own edit rather than wrapping up. It may have already made the "
            "change correctly; check the code panel below before re-sending."
        )
    return (
        f"claude stopped without finishing (stop_reason={stop_reason!r}, {num_turns} turns used)."
    )


def _run_codex_edit(record: ResearchRecord, instruction: str) -> tuple[bool, str]:
    """Invoke `codex exec` to edit `record.strategy_path`. Returns (ok, message)."""
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return False, "`codex` CLI not found on PATH — install Codex CLI first."

    prompt = build_prompt(record, instruction)

    fd, last_message_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        try:
            # The prompt is sent over stdin rather than as an argv entry: `codex`
            # resolves to a .cmd shim on Windows, which Python can only launch by
            # routing the command line through cmd.exe. cmd.exe's command line is
            # single-line, so a newline anywhere in an argv-passed prompt (ours has
            # plenty) silently truncated it and dropped every flag after the first
            # line — which is exactly what surfaced as "not inside a trusted
            # directory" (`--skip-git-repo-check` never made it to codex). Stdin
            # has no such limit.
            result = subprocess.run(
                [
                    codex_bin,
                    "exec",
                    "--sandbox",
                    "workspace-write",
                    "--cd",
                    str(record.path),
                    "--skip-git-repo-check",
                    "--output-last-message",
                    last_message_path,
                ],
                cwd=str(record.path),
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return False, f"codex exec timed out after {_TIMEOUT_SECONDS}s."
        except OSError as e:
            return False, f"Failed to launch codex: {e}"

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:2000]
            return False, f"codex exited {result.returncode}: {detail}"

        message = ""
        try:
            with open(last_message_path, encoding="utf-8") as f:
                message = f.read().strip()
        except OSError:
            pass

        return True, message or "Done."
    finally:
        with contextlib.suppress(OSError):
            os.unlink(last_message_path)

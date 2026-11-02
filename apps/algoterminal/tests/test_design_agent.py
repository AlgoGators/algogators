"""Tests for the design-stage agent wrapper (CLIs stubbed, nothing executed)."""

from __future__ import annotations

import json
import subprocess

import pytest
from algoterminal.research import design_agent, storage
from algoterminal.research.design_agent import (
    Engine,
    _check_syntax,
    _describe_claude_error,
    available_engines,
    build_prompt,
    run_edit,
)

from .conftest import SMA_STRATEGY, make_hypothesis


@pytest.fixture
def record():
    return storage.create_record(make_hypothesis())


def _which(mapping):
    return lambda binary: mapping.get(binary)


class TestAvailableEngines:
    def test_none_installed(self, monkeypatch):
        monkeypatch.setattr(design_agent.shutil, "which", _which({}))
        assert available_engines() == []

    def test_claude_only(self, monkeypatch):
        monkeypatch.setattr(design_agent.shutil, "which", _which({"claude": "C:/bin/claude"}))
        assert available_engines() == [Engine.CLAUDE]

    def test_both(self, monkeypatch):
        monkeypatch.setattr(
            design_agent.shutil,
            "which",
            _which({"claude": "C:/bin/claude", "codex": "C:/bin/codex"}),
        )
        assert available_engines() == [Engine.CLAUDE, Engine.CODEX]


class TestBuildPrompt:
    def test_mentions_contract_and_instruction(self, record):
        prompt = build_prompt(record, "make it a mean reversion strategy")
        assert str(record.strategy_path) in prompt
        assert "generate_signals" in prompt
        assert "size_positions" in prompt
        assert "apply_risk_rules" in prompt
        assert "make it a mean reversion strategy" in prompt
        assert "Test Momentum" in prompt


class TestCheckSyntax:
    def test_no_strategy_file_passthrough(self, record):
        assert _check_syntax(record, True, "msg") == (True, "msg")

    def test_valid_syntax_passthrough(self, record):
        record.strategy_path.write_text(SMA_STRATEGY, encoding="utf-8")
        assert _check_syntax(record, True, "msg") == (True, "msg")

    def test_syntax_error_flips_ok(self, record):
        record.strategy_path.write_text("def broken(:\n", encoding="utf-8")
        ok, message = _check_syntax(record, True, "msg")
        assert not ok
        assert "syntax error" in message

    def test_failed_run_with_valid_syntax_notes_it(self, record):
        record.strategy_path.write_text(SMA_STRATEGY, encoding="utf-8")
        ok, message = _check_syntax(record, False, "agent failed")
        assert not ok
        assert "syntax checks out" in message


class TestDescribeClaudeError:
    def test_result_field_reported(self):
        assert "boom" in _describe_claude_error({"result": "boom"})

    def test_out_of_turns(self):
        message = _describe_claude_error({"stop_reason": "tool_use", "num_turns": 15})
        assert "ran out of turns" in message
        assert "15" in message

    def test_generic_stop(self):
        message = _describe_claude_error({"stop_reason": "other", "num_turns": 3})
        assert "stop_reason='other'" in message


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestClaudeEdit:
    def test_missing_cli(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({}))
        ok, message = run_edit(Engine.CLAUDE, record, "do it")
        assert not ok
        assert "not found on PATH" in message

    def test_successful_edit(self, monkeypatch, record):
        record.strategy_path.write_text(SMA_STRATEGY, encoding="utf-8")
        monkeypatch.setattr(design_agent.shutil, "which", _which({"claude": "claude"}))
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["cwd"] = kwargs.get("cwd")
            return _completed(stdout=json.dumps({"result": "edited it"}))

        monkeypatch.setattr(design_agent.subprocess, "run", fake_run)
        ok, message = run_edit(Engine.CLAUDE, record, "do it")
        assert ok
        assert message == "edited it"
        assert seen["cwd"] == str(record.path)
        assert "--allowedTools" in seen["cmd"]

    def test_success_with_non_json_output(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({"claude": "claude"}))
        monkeypatch.setattr(
            design_agent.subprocess, "run", lambda *a, **k: _completed(stdout="plain text")
        )
        ok, message = design_agent._run_claude_edit(record, "do it")
        assert ok
        assert "wasn't JSON" in message

    def test_nonzero_exit_with_payload(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({"claude": "claude"}))
        payload = json.dumps({"stop_reason": "tool_use", "num_turns": 15})
        monkeypatch.setattr(
            design_agent.subprocess,
            "run",
            lambda *a, **k: _completed(returncode=1, stdout=payload),
        )
        ok, message = design_agent._run_claude_edit(record, "do it")
        assert not ok
        assert "ran out of turns" in message

    def test_nonzero_exit_without_payload(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({"claude": "claude"}))
        monkeypatch.setattr(
            design_agent.subprocess,
            "run",
            lambda *a, **k: _completed(returncode=2, stderr="kaput"),
        )
        ok, message = design_agent._run_claude_edit(record, "do it")
        assert not ok
        assert "claude exited 2" in message
        assert "kaput" in message

    def test_timeout(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({"claude": "claude"}))

        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=300)

        monkeypatch.setattr(design_agent.subprocess, "run", fake_run)
        ok, message = design_agent._run_claude_edit(record, "do it")
        assert not ok
        assert "timed out" in message

    def test_launch_failure(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({"claude": "claude"}))

        def fake_run(*a, **k):
            raise OSError("cannot exec")

        monkeypatch.setattr(design_agent.subprocess, "run", fake_run)
        ok, message = design_agent._run_claude_edit(record, "do it")
        assert not ok
        assert "Failed to launch claude" in message


class TestCodexEdit:
    def test_missing_cli(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({}))
        ok, message = run_edit(Engine.CODEX, record, "do it")
        assert not ok
        assert "not found on PATH" in message

    def test_successful_edit(self, monkeypatch, record):
        record.strategy_path.write_text(SMA_STRATEGY, encoding="utf-8")
        monkeypatch.setattr(design_agent.shutil, "which", _which({"codex": "codex"}))
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["input"] = kwargs.get("input")
            out_path = cmd[cmd.index("--output-last-message") + 1]
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("codex did the thing\n")
            return _completed()

        monkeypatch.setattr(design_agent.subprocess, "run", fake_run)
        ok, message = run_edit(Engine.CODEX, record, "my instruction")
        assert ok
        assert message == "codex did the thing"
        assert "my instruction" in seen["input"]

    def test_success_without_message_file_content(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({"codex": "codex"}))
        monkeypatch.setattr(design_agent.subprocess, "run", lambda *a, **k: _completed())
        ok, message = design_agent._run_codex_edit(record, "do it")
        assert ok
        assert message == "Done."

    def test_nonzero_exit(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({"codex": "codex"}))
        monkeypatch.setattr(
            design_agent.subprocess,
            "run",
            lambda *a, **k: _completed(returncode=3, stderr="denied"),
        )
        ok, message = design_agent._run_codex_edit(record, "do it")
        assert not ok
        assert "codex exited 3" in message

    def test_timeout(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({"codex": "codex"}))

        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd="codex", timeout=300)

        monkeypatch.setattr(design_agent.subprocess, "run", fake_run)
        ok, message = design_agent._run_codex_edit(record, "do it")
        assert not ok
        assert "timed out" in message

    def test_launch_failure(self, monkeypatch, record):
        monkeypatch.setattr(design_agent.shutil, "which", _which({"codex": "codex"}))

        def fake_run(*a, **k):
            raise OSError("cannot exec")

        monkeypatch.setattr(design_agent.subprocess, "run", fake_run)
        ok, message = design_agent._run_codex_edit(record, "do it")
        assert not ok
        assert "Failed to launch codex" in message


class TestRunEditDispatch:
    def test_run_edit_applies_syntax_check(self, monkeypatch, record):
        record.strategy_path.write_text("def broken(:\n", encoding="utf-8")
        monkeypatch.setattr(design_agent.shutil, "which", _which({"claude": "claude"}))
        monkeypatch.setattr(
            design_agent.subprocess,
            "run",
            lambda *a, **k: _completed(stdout=json.dumps({"result": "done"})),
        )
        ok, message = run_edit(Engine.CLAUDE, record, "do it")
        assert not ok
        assert "syntax error" in message

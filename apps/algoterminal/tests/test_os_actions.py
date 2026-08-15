"""Tests for launching external tools (everything stubbed, nothing launched)."""

from __future__ import annotations

import subprocess
import sys

import pytest
from algoterminal.tui import os_actions


@pytest.fixture
def popen_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kwargs: calls.append(cmd))
    return calls


def _set_platform(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)


class TestFindVscode:
    def test_found(self, monkeypatch):
        monkeypatch.setattr(os_actions.shutil, "which", lambda name: "C:/bin/code")
        assert os_actions.find_vscode() == "C:/bin/code"

    def test_missing(self, monkeypatch):
        monkeypatch.setattr(os_actions.shutil, "which", lambda name: None)
        assert os_actions.find_vscode() is None


class TestCanOpenFileLocation:
    @pytest.mark.parametrize(
        ("platform", "binary"),
        [("win32", "explorer"), ("darwin", "open"), ("linux", "xdg-open")],
    )
    def test_depends_on_platform_binary(self, monkeypatch, platform, binary):
        _set_platform(monkeypatch, platform)
        monkeypatch.setattr(
            os_actions.shutil,
            "which",
            lambda name, expected=binary: "/bin/x" if name == expected else None,
        )
        assert os_actions.can_open_file_location()

    def test_missing_binary(self, monkeypatch):
        _set_platform(monkeypatch, "linux")
        monkeypatch.setattr(os_actions.shutil, "which", lambda name: None)
        assert not os_actions.can_open_file_location()


class TestOpenFileLocation:
    def test_windows_selects_file(self, monkeypatch, popen_calls, tmp_path):
        _set_platform(monkeypatch, "win32")
        target = tmp_path / "strategy.py"
        target.write_text("x = 1\n", encoding="utf-8")
        os_actions.open_file_location(target)
        assert popen_calls == [f'explorer /select,"{target}"']

    def test_windows_opens_directory(self, monkeypatch, popen_calls, tmp_path):
        _set_platform(monkeypatch, "win32")
        os_actions.open_file_location(tmp_path)
        assert popen_calls == [["explorer", str(tmp_path)]]

    def test_darwin_reveals_file(self, monkeypatch, popen_calls, tmp_path):
        _set_platform(monkeypatch, "darwin")
        target = tmp_path / "strategy.py"
        target.write_text("x = 1\n", encoding="utf-8")
        os_actions.open_file_location(target)
        assert popen_calls == [["open", "-R", str(target)]]

    def test_darwin_opens_directory(self, monkeypatch, popen_calls, tmp_path):
        _set_platform(monkeypatch, "darwin")
        os_actions.open_file_location(tmp_path)
        assert popen_calls == [["open", str(tmp_path)]]

    def test_linux_opens_parent_for_files(self, monkeypatch, popen_calls, tmp_path):
        _set_platform(monkeypatch, "linux")
        target = tmp_path / "strategy.py"
        target.write_text("x = 1\n", encoding="utf-8")
        os_actions.open_file_location(target)
        assert popen_calls == [["xdg-open", str(tmp_path)]]

    def test_linux_opens_directory_directly(self, monkeypatch, popen_calls, tmp_path):
        _set_platform(monkeypatch, "linux")
        os_actions.open_file_location(tmp_path)
        assert popen_calls == [["xdg-open", str(tmp_path)]]


class TestOpenInVscode:
    def test_launches_code(self, monkeypatch, popen_calls, tmp_path):
        monkeypatch.setattr(os_actions.shutil, "which", lambda name: "C:/bin/code")
        os_actions.open_in_vscode(tmp_path)
        assert popen_calls == [["C:/bin/code", str(tmp_path)]]

    def test_missing_cli_raises(self, monkeypatch, popen_calls, tmp_path):
        monkeypatch.setattr(os_actions.shutil, "which", lambda name: None)
        with pytest.raises(FileNotFoundError):
            os_actions.open_in_vscode(tmp_path)
        assert popen_calls == []

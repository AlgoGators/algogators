"""Tests for platform/ci/quality_needed.py, the skip-unaffected-quality rule."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "platform" / "ci"))

import quality_needed  # noqa: E402

PATHS = [
    "libs/lib-a/**",
    "uv.lock",
    "pyproject.toml",
    ".github/workflows/_quality-python.yml",
    ".github/workflows/lib-a.quality.yml",
]


def pkg(name: str, version: str, deps: tuple[str, ...] = (), **extra: object) -> dict:
    entry: dict = {
        "name": name,
        "version": version,
        "source": {"registry": "https://pypi.org/simple"},
    }
    if deps:
        entry["dependencies"] = [{"name": d} for d in deps]
    entry.update(extra)
    return entry


def lock(*packages: dict) -> dict:
    root = {
        "name": "workspace",
        "version": "0",
        "source": {"virtual": "."},
        "dev-dependencies": {"dev": [{"name": "pytest"}]},
    }
    return {"package": [root, *packages]}


BASE = lock(
    {
        "name": "lib-a",
        "version": "0.1.0",
        "source": {"editable": "libs/lib-a"},
        "dependencies": [{"name": "sqlalchemy"}],
    },
    {
        "name": "svc-b",
        "version": "0.1.0",
        "source": {"editable": "services/svc-b"},
        "dependencies": [{"name": "aiohttp"}],
    },
    pkg("sqlalchemy", "1.4.54", ("greenlet",)),
    pkg("greenlet", "3.5.5"),
    pkg("aiohttp", "3.9.0"),
    pkg("pytest", "8.0.0"),
)


def with_changes(**versions: str) -> dict:
    out = {"package": []}
    for entry in BASE["package"]:
        entry = dict(entry)
        if entry["name"] in versions:
            entry["version"] = versions[entry["name"]]
        out["package"].append(entry)
    return out


@pytest.fixture
def locks(monkeypatch: pytest.MonkeyPatch):
    store: dict[str, dict | None] = {}
    monkeypatch.setattr(quality_needed, "lock_at", lambda rev, root=None: store.get(rev))
    return store


def decide(changed: list[str]) -> tuple[bool, str]:
    return quality_needed.decide(changed, PATHS, "lib-a", "base", "head")


def test_member_code_change_runs(locks) -> None:
    run, reason = decide(["libs/lib-a/src/x.py"])
    assert run and "libs/lib-a/src/x.py" in reason


def test_unrelated_change_skips(locks) -> None:
    run, _ = decide(["services/svc-b/app.py", "README.md"])
    assert not run


def test_root_config_change_runs(locks) -> None:
    # Root pyproject.toml carries ruff/mypy/pytest config shared by every member.
    assert decide(["pyproject.toml", "uv.lock"])[0]


def test_lock_change_in_another_members_deps_skips(locks) -> None:
    locks["base"], locks["head"] = BASE, with_changes(aiohttp="3.14.1")
    run, reason = decide(["uv.lock", "services/svc-b/pyproject.toml"])
    assert not run and "not lib-a's" in reason


def test_lock_change_in_own_transitive_dep_runs(locks) -> None:
    locks["base"], locks["head"] = BASE, with_changes(greenlet="3.6.0")
    run, reason = decide(["uv.lock"])
    assert run and "greenlet" in reason


def test_lock_change_in_ci_tooling_runs(locks) -> None:
    # The workspace root's dev group (pytest, ruff, mypy) runs every member's checks.
    locks["base"], locks["head"] = BASE, with_changes(pytest="9.0.0")
    assert decide(["uv.lock"])[0]


def test_unreadable_lock_fails_open(locks) -> None:
    locks["base"], locks["head"] = None, BASE
    run, reason = decide(["uv.lock"])
    assert run and "could not be read" in reason


def test_no_base_commit_runs(capsys: pytest.CaptureFixture[str]) -> None:
    quality_needed.main(
        ["--workflow", ".github/workflows/x.quality.yml", "--member-path", "libs/x", "--base", ""]
    )
    assert "run=true" in capsys.readouterr().out


def test_zero_sha_base_runs(capsys: pytest.CaptureFixture[str]) -> None:
    quality_needed.main(["--workflow", "x", "--member-path", "x", "--base", "0" * 40])
    assert "run=true" in capsys.readouterr().out


def test_workflow_without_paths_runs(capsys: pytest.CaptureFixture[str]) -> None:
    # Deploy/publish callers have no pull_request.paths: always run.
    quality_needed.main(
        [
            "--workflow",
            ".github/workflows/svc-data-ngin.deploy.yml",
            "--member-path",
            "services/data-ngin",
            "--base",
            "HEAD",
        ]
    )
    assert "run=true" in capsys.readouterr().out

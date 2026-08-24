"""Shared machinery for the monorepo's CI meta-tooling.

Two consumers import this:

* ``check_workflow_paths.py`` -- verifies each member's path filters are honest
* ``new_service.py``          -- generates a member's workflow files

The one idea holding it together: a member's ``paths:`` filter is the *only*
declaration of what that member owns. Everything else -- which checks a PR must
wait for, whether a shared change fans out correctly -- is derived from it. That
makes the filter worth validating hard, which is what these tools do.

Note on the directory name: ``platform`` shadows a stdlib module if it ever
becomes an importable package. It must never contain an ``__init__.py``;
``check_workflow_paths.py`` enforces that.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on 3.9/3.10
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: Directories that may contain members. The tier a member lives in decides how
#: it ships, which decides which publish workflow it gets. `platform` holds
#: cross-cutting members (e.g. platform/db) that are neither standalone libs
#: nor deployable services.
MEMBER_ROOTS = ("libs", "apps", "services", "platform")

#: A directory is a member if it contains one of these. Discovery stops
#: descending once it finds one, so nested source and test directories under a
#: member are never treated as separate members.
MANIFESTS = {
    "pyproject.toml": "python",
    "package.json": "node",
    "CMakeLists.txt": "cpp",
}

PRUNE_DIRS = {
    ".git",
    "node_modules",
    "build",
    "dist",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "vcpkg_installed",
    "third_party",
    "htmlcov",
    ".tox",
}

#: Paths every member of a given archetype must list, because a change to any of
#: them changes that member's build even though it lives outside its directory.
ALWAYS_REQUIRED: dict[str, tuple[str, ...]] = {
    "python": ("uv.lock", "pyproject.toml", ".github/workflows/_quality-python.yml"),
    "node": ("pnpm-lock.yaml", "pnpm-workspace.yaml", ".github/workflows/_quality-node.yml"),
    "cpp": ("vcpkg.json", "CMakePresets.json", ".github/workflows/_quality-cpp.yml"),
}

#: Anything referencing the shared contracts must rebuild when they change.
#: Detected by content scan rather than by a declared dependency, because C++
#: consumes generated headers and Python consumes generated models -- there is
#: no single manifest that records the edge.
CONTRACTS_GLOB = "platform/contracts/**"
CONTRACTS_MARKERS = ("platform/contracts", "algogators.contracts", "algogators/contracts")


# ---------------------------------------------------------------------------
# GitHub path-filter glob semantics
# ---------------------------------------------------------------------------


@lru_cache(maxsize=512)
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a GitHub Actions path glob into a regex.

    GitHub's rules, which are not fnmatch's:

    * ``*``  matches any run of characters except ``/``
    * ``**`` matches any run of characters including ``/``
    * ``**/`` additionally matches zero directories, so ``**/*.md`` matches a
      top-level ``README.md`` -- fnmatch gets this wrong
    * ``?``  matches a single character except ``/``
    """
    out: list[str] = ["^"]
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def path_matches(file_path: str, patterns: list[str]) -> bool:
    """Does ``file_path`` survive ``patterns`` as GitHub would evaluate them?

    Patterns apply in order and the last one to match wins, so a trailing
    ``!libs/x/**/*.md`` removes documentation that an earlier ``libs/x/**``
    included. Order is significant; a negation placed first does nothing.
    """
    matched = False
    for raw in patterns:
        negated = raw.startswith("!")
        pattern = raw[1:] if negated else raw
        if glob_to_regex(pattern).match(file_path):
            matched = not negated
    return matched


def any_match(files: list[str], patterns: list[str]) -> bool:
    """Would a workflow with ``patterns`` trigger for this changed-file set?"""
    return any(path_matches(f, patterns) for f in files)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """A directory that builds, tests and ships as a unit."""

    path: str  # repo-relative, forward slashes: "libs/algosystem"
    archetype: str  # python | node | cpp
    tier: str  # libs | apps | services | platform
    name: str  # distribution/package name, or the directory name

    @property
    def own_glob(self) -> str:
        return f"{self.path}/**"

    @property
    def slug(self) -> str:
        """Workflow filename stem: ``lib-algosystem``, ``app-algolens-web``."""
        prefix = {"libs": "lib", "apps": "app", "services": "svc", "platform": "platform"}[self.tier]
        rest = self.path.split("/", 1)[1].replace("/", "-")
        return f"{prefix}-{rest}"

    @property
    def gate_name(self) -> str:
        """The stable check-run name branch protection would reference."""
        return f"{self.slug} quality"


def discover_members(root: Path = REPO_ROOT) -> list[Member]:
    """Find every member under libs/, apps/ and services/."""
    members: list[Member] = []
    for tier in MEMBER_ROOTS:
        tier_dir = root / tier
        if not tier_dir.is_dir():
            continue
        _walk(tier_dir, tier, root, members)
    return sorted(members, key=lambda m: m.path)


def _walk(directory: Path, tier: str, root: Path, out: list[Member]) -> None:
    manifest = next((m for m in MANIFESTS if (directory / m).is_file()), None)
    if manifest is not None:
        rel = directory.relative_to(root).as_posix()
        out.append(
            Member(
                path=rel,
                archetype=MANIFESTS[manifest],
                tier=tier,
                name=_member_name(directory, manifest) or directory.name,
            )
        )
        return  # a member is a leaf; do not descend into it
    for child in sorted(directory.iterdir()):
        if child.is_dir() and child.name not in PRUNE_DIRS:
            _walk(child, tier, root, out)


def _member_name(directory: Path, manifest: str) -> str | None:
    try:
        if manifest == "pyproject.toml":
            data = tomllib.loads((directory / manifest).read_text("utf-8"))
            return data.get("project", {}).get("name") or (
                data.get("tool", {}).get("poetry", {}).get("name")
            )
        if manifest == "package.json":
            return json.loads((directory / manifest).read_text("utf-8")).get("name")
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return None
    return None


def uses_contracts(member: Member, root: Path = REPO_ROOT) -> bool:
    """Does this member reference the shared contracts?

    A content scan, not a manifest lookup: the C++ side consumes generated
    headers and the Python side generated models, and neither edge is recorded
    anywhere a dependency resolver would see it.
    """
    base = root / member.path
    for file in base.rglob("*"):
        if not file.is_file() or file.suffix.lower() not in {
            ".py",
            ".pyi",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".cpp",
            ".cc",
            ".hpp",
            ".h",
            ".cmake",
            ".txt",
            ".toml",
            ".json",
            ".yaml",
            ".yml",
        }:
            continue
        if any(part in PRUNE_DIRS for part in file.relative_to(base).parts):
            continue
        try:
            text = file.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        if any(marker in text for marker in CONTRACTS_MARKERS):
            return True
    return False


def required_paths(member: Member, root: Path = REPO_ROOT) -> list[str]:
    """Everything this member's quality workflow must list under ``paths:``."""
    required = [member.own_glob, *ALWAYS_REQUIRED[member.archetype]]
    required.append(f".github/workflows/{member.slug}.quality.yml")
    if uses_contracts(member, root):
        required.append(CONTRACTS_GLOB)
    return required


# ---------------------------------------------------------------------------
# Workflow files
# ---------------------------------------------------------------------------


@dataclass
class QualityWorkflow:
    """A parsed ``<slug>.quality.yml`` caller."""

    file: Path
    slug: str
    pr_paths: list[str] = field(default_factory=list)
    push_paths: list[str] = field(default_factory=list)
    gate_name: str | None = None
    member_path: str | None = None

    @property
    def rel(self) -> str:
        return self.file.relative_to(REPO_ROOT).as_posix()


def load_quality_workflows(workflow_dir: Path = WORKFLOW_DIR) -> list[QualityWorkflow]:
    """Parse every ``*.quality.yml`` caller in the workflow directory."""
    results: list[QualityWorkflow] = []
    if not workflow_dir.is_dir():
        return results
    for file in sorted(workflow_dir.glob("*.quality.yml")):
        data = yaml.safe_load(file.read_text("utf-8")) or {}
        # PyYAML resolves the bare key `on` to the boolean True (YAML 1.1),
        # which is why this checks both spellings.
        triggers = data.get("on") or data.get(True) or {}
        wf = QualityWorkflow(
            file=file,
            slug=file.name.removesuffix(".quality.yml"),
            pr_paths=list((triggers.get("pull_request") or {}).get("paths") or []),
            push_paths=list((triggers.get("push") or {}).get("paths") or []),
        )
        for job in (data.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            if "uses" in job and isinstance(job.get("with"), dict):
                wf.member_path = wf.member_path or job["with"].get("path")
            # The gate job is the one that reports the required check: it names
            # itself explicitly and depends on the quality job.
            if job.get("name") and "needs" in job and "uses" not in job:
                wf.gate_name = job["name"]
        results.append(wf)
    return results


def changed_files(base_ref: str, head_ref: str = "HEAD", root: Path = REPO_ROOT) -> list[str]:
    """Files changed between the merge base of ``base_ref`` and ``head_ref``.

    Three dots, not two: a PR is judged on what it adds relative to where it
    branched, not on everything that has landed on main since.
    """
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

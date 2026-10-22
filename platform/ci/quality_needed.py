"""Decide whether a Python member's quality matrix needs to run for this change.

Called by ``_quality-python.yml``'s ``detect`` job. A member's ``paths:`` filter
deliberately lists ``uv.lock``, because the workspace shares one lockfile and a
change to it *can* change what the member installs. It usually does not: most
lock changes add or bump another member's dependencies. Running algosystem's
six-job OS/Python matrix for a data-ngin-only bump is the waste this removes.

The rule, applied to the files changed between ``--base`` and ``--head``:

* any changed file that matches the caller's ``pull_request.paths`` *other
  than* ``uv.lock`` -> run (member code, its workspace deps, root config, CI)
* only ``uv.lock`` matched -> run only if the member's resolved dependency
  closure differs between the two locks. The closure is every package reachable
  from the member (all extras and groups, which is what CI syncs) plus the
  workspace root's dev group (pytest, ruff, mypy: the tools CI runs it with),
  compared entry by entry, versions, sources and hashes included.
* nothing matched -> skip (only reachable from push/dispatch, where GitHub has
  already applied the path filter coarsely)

Anything it cannot determine (no base commit, unreadable lock) means run: a
skipped check that should have run is the expensive mistake here.

Prints ``run=true|false`` and ``reason=...`` lines for ``$GITHUB_OUTPUT``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Any

from _common import REPO_ROOT, discover_members, load_quality_workflows, path_matches

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on 3.9/3.10
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

LOCKFILE = "uv.lock"
WORKSPACE_ROOT_SOURCE = {"virtual": "."}


def _git(*args: str, root: Path = REPO_ROOT) -> str | None:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return proc.stdout if proc.returncode == 0 else None


def lock_at(rev: str, root: Path = REPO_ROOT) -> dict[str, Any] | None:
    text = _git("show", f"{rev}:{LOCKFILE}", root=root)
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _edges(package: dict[str, Any]) -> list[str]:
    deps = list(package.get("dependencies") or [])
    for group in (package.get("optional-dependencies") or {}).values():
        deps.extend(group)
    for group in (package.get("dev-dependencies") or {}).values():
        deps.extend(group)
    return [d["name"] for d in deps]


def closure(lock: dict[str, Any], member_name: str) -> dict[str, list[str]]:
    """Every lock entry the member's environment can contain, as canonical JSON.

    A name can appear more than once in a lock (per-platform forks), so each
    name maps to all of its entries, sorted.
    """
    by_name: dict[str, list[dict[str, Any]]] = {}
    for package in lock.get("package") or []:
        by_name.setdefault(package["name"], []).append(package)

    roots = [member_name]
    roots += [
        p["name"] for p in lock.get("package") or [] if p.get("source") == WORKSPACE_ROOT_SOURCE
    ]

    seen: dict[str, list[str]] = {}
    queue = deque(roots)
    while queue:
        name = queue.popleft()
        if name in seen or name not in by_name:
            continue
        entries = by_name[name]
        seen[name] = sorted(json.dumps(e, sort_keys=True) for e in entries)
        for entry in entries:
            queue.extend(_edges(entry))
    return seen


def decide(
    changed: list[str],
    patterns: list[str],
    member_name: str,
    base: str,
    head: str,
    root: Path = REPO_ROOT,
) -> tuple[bool, str]:
    relevant = [f for f in changed if path_matches(f, patterns)]
    if not relevant:
        return False, "no changed file matches this member's paths"
    others = [f for f in relevant if f != LOCKFILE]
    if others:
        shown = ", ".join(others[:5]) + (" ..." if len(others) > 5 else "")
        return True, f"member-relevant files changed: {shown}"

    old, new = lock_at(base, root), lock_at(head, root)
    if old is None or new is None:
        return True, "uv.lock changed and could not be read at both revisions"
    before, after = closure(old, member_name), closure(new, member_name)
    if member_name not in after:
        return True, f"{member_name} is not in the head uv.lock"
    if before == after:
        return False, f"uv.lock changed, but not {member_name}'s resolved dependencies"
    differing = sorted(n for n in before.keys() | after.keys() if before.get(n) != after.get(n))
    shown = ", ".join(differing[:8]) + (" ..." if len(differing) > 8 else "")
    return True, f"uv.lock changes {member_name}'s resolved dependencies: {shown}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--workflow",
        required=True,
        help="caller workflow, e.g. .github/workflows/lib-x.quality.yml",
    )
    parser.add_argument("--member-path", required=True, help="e.g. libs/algosystem")
    parser.add_argument("--base", default="", help="commit to compare against; empty means run")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)

    def emit(run: bool, reason: str) -> int:
        print(f"run={'true' if run else 'false'}")
        print(f"reason={reason}")
        return 0

    if not args.base or set(args.base) == {"0"}:
        return emit(True, "no base commit to compare against")

    workflow = Path(args.workflow).as_posix()
    callers = [wf for wf in load_quality_workflows() if wf.rel == workflow]
    if not callers or not callers[0].pr_paths:
        return emit(True, f"no pull_request.paths found in {workflow}")
    members = [m for m in discover_members() if m.path == args.member_path]
    if not members:
        return emit(True, f"{args.member_path} is not a workspace member")

    diff = _git("diff", "--name-only", args.base, args.head)
    if diff is None:
        return emit(True, f"could not diff {args.base}..{args.head}")
    changed = [line for line in diff.splitlines() if line]
    run, reason = decide(changed, callers[0].pr_paths, members[0].name, args.base, args.head)
    return emit(run, reason)


if __name__ == "__main__":
    sys.exit(main())

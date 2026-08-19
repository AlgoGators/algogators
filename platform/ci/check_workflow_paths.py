#!/usr/bin/env python
"""Verify that every member's CI path filters still describe reality.

Path-filtered CI trades safety for speed: a service whose files did not change
does not get tested. That trade is only sound while the filters are accurate,
and an inaccurate filter fails *silently* -- a renamed directory or a new
dependency on the shared contracts quietly removes a service from CI, and
nothing turns red to tell you. Everything still passes. That is the failure this
script exists to make loud.

Run locally with ``just check-paths``; runs in CI on every PR.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on 3.9/3.10
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

from _common import (
    ALWAYS_REQUIRED,
    CONTRACTS_GLOB,
    REPO_ROOT,
    WORKFLOW_DIR,
    Member,
    QualityWorkflow,
    discover_members,
    load_quality_workflows,
    required_paths,
    uses_contracts,
)


class Report:
    """Collects problems so one run reports all of them, not just the first."""

    def __init__(self) -> None:
        self.problems: list[tuple[str, str, str]] = []

    def fail(self, where: str, what: str, fix: str) -> None:
        self.problems.append((where, what, fix))

    @property
    def ok(self) -> bool:
        return not self.problems

    def emit(self) -> None:
        if self.ok:
            return
        print(f"\n{len(self.problems)} problem(s):\n", file=sys.stderr)
        for where, what, fix in self.problems:
            print(f"  {where}", file=sys.stderr)
            print(f"    problem: {what}", file=sys.stderr)
            print(f"    fix:     {fix}\n", file=sys.stderr)
        # Annotate the PR diff directly rather than making people read the log.
        for where, what, fix in self.problems:
            print(f"::error file={where}::{what} -- {fix}")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def rule_no_platform_package(report: Report, root: Path) -> None:
    """`platform/` must never become an importable package.

    Python's standard library has a `platform` module. A namespace package of
    the same name at the repo root can shadow it for any tool that puts the root
    on sys.path, and the resulting ImportError is deeply confusing.
    """
    offender = root / "platform" / "__init__.py"
    if offender.exists():
        report.fail(
            "platform/__init__.py",
            "platform/ has become an importable package and shadows the stdlib `platform` module",
            "delete platform/__init__.py; keep this directory a plain namespace of subdirectories",
        )


def rule_every_member_has_a_caller(
    report: Report, members: list[Member], workflows: dict[str, QualityWorkflow]
) -> None:
    for member in members:
        if member.slug not in workflows:
            report.fail(
                f"{member.path}/",
                f"member has no quality workflow (expected .github/workflows/{member.slug}.quality.yml)",
                f"run: just new-service {member.tier} {member.path}",
            )


def rule_no_orphan_callers(
    report: Report, members: list[Member], workflows: dict[str, QualityWorkflow]
) -> None:
    known = {m.slug for m in members}
    for slug, wf in workflows.items():
        if slug not in known:
            report.fail(
                wf.rel,
                "quality workflow has no matching member directory",
                "delete the workflow, or restore the member it gates",
            )


def rule_triggers_agree(report: Report, wf: QualityWorkflow) -> None:
    """`pull_request.paths` and `push.paths` must be identical.

    Actions has no YAML anchors, so the two lists are copy-pasted and drift.
    When they do, a PR gets tested and the post-merge push does not (or the
    reverse), which is how a green PR becomes a red main.
    """
    if not wf.pr_paths:
        report.fail(
            wf.rel, "no `on.pull_request.paths` filter", "add one, or the workflow runs on every PR"
        )
        return
    if not wf.push_paths:
        report.fail(
            wf.rel, "no `on.push.paths` filter", "mirror the pull_request.paths list exactly"
        )
        return
    if wf.pr_paths != wf.push_paths:
        only_pr = [p for p in wf.pr_paths if p not in wf.push_paths]
        only_push = [p for p in wf.push_paths if p not in wf.pr_paths]
        report.fail(
            wf.rel,
            f"pull_request.paths and push.paths differ (pr-only: {only_pr or '-'}, push-only: {only_push or '-'})",
            "make the two lists byte-identical, including order",
        )


def rule_required_paths_present(report: Report, wf: QualityWorkflow, member: Member) -> None:
    """Every path that can change this member's build must be listed."""
    listed = set(wf.pr_paths)
    for needed in required_paths(member, REPO_ROOT):
        if needed not in listed:
            why = (
                "the member references platform/contracts, so a schema change must retest it"
                if needed == CONTRACTS_GLOB
                else "a change here changes this member's build"
            )
            report.fail(
                wf.rel,
                f"missing required path `{needed}` -- {why}",
                f"add `- '{needed}'` to both paths lists",
            )

    if uses_contracts(member, REPO_ROOT) and CONTRACTS_GLOB not in listed:
        return  # already reported above

    # The reverse mistake: listing contracts without consuming them means every
    # schema edit drags this member's matrix along for nothing.
    if CONTRACTS_GLOB in listed and not uses_contracts(member, REPO_ROOT):
        report.fail(
            wf.rel,
            f"lists `{CONTRACTS_GLOB}` but nothing in {member.path} references the contracts",
            "drop it; a schema change should not rebuild members that do not consume schemas",
        )


def rule_patterns_resolve(report: Report, wf: QualityWorkflow, root: Path) -> None:
    """Every positive pattern must match something that exists.

    This is the rename detector. `services/data_ngin/**` after the directory is
    renamed to `data-ngin` matches nothing, so the workflow never fires again --
    and looks completely normal in review.
    """
    for pattern in wf.pr_paths:
        if pattern.startswith("!"):
            continue  # negations legitimately match nothing
        literal = pattern.split("*", 1)[0].rstrip("/")
        if not literal:
            continue
        target = root / literal
        if not target.exists():
            report.fail(
                wf.rel,
                f"path `{pattern}` matches nothing on disk (`{literal}` does not exist)",
                "fix the glob, or remove it if the directory is gone",
            )


def rule_gate_job(report: Report, wf: QualityWorkflow, member: Member) -> None:
    """The caller must expose a stable, correctly named gate job.

    Branch protection cannot require the matrix job names -- those change
    whenever the matrix does -- so each member exposes one gate job whose name
    is stable. Keep it accurate even while `main` is unprotected: it is the
    name protection would have to reference.
    """
    if wf.gate_name is None:
        report.fail(
            wf.rel,
            "no gate job (a job with an explicit `name:` and a `needs:` on the quality job)",
            f"add a gate job named `{member.gate_name}` -- see platform/ci/templates/quality.yml.tmpl",
        )
    elif wf.gate_name != member.gate_name:
        report.fail(
            wf.rel,
            f"gate job is named `{wf.gate_name}`, expected `{member.gate_name}`",
            f"rename it to `{member.gate_name}` so the gate name stays stable",
        )


def rule_with_path_matches(report: Report, wf: QualityWorkflow, member: Member) -> None:
    if wf.member_path is None:
        report.fail(
            wf.rel, "the reusable is called without a `path:` input", f"pass `path: {member.path}`"
        )
    elif wf.member_path.rstrip("/") != member.path:
        report.fail(
            wf.rel,
            f"calls the reusable with `path: {wf.member_path}` but gates `{member.path}`",
            f"set `path: {member.path}`",
        )


def rule_reusable_exists(report: Report, wf: QualityWorkflow, member: Member) -> None:
    reusable = ALWAYS_REQUIRED[member.archetype][-1]
    if not (REPO_ROOT / reusable).exists():
        report.fail(
            wf.rel,
            f"reusable workflow `{reusable}` does not exist",
            "restore it or fix the archetype",
        )


def _normalize_dist(name: str) -> str:
    """PEP 503 normalization, so `platform_db` and `platform-db` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _python_member_manifest(member: Member) -> dict | None:
    manifest = REPO_ROOT / member.path / "pyproject.toml"
    if not manifest.exists():
        return None
    with manifest.open("rb") as fh:
        return tomllib.load(fh)


def rule_workspace_deps_are_packaged(report: Report, members: list[Member]) -> None:
    """A member that depends on a workspace lib must ship that lib with it.

    Workspace libs are not published to PyPI, so a dependency edge onto one is
    only sound while every shipped artifact bundles the lib. Two failure modes
    this makes loud:

    * a PyPI-published member (publish calls ``_publish-pypi``) gaining a
      workspace dependency -- the published wheel would be uninstallable, or
      worse, pip would resolve the internal name from the public index
      (dependency confusion);
    * a container-published member whose Dockerfile does not install the lib,
      or whose publish caller still builds from the member-only context where
      the lib is not even inside the build context.
    """
    by_dist: dict[str, Member] = {}
    deps_of: dict[str, set[str]] = {}
    for member in members:
        manifest = _python_member_manifest(member)
        if manifest is None:
            continue
        project = manifest.get("project", {})
        dist = _normalize_dist(project.get("name", ""))
        by_dist[dist] = member
        deps_of[dist] = {
            _normalize_dist(re.split(r"[\s<>=!~;\[]", dep, 1)[0])
            for dep in project.get("dependencies", [])
        }

    def workspace_closure(dist: str) -> set[str]:
        seen: set[str] = set()
        frontier = [dist]
        while frontier:
            for dep in deps_of.get(frontier.pop(), set()):
                if dep in by_dist and dep not in seen:
                    seen.add(dep)
                    frontier.append(dep)
        return seen

    for dist, member in by_dist.items():
        ws_deps = workspace_closure(dist)
        if not ws_deps:
            continue
        publish = WORKFLOW_DIR / f"{member.slug}.publish.yml"
        if not publish.exists():
            continue  # nothing shipped, nothing to bundle
        publish_text = publish.read_text(encoding="utf-8")
        publish_rel = f".github/workflows/{publish.name}"

        if "_publish-pypi" in publish_text:
            libs = ", ".join(sorted(by_dist[d].path for d in ws_deps))
            report.fail(
                f"{member.path}/pyproject.toml",
                f"published to PyPI but depends on workspace lib(s) {libs}, which are not on "
                "PyPI -- the wheel would be uninstallable, or pip would resolve the internal "
                "name from the public index",
                "drop the dependency, vendor the code, or publish the lib to PyPI first",
            )
            continue

        if "_publish-container" in publish_text:
            if not re.search(r"^\s*context:\s*['\"]?\.['\"]?\s*$", publish_text, re.M):
                report.fail(
                    publish_rel,
                    f"{member.path} depends on workspace lib(s) but the container build does "
                    "not use the repo-root context, so the libs are outside the build context",
                    "pass `context: .` to _publish-container.yml",
                )
            dockerfiles = sorted((REPO_ROOT / member.path).glob("Dockerfile*"))
            if not dockerfiles:
                report.fail(
                    publish_rel,
                    f"{member.path} publishes a container but has no Dockerfile",
                    "add one that installs the member and its workspace libs",
                )
            for dockerfile in dockerfiles:
                text = dockerfile.read_text(encoding="utf-8")
                for dep in sorted(ws_deps):
                    dep_path = by_dist[dep].path
                    if dep_path not in text:
                        report.fail(
                            f"{member.path}/{dockerfile.name}",
                            f"member depends on {dep_path} but this Dockerfile never installs "
                            "it -- the image build would resolve the internal name from PyPI",
                            f"COPY {dep_path} into the image and pip install it before the member",
                        )


# ---------------------------------------------------------------------------


def main() -> int:
    root = REPO_ROOT
    members = discover_members(root)
    workflows = {wf.slug: wf for wf in load_quality_workflows(WORKFLOW_DIR)}
    report = Report()

    rule_no_platform_package(report, root)
    rule_every_member_has_a_caller(report, members, workflows)
    rule_no_orphan_callers(report, members, workflows)
    rule_workspace_deps_are_packaged(report, members)

    for member in members:
        wf = workflows.get(member.slug)
        if wf is None:
            continue  # already reported
        rule_triggers_agree(report, wf)
        rule_required_paths_present(report, wf, member)
        rule_patterns_resolve(report, wf, root)
        rule_gate_job(report, wf, member)
        rule_with_path_matches(report, wf, member)
        rule_reusable_exists(report, wf, member)

    if members:
        print(f"Checked {len(members)} member(s):")
        for m in members:
            status = "ok" if m.slug in workflows else "NO WORKFLOW"
            print(f"  {m.path:<40} {m.archetype:<8} {status}")
    else:
        print("No members yet -- nothing to check.")
        print("Add one with: just new-service <libs|apps|services> <path>")

    report.emit()
    if report.ok:
        print("\nAll path filters are consistent.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

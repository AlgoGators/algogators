"""Behavioural tests for the monorepo's path-filtered CI.

``check_workflow_paths.py`` already verifies that each member's filters are
*internally consistent*. These tests cover the layer above it: that the filters
produce the right **runs** for a given changed-file set, and that the structural
invariants the checker assumes still hold.

The regression that motivated most of this file: ``paths`` fires when *any*
changed file matches, while ``paths-ignore`` fires when *any* changed file does
*not* match. Two byte-identical lists therefore do not partition the input, so a
mixed PR (member file + README) once triggered a member's real workflow *and*
its no-op ``.skip.yml`` twin, both publishing the same check name. The skip
workflows are gone; ``test_no_skip_workflows`` keeps them gone, and
``test_mixed_pr_fires_exactly_one_workflow`` asserts the behaviour their removal
bought.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
sys.path.insert(0, str(REPO_ROOT / "platform" / "ci"))

from _common import (  # noqa: E402
    MEMBER_ROOTS,
    any_match,
    discover_members,
    load_quality_workflows,
    path_matches,
)

MEMBERS = discover_members(REPO_ROOT)
QUALITY = {wf.slug: wf for wf in load_quality_workflows(WORKFLOWS)}


def workflow_docs(pattern: str) -> list[tuple[Path, dict]]:
    out = []
    for file in sorted(WORKFLOWS.glob(pattern)):
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        out.append((file, data))
    return out


def triggers(data: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True.
    return data.get("on") or data.get(True) or {}


# ---------------------------------------------------------------------------
# The core promise: a member runs only when its own inputs change
# ---------------------------------------------------------------------------


def test_members_were_discovered() -> None:
    """Guard against the whole suite silently passing on an empty member list."""
    assert MEMBERS, "no members discovered; the rest of this file would be vacuous"
    assert set(QUALITY) == {m.slug for m in MEMBERS}


@pytest.mark.parametrize("member", MEMBERS, ids=lambda m: m.slug)
def test_member_change_fires_its_own_workflow(member) -> None:
    changed = [f"{member.path}/some_source_file.py"]
    assert any_match(changed, QUALITY[member.slug].pr_paths)


@pytest.mark.parametrize("member", MEMBERS, ids=lambda m: m.slug)
def test_member_change_does_not_fire_unrelated_members(member) -> None:
    """The actual ask: CI must not run for members whose code did not change.

    Members legitimately depend on each other (a service lists the libs it
    consumes), so a dependent firing is correct. Anything that does *not*
    declare the changed path must stay silent.
    """
    changed = [f"{member.path}/some_source_file.py"]
    for other in MEMBERS:
        if other.slug == member.slug:
            continue
        if any_match(changed, QUALITY[other.slug].pr_paths):
            # Only acceptable if `other` explicitly declares a dependency on it.
            declared = [p for p in QUALITY[other.slug].pr_paths if p.startswith(member.path)]
            assert declared, (
                f"{other.slug} fires for a change in {member.path} "
                f"without declaring it as an input"
            )


def test_docs_only_change_fires_no_member() -> None:
    changed = ["README.md", "docs/architecture.md", "CONTRIBUTING.md"]
    fired = [slug for slug, wf in QUALITY.items() if any_match(changed, wf.pr_paths)]
    assert fired == []


def test_unrelated_member_file_does_not_fire_everything() -> None:
    changed = ["apps/website/site/index.html"]
    fired = [slug for slug, wf in QUALITY.items() if any_match(changed, wf.pr_paths)]
    assert fired == ["app-website"], fired


# ---------------------------------------------------------------------------
# The mixed-PR regression
# ---------------------------------------------------------------------------


def test_no_skip_workflows() -> None:
    """The no-op twins must stay deleted.

    They published the same check name as the real gate job, so on a mixed PR
    both ran and the trivial one usually reported first.
    """
    assert list(WORKFLOWS.glob("*.skip.yml")) == []


def test_mixed_pr_fires_exactly_one_workflow() -> None:
    """A PR touching one member plus an unowned file runs that member once."""
    changed = ["apps/algoterminal/main.py", "README.md"]
    fired = [slug for slug, wf in QUALITY.items() if any_match(changed, wf.pr_paths)]
    assert fired == ["app-algoterminal"], fired


def test_paths_and_paths_ignore_do_not_partition() -> None:
    """Documents *why* the twin-workflow pattern was unsound.

    This is the semantics that made byte-identical `paths` / `paths-ignore`
    lists both fire. If a future change reintroduces a `paths-ignore` companion,
    this is the behaviour it will run into.
    """
    paths = ["apps/algoterminal/**"]
    changed = ["apps/algoterminal/main.py", "README.md"]

    fires_on_paths = any_match(changed, paths)
    # `paths-ignore` triggers when ANY changed file fails to match the list.
    fires_on_paths_ignore = any(not path_matches(f, paths) for f in changed)

    assert fires_on_paths and fires_on_paths_ignore


def test_gate_names_are_unique() -> None:
    """One check name must map to exactly one workflow.

    Duplicate names are indistinguishable to branch protection, which is what
    made the skip twins dangerous rather than merely wasteful.
    """
    names = [wf.gate_name for wf in QUALITY.values() if wf.gate_name]
    assert len(names) == len(set(names)), "duplicate gate names"


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("member", MEMBERS, ids=lambda m: m.slug)
def test_pr_and_push_filters_are_identical(member) -> None:
    """Divergence means a green PR can become a red main."""
    wf = QUALITY[member.slug]
    assert wf.pr_paths and wf.pr_paths == wf.push_paths


@pytest.mark.parametrize("member", MEMBERS, ids=lambda m: m.slug)
def test_quality_workflow_filters_both_events(member) -> None:
    file = WORKFLOWS / f"{member.slug}.quality.yml"
    on = triggers(yaml.safe_load(file.read_text(encoding="utf-8")))
    assert on.get("pull_request", {}).get("paths")
    assert on.get("push", {}).get("paths")


def test_reusable_workflows_are_call_only() -> None:
    """A stray `on: push` in a reusable runs unfiltered and is invisible."""
    for file, data in workflow_docs("_*.yml"):
        assert set(triggers(data)) == {"workflow_call"}, file.name


def test_publish_workflows_are_tag_scoped() -> None:
    """Publishing must never happen on a branch push."""
    for file, data in workflow_docs("*.publish.yml"):
        on = triggers(data)
        assert "pull_request" not in on, file.name
        push = on.get("push") or {}
        assert push.get("tags"), f"{file.name} publishes without a tag filter"
        assert "branches" not in push, f"{file.name} publishes on a branch push"


def test_actionlint_is_path_filtered() -> None:
    """actionlint owns .github/ only, so it is filtered like any member."""
    on = triggers(yaml.safe_load((WORKFLOWS / "meta-actionlint.yml").read_text(encoding="utf-8")))
    assert on["pull_request"]["paths"] == [".github/workflows/**", ".github/actions/**"]


def test_workflow_paths_check_stays_unfiltered() -> None:
    """Deliberately unfiltered: it exists to catch a new member with no workflow.

    A path filter on the member directories would defeat its entire purpose --
    the missing workflow is exactly the failure it detects.
    """
    on = triggers(yaml.safe_load((WORKFLOWS / "meta-workflow-paths.yml").read_text("utf-8")))
    assert "paths" not in (on.get("pull_request") or {})


def test_security_scanning_stays_repo_wide() -> None:
    """A vulnerable transitive dependency is not scoped to a directory."""
    on = triggers(yaml.safe_load((WORKFLOWS / "security.yml").read_text(encoding="utf-8")))
    assert "paths" not in (on.get("pull_request") or {})


def test_every_member_has_quality_and_publish_workflows() -> None:
    """Platform members are workspace-internal: gated, but never published."""
    for member in MEMBERS:
        assert (WORKFLOWS / f"{member.slug}.quality.yml").exists(), member.slug
        publish_exists = (WORKFLOWS / f"{member.slug}.publish.yml").exists()
        if member.tier == "platform":
            assert not publish_exists, f"{member.slug} must not publish"
        else:
            assert publish_exists, member.slug


def test_no_workflow_references_a_missing_member_directory() -> None:
    known = {m.slug for m in MEMBERS}
    for file in sorted(WORKFLOWS.glob("*.quality.yml")):
        assert file.name.removesuffix(".quality.yml") in known, file.name


def test_member_roots_are_covered() -> None:
    """Every manifest-bearing directory under the member roots is a member."""
    for root in MEMBER_ROOTS:
        directory = REPO_ROOT / root
        if not directory.is_dir():
            continue
        for manifest in ("pyproject.toml", "package.json", "CMakeLists.txt"):
            for found in directory.glob(f"*/{manifest}"):
                rel = found.parent.relative_to(REPO_ROOT).as_posix()
                assert any(m.path == rel for m in MEMBERS), f"{rel} is not a discovered member"

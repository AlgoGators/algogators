# 0015. Path-filtered CI with no skip twins, guarded by a checker

- **Status:** Accepted
- **Date:** 2026-08-18
- **Sources:** #3; commits `5fb2d0eb`, `eb3e24c4`; `platform/ci/check_workflow_paths.py`

## Context

`paths` and `paths-ignore` lists do not split a change cleanly: a PR touching a member file and a README ran both a real workflow and its no-op `*.skip.yml` twin under the same check name, and the no-op one usually reported first.

## Decision

The skip twins were deleted. A member whose files are untouched runs no workflow at all. An unfiltered `meta-workflow-paths.yml` checks on every PR that the filters still match the tree.

## Consequences

Run `just check-paths` after moving files or adding a dependency on shared code.

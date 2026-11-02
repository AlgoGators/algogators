# 0011. Workspace libraries ship inside their consumers, never on PyPI

- **Status:** Accepted
- **Date:** 2026-08-15
- **Sources:** commits `f5a52b8b`, `5a2806d3`; `services/research-api/Dockerfile`

## Context

A Python member that depends on a workspace library cannot be installed from PyPI on its own, and pip could resolve the internal name from the public index instead (dependency confusion).

## Decision

A member published to PyPI may not depend on a workspace library. A container member that uses one builds from the repository root and copies every workspace library it uses.

## Consequences

`rule_workspace_deps_are_packaged` in `platform/ci/check_workflow_paths.py` enforces it on every PR.

# Root task runner. CI calls these recipes; so should you.
#
# The contract between CI and a service:
#
#   * The reusable workflow owns the ENVIRONMENT (checkout, toolchain, cache,
#     matrix, artifact upload, the pass/fail gate).
#   * This justfile owns the DEFAULT COMMANDS for each archetype.
#   * A service's own `<service>/justfile` overrides any recipe it needs to,
#     and only that recipe.
#
# That last layer is the escape hatch. When data-ngin needs Postgres up before
# pytest, it defines `test:` in services/data-ngin/justfile. Nothing is added to
# the shared workflow, so the next service that needs Kafka does not inherit a
# dead `needs-postgres:` input.

set shell := ["bash", "-uc"]
set windows-shell := ["C:/Program Files/Git/bin/bash.exe", "-uc"]

root := justfile_directory()

_default:
    @just --list --unsorted

# ---------------------------------------------------------------------------
# Dispatch: run a service's own recipe if it defines one, else the default.
# ---------------------------------------------------------------------------

# `just --show RECIPE` exits non-zero when the recipe is absent, which is how we
# detect an override without parsing the service's justfile.
_dispatch path recipe *args:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -f "{{ root }}/{{ path }}/justfile" ] \
       && just --justfile "{{ root }}/{{ path }}/justfile" --show "{{ recipe }}" >/dev/null 2>&1; then
        just --justfile "{{ root }}/{{ path }}/justfile" \
             --working-directory "{{ root }}/{{ path }}" "{{ recipe }}" {{ args }}
    else
        just "_default-{{ recipe }}" "{{ path }}" {{ args }}
    fi

# ---------------------------------------------------------------------------
# The three gates every service must answer to. CI calls exactly these.
# ---------------------------------------------------------------------------

lint path:
    @just _dispatch "{{ path }}" lint

typecheck path:
    @just _dispatch "{{ path }}" typecheck

test path:
    @just _dispatch "{{ path }}" test

# Everything, the way CI runs it. Use before pushing.
quality path:
    @just lint "{{ path }}"
    @just typecheck "{{ path }}"
    @just test "{{ path }}"

# ---------------------------------------------------------------------------
# Python defaults.
#
# Run from the repo root with an explicit path, never from inside the service.
# ruff walks up to find the root config either way, but mypy does not — running
# from the root is what lets every member share one [tool.mypy] block.
# ---------------------------------------------------------------------------

_default-lint path:
    uv run ruff check "{{ path }}"
    uv run ruff format --check "{{ path }}"

_default-typecheck path:
    uv run mypy {{ if env("MYPY_STRICT", "false") == "true" { "--strict" } else { "" } }} "{{ path }}"

_default-test path:
    uv run pytest "{{ path }}" \
        --cov="{{ path }}" \
        --cov-report=xml:"{{ path }}/coverage.xml" \
        --cov-report=term-missing \
        --cov-fail-under="${COVERAGE_MIN:-0}"

# ---------------------------------------------------------------------------
# Node defaults. A JS member overrides these in its own justfile only if it
# needs something beyond the package.json scripts.
# ---------------------------------------------------------------------------

_default-node-lint path:
    corepack pnpm --filter "./{{ path }}" run lint

_default-node-typecheck path:
    corepack pnpm --filter "./{{ path }}" run typecheck

_default-node-test path:
    corepack pnpm --filter "./{{ path }}" run test

# ---------------------------------------------------------------------------
# Repo-wide.
# ---------------------------------------------------------------------------

# Regenerate pydantic models and C++ headers from platform/contracts/schemas.
# Generated output is committed; CI fails if running this dirties the tree.
contracts:
    uv run python platform/contracts/codegen/generate.py

# Verify every service's workflow path filters still match reality.
check-paths:
    uv run python platform/ci/check_workflow_paths.py

# Generate the quality + publish workflow pair for a new service.
new-service kind name:
    uv run python platform/ci/new_service.py --kind "{{ kind }}" --name "{{ name }}"

# Install everything a fresh clone needs.
bootstrap:
    uv sync --all-groups
    corepack pnpm install --frozen-lockfile

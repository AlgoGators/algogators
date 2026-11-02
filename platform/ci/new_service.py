#!/usr/bin/env python
"""Generate the workflow triple for a monorepo member."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    ALWAYS_REQUIRED,
    REPO_ROOT,
    WORKFLOW_DIR,
    Member,
    discover_members,
    uses_contracts,
)

DEFAULTS: dict[str, dict[str, str | int]] = {
    "python": {
        "python_versions": '["3.11"]',
        "runners": '["ubuntu-latest"]',
        "coverage": 60,
        "strict": "false",
        "build": "false",
    },
    "node": {
        "node_versions": '["20"]',
        "runners": '["ubuntu-latest"]',
        "coverage": 50,
        "bundle": 500,
    },
    "cpp": {
        "builds": '[{"preset":"ci-gcc13","os":"ubuntu-latest"}]',
        "sanitizers": "[]",
    },
}


def normalize_path(kind: str, name: str) -> str:
    raw = name.replace("\\", "/").strip("/")
    if raw.startswith(f"{kind}/"):
        return raw
    return f"{kind}/{raw}"


def positive_paths(member: Member) -> list[str]:
    paths = [member.own_glob, *ALWAYS_REQUIRED[member.archetype]]
    paths.append(f".github/workflows/{member.slug}.quality.yml")
    if uses_contracts(member, REPO_ROOT):
        paths.append("platform/contracts/**")
    return paths


def yaml_list(items: list[str], indent: int = 6) -> str:
    pad = " " * indent
    return "\n".join(f"{pad}- '{item}'" for item in items)


def gate(member: Member) -> str:
    return f"""  gate:
    name: {member.gate_name}
    if: always()
    needs: [quality]
    runs-on: ubuntu-latest
    steps:
      - name: Report result
        run: |
          if [ "${{{{ needs.quality.result }}}}" != "success" ]; then
            echo "::error::quality result was ${{{{ needs.quality.result }}}}"
            exit 1
          fi
"""


def quality_python(member: Member, paths: list[str]) -> str:
    d = DEFAULTS["python"]
    return f"""name: {member.slug} · quality

on:
  pull_request:
    paths:
{yaml_list(paths)}
  push:
    branches: [main]
    paths:
{yaml_list(paths)}
  workflow_dispatch:

concurrency:
  group: {member.slug}-quality-${{{{ github.ref }}}}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  quality:
    uses: ./.github/workflows/_quality-python.yml
    with:
      path: {member.path}
      python-versions: '{d["python_versions"]}'
      runners: '{d["runners"]}'
      coverage-min: {d["coverage"]}
      strict-types: {d["strict"]}
      build-check: {d["build"]}
    secrets: inherit

{gate(member)}"""


def quality_node(member: Member, paths: list[str]) -> str:
    d = DEFAULTS["node"]
    return f"""name: {member.slug} · quality

on:
  pull_request:
    paths:
{yaml_list(paths)}
  push:
    branches: [main]
    paths:
{yaml_list(paths)}
  workflow_dispatch:

concurrency:
  group: {member.slug}-quality-${{{{ github.ref }}}}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  quality:
    uses: ./.github/workflows/_quality-node.yml
    with:
      path: {member.path}
      node-versions: '{d["node_versions"]}'
      runners: '{d["runners"]}'
      coverage-min: {d["coverage"]}
      bundle-budget-kb: {d["bundle"]}
    secrets: inherit

{gate(member)}"""


def quality_cpp(member: Member, paths: list[str]) -> str:
    d = DEFAULTS["cpp"]
    return f"""name: {member.slug} · quality

on:
  pull_request:
    paths:
{yaml_list(paths)}
  push:
    branches: [main]
    paths:
{yaml_list(paths)}
  workflow_dispatch:

concurrency:
  group: {member.slug}-quality-${{{{ github.ref }}}}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  quality:
    uses: ./.github/workflows/_quality-cpp.yml
    with:
      path: {member.path}
      builds: '{d["builds"]}'
      sanitizers: '{d["sanitizers"]}'
    secrets: inherit

{gate(member)}"""


def publish(member: Member) -> str:
    tag = member.name.removeprefix("@algogators/").replace("_", "-")
    if member.tier == "libs" or member.path == "apps/algoterminal":
        return publish_pypi(member, tag)
    if member.archetype == "node":
        return publish_static(member, tag)
    return publish_container(member, tag)


def publish_pypi(member: Member, tag: str) -> str:
    return f"""name: {member.slug} · publish

on:
  push:
    tags: ['{tag}/v*']
  workflow_dispatch:
    inputs:
      dry-run:
        type: boolean
        default: true

permissions:
  contents: read

jobs:
  quality:
    uses: ./.github/workflows/_quality-python.yml
    with:
      path: {member.path}
      coverage-min: {85 if member.tier == "libs" else 65}
      build-check: true
    secrets: inherit

  publish:
    needs: quality
    uses: ./.github/workflows/_publish-pypi.yml
    with:
      path: {member.path}
      environment: pypi
      dry-run: ${{{{ inputs.dry-run || false }}}}
    permissions:
      contents: read
      id-token: write
      attestations: write
"""


def publish_static(member: Member, tag: str) -> str:
    return f"""name: {member.slug} · publish

on:
  push:
    tags: ['{tag}/v*']
  workflow_dispatch:
    inputs:
      dry-run:
        type: boolean
        default: true

permissions:
  contents: read

jobs:
  quality:
    uses: ./.github/workflows/_quality-node.yml
    with:
      path: {member.path}
      coverage-min: 50
      bundle-budget-kb: 500
    secrets: inherit

  publish:
    needs: quality
    uses: ./.github/workflows/_publish-static-site.yml
    with:
      path: {member.path}
      target: pages
      dry-run: ${{{{ inputs.dry-run || false }}}}
    permissions:
      contents: read
      pages: write
      id-token: write
"""


def publish_container(member: Member, tag: str) -> str:
    image = f"algogators/{tag}"
    env = f"{tag}-production"
    return f"""name: {member.slug} · publish

on:
  push:
    tags: ['{tag}/v*']
  workflow_dispatch:
    inputs:
      dry-run:
        type: boolean
        default: true
      deploy:
        type: boolean
        default: false

permissions:
  contents: read

jobs:
  quality:
    uses: ./.github/workflows/_quality-{member.archetype}.yml
    with:
      path: {member.path}
    secrets: inherit

  image:
    needs: quality
    uses: ./.github/workflows/_publish-container.yml
    with:
      path: {member.path}
      image: {image}
      environment: ghcr
      push: ${{{{ !(inputs.dry-run || false) }}}}
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    secrets: inherit

  deploy:
    if: ${{{{ !inputs.dry-run && (github.event_name == 'push' || inputs.deploy) }}}}
    needs: image
    uses: ./.github/workflows/_deploy-ssh.yml
    with:
      environment: {env}
      image: {image}
      digest: ${{{{ needs.image.outputs.digest }}}}
      remote-path: /opt/algogators/{tag}
      service-name: {tag}
    secrets: inherit
"""


QUALITY_RENDERERS = {
    "python": quality_python,
    "node": quality_node,
    "cpp": quality_cpp,
}


def write(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; pass --force to overwrite")
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["libs", "apps", "services", "platform"], required=True)
    parser.add_argument("--name", required=True, help="member name or repo-relative path")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    member_path = normalize_path(args.kind, args.name)
    members = {m.path: m for m in discover_members(REPO_ROOT)}
    member = members.get(member_path)
    if member is None:
        print(
            f"{member_path} is not a discoverable member; add pyproject.toml, package.json, or CMakeLists.txt first",
            file=sys.stderr,
        )
        return 2

    paths = positive_paths(member)
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    write(
        WORKFLOW_DIR / f"{member.slug}.quality.yml",
        QUALITY_RENDERERS[member.archetype](member, paths),
        args.force,
    )
    if member.tier == "platform":
        # Platform members are workspace-internal: gated by quality, never
        # published anywhere.
        print(f"skipping publish workflow: {member.path} is a platform member")
        return 0
    write(WORKFLOW_DIR / f"{member.slug}.publish.yml", publish(member), args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

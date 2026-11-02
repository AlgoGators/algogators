# CI and deploys

**LAST UPDATED:** 2026-11-02 · **Commit:** `8c534c1b` (main, the code this page was checked against)

How GitHub Actions is laid out for the whole repository. How the engine itself deploys, its image
and its build cache are in [operations.md](../operations.md) and
[decision 0007](../decisions/0007-prod-deploys-mains-image.md); this page does not repeat them.

## Naming

Every workflow in `.github/workflows/` is `<kind>-<name>.<purpose>.yml`:

| Part | Values in use |
|---|---|
| kind | `app`, `lib`, `svc`, `platform` (from `apps/`, `libs/`, `services/`, `platform/`), `meta` for checks on CI itself |
| purpose | `quality` (checks), `publish` (tag-scoped release), `deploy` (from `prod`); trade-ngin adds `python`, `rpc`, `watchdog` |
| `_*.yml` | reusable, `workflow_call` only, never triggered directly |

`security.yml` is the one exception: repository-wide, no member. The quality workflow's gate job is
named `<slug> quality` (for example `app-website quality`).

## Path-filtered quality, and the guard

A member's `quality` workflow runs only when its own files change. Its `paths:` list is the only
declaration of what the member owns (`platform/ci/_common.py`), so it must stay true.

- **`meta-workflow-paths.yml`** runs `platform/ci/check_workflow_paths.py` and
  `tests/test_ci_path_filters.py` on every PR and push to `main`. It is not path-filtered on
  purpose: a new member directory with no workflow is invisible to a path filter.
- The checker fails when: a member has no caller, or a caller has no member; `pull_request.paths`
  and `push.paths` differ; a required path is missing (the member's own directory, its toolchain
  files, `platform/contracts/**` if it uses the contracts); a pattern matches nothing; the gate job
  is missing; a workspace lib a member depends on is not shipped with it; `platform/` gains an
  `__init__.py` (it would shadow Python's `platform`).
- Toolchain files always required: Python `uv.lock`, `pyproject.toml`, `_quality-python.yml`; Node
  `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `_quality-node.yml`; C++ `vcpkg.json`,
  `CMakePresets.json`, `_quality-cpp.yml`.
- Locally: `just check-paths` runs both. `just new-service <kind> <name>` writes a new member's
  quality and publish workflows (quality only for `platform/`); the member needs its manifest
  first (`CONTRIBUTING.md`).
- **`meta-actionlint.yml`** lints workflow syntax, filtered on `.github/workflows/**`.
- A `uv.lock` change fans out to every Python member, so `_quality-python.yml` first runs
  `platform/ci/quality_needed.py`: the matrix runs only if the member's resolved dependencies
  changed. It fails open (#17).

## Reusable workflows

| Workflow | Does |
|---|---|
| `_quality-python.yml` | `uv sync --locked`, then `just lint|typecheck|test <path>`; matrix over OS and Python; coverage floor, optional `mypy --strict` and wheel build |
| `_quality-node.yml` | pnpm, the member's `lint`, `typecheck`, `test`, `build` scripts; gzipped bundle budget; optional Playwright |
| `_quality-cpp.yml` | CMake presets from the root `CMakePresets.json` (not `just`); explicit `{preset, os}` pairs; optional sanitizers and clang-tidy |
| `_publish-container.yml` | build and push to `ghcr.io`, provenance attestation; outputs the digest, which deploys use instead of a tag |
| `_publish-pypi.yml` | build, check the wheel, attest, upload with PyPI trusted publishing (OIDC, no token); `dry-run` and TestPyPI inputs |
| `_publish-static-site.yml` | build a front end; `target: pages` or `s3` with CloudFront (OIDC role; no caller uses `s3`) |
| `_deploy-ssh.yml` | SSH to a box, log in to GHCR, then either fast-forward a sparse checkout to the built commit and run the member's `deploy.sh`, or `docker compose pull` and `up` |

The split is in the root `justfile`: the reusable workflow owns the environment, the member's
justfile or `package.json` owns the commands. A member that needs, say, Postgres for its tests
overrides `test` in its own justfile; nothing is added to the shared workflow.

## Releases and tags

Each member releases on its own tag `<name>/vX.Y.Z` through its `publish` workflow; the tag
glob matches exactly one workflow (`docs/runbooks/website-deploy.md`). Tags pushed so far:
`website/v*` (nine) and `trade-ngin/v0.1.1`. The `tradengin` wheel uses its own prefix
`tradengin/v*` ([decision 0005](../decisions/0005-tradengin-releases.md)).

`release-please-config.json` and `.release-please-manifest.json` at the root describe ten
packages with `/` as the tag separator, but no workflow runs release-please. The manifest's
versions are not kept up to date; tags are cut by hand.

## security.yml

Runs on every PR, every push to `main`, Mondays 07:00 UTC and by hand. No path filter: a
vulnerable dependency is a repository problem.

- **CodeQL** (`security-and-quality`) for the languages found under `libs/`, `apps/`,
  `services/`. On push and PR it skips languages the diff did not touch; the Monday run scans all.
  C++ is buildless (`build-mode: none`): autobuild cannot provide the vcpkg tree (`060245d9`).
  `cpp/missing-return` is excluded, since buildless extraction reports it falsely (`5ef0bb36`).
- **Dependency review** on PRs: fails on high severity, denies GPL-3.0, AGPL-3.0, SSPL-1.0.
- **SBOM** (CycloneDX) on pushes, kept as an artifact for 90 days.
- **Scorecard** was removed on 2026-08-24: it pointed at a tag that did not exist and graded
  things the repository does not act on (`83d4df3a`).

## The `prod` branch

`prod` is a deploy branch, not a release: a fast-forward of `main`. Three workflows deploy from
it, each only when its own paths changed:

| Workflow | Deploys | Paths | SSH secrets | Environment |
|---|---|---|---|---|
| `app-algolens.deploy.yml` | AlgoLens API and web images, `infra/algolens/deployment/deploy.sh` | `services/research-api`, `apps/algolens-web`, `libs/research-core`, `platform/db`, `infra/algolens`, its workflows | `ALGOLENS_SSH_*` | `algolens-production` |
| `svc-data-ngin.deploy.yml` | the Airflow image, after checking the DAGs import inside it | `services/data-ngin/**`, `platform/db/**` | `SSH_*` | none |
| `svc-trade-ngin.deploy.yml` | engine-rpc (see operations.md) | `services/trade-ngin/**` minus docs | `SSH_*` | none |

Each uses its own checkout directory on the box and a concurrency group that never cancels a
deploy mid-roll. Tag publishes do not deploy trade-ngin (`svc-trade-ngin.publish.yml`).

## Rules on `main`

No branch protection; one ruleset, "AI Review", on `refs/heads/main` (read with
`gh api repos/AlgoGators/algogators/rules/branches/main`): no deletion, no force push, a PR with
two approvals and code-owner review, CodeQL results (high security alerts, errors) and code
quality. **No status check is required**, so GitHub does not wait for the gate jobs; waiting is a
human rule (#179). Organisation admins bypass the ruleset. `prod` has no rules.

## Environments, secrets, variables

Names only; values live in GitHub settings.

- **Environments** on GitHub: `github-pages` (deploys from `main` and `website/v*` only),
  `algolens-production` (no protection rules). Named in workflows but not created yet, since GitHub
  creates one on first use: `pypi`, `ghcr`, `data-ngin-production`, `research-api-production`.
- **Repository secrets:** `SSH_HOST`, `SSH_USER`, `SSH_KEY` (data-ngin and trade-ngin deploys);
  `ALGOLENS_SSH_HOST`, `ALGOLENS_SSH_USER`, `ALGOLENS_SSH_KEY`; `DB_HOST`, `DB_PORT`, `DB_NAME`,
  `DB_USER`, `DB_PASSWORD` (the watchdog); `PYPI_API_TOKEN` (`tradengin` upload). `AWS_ROLE_ARN` is
  declared by `_publish-static-site.yml` but not set.
- **Variables:** `PYPI_PROJECT`. `ALGOLENS_PUBLIC_URL` is read but unset; the workflow's default
  applies.

## Decisions

- **2026-08-18 · A member whose files are untouched runs nothing.** The `*.skip.yml` twins that
  reported a passing check under the same name were deleted: `paths` and `paths-ignore` lists do
  not partition a change, so a mixed PR ran both (#3, `5fb2d0eb`, tests in `eb3e24c4`).
- **2026-08-15 · Workspace libs ship inside their consumers.** They are not on PyPI; a published
  wheel depending on one would be uninstallable or open to dependency confusion (`f5a52b8b`).
- **2026-10-08 · Production deploys from `prod`, tags stay releases.** First data-ngin (#16), then
  AlgoLens (#60) and trade-ngin (#120).
- **2026-10-22 · A deploy's paths list code, not workflow files**, so a change to a shared
  workflow does not redeploy every service by itself (#61, #62).

## Gotchas

- **`ubuntu-latest` moved to Ubuntu 26.04** and broke the vcpkg build; the gcc 13 builds pin
  `ubuntu-24.04` (#99, `65464d29`).
- **`grep -q` in a pipe under `pipefail` misses on large diffs** (SIGPIPE). CodeQL skipped every
  language on the trade-ngin import (#98) until a here-string replaced the pipe (`d85660ed`).
- **A `CMakeLists.txt` member with a `pyproject.toml` is C++** to the path checker (`1b843c33`).
- **`data-ngin/v*` and `research-api/v*` tag pushes run a deploy job**: the condition is
  `github.event_name == 'push' || inputs.deploy`, to an environment and a box path that differ
  from the `prod` deploys. No such tag exists yet; read the publish workflow before pushing one.
- **`app-algolens-web.publish.yml` also targets Pages**, and this repository's Pages site is
  algogators.com. An `algolens-web/v*` tag is refused by the `github-pages` environment (only
  `main` and `website/v*`), but Run workflow on `main` with `dry-run` unticked would replace the
  website with AlgoLens. AlgoLens ships from `prod` instead.
- **CODEOWNERS names teams that do not exist** (34 errors from
  `gh api repos/AlgoGators/algogators/codeowners/errors`); an unresolved owner is ignored silently.
- **Run workflow on a publish or deploy defaults to `dry-run: true`.** Untick it to ship.

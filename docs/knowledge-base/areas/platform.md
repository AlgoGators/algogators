# Platform and workspace

**LAST UPDATED:** 2026-11-02 · **Commit:** `8c534c1b` (main, the code this page was checked against)

The shared plumbing: `platform/`, the root Python and Node workspaces, the `justfile`, the C++
presets, the root `tests/` and how a new member is added. CI has its own page; only the parts that
live in `platform/ci` are here.

| What | State | Where |
|---|---|---|
| Shared schemas and generated bindings | placeholder: no schema yet | `platform/contracts/` |
| Shared reference data | placeholder: a README only | `platform/configs/` |
| Database config, access gate, people schema | real; a workspace member | `platform/db/` |
| CI generator and path checks | real | `platform/ci/`, root `tests/` |
| Python workspace | 7 members, one `uv.lock` | root `pyproject.toml` |
| Node workspace | `apps/algolens-web`, `apps/website` | `pnpm-workspace.yaml`, `package.json` |
| Task runner | the commands CI runs | root `justfile` |
| C++ presets and dependencies | used by trade-ngin | `CMakePresets.json`, `vcpkg.json` |

`platform/` must never become an importable package: a `platform/__init__.py` would shadow Python's
own `platform` module, and `check_workflow_paths.py` fails if one appears (`rule_no_platform_package`).

## contracts and configs

The plan (`platform/README.md`): data shapes that cross a service boundary are written once as YAML
under `contracts/schemas/`, and generated into pydantic models and C++ headers under
`contracts/codegen/generated/`, committed so a C++ build needs no Python. `just contracts` runs
`codegen/generate.py`, which today is a placeholder that only keeps the output folders. `configs/`
is meant for calendars, venue metadata and instrument definitions; nothing reads it yet. Trading
calendars as data are being designed in #30.

- A member that mentions the contracts in its source must list `platform/contracts/**` in its path
  filters. The checker finds this by scanning file contents, because no manifest records the edge
  (`uses_contracts` in `platform/ci/_common.py`).
- The README says CI fails when `just contracts` dirties the tree. No workflow runs it yet.

## platform/db

**What.** The `platform-db` package (`platform/db/README.md`):
- `DatabaseConfig`: reads `DB_*` from the environment, validates them, builds an escaped DSN and
  hides the password in its `repr`. Used by research-api and data-ngin.
- The access gate (`platform_db.gate`): a service asks an `AccessGate` for credentials with an
  `AccessRequest`. Today `EnvAccessGate` allows everything and returns the `DB_*` credentials; the
  seam exists so an IAM-backed gate can replace it without touching call sites (`2ed574f2`).
- `migrations/`: plain SQL, applied by hand (pgAdmin or `psql`), never by CI or a deploy.
- `platform_db.people_import`: loads a recruiting-form `.xlsx` export and the member roster CSV into
  the `people` schema, one `import_batch` a run; `--dry-run` runs every write and rolls back.

**The people schema** (`migrations/001_people_schema.sql`; the design is argued in
`docs/people-schema-reference.md`; added in #12, `17109de9`). Applicants, members and investors in
13 tables: `team`, `person`, `student`, `student_major`, `attachment`, `member`, `member_team`,
`application_form`, `application`, `application_team_preference`, `investor`, `import_batch`,
`change_log`; two views, `roster_public` (no e-mail, GPA, resume or date of birth) and
`applicant_history`. The design choices:
- every mutable table has `row_version` (optimistic concurrency for spreadsheet round trips) and
  `deleted_at` (no automated hard delete), with uniqueness only among live rows;
- triggers bump `row_version` and write `change_log`, so no writer can skip history; `attachment`
  is left out of the log so a PDF is not copied into every history row;
- enums are `TEXT` with a `CHECK`, never Postgres enums; all times are `timestamptz`;
- **no score table**: grades stay in the grading workbook and the database records only `stage`
  and `outcome` (#12);
- members and teams mirror the GitHub organisation, keyed by the numeric `github_user_id`; the login
  is never stored, and the mirror only flows from GitHub to the database (#7, `709664cb`);
- `investor` is a stub on purpose until individuals against entities is decided;
- grants to the read-write and read-only roles are guarded, so the migration does not fail where
  the roles are missing; until they exist, only the database owner can read the schema.

**Use.** `just migrate-idempotent platform/db` (Docker; Git Bash on Windows) applies every
migration twice to a throwaway Postgres and runs the checks in `tests/sql/`. The import:
`uv run python -m platform_db.people_import applications|members|close-cycle ... --dry-run`, with
`DB_*` in the environment. Its integration tests run only when `PEOPLE_TEST_DSN` is set.

**Gotchas.**
- `002_contract_ohlcv_1d.sql` is a data-ngin table (`futures_data`, a TimescaleDB hypertable, #78,
  `4b9be913`). It does not create its schema or the extension, which the recipe's default image
  (`postgres:16-alpine`, override with `PG_IMAGE`) does not have.
- Never put a row of the people schema in an issue, a PR or this repository.
- platform-db is not published (its publish workflow was dropped in `2ed574f2`). algosystem keeps its
  own config reader for that reason (libraries-and-research.md).

## platform/ci and the root tests/

| File | Does |
|---|---|
| `check_workflow_paths.py` | fails when a member has no quality workflow, a workflow has no member, a path filter misses a required input or names a path that does not exist, the gate job is missing, or a workspace lib would not ship with its consumer. Run by `just check-paths` and `meta-workflow-paths.yml` on every PR |
| `new_service.py` | writes a member's quality and publish workflows (no publish for `platform/`) |
| `quality_needed.py` | lets a Python member skip its matrix when only `uv.lock` changed and its own dependency closure did not; runs when unsure (#17, `5746599d`) |
| `check_attribution.py` | fails a PR whose description or commit messages carry AI attribution; run by `meta-attribution.yml` on every PR (#179) |
| `tests/test_ci_path_filters.py` | checks that filters give the right runs for a set of changed files; run by `meta-workflow-paths.yml` |
| `tests/test_quality_needed.py` | tests `quality_needed.py`; no workflow runs it, so run `uv run pytest tests` yourself |
| `tests/test_ci_attribution.py` | tests `check_attribution.py` on the attribution forms found in this repository and in trade-ngin, and on the PR template's own wording; run by `meta-workflow-paths.yml` and `just check-paths` |

Members are found under `libs/`, `apps/`, `services/` and `platform/` by their manifest;
`CMakeLists.txt` wins over `pyproject.toml`, because trade-ngin's `pyproject.toml` only builds its
Python wheel (`1b843c33`). The `*.skip.yml` twin workflows were deleted: `paths` and `paths-ignore`
lists do not partition a PR, so both twins ran and reported the same check name (#3, `5fb2d0eb`).
Open: #66 (a pattern whose prefix exists but matches no file still passes).

## Python workspace

One uv workspace, one `uv.lock`, so a dependency clash between members fails at resolve time
instead of in production (root `pyproject.toml`). Members are listed explicitly: `libs/algosystem`,
`platform/db`, `libs/research-core`, `services/data-ngin`, `services/research-api`,
`services/predict-ngin`, `apps/algoterminal`. trade-ngin is not one (scikit-build, its own
`pyproject.toml`), and Airflow stays out (data-ngin's DAGs ship in their own image).

- `requires-python >=3.11`: the lock resolves for every member at once.
- The `dev` group carries the tools CI uses, `just` included (`rust-just`), so CI and laptops run
  the same versions. `uv sync --all-packages --all-groups` is what CI installs.
- One ruff and one mypy config at the root. ruff finds it from anywhere; mypy does not, so the
  `justfile` runs mypy from the root with a path. mypy targets 3.12 because numpy's stubs use
  syntax 3.11 mode cannot parse. Imported code with old type errors is listed under
  `ignore_errors`; entries are removed as modules are cleaned, never added.

## Node workspace

pnpm 9.15 through corepack, Node 20 or later (`package.json`). The workspace protocol makes a
member depend on the local copy of another, never the registry (`pnpm-workspace.yaml`). The
website may leave the repository (#92).

## justfile

CI runs `just lint|typecheck|test <path>`; the reusable workflow owns the environment, the
`justfile` owns the commands. A member overrides a recipe by defining it in its own `justfile`
(data-ngin skips integration tests; predict-ngin adds extras and import contracts; platform/db adds
`migrate-idempotent`). Other recipes: `quality <path>` (all three), `check-paths`, `contracts`,
`new-service <kind> <name>`, `bootstrap` (`uv sync --all-groups` and `pnpm install`). Recipes run
in bash; on Windows, Git Bash at its default install path.

## CMakePresets.json and vcpkg.json

Root presets (`dev`, `ci-gcc13`, `ci-clang18`, `ci-msvc`, `ci-asan`, `ci-ubsan`; C++20; warnings
as errors on the gcc and clang CI presets) that `services/trade-ngin/CMakePresets.json` includes; vcpkg runs in manifest mode
from the root and needs `VCPKG_ROOT`. libpqxx is pinned to 7.10.5: 8.x changed row iteration and
breaks every row lambda in the engine (`43949c50`). Lift it only with a dedicated migration.

## Adding a member (CONTRIBUTING.md)

1. Create the directory under `libs/`, `services/` or `apps/` (`new_service.py` also accepts
   `platform`).
2. Add one manifest: `pyproject.toml`, `package.json` or `CMakeLists.txt`.
3. Python: add the path to `[tool.uv.workspace].members`. Node: add it to `pnpm-workspace.yaml`.
4. `just new-service <kind> <path>`, for example `just new-service services services/new-service`.
5. `just check-paths`.

Move structure first and code second: a migration PR is either scaffolding and CI, or the source
import, not both. If the new member depends on a workspace lib, its artifact must carry that lib
(libraries-and-research.md, research-core).

**Gotcha.** `release-please-config.json` and `.release-please-manifest.json` list every member, but
no workflow runs release-please. Releases are tags pushed by hand (`<name>/vX.Y.Z`).

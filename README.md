# AlgoGators

Monorepo scaffold for AlgoGators services, libraries, apps, shared contracts,
and CI machinery.

No external repository code has been imported yet. The member directories under
`libs/`, `services/`, and `apps/` are lightweight placeholders so workflow
ownership, path filters, release tags, and build boundaries can be reviewed
before the migrations start.

## Layout

| path | purpose |
|---|---|
| `libs/` | published packages consumed by other members |
| `services/` | unattended services and shared backend APIs deployed as containers |
| `apps/` | human-facing web and terminal apps |
| `platform/contracts/` | shared schemas and generated bindings |
| `platform/configs/` | shared operational/reference data |
| `platform/ci/` | monorepo CI generators and path-filter checks |
| `infra/` | deployment and infrastructure definitions |
| `docs/` | repo-wide documentation |

## CI Shape

Every member owns a thin workflow caller:

- `<slug>.quality.yml` runs only when that member or one of its declared inputs changes.
- `<slug>.skip.yml` is the no-op branch-protection companion for untouched members.
- `<slug>.publish.yml` is tag-scoped with `<name>/v*` for independent releases.

Reusable workflows own the toolchain setup. Member manifests and justfiles own
the commands and service-specific behavior.

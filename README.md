# AlgoGators

Monorepo scaffold for AlgoGators services, libraries, apps, shared contracts,
and CI machinery.

Imported members and their source repositories:

| member | source |
|---|---|
| `libs/algosystem` | `AlgoGators/algosystem` (main) |
| `services/data-ngin` | `AlgoGators/data-ngin` (declarative-ddd-refactor) |
| `services/research-api` | `AlgoGators/algolens` — `algolens-api`, package renamed `research_api` |
| `apps/algolens-web` | `AlgoGators/algolens` — `algolens-frontend` |
| `apps/website` | `AlgoGators/AlgoGatorsWebsite` (static site) |

Still placeholders: `libs/research-core`, `apps/algoterminal`,
`services/trade-ngin`.

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
- `<slug>.publish.yml` is tag-scoped with `<name>/v*` for independent releases.

A member whose files are untouched runs nothing at all. `just check-paths`
verifies every path filter still matches reality; it is the guard that makes
path-filtered CI safe.

Reusable workflows own the toolchain setup. Member manifests and justfiles own
the commands and service-specific behavior.

# AlgoLens

**LAST UPDATED:** 2026-11-02 · **Commit:** `8c534c1b` (main, the code this page was checked against)

AlgoLens is the club's web app over the trading books. Members read the portfolios, strategies
and incubation trials; the QT desk edits and approves the day's desk book; investors see the
published QT book and their reports; an admin assigns who sees what. A no-login public page is
planned, not built (#53, #56). It came from `AlgoGators/AlgoLens` in #60 (2026-10-22); that
repository no longer deploys (`app-algolens.deploy.yml` header).

## Layout

| Path | What |
|---|---|
| `apps/algolens-web/` | front end: React 18, Vite, Tailwind 4, Recharts; a pnpm workspace member. Entry `src/main.tsx`, `src/App.tsx`; layers `domain/`, `application/`, `infrastructure/`, `adapters/`, plus `components/` |
| `services/research-api/` | the API: Flask, gunicorn, flask-jwt-extended, flask-limiter, psycopg2, grpcio. Package `src/research_api/`; app factory `infrastructure/config/app_factory.py`; WSGI `research_api.wsgi:app`. Layer rules in `docs/architecture.md` (domain imports no Flask or psycopg2) |
| `services/research-api/algogators/`, `proto/` | vendored gRPC stubs and protos of the engine's desk service |
| `services/research-api/migrations/` | AlgoLens's own SQL migrations 001 to 007 and their rollbacks |
| `infra/algolens/deployment/` | production compose, edge nginx, `deploy.sh`, TLS renewal, `DEPLOYMENT.md` |
| `infra/algolens/sim/` | the QT desk simulator: AlgoLens, the real engine and Postgres on a movable clock |
| `docs/algolens/README.md` | the desk contract as AlgoLens implements it, screens and access |

The API depends on the workspace libraries `libs/research-core` and `platform/db`, so its image
builds from the repository root (`services/research-api/Dockerfile`). `pyproject.toml` describes
it as shared with AlgoTerminal.

## How it talks to the engine

- **Commands go over gRPC** to the engine's `engine-rpc` container on the private Docker network
  `qt` (`ENGINE_RPC_ADDR`, default `engine-rpc:50051`; 3 s default deadline;
  `infrastructure/rpc/settings.py`). Service `algogators.desk.DeskService`: `RunDesk`,
  `RequestOverride`, `RecordDecision`, `Publish`, `Rescind`, `GetRunStatus`, `GetDeskRisk`.
- **Postgres stays the record.** AlgoLens writes the command row to `trading.position_overrides`
  first, then calls the RPC with its id; the engine moves the row to `done`, `refused` or
  `failed`, and re-drives every `pending` row every 60 s, so a failed call loses nothing. The page
  polls the row (docs/algolens/README.md; AlgoLens #113, #115).
- **Every call carries `x-algogators-api-version`.** The engine refuses a missing header or another
  major version with `FAILED_PRECONDITION` (`infrastructure/rpc/version_header.py`).
- **Tables read:** `trading.positions` (books `system`, `qt_proposal`, `qt`), `live_results`,
  `equity_curve`, `executions`, `live_run_metadata` (published, `publish_source`, `sent_at`),
  `strategy_config`, `strategy_registry`, and `auth.users`.
- **Tables written:** `trading.position_overrides` (commands), `trading.strategy_config` (desk
  settings), `trading.strategy_registry` and `strategy_lifecycle_log` (incubation),
  `trading.desk_settings_locks`, `auth.user_screen_access` and its log, `investor.reports`,
  `investor.portfolio_links`. QT tables come from trade-ngin migrations 021 to 029
  (`migrations/README.md`).
- **Books are portfolio ids.** `trading.strategy_registry` lists the portfolios AlgoLens shows, by
  portfolio id, with display metadata and starting equity (migration 001). A failed or empty read
  is an error, never a built-in default list (D-23, `strategy_registry.py`; AlgoLens #143).
- **A day is approved when `live_run_metadata.published_at` is set**, not when a publish row is
  done: a rescind clears `published_at` and leaves the row as history (AlgoLens #129).
- **The daily book e-mail is not AlgoLens's.** Its report worker was removed in #86 (2026-10-30);
  the engine's Python report sends it (decisions.md). Migrations 006 and 007 stay as the record of
  what was applied; 007's rollback takes back the `sent_at` grant.

## Auth and roles, as built

- Login with e-mail and password (`/auth/login`, Werkzeug hashes in `auth.users`). The session is
  a JWT in an httpOnly cookie, 12 hours, with CSRF protection: the front end echoes the
  `csrf_access_token` cookie (`app_factory.py`, `apiRequest.ts`; AlgoLens #59).
- **Screens, not new roles** (2026-10-11): an admin, role `exec_board`, grants each user any of
  `member`, `qt`, `investor` (`domain/identity/screens.py`, AlgoLens #128). Screens and the role
  are re-read from the database on every request, never taken from the token (`adapters/http/access.py`,
  AlgoLens #137). Desk routes need `qt`; portfolio, strategy and incubation reads need `member`
  or `qt`; `/api/investor/*` needs `investor`; `/api/admin/*` needs `exec_board`. New sign-ups get
  no screen ("Access pending").
- Without migration 003 the API falls back to its backfill map (members get `member`,
  `exec_board` also gets `qt`) and logs a warning, so deploying ahead of the migration locks no one out.
- **Override approvals**: either approver named in `QT_APPROVERS` (VP or President) decides; the
  requester never decides their own. The e-mailed token travels in a POST body and is stored only
  as a sha256 (`desk.py`, `domain/qt/approvers.py`).
- **Feature flags**: every desk route answers 404 unless `QT_DESK_ENABLED` is on. `DEV_MODE=1`
  enables `/auth/dev-login` without credentials, and is ignored when `FLASK_ENV=production`.
  `SIM_MODE=1` (simulator only) adds `/api/sim`.
- The planned capability model with GitHub-team groups and investor sign-in by Google or
  Microsoft (#53) was not built; #54 was closed as replaced by the screens.

## Run and test locally

- API, from the repository root: `uv sync --all-groups`, then `just quality services/research-api`
  (ruff, ruff format, mypy, pytest with coverage; root `justfile`). Tests need no database.
  Postgres integration tests are opt-in through `ALGOLENS_TEST_DATABASE_URL`, localhost only
  (`tests/qt_fixture.py`). Variables are listed in `services/research-api/.env.example`.
- Web: `corepack pnpm install --frozen-lockfile`, then in `apps/algolens-web` `pnpm dev`
  (port 3000; `VITE_API_URL`, default port 5000; `VITE_DEV_MODE=1` uses the dev login).
  `just quality apps/algolens-web` runs lint, typecheck and vitest.
- The desk at any time of day: `infra/algolens/sim/` (`simctl`; its `AGENTS.md` holds the desk rules).
- CI: `svc-research-api.quality.yml` (Python 3.11, coverage 80) and
  `app-algolens-web.quality.yml` (Node 20, coverage 80, bundle budget 500 kB) on PRs and `main`.

## Deploy and where it runs

1. Merge to `main`, then fast-forward `prod` (shared with the engine and data-ngin deploys).
2. `app-algolens.deploy.yml` runs when the push touches the API, the web app, `research-core`,
   `platform/db` or `infra/algolens`: quality gates, then both images to GHCR
   (`algolens-backend`, `algolens-frontend`, the names the box already ran), then SSH.
3. On the production box, `infra/algolens/deployment/deploy.sh <backend@digest> <frontend@digest>`
   runs from its own sparse checkout (#61): a database pre-flight from the new image, stop the old
   containers, start the new ones, wait for `/health` through the edge, and roll back by itself to
   the last good pair on failure.

Three containers: an nginx edge that terminates TLS and routes `/auth`, `/portfolio`, `/api` and
`/health` to the API; the static front end; the API, also on the `qt` network. The env file lives
outside any checkout on the box (`DEPLOYMENT.md`). Rollback: re-run `deploy.sh` with the previous
pair it recorded, or re-run an earlier deploy run.

## Gotchas

- **The deploy does not wait out 09:00 to 10:15 New York** as the engine's does. Do not push AlgoLens
  to `prod` then (README rule; `DEPLOYMENT.md` says avoid 09:30).
- **AlgoLens is killed first under memory pressure**: hard `mem_limit` and `oom_score_adj: 800` on a
  box shared with the engine, and old and new containers never run side by side (`DEPLOYMENT.md`).
- **A new API path prefix needs a `location`** in `infra/algolens/deployment/edge-routes.conf`, or the
  edge never sends it to the API. Desk routes sit under `/portfolio/desk` for this reason.
- **`FLASK_ENV` defaults to `production`**: the API then refuses to start without `JWT_SECRET_KEY`
  and an explicit `CORS_ORIGINS`. Set `FLASK_ENV=development` locally.
- **`create_app` loads `.env` from `services/research-api/src/`** (`ENV_PATH` is `parents[3]` of
  `app_factory.py`), not from `services/research-api/` as the README says. Export the variables.
- **Stale paths after the move**: `services/research-api/README.md`, `docs/architecture.md` and
  `docs/algolens/README.md` still say `algolens-api/` and `algolens-frontend/`; the simulator README
  says "from the AlgoLens repository root". `docs/algolens/README.md` also carries host details
  that must not be copied anywhere new.
- **The front-end Dockerfile needs the repository root as context** (it copies the pnpm lockfile
  and workspace manifest), but `infra/algolens/docker-compose.dev.yml` and the simulator's compose
  pass `apps/algolens-web`.
- **Protos are vendored from `AlgoGators/trade-ngin`** at the commit in `proto/algogators/SOURCE`
  (`scripts/sync_protos.sh`). No workflow here runs `sync_protos.sh --check`; the script's "CI runs
  --check" meant the old repository's CI. After a desk.proto change, re-vendor and regenerate.
- **`app-algolens-web.publish.yml` and `svc-research-api.publish.yml` are not the production
  deploy.** They fire on `algolens-web/v*` and `research-api/v*` tags (none exist) and target Pages
  and another image name.
- **The deploy gate's coverage floors (60 API, 50 web) are lower** than the PR gates (80).
- **Migrations have no table of applied state** (ruling 26). Record the date in
  `migrations/README.md` when you apply one.
- **Fund totals count only ungrouped books**: the QT desk book and its model twin are listed but
  not added up (`counts_in_fund_total`, AlgoLens #131).

## Open work (AlgoGators/algogators)

- #53 AlgoLens audiences: exec, QT desk, members, investors, public (the design).
- #55 Investor portal: the investor's own book and its pages.
- #56 Public page: a no-login view of strategies and approved metrics.
- #144 Investor books on the engine: one portfolio per investor.
- #88 Show the engine's stored statistics and trade counts, read NULL safely, label figures.
- #58 Use algosystem for the statistics AlgoLens shows.
- #59 Read a book by portfolio id and a sleeve by portfolio and sleeve name.
- #57 Trading-run status inside the QT platform.
- #52 and #127 QT relative value analysis: the AlgoLens side.
- #178 Desk service and QT book open items, migration grants, guards and rollbacks.
- #65 Validate the portfolio strategy-list API response (good first issue).

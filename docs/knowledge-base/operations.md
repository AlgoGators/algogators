# Operations

How production runs as of 2026-11-02. Hosts, keys, database users and recipient lists are kept out
of this repository on purpose: ask Dom (people.md) for access.

## The live path

Everything that trades and reports runs on one EC2 box, "algocloud":

| What | Where |
|---|---|
| The engine and the QT desk | the `engine-rpc` container: the gRPC desk service, the catch-up scheduler and every model run. Compose project in `/home/ubuntu/qt-engine` on the box; the image is the one line `QT_ENGINE_IMAGE=` in its `.env` |
| Book configs | `/home/ubuntu/qt-engine/config/portfolios/<book>/` on the box (`portfolio.json`, `risk.json`, `email.json`). They start from `services/trade-ngin/config_template/` but are edited on the box and are not synced back |
| Data | data-ngin's Airflow on the same box writes the market data the engine reads (`svc-data-ngin.deploy.yml`) |
| AlgoLens | the web app and API, deployed by `app-algolens.deploy.yml` |
| Logs | `docker logs engine-rpc`: one JSON object a line (`ts`, `level`, `logger`, `msg`, plus fields such as `portfolio_id`, `date`, `reason`, `error`) |

The QT day (scheduler header in `services/rpc/algogators_rpc/services/desk/catchup.py` under
`services/trade-ngin/`): model run from about 06:45 to 07:00 New York time, desk approval by 09:30,
the e-mail at 09:30, the fallback publish at 10:00, passes every 5 minutes until 10:30 and every 30
after, until 22:00. A published day whose e-mail did not go out before the day passed is **not
sent late**: the scheduler logs a `missed_send` alert. The send records `sent_at` on the day's row
of `trading.live_run_metadata`.

## Deploying the engine

1. Merge to `main`. `svc-trade-ngin.quality.yml` builds, tests and pushes
   `ghcr.io/algogators/trade-ngin-engine:sha-<commit>`.
2. Fast-forward `prod` to that commit: `git push origin <commit>:prod`. `prod` is shared with the
   AlgoLens and data-ngin deploys; each deploys only when its own paths changed.
3. `svc-trade-ngin.deploy.yml` finds main's image (or builds one), SSHes in and runs
   `services/trade-ngin/deploy/deploy.sh <image@digest>`, which waits for no engine run and for the
   09:00 to 10:15 quiet window to pass, switches the image, waits for the healthcheck and switches
   back if it fails.

**Rollback:** on the box, `deploy.sh <previous image ref>`. deploy.sh prints the previous ref; the
deploy run's log has it too. Re-running an earlier deploy run also works.

**After a deploy**, read the next catch-up pass in the logs. A config the new engine refuses shows
as `Failed to load configuration` on every model run and send (it happened on 2026-11-01; see
[decision 0006](decisions/0006-risk-config-schema-2.md)).

## Checks that run by themselves

| Check | What |
|---|---|
| `svc-trade-ngin.watchdog.yml` | every morning: did the engine write the day's results? It checks the outcome in the database (DB_* repository secrets), so it catches a dead container, cron or binary alike. It exists because live trading once stopped for 81 days unnoticed |
| `AlgoGators/issue-tracker` publish | hourly; rebuilds the tracker page |
| Grafana | alert rules and silences for the services (ask Dom for access) |

## Database

PostgreSQL on its own host. Credentials come from the environment (`DB_*`), never from a file in
the repository. For a check, connect read-only (`options=-c default_transaction_read_only=on`);
writes to production go through a reviewed script that a person runs. Migrations are in
`services/trade-ngin/migrations/`; destructive ones need an explicit go-ahead.

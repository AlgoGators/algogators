# data-ngin production deploy

data-ngin's Airflow stack runs on the **algocloud** EC2 box (`i-0fd5cfa45d7a13d0b`,
t2.medium: 2 vCPU, 4 GB RAM, Ubuntu 22.04), next to Postgres/TimescaleDB,
trade-ngin and AlgoLens.

## How a deploy works

Push to the `prod` branch. `.github/workflows/svc-data-ngin.deploy.yml` then:

1. runs the data-ngin quality gate (tests, lint, coverage);
2. builds `Dockerfile.airflow` and pushes `ghcr.io/algogators/data-ngin-airflow`;
3. parses every DAG and `webserver_config.py` inside that exact image, and fails
   unless `data_pipeline_dag`, `new_data_pipeline_dag` and `tiingo_data_dag`
   load with no import errors;
4. SSHes to the box (repo secrets `SSH_HOST`, `SSH_USER`, `SSH_KEY`), fast-forwards
   the sparse checkout at `/home/ubuntu/algogators` to the commit it built (cloned
   on first deploy), and runs `deploy.sh <image digest>`.

`deploy.sh` pulls the image, waits for any running DAG run to finish (up to
45 minutes), restarts the stack from `docker-compose.prod.yml`, checks API health
and DAG registration, and installs the host cron jobs from `cron/` as
`/etc/cron.d/algogators-data-ngin`.

The box never builds anything: CI builds and tests the image, and the box only
pulls it by digest. The checkout carries only this directory; the code ships
inside the image.

Avoid pushing to `prod` between 11:00 and 12:30 UTC, the ingestion window. A
deploy started then waits for the runs to finish before it restarts anything.

## What lives where

| What | Where |
|---|---|
| DAG schedules (ingestion) | `services/data-ngin/dags/` (baked into the image) |
| Host cron jobs (backup, freshness check) | `deploy/cron/` (installed by every deploy) |
| Secrets | `/home/ubuntu/.config/algogators/data-ngin.env` on the box, mode 600, never in git |
| Deployed / previous image | `/home/ubuntu/.config/algogators/data-ngin.{current,previous}-image` |

The env file holds `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`,
`DATABENTO_API_KEY`, the `TIINGO_API_KEY_*` keys, `GITHUB_TOKEN` (a fine-grained
PAT with Issues read/write on AlgoGators/algogators, used by the DAG-failure
notifier and the freshness check), and the `AIRFLOW_*` values
(`AIRFLOW_DB_CONN`, `AIRFLOW_JWT_SECRET`, `AIRFLOW_SECRET_KEY`,
`AIRFLOW_OIDC_SECRET`, `AIRFLOW_LOGIN`, `AIRFLOW_BASE_URL`, `AIRFLOW_PROXY_IPS`).
To change one, edit the file on the box and re-run the last deploy.

The backup script reads the database password from `~/.pgpass`. Update that file
too when the database password is rotated.

## Rollback

```bash
ssh ubuntu@<box>
cd ~/algogators/services/data-ngin/deploy
./deploy.sh "$(cat ~/.config/algogators/data-ngin.previous-image)"
```

Or re-run an earlier successful run of the deploy workflow.

**Back to the pre-monorepo stack** (only until it is retired): the first deploy
replaced containers that ran from the standalone repo checkout in `~/data-ngin`,
using the `data-ngin-airflow:local` image with the source bind-mounted. That
image has no `data_ngin` code of its own, so do not pass it to `deploy.sh`.
Instead:

```bash
cd ~/data-ngin && docker compose up -d
sudo rm /etc/cron.d/algogators-data-ngin
crontab -e   # restore: 0 2 * * 1,4 /home/ubuntu/pg_backup.sh >> /home/ubuntu/pg_backup.log 2>&1
```

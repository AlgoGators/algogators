#!/bin/bash
# Daily data-freshness check, run by /etc/cron.d/algogators-data-ngin.
#
# Runs inside the airflow-scheduler container: that image already has the
# data_ngin package, and the container already has the DB_* and GITHUB_TOKEN
# values from the env file. GitHub-hosted runners cannot reach the database
# (private VPC address), which is why this is a box cron job, not an Action.
set -euo pipefail

echo "[$(date -Is)] data freshness check"
docker exec airflow-scheduler python -m data_ngin.ops.check_data_freshness
echo "[$(date -Is)] OK"

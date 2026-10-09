#!/usr/bin/env bash
# Roll the data-ngin Airflow stack on the algocloud box to one image digest.
#
#   deploy.sh ghcr.io/algogators/data-ngin-airflow@sha256:...
#
# CI (svc-data-ngin.publish.yml, on a push to `prod`) fast-forwards the prod
# checkout to the commit it built, then runs this script from that checkout.
# Rolling back is the same command with the previous image ref, which this
# script records in $STATE_DIR/data-ngin.previous-image.
#
# Steps: pull the image, wait for running DAG runs to finish (restarting the
# scheduler mid-run kills their tasks), restart the stack, check health and DAG
# import errors, then install the host cron jobs.
set -euo pipefail

IMAGE_REF="${1:?usage: deploy.sh <image-ref>}"
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/../../.." && pwd)"
STATE_DIR="${STATE_DIR:-/home/ubuntu/.config/algogators}"
DATA_NGIN_ENV_FILE="${DATA_NGIN_ENV_FILE:-$STATE_DIR/data-ngin.env}"
# Longest we wait for in-flight DAG runs before giving up on this deploy.
WAIT_FOR_RUNS_MINUTES="${WAIT_FOR_RUNS_MINUTES:-45}"
DAG_IDS=(data_pipeline_dag new_data_pipeline_dag tiingo_data_dag)
CRON_TARGET=/etc/cron.d/algogators-data-ngin

log() { echo "[$(date -Is)] $*"; }
die() { echo "::error::$*" >&2; exit 1; }

[ -f "$DATA_NGIN_ENV_FILE" ] || die "env file $DATA_NGIN_ENV_FILE is missing (see deploy/README.md)"

export IMAGE_REF DATA_NGIN_ENV_FILE
compose() {
    docker compose -f "$DEPLOY_DIR/docker-compose.prod.yml" --env-file "$DATA_NGIN_ENV_FILE" "$@"
}
# Run the Airflow CLI in the running scheduler container.
airflow_cli() { docker exec airflow-scheduler airflow "$@"; }

log "pulling $IMAGE_REF"
docker pull "$IMAGE_REF"

if docker ps --format '{{.Names}}' | grep -qx airflow-scheduler; then
    deadline=$(( $(date +%s) + WAIT_FOR_RUNS_MINUTES * 60 ))
    while :; do
        running=0
        unknown=0
        for dag in "${DAG_IDS[@]}"; do
            # Fail closed: if the CLI errors or prints something unexpected we
            # do not know the DAG is idle, so keep waiting rather than restart
            # the scheduler under a live ingestion run.
            if out=$(airflow_cli dags list-runs "$dag" --state running -o json 2>/dev/null) \
                && n=$(printf '%s' "$out" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null); then
                running=$(( running + n ))
            elif airflow_cli dags list -o json 2>/dev/null | grep -q "\"dag_id\": \"$dag\""; then
                unknown=1
            fi
            # else: the DAG is not registered yet (first deploy of a new DAG), so it has no runs.
        done
        [ "$running" -eq 0 ] && [ "$unknown" -eq 0 ] && break
        [ "$(date +%s)" -lt "$deadline" ] || die "DAG runs still running (or state unreadable) after ${WAIT_FOR_RUNS_MINUTES}m; deploy aborted, nothing changed"
        log "$running DAG run(s) in progress (state unreadable: $unknown); waiting"
        sleep 60
    done
fi

previous=$(docker inspect airflow-scheduler --format '{{.Config.Image}}' 2>/dev/null || true)
# Only a CI-built image is a valid argument to this script. Anything else is
# the pre-monorepo stack (data-ngin-airflow:local, which bind-mounted its
# source), whose rollback is to start that stack again from ~/data-ngin.
case "$previous" in
    ghcr.io/*) rollback="$0 $previous" ;;
    *) rollback="cd ~/data-ngin && docker compose up -d, and restore the pg_backup line in 'crontab -e'" ;;
esac

# Stop the running Airflow processes before starting the new ones: old and
# new Airflow side by side (plus `db migrate`) roughly doubles Airflow's
# memory on a box shared with Postgres, trade-ngin and AlgoLens.
log "stopping the running Airflow stack"
compose stop airflow-apiserver airflow-scheduler airflow-dag-processor

log "starting $IMAGE_REF"
compose up -d --remove-orphans

log "waiting for the API server"
healthy=0
for _ in $(seq 1 60); do
    if curl -fsS --max-time 5 http://localhost:8080/api/v2/monitor/health >/dev/null 2>&1; then
        healthy=1
        break
    fi
    sleep 5
done
[ "$healthy" -eq 1 ] || die "API server never became healthy; roll back with: $rollback"

log "checking DAG import errors"
# The dag-processor needs a parse cycle before the DAGs are registered.
dags_ok=0
for _ in $(seq 1 24); do
    errors=$(airflow_cli dags list-import-errors -o json 2>/dev/null || echo unknown)
    listed=$(airflow_cli dags list -o json 2>/dev/null || echo '[]')
    missing=$(python3 - "$listed" "${DAG_IDS[@]}" <<'EOF'
import json, sys
have = {d.get("dag_id") for d in json.loads(sys.argv[1])}
print(" ".join(d for d in sys.argv[2:] if d not in have))
EOF
)
    if [ "$errors" = "[]" ] && [ -z "$missing" ]; then
        dags_ok=1
        break
    fi
    sleep 10
done
if [ "$dags_ok" -ne 1 ]; then
    echo "import errors: $errors" >&2
    die "DAGs not registered cleanly (missing: ${missing:-none}); roll back with: $rollback"
fi

log "installing host cron jobs"
chmod +x "$DEPLOY_DIR"/cron/*.sh
tmp=$(mktemp)
sed "s|@REPO_DIR@|$REPO_DIR|g" "$DEPLOY_DIR/cron/algogators-data-ngin.cron" > "$tmp"
sudo install -m 0644 -o root -g root "$tmp" "$CRON_TARGET"
rm -f "$tmp"
# The backup used to be a line in ubuntu's own crontab pointing at
# ~/pg_backup.sh. Drop it so the backup does not run twice.
if crontab -l 2>/dev/null | grep -q 'pg_backup.sh'; then
    crontab -l | grep -v 'pg_backup.sh' | crontab -
    log "removed the legacy pg_backup line from the ubuntu crontab"
fi

mkdir -p "$STATE_DIR"
case "$previous" in ghcr.io/*) [ "$previous" != "$IMAGE_REF" ] && echo "$previous" > "$STATE_DIR/data-ngin.previous-image" || true ;; esac
echo "$IMAGE_REF" > "$STATE_DIR/data-ngin.current-image"

docker image prune -f --filter 'until=168h' >/dev/null
log "deployed $IMAGE_REF (previous: ${previous:-none})"

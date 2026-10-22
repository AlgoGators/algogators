#!/bin/bash
set -euo pipefail

# Backs up every database we care about to S3. Installed by deploy.sh as
# /etc/cron.d/algogators-data-ngin and run from the prod checkout, so this file
# (not a copy in ~ubuntu) is the one that runs.
#
# Replaces a version that silently uploaded 20-byte empty files from
# 2025-11-06 to 2026-08-06 (78 consecutive failed runs). Three compounding
# faults caused that, all fixed here:
#
#   1. DB_HOST was a public EC2 IP (3.140.200.228) that changed when the
#      instance restarted. localhost cannot go stale, and still resolves now
#      that port 5432 is firewalled to the security group.
#   2. No error handling: pg_dump failed, gzip wrote an empty file, and
#      `aws s3 cp` uploaded it and exited 0, so cron recorded success.
#      set -euo pipefail plus explicit checks stop that.
#   3. No sanity check on the output. MIN_BYTES rejects an implausibly small
#      dump before it reaches S3 and creates the appearance of a backup.
#
# Also adds new_algo_data, which was never in scope before despite holding all
# the equities data.
#
# Requires a matching ~/.pgpass line:  localhost:5432:*:postgres:<password>
# UPDATE THAT FILE WHEN THE DB PASSWORD IS ROTATED, or backups break again.

TIMESTAMP=$(date +"%F-%H%M")
DB_HOST="localhost"
DB_USER="postgres"
S3_BUCKET="algogators-postgres-backup"
DATABASES=("algo_data" "new_algo_data")
MIN_BYTES=100000   # a real dump is hundreds of MB; 20 bytes is an empty gzip header

# Prometheus metrics for alerting (pg-backup-failed, pg-backup-stale in Grafana),
# read by node_exporter's textfile collector. Written on every exit, including
# set -e aborts, so a crash mid-run still reports a failure.
TEXTFILE_DIR="/var/lib/node_exporter/textfile"
METRICS_FILE="${TEXTFILE_DIR}/pg_backup.prom"
declare -A DUMP_BYTES=()

write_metrics() {
    local code=$?
    local now last_success
    now=$(date +%s)
    last_success=$(awk '/^pg_backup_last_success_timestamp_seconds /{print $2}' "$METRICS_FILE" 2>/dev/null || true)
    [ "$code" -eq 0 ] && last_success=$now
    {
        echo "# HELP pg_backup_last_run_timestamp_seconds When pg_backup.sh last finished."
        echo "# TYPE pg_backup_last_run_timestamp_seconds gauge"
        echo "pg_backup_last_run_timestamp_seconds ${now}"
        echo "# HELP pg_backup_last_exit_code Exit code of the last pg_backup.sh run."
        echo "# TYPE pg_backup_last_exit_code gauge"
        echo "pg_backup_last_exit_code ${code}"
        echo "# HELP pg_backup_last_success_timestamp_seconds When pg_backup.sh last succeeded."
        echo "# TYPE pg_backup_last_success_timestamp_seconds gauge"
        echo "pg_backup_last_success_timestamp_seconds ${last_success:-0}"
        echo "# HELP pg_backup_dump_bytes Compressed dump size from the last run."
        echo "# TYPE pg_backup_dump_bytes gauge"
        for db in "${!DUMP_BYTES[@]}"; do
            echo "pg_backup_dump_bytes{database=\"${db}\"} ${DUMP_BYTES[$db]}"
        done
    } > "${METRICS_FILE}.$$" && mv "${METRICS_FILE}.$$" "$METRICS_FILE" || true
}
trap write_metrics EXIT

FAILED=0
for DB_NAME in "${DATABASES[@]}"; do
    FILENAME="${DB_NAME}_${TIMESTAMP}.sql.gz"
    TMP_PATH="/tmp/${FILENAME}"

    echo "[$(date -Is)] dumping ${DB_NAME}..."
    if ! pg_dump -h "$DB_HOST" -U "$DB_USER" "$DB_NAME" | gzip > "$TMP_PATH"; then
        echo "[$(date -Is)] ERROR: pg_dump failed for ${DB_NAME}" >&2
        rm -f "$TMP_PATH"
        FAILED=1
        continue
    fi

    SIZE=$(stat -c%s "$TMP_PATH")
    if [ "$SIZE" -lt "$MIN_BYTES" ]; then
        echo "[$(date -Is)] ERROR: ${DB_NAME} dump is only ${SIZE} bytes -- refusing to upload" >&2
        rm -f "$TMP_PATH"
        FAILED=1
        continue
    fi

    aws s3 cp "$TMP_PATH" "s3://${S3_BUCKET}/postgres-backups/${FILENAME}"
    echo "[$(date -Is)] OK: ${DB_NAME} (${SIZE} bytes)"
    DUMP_BYTES[$DB_NAME]=$SIZE
    rm -f "$TMP_PATH"
done

if [ "$FAILED" -ne 0 ]; then
    echo "[$(date -Is)] BACKUP RUN FAILED -- see errors above" >&2
fi
exit $FAILED

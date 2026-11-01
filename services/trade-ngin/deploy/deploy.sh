#!/usr/bin/env bash
# Roll engine-rpc on the algocloud box to one image digest.
#
#   deploy.sh ghcr.io/algogators/trade-ngin-engine@sha256:...
#
# CI (svc-trade-ngin.deploy.yml, on a push to `prod`) fast-forwards a sparse
# checkout of this directory to the commit it built, then runs this script.
# engine-rpc is the whole live path: the QT desk, the catch-up scheduler and
# every model run happen inside it. The host's compose project in $QT_DIR is
# left as it is; only QT_ENGINE_IMAGE in its .env changes.
#
# Steps: pull the image and check it has the engine, wait until no engine run is
# in progress and the 09:30 send / 10:00 cutoff is not near, switch the image,
# wait for the healthcheck, and switch back if it never passes.
set -euo pipefail

IMAGE_REF="${1:?usage: deploy.sh <image-ref>}"
QT_DIR="${QT_DIR:-/home/ubuntu/qt-engine}"
ENGINE_BINARY="${ENGINE_BINARY:-/app/build/bin/Release/live_portfolio_conservative}"
# Longest we wait for a quiet moment before giving up on this deploy.
WAIT_MINUTES="${WAIT_MINUTES:-45}"
# New York time, HHMM: no restart from shortly before the 09:30 send until after the 10:00 cutoff.
QUIET_FROM="${QUIET_FROM:-0900}"
QUIET_TO="${QUIET_TO:-1015}"
SERVICE=engine-rpc

log() { echo "[$(date -Is)] $*"; }
die() { echo "::error::$*" >&2; exit 1; }

# _deploy-ssh.yml exports COMPOSE_FILE (docker-compose.prod.yml, the other services' file name);
# engine-rpc's project is $QT_DIR/docker-compose.yml, which compose finds by itself.
unset COMPOSE_FILE
cd "$QT_DIR"
[ "$(grep -c '^QT_ENGINE_IMAGE=' .env)" -eq 1 ] || die "$QT_DIR/.env must have exactly one QT_ENGINE_IMAGE= line"
previous=$(sed -n 's/^QT_ENGINE_IMAGE=//p' .env)
if [ "$previous" = "$IMAGE_REF" ]; then
    log "$IMAGE_REF is already deployed"
    exit 0
fi

log "pulling $IMAGE_REF"
docker pull "$IMAGE_REF"
docker run --rm --entrypoint test "$IMAGE_REF" -x "$ENGINE_BINARY" \
    || die "$IMAGE_REF has no $ENGINE_BINARY; nothing changed"

# A restart kills a model run or an override mid-write, so wait for a moment when neither is running.
busy_reason() {
    local now
    now=$((10#$(TZ=America/New_York date +%H%M)))
    if [ "$now" -ge $((10#$QUIET_FROM)) ] && [ "$now" -lt $((10#$QUIET_TO)) ]; then
        echo "inside the ${QUIET_FROM}-${QUIET_TO} ET send window"
    elif docker top "$SERVICE" -eo args 2>/dev/null | grep -q "${ENGINE_BINARY##*/}"; then
        echo "an engine run is in progress"
    fi
}
deadline=$(( $(date +%s) + WAIT_MINUTES * 60 ))
while reason=$(busy_reason) && [ -n "$reason" ]; do
    [ "$(date +%s)" -lt "$deadline" ] || die "still busy after ${WAIT_MINUTES}m ($reason); deploy aborted, nothing changed"
    log "$reason; waiting"
    sleep 30
done

set_image() { sed -i "s|^QT_ENGINE_IMAGE=.*|QT_ENGINE_IMAGE=$1|" .env; }
cp -p .env ".env.bak-$(date -u +%Y%m%dT%H%M%SZ)"
# Keep the ten newest backups (names are ours: .env.bak-<UTC stamp>).
# shellcheck disable=SC2012
ls -1t .env.bak-2* 2>/dev/null | tail -n +11 | xargs -r rm -f

log "starting $IMAGE_REF (previous: $previous)"
set_image "$IMAGE_REF"
if ! docker compose up -d --wait --wait-timeout 300 "$SERVICE"; then
    docker logs --tail 50 "$SERVICE" >&2 || true
    log "not healthy; switching back to $previous"
    set_image "$previous"
    docker compose up -d --wait --wait-timeout 300 "$SERVICE" \
        || die "rollback to $previous is not healthy either; engine-rpc needs attention now"
    die "$IMAGE_REF never became healthy; rolled back to $previous"
fi

echo "$previous" > .previous-image
# Old engine images are ~0.9 GB each: keep the running one and the previous one.
keep=$(docker image inspect --format '{{.Id}}' "$IMAGE_REF" "$previous" 2>/dev/null || true)
docker images --format '{{.ID}}' "${IMAGE_REF%@*}" | sort -u | while read -r id; do
    grep -q "${id}" <<< "$keep" || docker rmi "$id" >/dev/null 2>&1 || true
done
log "deployed $IMAGE_REF (previous: $previous; roll back with: $0 $previous)"

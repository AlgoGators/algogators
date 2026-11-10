#!/usr/bin/env bash
# Local rehearsal server inside WSL: Postgres 16 + TimescaleDB on 127.0.0.1:5433, database release_scratch.
# Run once with sudo. Touches nothing outside this WSL install and never connects to production.
# Prints no password; the login is kept in ~/.scratch-pg (mode 600) for the rehearsal scripts.
# Stop it to give the memory back:  sudo pg_ctlcluster 16 scratch stop
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 2; }
U="${SUDO_USER:?run with sudo from your own account}"
H=$(getent passwd "$U" | cut -d: -f6)
PORT=5433 DB=release_scratch ROLE=release_scratch

# No default cluster: it would only sit there using memory.
mkdir -p /etc/postgresql-common
grep -qs '^create_main_cluster' /etc/postgresql-common/createcluster.conf \
  || echo 'create_main_cluster = false' >> /etc/postgresql-common/createcluster.conf

apt-get update -qq
apt-get install -y -qq curl gnupg lsb-release ca-certificates > /dev/null
install -d /etc/apt/keyrings
curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey | gpg --dearmor --yes -o /etc/apt/keyrings/timescaledb.gpg
echo "deb [signed-by=/etc/apt/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -cs) main" \
  > /etc/apt/sources.list.d/timescaledb.list
apt-get update -qq
apt-get install -y -qq postgresql-16 timescaledb-2-postgresql-16 > /dev/null

pg_lsclusters -h | awk '{print $2}' | grep -qx scratch || pg_createcluster 16 scratch -p "$PORT" > /dev/null
C=/etc/postgresql/16/scratch/conf.d/release.conf
cat > "$C" <<EOF
shared_preload_libraries = 'timescaledb'
timescaledb.telemetry_level = off
listen_addresses = '127.0.0.1'
timezone = 'UTC'
shared_buffers = 512MB
work_mem = 32MB
maintenance_work_mem = 256MB
max_connections = 30
EOF
pg_ctlcluster 16 scratch restart 2>/dev/null || pg_ctlcluster 16 scratch start

P="$H/.scratch-pg"
if [ ! -s "$P" ]; then
  PW=$(head -c 24 /dev/urandom | base64 | tr -d '/+=')
  install -m 600 -o "$U" /dev/null "$P"
  printf 'export PGHOST=127.0.0.1 PGPORT=%s PGUSER=%s PGPASSWORD=%s SCRATCH_DB=%s\n' "$PORT" "$ROLE" "$PW" "$DB" > "$P"
  sudo -u postgres psql -X -q -p "$PORT" -v ON_ERROR_STOP=1 <<EOF
create role $ROLE login superuser password '$PW';
EOF
fi
sudo -u postgres psql -X -qAt -p "$PORT" -c "select 1 from pg_database where datname='$DB'" | grep -q 1 \
  || sudo -u postgres createdb -p "$PORT" -O "$ROLE" "$DB"
sudo -u postgres psql -X -q -p "$PORT" -d "$DB" -c 'create extension if not exists timescaledb'

. "$P"
echo "SCRATCH READY: $(PGDATABASE=$DB psql -X -At -c "select current_database()||', tz='||current_setting('TimeZone')||', timescaledb '||extversion||', '||split_part(version(),' ',2) from pg_extension where extname='timescaledb'")"

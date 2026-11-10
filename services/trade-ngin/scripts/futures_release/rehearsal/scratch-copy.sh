#!/usr/bin/env bash
# Copies production into the local rehearsal server. Production is only read: every session there is read-only.
# futures_data, metadata and trading come with their rows; backtest comes as empty tables (rehearsal runs only add to it).
# The local copy's tables are plain tables, not hypertables. Re-running replaces the local copy.
# Stops before copying if the rows are over 30 GB, unless FORCE=1.
set -euo pipefail
CFG="$(cd "$(dirname "$0")/../../.." && pwd)/config/defaults.json"
LOG="$HOME/scratch-copy.log"; : > "$LOG"
. "$HOME/.scratch-pg"
L_HOST=$PGHOST L_PORT=$PGPORT L_USER=$PGUSER L_PW=$PGPASSWORD L_DB=$SCRATCH_DB
[ "$L_HOST" = 127.0.0.1 ] && [[ "$L_DB" == *scratch* ]] || { echo "local target is not a scratch database"; exit 2; }
unset PGHOST PGPORT PGUSER PGPASSWORD
export CFG
eval "$(python3 -c '
import json, os, shlex
d = json.load(open(os.environ["CFG"]))["database"]
for k, v in (("P_HOST", d["host"]), ("P_PORT", d["port"]), ("P_USER", d["username"]), ("P_PW", d["password"]), ("P_DB", d["name"])):
    print("%s=%s" % (k, shlex.quote(str(v))))')"
[ "$P_DB" = new_algo_data ] || { echo "defaults.json names $P_DB"; exit 2; }

prod() { PGHOST=$P_HOST PGPORT=$P_PORT PGUSER=$P_USER PGPASSWORD=$P_PW PGDATABASE=$P_DB PGCONNECT_TIMEOUT=20 \
         PGOPTIONS='-c default_transaction_read_only=on' "$@"; }
loc()  { PGHOST=$L_HOST PGPORT=$L_PORT PGUSER=$L_USER PGPASSWORD=$L_PW PGDATABASE=$L_DB "$@"; }

TABLES="select quote_ident(n.nspname)||'.'||quote_ident(c.relname) from pg_class c join pg_namespace n on n.oid=c.relnamespace
        where c.relkind in ('r','p') and not c.relispartition and n.nspname in ('futures_data','metadata','trading') order by 1"

echo "== sizes on production"
prod psql -X -At -F' ' -c "
  select n.nspname, c.relname,
         case when h.hypertable_name is not null then hypertable_size(c.oid) else pg_total_relation_size(c.oid) end as b
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  left join timescaledb_information.hypertables h on h.hypertable_schema=n.nspname and h.hypertable_name=c.relname
  where c.relkind in ('r','p') and not c.relispartition and n.nspname in ('futures_data','metadata','trading')
  order by b desc" > "$HOME/scratch-sizes.txt"
head -8 "$HOME/scratch-sizes.txt" | awk '{printf "%-14s %-34s %8.2f GB\n", $1, $2, $3/1073741824}'
TOTAL=$(awk '{s+=$3} END {printf "%.0f", s/1073741824}' "$HOME/scratch-sizes.txt")
echo "total about $TOTAL GB"
[ "$TOTAL" -le 30 ] || [ "${FORCE:-}" = 1 ] || { echo "STOPPED: over 30 GB. Nothing copied."; exit 3; }

echo "== structure"
loc psql -X -q -v ON_ERROR_STOP=1 -c 'drop schema if exists futures_data, metadata, trading, backtest cascade' 2>> "$LOG"
prod pg_dump -s --no-owner --no-acl -n futures_data -n metadata -n trading -n backtest \
  | sed '/transaction_timeout/d' | loc psql -X -q > /dev/null 2>> "$LOG" || true
# Timescale's guard on a hypertable parent would block every insert into the plain local table.
loc psql -X -At -c "select format('drop trigger %I on %s;', t.tgname, t.tgrelid::regclass) from pg_trigger t where t.tgname='ts_insert_blocker'" \
  | loc psql -X -q 2>> "$LOG"
echo "structure errors: $(grep -c 'ERROR' "$LOG" || true)"

echo "== rows"
for t in $(prod psql -X -At -c "$TABLES"); do
  r=$( { prod psql -X -q -c "\\copy (select * from $t) to stdout" \
         | PGOPTIONS='-c session_replication_role=replica' loc psql -X -c "\\copy $t from stdin"; } 2>> "$LOG" ) || { echo "FAILED: $t"; tail -3 "$LOG"; exit 1; }
  echo "$t ${r#COPY }"
done

# Sequences start from what is stored, or the first insert on the copy would collide.
loc psql -X -At -c "
  select format('select setval(%L, greatest(coalesce((select max(%I) from %s), 0), 1));', pg_get_serial_sequence(a.attrelid::regclass::text, a.attname), a.attname, a.attrelid::regclass)
  from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace
  where n.nspname in ('futures_data','metadata','trading','backtest') and c.relkind in ('r','p') and a.attnum>0 and not a.attisdropped
    and pg_get_serial_sequence(a.attrelid::regclass::text, a.attname) is not null" \
  | loc psql -X -q > /dev/null 2>> "$LOG"

loc psql -X -q -c 'analyze' 2>> "$LOG"
echo "COPY DONE. Errors in $LOG: $(grep -c 'ERROR' "$LOG" || true)"
grep 'ERROR' "$LOG" | sort | uniq -c | sort -rn | head -8 | cut -c1-150 || true

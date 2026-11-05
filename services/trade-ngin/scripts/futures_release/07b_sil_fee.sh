#!/bin/zsh
# 07b_sil_fee.sh: SIL's fee on the metadata row, 0.860 to 1.360 (README, "Before the seed: the fees"). Step 8a: after the
# micros fill, before the check of step 12.
#
#   zsh 07b_sil_fee.sh production                       new_algo_data (config/defaults.json); asks for the database name
#   SCRATCH_DB=<clone> CONFIRM=<clone> zsh 07b_sil_fee.sh scratch   a rehearsal clone (PG* from the environment)
#
# CHANGES: one row of metadata.contract_metadata, by load/fix_sil_fee.sql: "Fee Per Contract" and the fee line of
#   "Additional Notes". One transaction; it stops unless exactly that row is in its old state. A second run changes nothing.
# READS: the row; sql/expected_fingerprint.txt. The table's md5 afterwards must equal the fingerprint.
# TAKES: seconds. UNDO: load/fix_sil_fee.sql with its two values swapped.
set -u
. "${0:A:h}/lib.zsh"
case "${1:-}" in production|scratch) TARGET=$1 ;; *) sed -n '2,/^set -u/p' "$0" | sed '$d'; exit 2 ;; esac
resolve_target
mkdir -p "$RUN_DIR/data"; LOG="$RUN_DIR/data/sil_fee_${TAG}.log"
check_db
fee_now() { qr "select \"Fee Per Contract\" from metadata.contract_metadata where \"Databento Symbol\"='SIL'" }
fee=$(fee_now) || die "cannot read SIL's row"
if [ "$fee" = 0.860 ]; then
  c="${CONFIRM:-}"
  if [ -z "$c" ]; then print -n "type the database name to change SIL's fee on it: "; read -r c; fi
  [ "$c" = "$DBNAME" ] || die "'$c' is not $DBNAME; nothing changed" 2
  $PGBIN/psql -X -q -f "$PROD/load/fix_sil_fee.sql" || die "the update failed; nothing changed"
elif [ "$fee" = 1.360 ]; then say "SIL's fee is already 1.360; nothing changed"
else die "SIL's fee reads '$fee', neither 0.860 nor 1.360; nothing changed"; fi
want=$(awk '$1 == "metadata.contract_metadata" { print $3 }' "$PROD/sql/expected_fingerprint.txt")
got=$(qr "select md5(string_agg(t::text, E'\n' order by \"Databento Symbol\")) from metadata.contract_metadata t") || die "cannot read the metadata"
[ "$got" = "$want" ] || die "metadata md5 $got, expected $want"
say "SIL fee $(fee_now), metadata md5 $got: PASS"

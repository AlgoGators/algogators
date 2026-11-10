#!/usr/bin/env bash
# Rehearses the data steps (2, 3, 5, 7, 8, 8a) on the LOCAL scratch copy only. Stops at the first failure.
#   rehearse-data.sh <pull folder> [hole rows] [micro rows]
# Hole and micro counts are what the 2026-11-04 pull gave (476 and 8); a later pull gives other numbers, read them from the step output.
set -uo pipefail
. "$(dirname "$0")/lib.sh"
PULL="$(cd "${1:?pull folder}" && pwd)"; HOLES="${2:-476}"; MICROS="${3:-8}"
L=~/rehearse-data.log; : > "$L"
step zsh $S/01_readonly_checks.sh scratch start
step zsh $S/06_build_backfill.sh scratch "$PULL"
BF=$(grep -a '^BACKFILL=' "$L" | tail -1 | cut -d= -f2-); ROWS=$(grep -a '^ROWS=' "$L" | tail -1 | cut -d= -f2-)
[ -n "$BF" ] && [ -n "$ROWS" ] || { echo "no BACKFILL/ROWS printed"; exit 1; }
export BACKFILL="$BF" ROWS FILL_YES=1 CONFIRM=$SCRATCH_DB
step zsh $S/06_micros_part_A2.sh scratch
step zsh $S/04_fill_holes.sh scratch "$HOLES"
step zsh $S/07_micros_part_B.sh scratch
step zsh $S/08_fill_micros.sh scratch "$SCRATCH_DB" "$MICROS"
step zsh $S/07b_sil_fee.sh scratch
step zsh $S/01_readonly_checks.sh scratch after_data
echo "=== expected"; cut -c1-90 $S/sql/expected_fingerprint.txt

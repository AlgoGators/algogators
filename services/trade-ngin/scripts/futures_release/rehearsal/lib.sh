# Sourced by the rehearsal scripts: goes to the engine folder and loads the scratch login. Refuses anything but a local scratch DB.
ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ENGINE" || exit 1
. ~/.scratch-pg
[ "$PGHOST" = 127.0.0.1 ] && [[ "$SCRATCH_DB" == *scratch* ]] || { echo "not a local scratch database"; exit 2; }
S=scripts/futures_release
step() { echo "=== $*" | tee -a "$L"; "$@" < /dev/null 2>&1 | tee -a "$L" | tail -16 | cut -c1-200; rc=${PIPESTATUS[0]}; echo "--- exit $rc"; [ "$rc" = 0 ] || exit "$rc"; }

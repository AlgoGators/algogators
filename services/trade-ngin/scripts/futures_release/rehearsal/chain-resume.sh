#!/usr/bin/env bash
# Resumes the LOCAL scratch chain without a reset: REF=<sha> chain-resume.sh <first date to run>. Runs the check at the end.
set -uo pipefail
. "$(dirname "$0")/lib.sh"
export TARGET=scratch REF="${REF:?REF=<commit sha>}" END="${END:-2026-10-24}" FROM="${1:?first date}"
T0=$(date +%s)
zsh $S/11_live_chain.sh run < /dev/null 2>&1 | grep -aE 'rc=[1-9]|CHAIN DONE|REFUS|die|lock|already|exited' | tail -8 | cut -c1-200
echo "finished after $(( $(date +%s) - T0 ))s"
zsh $S/11_live_chain.sh check < /dev/null 2>&1 | tail -22 | cut -c1-170

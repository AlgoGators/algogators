#!/usr/bin/env bash
# Full live chain on the LOCAL scratch copy: REF=<sha> chain.sh <run tag>. Resets the book, runs to END, then checks it.
# About 2.2 hours at ~21 s a run on a 6 GB WSL. Close other memory users: a low-memory kill stops it (resume with chain-resume.sh).
set -uo pipefail
. "$(dirname "$0")/lib.sh"
export TARGET=scratch REF="${REF:?REF=<commit sha the release runs>}" END="${END:-2026-10-24}" CONFIRM=$SCRATCH_DB
export ACCEPT_METADATA_MD5="${ACCEPT_METADATA_MD5:-}"
rm -f futures_release_run/chain_end_*scratch*.txt
T0=$(date +%s)
zsh $S/10_live_reset.sh reset < /dev/null 2>&1 | tail -4 | cut -c1-170; [ "${PIPESTATUS[0]}" = 0 ] || exit 1
zsh $S/11_live_chain.sh run < /dev/null > ~/chain-${1:?run tag}.log 2>&1; rc=$?
echo "run exit $rc after $(( $(date +%s) - T0 ))s"; tail -5 ~/chain-$1.log | cut -c1-200
zsh $S/11_live_chain.sh check < /dev/null 2>&1 | tail -22 | cut -c1-170

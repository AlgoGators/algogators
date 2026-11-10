#!/usr/bin/env bash
# Pre-builds the release worktree with 2 jobs, so lib.zsh's -j 6 build has nothing left to do on a small WSL.
#   prebuild.sh [ref]   (default origin/main). Tests are off: GTest is not needed for the release.
set -uo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"; cd "$ROOT" || exit 1
git fetch -q origin main
REF=$(git rev-parse "${1:-origin/main}"); echo "REF=$REF ($(git log -1 --format='%h %s' "$REF" | cut -c1-80))"
T=$(grep -hoE 'prepare_worktree [a-z_ ]+' services/trade-ngin/scripts/futures_release/*.sh | sed 's/prepare_worktree //' | tr ' ' '\n' | sort -u | tr '\n' ' ')
echo "targets: $T"
G=$ROOT/.worktrees/futures_release
[ -d "$G" ] || git worktree add -q --detach "$G" "$REF"
git -C "$G" checkout -q --detach "$REF"
W=$G/services/trade-ngin
cmake -S "$W" -B "$W/build" -DCMAKE_BUILD_TYPE=Release -DTRADE_NGIN_BUILD_TESTS=OFF 2>&1 | tail -4
cmake --build "$W/build" -j 2 --target $T 2>&1 | tail -6
echo "build exit: ${PIPESTATUS[0]}"
free -m | sed -n 2p

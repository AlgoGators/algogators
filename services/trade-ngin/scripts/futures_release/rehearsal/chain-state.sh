#!/usr/bin/env bash
# Is the scratch chain running, how much memory is free, and how far it got.
ENGINE="$(cd "$(dirname "$0")/../../.." && pwd)"
pgrep -af 'live_portfolio|11_live_chain' | grep -v pgrep | cut -c1-90
free -m | sed -n 2p
f="$ENGINE/futures_release_run/scratch_scratch/live/checks/progress.txt"
wc -l < "$f"; tail -3 "$f" | cut -c1-70

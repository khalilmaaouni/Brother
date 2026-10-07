#!/bin/bash
# Silent watch for the unattended A/B/C sequence: returns on the first ALERT, arm END, DONE, or a loop_watch ACT (warning
# class) that was not already acknowledged in $1 (comma list). Prints only that event and the latest RUN-ABC lines.
AB=~/.claude/evidence/loop-run-2026-09-23/ab; E=~/.claude/evidence/loop-run-2026-09-23; SEEN=${1:-}
N0=$(grep -cE 'ALERT|END$|RUN ABC DONE' $AB/RUN-ABC.log)
while :; do
  N=$(grep -cE 'ALERT|END$|RUN ABC DONE' $AB/RUN-ABC.log)
  [ "$N" -gt "$N0" ] && { grep -E 'ALERT|END$|RUN ABC DONE|arm . ended' $AB/RUN-ABC.log | tail -3; exit 0; }
  if kill -0 $(cat $E/driver.pid 2>/dev/null) 2>/dev/null; then
    V=$(python3 -B ~/.claude/bin/loop_watch.py --lanes 6 --seen "$SEEN" --pid-file $E/driver.pid --log "$(cat $E/current-log.txt)" 2>&1 | tail -1)
    case "$V" in "ACT terminal"*) ;; ACT*) echo "$(date +%H:%M:%S) $V"; exit 0;; esac
  fi
  sleep 20
done

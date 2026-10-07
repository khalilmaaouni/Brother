#!/bin/bash
# Second measurement (owner 18:2x), unattended: two 60 min loop arms in seeded order (O A2), then the endpoint and analysis.
set -u
AB=~/.claude/evidence/loop-run-2026-09-23/ab; E=~/.claude/evidence/loop-run-2026-09-23; M=${1:-60}
log() { echo "$(date '+%H:%M:%S') $*" >> $AB/RUN-ABC.log; }
for ARM in O A2; do
  log "ARM $ARM START ($M min)"
  bash $AB/start_loop_arm.sh "$ARM" "$M" >> $AB/RUN-ABC.log 2>&1 || { log "ALERT ARM $ARM FAILED TO START"; exit 2; }
  bash $AB/end_loop_arm.sh "$ARM" >> $AB/RUN-ABC.log 2>&1
  bash $E/stop-all.sh >> $AB/RUN-ABC.log 2>&1
  log "ARM $ARM END"
done
log "SCORING"
python3 -B $AB/score_arms.py >> $AB/RUN-ABC.log 2>&1; log "score exit $?"
python3 -B $AB/analyze_abc.py > $AB/RESULT-print.txt 2>&1; log "analyze exit $?"
log "RUN ABC DONE"

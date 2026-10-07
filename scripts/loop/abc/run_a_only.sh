#!/bin/bash
# Rerun of arm A alone (17:1x, owner deadline 18:15), then the blind endpoint and the analysis over all three arms.
set -u
AB=~/.claude/evidence/loop-run-2026-09-23/ab; E=~/.claude/evidence/loop-run-2026-09-23; M=${1:-60}
log() { echo "$(date '+%H:%M:%S') $*" >> $AB/RUN-ABC.log; }
log "ARM A RERUN START ($M min)"
bash $AB/start_loop_arm.sh A "$M" >> $AB/RUN-ABC.log 2>&1 || { log "ALERT ARM A FAILED TO START"; exit 2; }
bash $AB/end_loop_arm.sh A >> $AB/RUN-ABC.log 2>&1
bash $E/stop-all.sh >> $AB/RUN-ABC.log 2>&1
log "ARM A END"
log "SCORING"
python3 -B $AB/score_arms.py >> $AB/RUN-ABC.log 2>&1; log "score exit $?"
python3 -B $AB/analyze_abc.py > $AB/RESULT-print.txt 2>&1; log "analyze exit $?"
log "RUN ABC DONE"

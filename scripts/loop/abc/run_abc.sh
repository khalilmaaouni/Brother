#!/bin/bash
# The whole A/B/C test, unattended (owner 2026-09-23: three sequential equal slots, seeded order A, C, B, finish about
# 18:15). Each arm gets M minutes; between arms everything of the loop is stopped and the meters are read. Then the blind
# endpoint and the analysis. Every step's line goes to RUN-ABC.log; an arm that fails to start STOPS the sequence (order kept).
set -u
AB=~/.claude/evidence/loop-run-2026-09-23/ab; E=~/.claude/evidence/loop-run-2026-09-23; M=${1:-64}
log() { echo "$(date '+%H:%M:%S') $*" >> $AB/RUN-ABC.log; }
loop_arm() {
  log "ARM $1 START ($M min)"
  bash $AB/start_loop_arm.sh "$1" "$M" >> $AB/RUN-ABC.log 2>&1 || { log "ALERT ARM $1 FAILED TO START: sequence stopped, order kept"; exit 2; }
  bash $AB/end_loop_arm.sh "$1" >> $AB/RUN-ABC.log 2>&1
  bash $E/stop-all.sh >> $AB/RUN-ABC.log 2>&1
  log "ARM $1 END"
}
log "RUN ABC BEGIN, $M min per arm, order A C B"
loop_arm A
log "ARM C START ($M min)"
if bash $AB/start_arm_c.sh >> $AB/RUN-ABC.log 2>&1; then
  S=$(date +%s); while [ $(( $(date +%s) - S )) -lt $(( M * 60 )) ]; do sleep 10; done
  bash $AB/stop_arm_c.sh >> $AB/RUN-ABC.log 2>&1
else
  log "ALERT ARM C FAILED TO START: sequence stopped, order kept"; exit 2
fi
log "ARM C END"
loop_arm B
log "SCORING"
python3 -B $AB/score_arms.py >> $AB/RUN-ABC.log 2>&1; log "score exit $?"
python3 -B $AB/analyze_abc.py > $AB/RESULT-print.txt 2>&1; log "analyze exit $?"
log "RUN ABC DONE"

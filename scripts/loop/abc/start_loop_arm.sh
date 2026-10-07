#!/bin/bash
# Start loop arm A or B for MINUTES (default 48) on its own worktree, own unit-runs folder, the frozen sample's units.
# Owner's arm definitions (question UI 2026-09-23):
#   A: builds 8 Sonnet 5 high; plan and advisor Opus 5.5 high (Fable on call to the orchestrator); checker Jev typed.
#   B: DeepSeek 5 + Sonnet 2 + Muse 1 builds; same advisor and planner; Jev checker; docs Luna medium outside the loop.
# Held constant: 6 lanes, 8 workers per round, advisor on, build plan on, grader, executed probes (DeepSeek + Muse, fixed
# in probe_wave.py: the measuring instrument), landing gates. OpenRouter bridge at xhigh, full max (owner 14:1x).
set -u
ARM=$1; MIN=${2:-48}; E=~/.claude/evidence/loop-run-2026-09-23; AB=$E/ab; a=$(echo "$ARM" | tr 'A-Z' 'a-z')
T=~/Brother/.claude/worktrees/abc-$a; STOP=$(date -v+${MIN}M '+%H:%M')
bash ~/Brother/.claude/worktrees/guards-fix/scripts/loop/stop_loop.sh --dry || { echo "loop alive; refusing"; exit 1; }
bash ~/.claude/bin/loop_guard.sh check || { echo "lease held; refusing"; exit 1; }
[ -d "$T" ] || { echo "no tree $T; run setup_arms.sh"; exit 1; }
SCOPE="^($(python3 -c "import json;print('|'.join(r['unit'] for r in json.load(open('$AB/sample.json'))['sample']))"))\$"
# ISOLATED RUN HISTORY: the pool reads READY/EXHAUSTED marks from unit-runs, so each arm starts from an empty folder
R=~/.claude/evidence/unit-runs; [ -L "$R" ] && rm "$R"; [ -d "$R" ] && mv "$R" ~/.claude/evidence/unit-runs-before-abc-$(date +%H%M)
mkdir -p ~/.claude/evidence/unit-runs-arm-$a; ln -s ~/.claude/evidence/unit-runs-arm-$a "$R"
# THE CANARY'S FIXTURE ONLY (14:51: an empty history made the canary NO-DATA and the intake refused arm A): its one known
# good build, a finished unit's sub unit outside every arm's scope, so the pool never reads it
CAN=$(ls -d ~/.claude/evidence/unit-runs-before-abc-*/D6.6-153134 2>/dev/null | head -1)
[ -n "$CAN" ] && [ ! -e "$R/D6.6-153134" ] && cp -R "$CAN" "$R/"
cd "$T" || exit 1
WORDS="A/B/C arm $ARM, sequential random order A C B, equal slots to 18:00, 60 USD OpenRouter grant until 6pm; OpenRouter models on xhigh with enough context; Claude as many tokens as necessary"
[ -e ~/.claude/evidence/LOOP-HOLD.txt ] && mv ~/.claude/evidence/LOOP-HOLD.txt $AB/LOOP-HOLD-lifted-for-$a.txt
python3 -B "$T/scripts/loop/loop_intake.py" prepare finisher=deepseek documenter=luna --deadline "$STOP" --budget-usd 25 --scope "$SCOPE" --words "$WORDS" > $AB/intake-$a.txt 2>&1
IRC=$?; tail -3 $AB/intake-$a.txt; [ $IRC -eq 0 ] || { [ -e $AB/LOOP-HOLD-lifted-for-$a.txt ] && mv $AB/LOOP-HOLD-lifted-for-$a.txt ~/.claude/evidence/LOOP-HOLD.txt; echo "intake exit $IRC NOT READY; HOLD restored; nothing started"; exit 2; }
set -a; . ~/.claude/evidence/loop-intake/launch-env.sh; set +a
export BROTHER_LAUNCH_WORKTREE="$T" BROTHER_SCOPE="$SCOPE" BROTHER_LANES=6 BROTHER_WORKERS_PER_ROUND=8 BROTHER_WIP=8
export BROTHER_REPAIR_ADVISOR=on BROTHER_BUILD_PLAN=on BROTHER_JEV_CHECK=on
export BROTHER_BUILD_PLAN_MODEL=opus55 BROTHER_PLANNER_EFFORT=high BROTHER_REPAIR_ADVISOR_MODEL=opus55 BROTHER_ADVISOR_EFFORT=high
export BROTHER_CLAUDE_EFFORT=high BROTHER_BRIDGE_EFFORT=xhigh
# LANDING VALUE 5 USD for BOTH loop arms (14:59: at the 1 USD default the EV gate refused every all Sonnet round before round 0,
# so arm A measured a constant, not a model). Same value for A and B; later rounds still gated by their own measured odds.
export BROTHER_VALUE_PER_LANDING=5
case "$ARM" in
  A|A2) export BROTHER_WORKER_MIX="sonnet:8";;
  O) # THE COST OPTIMIZED LOOP (owner 18:2x "bring down the cost of Brother.loop bellow that of C"): measured in arm B,
     # a Sonnet build cost 0.60 USD against about 0.02 for DeepSeek at a 28 percent grader pass, and all 21 Sonnet builds
     # were discarded; planner and advisor on Sonnet 5 at high (his floor) instead of Opus 5.5, half the list price
     export BROTHER_WORKER_MIX="deepseek:6,muse:2" BROTHER_BUILD_PLAN_MODEL=sonnet BROTHER_REPAIR_ADVISOR_MODEL=sonnet;;
  B) export BROTHER_WORKER_MIX="deepseek:5,sonnet:2,muse:1";;
  *) echo "arm must be A, A2, B or O"; exit 2;;
esac
unset BROTHER_PIN_MODEL BROTHER_CHECKER
env | grep -E '^BROTHER_' | sort > $AB/env-$a.txt
LOG=~/.claude/evidence/loop-until-abc-$a-$(date +%H%M).log
python3 -B $AB/or_meter.py start-$a || { echo "OpenRouter meter NO-DATA: refusing to start an unmetered arm"; exit 2; }
date '+%F %T' > $AB/start-$a.txt
# OWN SESSION (15:02: arm A's driver got INT or TERM from an unknown sender): setsid, so no signal to the launcher's group reaches it
nohup python3 -c 'import os,sys; os.setsid(); os.execvp("bash", ["bash"] + sys.argv[1:])' ~/.claude/bin/loop_until.sh "$STOP" 120 > "$LOG" 2>&1 &
echo $! > $E/driver.pid; sleep 4
DL=$(grep -oE 'log /[^ ]+\.log' "$LOG" | head -1 | cut -c5-); echo "${DL:-$LOG}" > $E/current-log.txt
echo "arm $ARM driver $(cat $E/driver.pid) until $STOP log $(cat $E/current-log.txt)"; head -6 "$LOG"

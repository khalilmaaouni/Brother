#!/bin/bash
# Arm C, SCRIPTED HUMAN DEVELOPER (owner 2026-09-23: "C which is manual should be like a human working on Claude Code in a
# real session ... We have to compare to what a real human developer would do"; no MCP). One continuing Claude Code
# session (Opus 5.5, effort high), STANDARD setup: project settings only (the repo's AGENTS.md and project hooks), no MCP
# servers, no user plugins. One sub unit at a time, three turns each, as a developer types them: implement, verify on both
# Pythons and fix, commit. No artificial waits (the most favourable pace for manual work); human review time is applied
# afterwards as a sensitivity. Sample first (nearest the median first), then the next open sub units of the same units.
# ONE continuous session (c_driver.py, owner 14:4x): no per turn restart, no early cut off; stop_arm_c.sh ends it at the slot end.
set -u
E=~/.claude/evidence/loop-run-2026-09-23; AB=$E/ab; T=~/Brother/.claude/worktrees/abc-c
[ -d "$T" ] || { echo "no tree $T"; exit 1; }
python3 -B $AB/or_meter.py start-c || { echo "OpenRouter meter NO-DATA: refusing to start an unmetered arm"; exit 2; }
python3 - "$AB" "$T" > $AB/c-queue.tsv <<'EOF'
import json, sys
ab, tree = sys.argv[1], sys.argv[2]
s = json.load(open(ab + "/sample.json"))["sample"]
plan = json.load(open(tree + "/docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json")); u = {x["id"]: x for x in plan["units"]}
q = [(r["unit"], r["sub"], r["check"]) for r in s]
for r in s:                                    # extras: the next open sub units of the same units, same order
    subs = u[r["unit"]]["sub_units"]; i = subs.index(r["sub"])
    for nxt in subs[i + 1:]: q.append((r["unit"], nxt, ""))
for unit, sub, chk in q: print("\t".join((unit, sub, u[unit]["spec"], chk)))
EOF
SID=$(uuidgen | tr 'A-Z' 'a-z'); echo "$SID" > $AB/arm-c-session.txt
date '+%F %T' > $AB/start-c.txt; : > $AB/c-turns.tsv
nohup python3 -B $AB/c_driver.py "$AB" "$T" "$SID" > $AB/c-driver.log 2>&1 &
echo $! > $AB/arm-c.pid; sleep 3
kill -0 $(cat $AB/arm-c.pid) 2>/dev/null || { echo "arm C driver died at start"; tail -5 $AB/c-driver.log; exit 2; }
echo "arm C driver pid $(cat $AB/arm-c.pid) session $SID started $(cat $AB/start-c.txt), queue $(wc -l < $AB/c-queue.tsv | tr -d ' ') sub units"

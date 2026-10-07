#!/bin/bash
# End arm C at its slot: stop the headless session (TERM, then KILL after 20 s), record the end time and its commits.
AB=~/.claude/evidence/loop-run-2026-09-23/ab; P=$(cat $AB/arm-c.pid 2>/dev/null)
SID=$(cat $AB/arm-c-session.txt 2>/dev/null)
# the driver loop first (no new turn starts), then the Claude turn in flight, found by its session id (never by name)
[ -n "$P" ] && kill -TERM "$P" 2>/dev/null
for i in 1 2 3 4; do C=$(pgrep -f -- "$SID"); [ -z "$C" ] && break; kill -TERM $C 2>/dev/null; sleep 5; done
C=$(pgrep -f -- "$SID"); [ -n "$C" ] && kill -KILL $C
[ -n "$(pgrep -f -- "$SID")" ] && echo "WARN a process carrying the arm C session is still alive"
date '+%F %T' > $AB/end-c.txt
python3 -B $AB/or_meter.py end-c
S=$(cut -c1-19 $AB/start-c.txt); N=$(cut -c1-19 $AB/end-c.txt)
python3 -B $AB/claude_window.py "$S" "$N" --only abc-c > $AB/claude-c.json
python3 -B $AB/claude_window.py "$S" "$N" --exclude cac2b82f-8813-4310-aeaa-3b308613f95b > $AB/claude-machine-c.json
python3 -B $AB/claude_window.py "$S" "$N" --session cac2b82f-8813-4310-aeaa-3b308613f95b > $AB/claude-orch-c.json
git -C ~/Brother/.claude/worktrees/abc-c log --format='%h %ad %s' --date=format:'%H:%M:%S' $(cat $AB/BASE.txt)..HEAD > $AB/commits-c.txt
git -C ~/Brother/.claude/worktrees/abc-c status --porcelain > $AB/uncommitted-c.txt
echo "arm C ended $(cat $AB/end-c.txt): $(wc -l < $AB/commits-c.txt | tr -d ' ') commit(s), $(wc -l < $AB/uncommitted-c.txt | tr -d ' ') uncommitted path(s)"

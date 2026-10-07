#!/bin/bash
# End a loop arm (A or B): wait for its driver to exit (it stops itself at the deadline), stop whatever of the loop is
# still alive 3 minutes later, then record end time, commits, the final BUDGET line and Claude tokens for the window.
set -u
ARM=$1; a=$(echo "$ARM" | tr 'A-Z' 'a-z'); E=~/.claude/evidence/loop-run-2026-09-23; AB=$E/ab
P=$(cat $E/driver.pid); L=$(cat $E/current-log.txt)
while kill -0 "$P" 2>/dev/null; do sleep 15; done
date '+%F %T' > $AB/end-$a.txt
sleep 180
bash ~/Brother/.claude/worktrees/guards-fix/scripts/loop/stop_loop.sh --dry > /dev/null 2>&1 || bash ~/Brother/.claude/worktrees/guards-fix/scripts/loop/stop_loop.sh > $AB/stop-$a.log 2>&1
git -C ~/Brother/.claude/worktrees/abc-$a log --format='%h %ad %s' --date=format:'%H:%M:%S' $(cat $AB/BASE.txt)..HEAD > $AB/commits-$a.txt
grep -E '^BUDGET' "$L" | tail -1 > $AB/budget-$a.txt
grep -E '^(LANDED|CLOSED|PULSE)' "$L" > $AB/landings-$a.txt
python3 -B $AB/or_meter.py end-$a   # AFTER the stop: straggler calls killed by the stop are billed by now
S=$(cut -c1-19 $AB/start-$a.txt); N=$(date '+%F %T')
python3 -B $AB/claude_window.py "$S" "$N" --only model-call --calls > $AB/claude-$a.json
python3 -B $AB/claude_window.py "$S" "$N" --exclude cac2b82f-8813-4310-aeaa-3b308613f95b > $AB/claude-machine-$a.json
python3 -B $AB/claude_window.py "$S" "$N" --session cac2b82f-8813-4310-aeaa-3b308613f95b > $AB/claude-orch-$a.json
echo "arm $ARM ended $(cat $AB/end-$a.txt): $(wc -l < $AB/commits-$a.txt | tr -d ' ') commit(s); $(cat $AB/budget-$a.txt)"

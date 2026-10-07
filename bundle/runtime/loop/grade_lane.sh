#!/bin/bash
# usage (repo root): grade_lane.sh <wave dir> <lane> ; grades <lane>-r0, r1, r2 ... in order and STOPS at the first PASS.
W=$1; L=$2; mkdir -p "$W/grades"
# ONE APPEND AT THE SOURCE (fix 8, 2026-09-22): every grade of the loop and of the repair lane passes here, so this is where the
# unit ledger grows. Its verdict never changes this script's exit code; a run folder it cannot read is NO-DATA on stdout.
# unit_ledger's OWN exit decides NO-DATA (2026-09-27): piped into tail, the status was tail's, so a failed append never said so.
ledger() { if [ -f ~/.claude/bin/unit_ledger.py ]; then lo=$(python3 -B ~/.claude/bin/unit_ledger.py --append-run "$W_LEDGER" 2>&1); lrc=$?; printf '%s\n' "$lo" | tail -n 1; [ "$lrc" -eq 0 ] || echo "LEDGER NO-DATA: append failed, exit $lrc (the ledger readers are blind to this grade)"; else echo "LEDGER NO-DATA: unit_ledger.py not installed"; fi; }
# a unit run has round dirs (W is one), a repair wave has its results at its top: the ledger takes the folder that holds results.json
if [ -f "$W/results.json" ] && [ -f "$(dirname "$W")/PID" ]; then W_LEDGER="$(dirname "$W")"; elif [ -f "$W/results.json" ] && ls "$(dirname "$W")"/round* >/dev/null 2>&1; then W_LEDGER="$(dirname "$W")"; else W_LEDGER="$W"; fi
for b in "$W"/out/"$L"-r*-build.json; do
  [ -e "$b" ] || continue
  n=$(basename "$b" -build.json); g="$W/grades/$n.txt"
  [ -s "$g" ] || ~/.claude/bin/grade_one.sh "$b"
  # THE GRADER'S EXIT AND ITS LAST VERDICT LINE DECIDE, NOTHING ELSE (Codex audit F3, 2026-09-27): any line starting PASS
  # used to count, and a worker's own text printed one above the real FAIL of a grade that exited 1.
  if [ "$(tail -n 1 "$g" 2>/dev/null)" = "exit=0" ] && [ "$(grep -E '^(PASS|FAIL)' "$g" | tail -n 1)" = "PASS" ]; then echo "$L PASS $n"; ledger; exit 0; fi
done
echo "$L NO-PASS"; ledger; exit 1

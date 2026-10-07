#!/bin/sh
# L5d, the owner's one command for the quiet window (docs/plan/specs/L5d.md step 10 to 13).
#
# usage (repo root, every Claude session closed): sh scripts/capture_l5d_quiet.sh [OUT_DIR]
#
# 1. refuses (exit 2) when OUT_DIR holds no loaded capture (spec step 9, loaded-l1-capture.json), naming the
#    command that takes one;
# 2. checks the quiet condition twice 60 s apart through scripts/perf_audit_run.py quiet-check (1 minute load at
#    or under the classifier's bar and claude_processes <= 1, counted by executable basename) and refuses with
#    exit 2 when the machine is not quiet, writing nothing;
# 3. runs the measurement the spec names (scripts/required_fast.sh once, serial, wall by time.monotonic, load
#    before and after) under a dated label so a second run never clobbers the first, classifies it against the
#    loaded capture, writes docs/architecture/PERF-AUDIT-L5D.json for a verdict that names a cause, and ends on
#    scripts/donecheck_l5d.py, whose verdict is the last thing printed;
# 4. names the L4 command that consumes the gate log this run wrote.
#
# OUT_DIR defaults to the captures directory holding the real loaded capture of 2026-09-29 (wall 2880 s at load
# 37.0 on 8 cores, gate killed by its timeout); L5D_OUT_DIR overrides it, and L5D_PYTHON names the interpreter
# (the test stands a stub in). No em or en dashes.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 2
PY="${L5D_PYTHON:-python3}"
OUT_DIR="${1:-${L5D_OUT_DIR:-$HOME/.claude/evidence/loop-remediation-0926/night-0929/l5d-c0/captures}}"
LOADED="$OUT_DIR/loaded-l1-capture.json"
LABEL="quiet-$(date +%Y%m%d%H%M%S)"

if [ ! -f "$LOADED" ]; then
  echo "NO-DATA: no loaded capture at $LOADED (spec step 9); take one while the machine is loaded with:"
  echo "  $PY -B scripts/perf_audit_run.py loaded --out-dir \"$OUT_DIR\""
  exit 2
fi

echo "quiet check: two readings 60 s apart"
if ! "$PY" -B scripts/perf_audit_run.py quiet-check --gap 60; then
  echo "NOT DONE: the machine is not quiet; nothing captured, nothing written (exit 2)"
  exit 2
fi

echo "quiet capture $LABEL against $LOADED"
"$PY" -B scripts/perf_audit_run.py quiet --out-dir "$OUT_DIR" --label "$LABEL" --budget 119 --gap 60
rc=$?
echo "L4 next, on the gate log this run wrote:"
echo "  python3 -B scripts/capture_l4_measurement.py \"$OUT_DIR/$LABEL-gate.log\" \"$OUT_DIR/l4\""
exit $rc

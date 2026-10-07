#!/bin/bash
# The proof pair launcher: RB, then RC starting 0 to 30 s after RB ends, on ONE frozen candidate, each run's result
# recorded, the pair accepted. It is deployed into the bin like every loop tool, so it is frozen with the pair.
# usage: proof_pair.sh [--rehearsal] [GAP]        GAP: seconds between passes (default 240)
#
# THE LIFECYCLE (docs/plan/PROOF-ACCEPTANCE.md; design S0 to S14, objections 5, 6, 7 and 18):
#   S0  PREFLIGHT. The owner's controls are clear: it refuses while LOOP-HOLD.txt or LOOP-PAUSE.txt exists and never
#       creates or removes either. The environment E that both runs get is assembled once: env -i with HOME, USER, LOGNAME (Claude finds its Keychain login by account name: without USER every
#       seat and the driver's refresh read Not logged in, 2026-10-04/05), PATH and
#       LANG, then launch-env.sh (the intake's settings), then the pair additions. E is validated: no BROTHER_CHECKER
#       (objection 5), no BROTHER_PROOF_MIN_WINDOW_S outside --rehearsal, a READY pair intake record that covers the
#       whole pair (objection 6), and with bounded abandons counted, a price catalog young enough to last the pair.
#       The pair directory is created with a plain mkdir (objection 7).
#   S1  FREEZE. The OpenRouter ledger baseline is captured once for both runs, then the candidate is frozen under E
#       (module roots bin/candidate, bin/candidate/scripts and the hooks root), and pair.json is written once.
#   S2  RB: the driver runs under E with BROTHER_PROOF_PHASE=RB and BROTHER_PROOF_RUN_DIR (it creates that directory
#       itself, refusing one that exists). Deadline: now + window + 180 s, rounded up to the minute. RB.result.json.
#   S11 HANDOFF: RB exited 0 with an unchanged DEADLINE receipt naming its own directory, the controls are still clear
#       and the pair record still covers RC. Anything else: no RC.
#   S12 RC as S2, RC.result.json.   S14 proof_accept.py --pair, its verdict kept as verdict.json in the pair directory.
# --rehearsal (objection 18) carries BROTHER_PROOF_MIN_WINDOW_S (required) and the disk, swap and file event seams and
# STOP_LOOP_ONLY from the caller into E; the knob is then frozen in the manifest, and acceptance refuses the pair.
# EXIT: 2 refused before RB (nothing started), 3 RB ran and RC did not start, else proof_accept's (0 PASS, 1 FAIL,
# 2 NO-DATA). Every refusal or stop prints one PAIR line and appends it to LOOP-ALARM-HISTORY.txt.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REHEARSAL=0; [ "${1:-}" = --rehearsal ] && { REHEARSAL=1; shift; }
GAP="${1:-240}"
case "$GAP" in ''|*[!0-9]*) echo "PAIR REFUSED: the gap between passes must be a whole number of seconds, got '$GAP'"; exit 2;; esac
HIST="$HOME/.claude/evidence/LOOP-ALARM-HISTORY.txt"
alarm() { mkdir -p "$(dirname "$HIST")" 2>/dev/null; printf '%s\n\n' "$1 at $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$HIST" 2>/dev/null; }
refuse() { echo "PAIR REFUSED: $1"; alarm "PAIR REFUSED: $1"; exit 2; }
stop() { echo "PAIR STOPPED after RB: $1"; alarm "PAIR STOPPED after RB: $1"; exit 3; }

WT="${BROTHER_LAUNCH_WORKTREE:-$PWD}"
RUNS="${BROTHER_RUNS_ROOT:-$HOME/.claude/evidence/loop-runs}"
STATE="${BROTHER_OR_STATE_ROOT:-$HOME/.claude/brother-or-dispatch-state}"
SCRATCH="${BROTHER_SCRATCH:-$HOME/.claude/brother-scratch}"
LAUNCH_ENV="$HOME/.claude/evidence/loop-intake/launch-env.sh"

# ---- S0: E. Sourced in a clean shell, dumped NUL separated so no value can split an entry.
DUMP='import os, sys
sys.stdout.write("".join("%s=%s\0" % kv for kv in sorted(os.environ.items()) if kv[0] not in ("PWD", "OLDPWD", "SHLVL", "_")))'
E=()
while IFS= read -r -d '' kv; do E+=("$kv"); done < <(env -i HOME="$HOME" USER="${USER:-$(id -un)}" LOGNAME="${LOGNAME:-$(id -un)}" PATH="$PATH" LANG="${LANG:-en_US.UTF-8}" \
  bash -c '. "$1" >/dev/null 2>&1 || exit 3; exec python3 -B -c "$2"' _ "$LAUNCH_ENV" "$DUMP")
get() { local kv; for kv in ${E[@]+"${E[@]}"}; do case "$kv" in "$1="*) printf '%s' "${kv#*=}"; return 0;; esac; done; return 1; }
put() { local kv out=(); for kv in ${E[@]+"${E[@]}"}; do case "$kv" in "$1="*) ;; *) out+=("$kv");; esac; done; E=(${out[@]+"${out[@]}"} "$1=$2"); }
inE() { env -i ${E[@]+"${E[@]}"} "$@"; }
get HOME >/dev/null || refuse "the launch settings $LAUNCH_ENV could not be read into the pair's environment"
# THE DESTINATION IS PINNED (Astra review 2026-10-05): the lander pushes to the launch worktree's upstream, so the pair
# records it once and every land_batch call refuses a different one (BROTHER_EXPECTED_UPSTREAM). A caller that names the
# destination it expects (a practice proof) is refused here, before anything runs, when the worktree tracks another.
UP=$(git -C "$WT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null) || UP=""
[ -n "$UP" ] || refuse "the launch worktree $WT has no upstream, so there is no destination to pin"
WANT=$(printenv BROTHER_EXPECTED_UPSTREAM) || WANT=""
[ -z "$WANT" ] || [ "$WANT" = "$UP" ] || refuse "the launch worktree $WT tracks $UP, not the expected $WANT"
put BROTHER_EXPECTED_UPSTREAM "$UP"
get BROTHER_CHECKER >/dev/null && refuse "BROTHER_CHECKER is set in the pair's environment (launch-env.sh); a checker's paid calls sit outside the pair's accounting, so a proof pair runs without one"
if [ "$REHEARSAL" = 1 ]; then
  W="${BROTHER_PROOF_MIN_WINDOW_S:-}"
  case "$W" in ''|*[!0-9]*|0) refuse "--rehearsal needs BROTHER_PROOF_MIN_WINDOW_S, a positive whole number of seconds";; esac
  put BROTHER_PROOF_MIN_WINDOW_S "$W"
  for k in BROTHER_DISK_FREE_KB BROTHER_SWAP_USED_MB BROTHER_FSEVENTSD_KB STOP_LOOP_ONLY; do
    v=$(printenv "$k") && put "$k" "$v"
  done
else
  get BROTHER_PROOF_MIN_WINDOW_S >/dev/null && refuse "BROTHER_PROOF_MIN_WINDOW_S is set in the pair's environment outside --rehearsal; a shortened window is never proof"
  W=28800
fi
HELD=$(inE python3 -B "$HERE/loop_hold.py" proof_pair 2>&1) || refuse "the owner's controls are not clear (${HELD}); nothing starts and neither file is touched"
COVER=$(inE BROTHER_PROOF_PHASE=RB python3 -B "$HERE/loop_intake.py" status 2>&1) || refuse "no pair intake record covers this pair: $(printf '%s' "$COVER" | head -1)"
BOUND=$(inE python3 -B -c 'import os, sys
sys.path.insert(0, sys.argv[1]); import proof_ledger
if not proof_ledger.BOUNDED_ABANDON_COUNTS:
    sys.exit(0)
sys.path.insert(0, os.path.join(sys.argv[1], "candidate"))
from plugin.runtime.brother.core import openrouter_prices as P
try:
    age = P.catalog_age_days(os.path.join(sys.argv[2], "openrouter-models.json"))
except P.CatalogError as exc:
    print("the price catalog cannot be read (%s)" % exc); sys.exit(1)
if age + 17 / 24.0 >= P.DEFAULT_MAX_AGE_DAYS:
    print("the price catalog is %.2f days old and would go stale inside the pair (limit %.1f days)" % (age, P.DEFAULT_MAX_AGE_DAYS)); sys.exit(1)' "$HERE" "$STATE" 2>&1) \
  || refuse "bounded abandons are counted and ${BOUND:-the price catalog check failed}"
[ -d "$HERE/candidate" ] || refuse "$HERE/candidate is not a directory: deploy the candidate first"

# ---- the pair directory, then S1
ID="$(date +%Y%m%d-%H%M%S)-$$"
PAIR="$RUNS/pair-$ID"; RB="$RUNS/run-RB-$ID"; RC="$RUNS/run-RC-$ID"
mkdir -p "$RUNS" && mkdir "$PAIR" || refuse "the pair directory $PAIR could not be created fresh"
echo "PAIR $ID: pair directory $PAIR"
SHA=$(inE python3 -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import proof_ledger; print(proof_ledger.capture(sys.argv[2], sys.argv[3]))' \
  "$HERE" "$STATE/openrouter-ledger.jsonl" "$PAIR/ledger-baseline.json" 2>&1) || refuse "the ledger baseline could not be captured: $(printf '%s' "$SHA" | tail -1)"
put PYTHONDONTWRITEBYTECODE 1
put PYTHONNOUSERSITE 1
put BROTHER_CODE_ROOT "$HERE/candidate"
put BROTHER_CLAUDE_CALLS_LEDGER "$PAIR/claude-calls.jsonl"
put BROTHER_FREEZE_MANIFEST "$PAIR/freeze.json"
put BROTHER_OR_STATE_ROOT "$STATE"
put BROTHER_PROOF_BASELINE "$PAIR/ledger-baseline.json"
put BROTHER_PROOF_BASELINE_SHA256 "$SHA"
put BROTHER_LAUNCH_WORKTREE "$WT"
put BROTHER_RUNS_ROOT "$RUNS"
put BROTHER_SCRATCH "$SCRATCH"
# the driver's own defaults, set here so the driver never adds a variable the freeze did not see; the VALUES must equal
# loop_until.sh's (plan E step 2f turned the advisor and build plan off on 2026-10-01: the proof measures what ships)
get BROTHER_REPAIR_ADVISOR >/dev/null || put BROTHER_REPAIR_ADVISOR off
get BROTHER_BUILD_PLAN >/dev/null || put BROTHER_BUILD_PLAN off
# FX-09 (2026-09-29) gave the driver a BROTHER_BRIEF_SCREEN default that this list did not carry, so every RB start read
# "FAIL drift in environment" against the manifest frozen here (2026-10-04); test_proof_pair_record pins the two lists.
get BROTHER_BRIEF_SCREEN >/dev/null || put BROTHER_BRIEF_SCREEN on
# A work input the candidate stage copied (native_worker reaches land_apply since 2026-10-05) runs from bin/candidate, so
# the freeze needs its relocated declaration too; deploy_stamped.candidate_work_inputs is the one list.
CWI=$(inE python3 -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import deploy_stamped as D
for w in D.candidate_work_inputs(sys.argv[2]): print("--work-input=" + w)' "$HERE" "$HERE/candidate" 2>&1) \
  || refuse "the candidate's work inputs could not be listed: $(printf '%s' "$CWI" | tail -1)"
WI=(--work-input land_apply.py:main=build-receipt-covers-new-modules)
while IFS= read -r w; do [ -n "$w" ] && WI+=("$w"); done <<< "$CWI"
FROZE=$(inE python3 -B "$HERE/freeze_manifest.py" write "$PAIR/freeze.json" --bin "$HERE" \
  --module-root "$HERE/candidate" --module-root "$HERE/candidate/scripts" --module-root "$HOME/.claude/hooks" \
  --optional-import duckdb=rollups "${WI[@]}" 2>&1) \
  || refuse "the freeze write refused: $(printf '%s' "$FROZE" | grep -E '^(NO-DATA|FAIL)' | head -3 | tr '\n' ' ')"
printf '%s\0' "${E[@]}" > "$PAIR/environment" || refuse "the pair's environment could not be recorded"
ENV_SHA=$(inE python3 -B -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$PAIR/environment")
MAN_SHA=$(inE python3 -B -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$PAIR/freeze.json")
UNTIL=$(inE python3 -B -c 'import json, sys; print(json.load(open(sys.argv[1]))["pair_until"])' "$HOME/.claude/evidence/loop-intake/CURRENT.json" 2>&1) \
  || refuse "the pair intake record names no pair_until"
OUT=$(inE python3 -B "$HERE/proof_launch.py" pair-write --pair-dir "$PAIR" --rb "$RB" --rc "$RC" --env-sha256 "$ENV_SHA" \
  --manifest-sha256 "$MAN_SHA" --pair-until "$UNTIL" 2>&1) || refuse "pair.json could not be written: $OUT"

deadline() {                    # now + window + 180 s, rounded up to the minute, as the driver reads it
  local d=$(( $(date +%s) + W + 180 )); d=$(( (d + 59) / 60 * 60 ))
  date -r "$d" '+%Y-%m-%d %H:%M'
}
run() {                         # run <RB|RC> <run dir>: the driver under E, then its result recorded once
  local dl rc rec=()
  dl=$(deadline)
  echo "PAIR $1: launching into $2, deadline $dl"
  inE BROTHER_PROOF_PHASE="$1" BROTHER_PROOF_RUN_DIR="$2" bash "$HERE/loop_until.sh" "$dl" "$GAP"
  rc=$?
  echo "PAIR $1: driver exit $rc"
  [ -f "$2/receipt/receipt.json" ] && rec=(--receipt "$2/receipt/receipt.json")
  OUT=$(inE python3 -B "$HERE/proof_launch.py" result --pair-dir "$PAIR" --phase "$1" --exit "$rc" ${rec[@]+"${rec[@]}"} 2>&1) \
    || echo "PAIR $1: the result could not be recorded: $OUT"
}
run RB "$RB"
# ---- S11: the handoff decides whether RC may start
OUT=$(inE python3 -B "$HERE/proof_launch.py" handoff --pair-dir "$PAIR" 2>&1) || stop "$OUT"
HELD=$(inE python3 -B "$HERE/loop_hold.py" proof_pair 2>&1) || stop "the owner's controls are not clear (${HELD}); both files are left as they are"
COVER=$(inE BROTHER_PROOF_PHASE=RC python3 -B "$HERE/loop_intake.py" status 2>&1) || stop "the pair intake record no longer covers RC: $(printf '%s' "$COVER" | head -1)"
echo "PAIR HANDOFF: RB ended DEADLINE with its receipt; RC starts"
run RC "$RC"
# ---- S14
inE python3 -B "$HERE/proof_accept.py" --pair "$PAIR" "$RB" "$RC" > "$PAIR/verdict.json"
A=$?
case "$A" in 0) WORD=PASS;; 1) WORD=FAIL;; *) WORD=NO-DATA;; esac
echo "PAIR VERDICT $WORD: $PAIR/verdict.json"
inE python3 -B -c 'import json, sys
rows = json.load(open(sys.argv[1]))["pair_checks"]
gap = [r for r in rows if r["check"] == "consecutive_windows"][0]
print("PAIR GAP %s s from RB receipt end to RC work start (%s)" % (gap.get("gap_seconds", "NO-DATA"), gap["verdict"]))' "$PAIR/verdict.json" 2>/dev/null \
  || echo "PAIR GAP NO-DATA: the verdict could not be read"
exit "$A"

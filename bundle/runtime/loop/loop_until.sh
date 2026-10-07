#!/bin/bash
# Run brother.loop passes until a wall clock hour, then stop and say why.
# usage: loop_until.sh <HH:MM> [seconds-between-passes]      e.g. loop_until.sh 18:00 240
#
# WHY A DRIVER AND NOT A CRON. The build runners are detached and keep working between orchestrator turns, but
# LANDING, CLOSING and REFILLING the pool are orchestrator actions, so without a driver the loop builds and then
# sits on finished work nobody lands. Measured 2026-09-21: nine finished builds sat unlanded and invisible.
#
# IT STOPS FOR FOUR REASONS AND NAMES EACH ONE. A run that cannot end is how an unattended loop pays forever:
#   42 FINISHED  every in scope unit is DONE with evidence, nothing READY, no runner alive
#   43 STALLED   work remains and not one sub unit is admissible; needs a decision, not another pass
#   DEADLINE     the wall clock hour arrived
#   UNFUNDED     burn_guard funds no lane, so another pass would spend nothing and change nothing
#   BUDGET       the run has spent the budget the owner set at intake (BUDGET_USD from loop_intake.py status at the
#                start, status --running every pass); exit 3
# Every unknown keeps working rather than stopping, because stopping early abandons real work while one more
# pass costs cents. The deadline is absolute and is checked BEFORE each pass, never after.
set -u
# A FLAG IS NEVER A DEADLINE. --help used to be read as the stop time: the script created its run scratch, pruned
# older scratch and overwrote the heartbeat before refusing. Help prints the header and exits 0, and any other
# argument that starts with a dash exits 2, both before anything is created, pruned or written.
case "${1:-}" in
  -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  -*) echo "loop_until.sh: unknown argument '$1': nothing was started. Run it with --help for the usage." >&2; exit 2 ;;
esac
# NO BYTECODE INTO THE FROZEN BIN, NO USER SITE (U2 B5-07, U4 B5-09), exported first so every child inherits both. A
# tool run without -B wrote __pycache__ into the frozen bin, which the end freeze reads as drift, and the user site's
# startup files ran before any tool. The pair launcher (proof_pair.sh) freezes the same two values.
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
STOP_HHMM="${1:-18:00}"
GAP="${2:-240}"
# THE WORKTREE IS THE ONE THE DRIVER IS STARTED IN, or the one named in BROTHER_LAUNCH_WORKTREE; never a literal path.
# A literal was true on one laptop and false in every other clone (owner, 2026-09-22: fix it at the root).
WT="${BROTHER_LAUNCH_WORKTREE:-$PWD}"; export BROTHER_LAUNCH_WORKTREE="$WT"
LOOP_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)   # resolved once, absolute: a later cd cannot move it
# ONE SCRATCH ROOT, ONE LIFETIME (owner law 2026-09-24): every temp file any child makes (fan outs, probes, spec
# gates, module tests inside a grade) lands under this run's own TMPDIR, pruned by age at every pass and removed
# when the run ends; nothing is left for the system temp folder to collect (39,916 leaked entries on 2026-09-23).
# The grader's slot sandboxes live beside it and are reused, never here.
export BROTHER_SCRATCH="${BROTHER_SCRATCH:-$HOME/.claude/brother-scratch}"
RUN_TMP="$BROTHER_SCRATCH/run-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$RUN_TMP" && export TMPDIR="$RUN_TMP"
# PRUNE THE WHOLE ROOT, NOT ONLY run-* (2026-09-30): the pattern above matched only the date stamped
# run directories, so 98 entries holding 6.7 GB of named lanes were never pruned, 55 of them registered
# git worktrees. scratch_prune.py widens the pattern AND adds the gate that widening requires: it keeps
# anything inside the age window, anything with an open file handle, and any worktree with uncommitted
# changes, removes a registered worktree with git rather than rm, and keeps whatever it cannot read.
python3 -B "$(dirname "${BASH_SOURCE[0]}")/scratch_prune.py" --root "$BROTHER_SCRATCH" \
  --repo "$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null)" \
  --max-age-hours 24 >/dev/null 2>&1 || true
# THE RECORDER IS ON FOR EVERY REAL RUN (owner 2026-09-24 08:5x: "why did we develop all this if you aren't using it
# anywhere"). The board's learning system (D0 to D13: journal, gate log, reports, worlds, replay, candidate grading,
# shadow runs, canaries with promotion and rollback, check ordering) records only when BROTHER_RUN_DIR names a run
# directory; the loop never set it, so every night left no learning event behind. The run directory is DURABLE
# (never under the pruned scratch); the three sandbox tools keep stripping the variable so test runs stay clean.
export BROTHER_RUNS_ROOT="${BROTHER_RUNS_ROOT:-$HOME/.claude/evidence/loop-runs}"   # the run directory: make_run_dir below
# the advisors and the brief screen are ON unless the intake says otherwise (owner 2026-09-22: repair advisor on high; plan
# before the build; FX-09: the brief carries THE GRADER'S SCREEN, generated from grade_build.py; export off to restore)
export BROTHER_REPAIR_ADVISOR="${BROTHER_REPAIR_ADVISOR:-off}" BROTHER_BUILD_PLAN="${BROTHER_BUILD_PLAN:-off}"   # off by plan E step 2f: no measured uplift
export BROTHER_BRIEF_SCREEN="${BROTHER_BRIEF_SCREEN:-on}"
# THE LOG NAME CARRIES THE DATE (finding 13, 2026-09-26): named by HH:MM alone, a later run at the same minute appended
# to an older day's log, and two logs were found holding two runs each.
LOG=~/.claude/evidence/loop-until-$(date +%Y%m%d-%H%M).log
ALARM=~/.claude/evidence/LOOP-ALARM.txt
HIST=~/.claude/evidence/LOOP-ALARM-HISTORY.txt
# A LOOP THAT REFUSES TO START MUST NOT BE SILENT. The four checks below all sat BEFORE the start
# note and every alarm channel, so each one exited 2 having written nothing anywhere. From outside,
# "refused to start" and "never launched" were the same silence: the owner starts a loop, walks
# away, and nothing runs and nothing says so. That is the wasted evening of 2026-09-21 in a new
# costume, so the refusals now speak on the same channels every other terminal state uses.
# refuse() NEVER touches the lease or the claim: two of these refusals happen precisely because
# ANOTHER session holds them, and releasing someone else's lease would be far worse than the bug.
refuse() {                      # refuse <one line reason>
  local msg; msg=$(printf '%s' "$1" | tr -d '"\\' | cut -c1-200)
  printf '%s\n' "LOOP REFUSED-TO-START at $(date '+%Y-%m-%d %H:%M:%S %Z')" "$1" "log: ${LOG}" >> "$HIST"
  echo "" >> "$HIST"
  python3 -B ~/.claude/bin/loop_heartbeat.py write --state REFUSED --reason "$1" --passes 0 --log "$LOG" >/dev/null 2>&1 || true
  osascript -e "display notification \"${msg}\" with title \"brother.loop REFUSED TO START\"" >/dev/null 2>&1 || true
  echo "REFUSED TO START: $1"
}
# THE RUN DIRECTORY. Outside a proof: a fresh durable folder under BROTHER_RUNS_ROOT. In a proof phase the pair launcher
# (proof_pair.sh) names it in BROTHER_PROOF_RUN_DIR and it is created HERE with a plain mkdir (objection 7): one that
# already exists is a reuse, refused and recorded outside it (proof_launch.refuse), so an older consistent run can never
# stand for a relaunch that failed. It is made after the owner's controls and the intake were read, so a refused start
# leaves no empty run directory behind.
make_run_dir() {
  if [ -n "${BROTHER_PROOF_PHASE:-}" ]; then
    if [ -z "${BROTHER_PROOF_RUN_DIR:-}" ]; then
      refuse "a proof phase needs BROTHER_PROOF_RUN_DIR from the pair launcher (proof_pair.sh), and none was given"; return 1
    fi
    export BROTHER_RUN_DIR="$BROTHER_PROOF_RUN_DIR"
    if ! mkdir "$BROTHER_RUN_DIR" 2>/dev/null; then
      if [ -e "$BROTHER_RUN_DIR" ] || [ -L "$BROTHER_RUN_DIR" ]; then
        python3 -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import proof_launch; sys.exit(1 if proof_launch.refuse(sys.argv[2], sys.argv[3]) else 0)' \
          "$LOOP_DIR" "$BROTHER_RUN_DIR" "the proof run directory already exists" >/dev/null 2>&1 \
          || echo "NOTE: the refusal could not be recorded outside $BROTHER_RUN_DIR"
      fi
      refuse "the proof run directory $BROTHER_RUN_DIR already exists or cannot be created; a proof run starts only in a fresh one"; return 1
    fi
    return 0
  fi
  export BROTHER_RUN_DIR="$BROTHER_RUNS_ROOT/run-$(date +%Y%m%d-%H%M%S)-$$"
  mkdir -p "$BROTHER_RUN_DIR" || { refuse "the run directory $BROTHER_RUN_DIR cannot be created; nothing would be recorded"; return 1; }
  # ABSOLUTE, ALWAYS (2026-10-04): every Claude call of the run is tagged from <run dir>/proof/start.json, and a relative
  # run directory names no run identity, so it would refuse each call
  BROTHER_RUN_DIR="$(cd "$BROTHER_RUN_DIR" && pwd)" || { refuse "the run directory cannot be made absolute"; return 1; }
  export BROTHER_RUN_DIR
}
# THE CODE ROOT (U3, B5-04 and B5-08): the tree the loop's CODE runs from, kept apart from the tree it lands into. The
# pair launcher sets BROTHER_CODE_ROOT to the frozen candidate (<bin>/candidate); outside a proof, with nothing set, the
# launch worktree is the code root, as before. A set value that is not an absolute directory, or a proof phase with no
# value, refuses: frozen code never quietly falls back to the landing tree (model_router.code_root() reads it the same way).
if [ -n "${BROTHER_CODE_ROOT:-}" ]; then
  case "$BROTHER_CODE_ROOT" in /*) ;; *) refuse "BROTHER_CODE_ROOT ${BROTHER_CODE_ROOT} is not an absolute path"; exit 2;; esac
  [ -d "$BROTHER_CODE_ROOT" ] || { refuse "BROTHER_CODE_ROOT ${BROTHER_CODE_ROOT} is not a directory"; exit 2; }
  CODE_ROOT="$BROTHER_CODE_ROOT"
elif [ -n "${BROTHER_PROOF_PHASE:-}" ]; then
  refuse "a proof phase runs frozen code only, and BROTHER_CODE_ROOT is not set"; exit 2
else
  CODE_ROOT="$WT"
fi
# THE DEADLINE HAS A DATE (H3, 2026-09-24 20:4x). Three forms: "HH:MM" (today; a time already past today means
# tomorrow, since a night armed at 20:36 for 05:00 can only mean the coming night), "YYYY-MM-DD HH:MM" (absolute),
# "tomorrow HH:MM". Before this, 05:00 armed before midnight was refused as "not in the future" and the night scripts
# only ever ran after midnight. STOP_HHMM keeps the HH:MM part for the funding guard and every message.
STOP_ARG="${STOP_HHMM}"; STOP_DATE="$(date +%Y-%m-%d)"
case "$STOP_ARG" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]\ [0-9]*:[0-9]*) STOP_DATE="${STOP_ARG%% *}"; STOP_HHMM="${STOP_ARG##* }";;
  tomorrow\ [0-9]*:[0-9]*) STOP_DATE="$(date -v+1d +%Y-%m-%d)"; STOP_HHMM="${STOP_ARG##* }";;
  [0-9]*:[0-9]*) ;;
  *) refuse "could not read the stop time '${STOP_ARG}'; give HH:MM, 'tomorrow HH:MM' or 'YYYY-MM-DD HH:MM'"; exit 2;;
esac
# the funding guard sizes lanes to THIS deadline, rounded up to the next hour (later hour = fewer lanes = the safe side).
# Exported only here, after the argument is parsed: a dated argument read as a number printed `value too great for base`.
export BROTHER_STOP_HOUR=$(( 10#${STOP_HHMM%%:*} + ( 10#${STOP_HHMM##*:} > 0 ? 1 : 0 ) ))
# THE DEADLINE IS THE MINUTE NAMED, TO THE SECOND: BSD date -j -f fills every field its format omits from the clock, so
# a format without seconds put each deadline 0 to 59 s after the minute (measured 2026-09-27: 02:52 read as 02:52:43).
STOP_EPOCH=$(date -j -f "%Y-%m-%d %H:%M:%S" "${STOP_DATE} ${STOP_HHMM}:00" +%s 2>/dev/null) || {
  refuse "could not read the stop time '${STOP_ARG}'; refusing to run without a deadline"; exit 2; }
NOW=$(date +%s)
if [ "$STOP_EPOCH" -le "$NOW" ] && [ "$STOP_ARG" = "$STOP_HHMM" ]; then
  STOP_DATE="$(date -v+1d +%Y-%m-%d)"; STOP_EPOCH=$(date -j -f "%Y-%m-%d %H:%M:%S" "${STOP_DATE} ${STOP_HHMM}:00" +%s)
  echo "DEADLINE ${STOP_HHMM} is past for today, so it means tomorrow ${STOP_DATE}"
fi
[ "$STOP_EPOCH" -le "$NOW" ] && { refuse "${STOP_ARG} is not in the future, so there is no deadline to run to"; exit 2; }

# THE DISK FLOOR (2026-09-23 21:41: the night run died on "No space left on device" in the middle of a pass and left
# no terminal line; the disk had filled while it ran). Below BROTHER_DISK_FLOOR_KB (default 2 GB) nothing starts, and
# a running driver stops its runners and raises DISK before the pass, while its own log can still be written.
# Unreadable free space holds exactly like too little: an unknown disk is never a roomy one. BROTHER_DISK_FREE_KB is
# the test seam that stands in for the df reading.
DISK_FLOOR_KB=${BROTHER_DISK_FLOOR_KB:-2097152}
# THE RESOURCE HOLD (owner law 2026-09-24, "optimize everything CPU, disk space, RAM"): the same function also reads
# swap and the file event daemon. On 2026-09-23 the loop's file churn (a clone per grade, a temp folder per call,
# a transcript per call) fed fseventsd to a 39 GB footprint and 32 GB of swap, which is what filled the disk; the
# disk floor alone fired only after the damage. Swap over BROTHER_SWAP_CEILING_MB (8 GB) or fseventsd over
# BROTHER_FSEVENTSD_CEILING_KB (2 GB) holds like a full disk. BROTHER_SWAP_USED_MB and BROTHER_FSEVENTSD_KB are the
# test seams. Unreadable holds: an unknown reading is never a roomy one.
SWAP_CEILING_MB=${BROTHER_SWAP_CEILING_MB:-8192}; FSE_CEILING_KB=${BROTHER_FSEVENTSD_CEILING_KB:-2097152}
disk_hold() {                   # prints why the machine holds the run, nothing when it does not
  local f s e
  f=${BROTHER_DISK_FREE_KB:-$(df -k "$HOME" 2>/dev/null | awk 'NR==2{print $4}')}
  case "$f" in
    ''|*[!0-9]*) echo "free disk space is unreadable";;
    *) [ "$f" -lt "$DISK_FLOOR_KB" ] && echo "free disk space $((f / 1024)) MB is under the floor of $((DISK_FLOOR_KB / 1024)) MB";;
  esac
  # THE SWAP HOLD IS OFF UNLESS BROTHER_SWAP_GUARD=on (owner 2026-09-25 09:0x: "Remove this until I say so"). It read
  # the whole machine's swap, so the owner's own apps (Teams and Edge, about 5 GB) stopped two runs in twelve hours
  # (22:11 on the 24th, 02:17 on the 25th) while the loop's own use was small. The disk floor and the daemon hold stay.
  if [ "${BROTHER_SWAP_GUARD:-off}" = on ]; then
  s=${BROTHER_SWAP_USED_MB:-$(sysctl -n vm.swapusage 2>/dev/null | sed -nE 's/.*used = ([0-9]+)(\.[0-9]+)?M.*/\1/p')}
  case "$s" in
    ''|*[!0-9]*) echo "swap usage is unreadable";;
    *) [ "$s" -gt "$SWAP_CEILING_MB" ] && echo "swap in use $s MB is over the ceiling of $SWAP_CEILING_MB MB";;
  esac
  fi
  e=${BROTHER_FSEVENTSD_KB:-$(ps -axo rss=,comm= 2>/dev/null | awk '$2 ~ /fseventsd$/{print $1; exit}')}
  case "$e" in
    ''|*[!0-9]*) echo "the file event daemon size is unreadable";;
    *) [ "$e" -gt "$FSE_CEILING_KB" ] && echo "the file event daemon holds $((e / 1024)) MB, over the ceiling of $((FSE_CEILING_KB / 1024)) MB: the machine is buffering file churn"; ;;
  esac
}
DISK_WHY=$(disk_hold); [ -n "$DISK_WHY" ] && { refuse "${DISK_WHY}; free space before starting a run"; exit 2; }

# THE OWNER'S STOP OUTRANKS EVERY SESSION. On 2026-09-22 the owner wrote "Stop the loop and fix all these issues" at
# 07:40 and an orchestrator session restarted it at 07:58 and again at 08:03 to prove its own fixes: the order had
# been answered in words and was checked by nothing, and the only deploy path also started the driver. A HOLD file
# is the order made mechanical. While ~/.claude/evidence/LOOP-HOLD.txt exists NO driver starts, whoever asks; its
# text is the reason shown. It is written when the owner says stop and removed only on the owner's word, never by
# a session that wants to see its fix run. Checked BEFORE the lease, so a held loop touches nothing.
# One reader for both controls. A missing tool or crashed reader also holds.
control_clear() {
  local rc
  CONTROL_WHY=$(python3 -B "${LOOP_DIR}/loop_hold.py" driver 2>&1); rc=$?
  case "$rc" in
    0) return 0;;
    5) [ -n "$CONTROL_WHY" ] || CONTROL_WHY="HELD: control reader returned no reason";;
    *) CONTROL_WHY="NO-DATA: control reader failed (exit ${rc}); ${CONTROL_WHY}";;
  esac
  return 5
}
if ! control_clear; then
  refuse "a HOLD is in force (${CONTROL_WHY}); owner controls must be readable and clear"; exit 2
fi

# NO RUN WITHOUT AN INTAKE (owner, 2026-09-22: "We need an intake process for the loop preparation"). The night before,
# a run started from a hand edited env file: a new judge on as a gate, a deadline and a cap that disagreed between
# files, no end to end proof. loop_intake.py checks all of it and writes a record; this driver starts ONLY on a record
# whose verdict is READY and that is fresh. A missing tool, a missing record, a stale one or any other verdict all
# refuse: the unknown never starts a run. Checked before the lease, so a refused start touches nothing.
INTAKE_SAYS=$(python3 -B ~/.claude/bin/loop_intake.py status 2>&1); INTAKE_RC=$?
if [ "$INTAKE_RC" -ne 0 ]; then
  refuse "no startable intake record ($(printf '%s' "$INTAKE_SAYS" | tail -1 | tr -d '"\\' | cut -c1-140)); run loop_intake.py guide, then prepare"; exit 2
fi
# THE BUDGET IS A CONTROL, NOT A RECORD. Until 2026-09-22 13:16 the intake wrote the owner's budget into CURRENT.json and
# nothing read it: the only stop was the global cap, 10 USD above the figure he gave. The driver reads the budget from
# the intake, the spend from burn_guard at the start and before every pass, and stops the run when the difference reaches
# the budget. Unreadable money refuses to start and refuses to pass: an unknown spend is never treated as a small one.
BUDGET_USD=$(printf '%s\n' "$INTAKE_SAYS" | sed -n 's/^BUDGET_USD \([0-9][0-9.]*\)$/\1/p' | head -1)
if [ -z "$BUDGET_USD" ]; then
  refuse "the intake record names no budget (no BUDGET_USD line from loop_intake.py status); prepare a new intake with --budget-usd"; exit 2
fi
# THE INTAKE'S FIGURES AS READ AT THIS START (U1, B5-11): every pass compares against these, never against this driver's
# own argument, because a pair record names the pair's end, not this run's deadline. A later difference is a change the
# owner made, recorded as an intervention (U11); an unchanged figure is not one.
intake_deadline() { printf '%s\n' "$1" | sed -n 's/^DEADLINE \(.*\)$/\1/p' | head -1; }
INTAKE_DEADLINE0=$(intake_deadline "$INTAKE_SAYS")
money_of() { printf '%s\n' "$1" | sed -n 's/^MONEY *spent \([0-9][0-9.]*\) .*/\1/p' | head -1; }
# A guard that printed a MONEY line and no FUNDING line READ the money (the format before 2026-09-27): its zero is SPENT.
# Only a read that printed no MONEY line at all is an unknown.
funding_of() { local f; f=$(printf '%s\n' "$1" | sed -n 's/^FUNDING \([A-Z-]*\).*/\1/p' | head -1)
  if [ -z "$f" ] && printf '%s\n' "$1" | grep -q '^MONEY'; then f=SPENT; fi; printf '%s\n' "$f"; }
funding_why() { { printf '%s\n' "$1" | grep -m1 '^NO-DATA' || printf '%s\n' "$1" | sed -n 's/^FUNDING [A-Z-]* //p' | head -1 | grep . || printf '%s\n' "$1" | grep -m1 '^MONEY'; } | tr -d '"\\' | cut -c1-200; }
# THE BUDGET COUNTS BOTH LEDGERS (2026-09-24, I5): the MONEY line is OpenRouter's; the Claude children (planner, advisor,
# native builders) write done rows with cost_usd to the Claude ledger, which used to sit outside the run budget entirely
# (24.02 USD of Claude in 22 minutes once read as "BUDGET 2.60"). An unreadable ledger stops further spending and remains unknown.
CLAUDE_LEDGER="${BROTHER_CLAUDE_CALLS_LEDGER:-$HOME/.claude/evidence/claude-calls.jsonl}"
# ONE LEDGER, SPELLED ABSOLUTE, FOR EVERY CHILD (Lane R): children run from other directories (the code root), and the
# proof start record binds this path, so a relative name is resolved once, here, and exported.
case "$CLAUDE_LEDGER" in "~/"*) CLAUDE_LEDGER="$HOME/${CLAUDE_LEDGER#\~/}";; /*) ;; *) CLAUDE_LEDGER="$PWD/$CLAUDE_LEDGER";; esac
export BROTHER_CLAUDE_CALLS_LEDGER="$CLAUDE_LEDGER"
# ONE READER OF THE CLAUDE LEDGER (review 2026-09-26): claude_ledger.py pairs a done row with its start only through the call
# id, refuses a null, NaN, negative or bool cost as evidence, and tells an unreadable ledger from an empty one. It prints
# "USD <x>" and "NOTE <text>"; an unreadable answer from the reader itself is NO-DATA too, never 0.0000 of known money.
make_run_dir || exit 2
# FX-06.3 THE RUNFLOW READER, ONCE PER RUN (R-FX-06-10). Right after the run directory exists and before every
# refusal below it, so a start that is refused records nothing: this block only reads. run_ledger.py mode prints
# the resolved word as its first line and, for an unknown value, one NOTE as its second; loop_runflow.py turns
# those two lines into the word the rest of this driver tests and the one log line an unknown value earns. The
# 2>/dev/null on both calls is the whole fallback: a missing or broken reader prints nothing, which reads off,
# which is today's behaviour. Nothing reaches $LOG unless a NOTE was printed, and that is before RUN START.
RUNFLOW_OUT=$(python3 -B "${LOOP_DIR}/run_ledger.py" mode 2>/dev/null)
RUNFLOW_PLAN=$(printf '%s\n' "$RUNFLOW_OUT" | python3 -B "${LOOP_DIR}/loop_runflow.py" plan 2>/dev/null)
RUNFLOW=$(printf '%s\n' "$RUNFLOW_PLAN" | head -1)
case "$RUNFLOW" in shadow|on) ;; *) RUNFLOW=off;; esac
RUNFLOW_NOTE=$(printf '%s\n' "$RUNFLOW_PLAN" | sed -n 2p)
if [ -n "$RUNFLOW_NOTE" ]; then echo "$RUNFLOW_NOTE" | tee -a "$LOG"; fi
# A HOLD OR PAUSE MAY APPEAR DURING THE SETUP ABOVE (intake, budget, ledger reads). Re-check the owner
# controls immediately before proof-start, so a proof never starts while the owner has stopped the loop.
if ! control_clear; then
  refuse "a HOLD is in force (${CONTROL_WHY}); owner controls must be readable and clear"; exit 2
fi
# Record the frozen boundary before any lease or paid pass starts.
RUN_START_ISO=$(python3 -B "${LOOP_DIR}/loop_receipt.py" proof-start --run-dir "$BROTHER_RUN_DIR" --deadline-epoch "$STOP_EPOCH")
if [ $? -ne 0 ]; then
  refuse "proof start could not record a frozen runtime and observed sandbox"; exit 2
fi
# A RUN'S OWN MONEY (owner, 2026-09-27: "Each run has its own budget and does not carry over. If a run stops for good it
# loses its budget ... Separate their budget ledgers"). A plain run's OpenRouter ledger, budget record and provider
# balance cache live in <run dir>/money, and every child spends there: the dispatcher's cap IS this run's budget until
# its deadline, and nothing another run or day wrote can fund or starve it. Before this, one shared ledger held every
# run since 2026-09-21, and one timed out call in it stopped a 100 USD run at 0.23 (15:18, UNFUNDED). A proof run keeps
# the shared root its pair launcher froze, and its own proof accounting.
run_budget_write() {
  [ -n "${RUN_MONEY_ROOT:-}" ] || return 0
  python3 -B -c 'import sys, datetime; sys.path.insert(0, sys.argv[1]); from plugin.runtime.brother.core import openrouter_ledger as L; L.write_run_budget(sys.argv[2], sys.argv[3], float(sys.argv[4]), datetime.datetime.fromtimestamp(int(sys.argv[5])).astimezone().isoformat(timespec="seconds"))' \
    "$CODE_ROOT" "$RUN_MONEY_ROOT" "$(basename "$BROTHER_RUN_DIR")" "$BUDGET_USD" "$STOP_EPOCH"
}
if [ -z "${BROTHER_PROOF_PHASE:-}" ]; then
  RUN_MONEY_ROOT="$BROTHER_RUN_DIR/money"
  if ! run_budget_write; then
    refuse "the run's own budget record could not be written in ${RUN_MONEY_ROOT}; nothing starts on money it cannot bound"; exit 2
  fi
  export BROTHER_OR_STATE_ROOT="$RUN_MONEY_ROOT"
  echo "MONEY ROOT ${RUN_MONEY_ROOT}: this run's own ledger, ${BUDGET_USD} USD of OpenRouter until ${STOP_HHMM}" | tee -a "$LOG"
fi
claude_tally() { python3 -B "${LOOP_DIR}/claude_ledger.py" tally "$CLAUDE_LEDGER" "$RUN_START_ISO" 2>/dev/null; }
claude_spent() { printf '%s\n' "$1" | sed -n 's/^USD \([0-9][0-9.]*\)$/\1/p' | head -1; }
claude_note() { if printf '%s\n' "$1" | grep -q '^USD '; then printf '%s\n' "$1" | sed -n 's/^NOTE //p' | head -1; else echo "NO-DATA: the Claude ledger reader gave no answer"; fi; }
SPENT0=$(money_of "$(python3 -B ~/.claude/bin/burn_guard.py 2>/dev/null)")
if [ -z "$SPENT0" ]; then
  refuse "burn_guard printed no MONEY line, so the spend against the ${BUDGET_USD} USD budget cannot be measured; nothing starts on unknown money"; exit 2
fi
if [ ! -f "$CODE_ROOT/scripts/worktree_sentry.py" ]; then
  refuse "the code root ${CODE_ROOT} has no scripts/worktree_sentry.py: start the driver from the launch worktree, or set BROTHER_LAUNCH_WORKTREE or BROTHER_CODE_ROOT"; exit 2
fi

# OWNERSHIP. Refuse to start beside another live loop: two drivers ran overlapping passes on 2026-09-21
# because an earlier stop killed a shell wrapper and not the script.
if ! bash ~/.claude/bin/loop_guard.sh acquire $$ ; then
  refuse "another loop already holds the lease; this driver will not run beside it"; exit 2
fi
# THE WORKTREE, not just the loop. The lease above stops a second DRIVER; it does nothing about a second
# SESSION, and a second session editing this tree is exactly what blocked the loop at 17:22 on 2026-09-21 and
# cost the owner 1h42m. This claim is cooperative and says so; the fingerprint check inside each pass is the
# half that needs nobody's agreement.
if ! python3 -B "$CODE_ROOT/scripts/worktree_sentry.py" claim $$ ; then
  bash ~/.claude/bin/loop_guard.sh release >/dev/null 2>&1
  refuse "another session claims this worktree; this driver will not edit a tree it does not own"; exit 2
fi
HEARTBEAT=~/.claude/evidence/LOOP-HEARTBEAT.json
# NEVER delete the previous alarm: this line used to be `rm -f "$ALARM"`, so starting a new run
# destroyed the record of how the last one ended. An alarm mechanism that erases the last alarm
# leaves nothing to find the morning after. Retire it into the history file instead.
if [ -f "$ALARM" ]; then cat "$ALARM" >> "$HIST"; echo "" >> "$HIST"; rm -f "$ALARM"; fi
# EVERY terminal state raises an alarm FILE as well as a log line. The log is where the 2026-09-21 block died
# unread for 1h42m. A file at a known path is what a watcher can see without reading a log it does not know about.
# A FILE IS NOT A NOTIFICATION. raise() wrote $ALARM and nothing else, so a blocked loop sat
# unread for 1h42m on 2026-09-21 while the owner waited for a result. A path only helps someone
# who already knows to look at it. announce() puts the same line where a human actually is: the
# desktop. It never fails the caller, because an alarm that can crash the driver is worse than
# no alarm. Deliberately no `say`: a spoken line at 03:00 is not a kindness.
announce() {                    # announce <STATE> <one line reason>
  local msg; msg=$(printf '%s' "$2" | tr -d '"\\' | cut -c1-200)
  osascript -e "display notification \"${msg}\" with title \"brother.loop $1\"" >/dev/null 2>&1 || true
  # A BANNER IS NOT AN ALARM UNDER DO NOT DISTURB. Measured 2026-09-22: the 07:34 UNPRODUCTIVE alarm posted its
  # banner with Focus on, macOS swallowed it, osascript still exited 0, and the driver believed it had told someone.
  # An ALERT is a window: Focus does not hide it and it stays on screen until a human dismisses it. It blocks the
  # osascript that shows it, so it runs detached and can never hold or fail the driver.
  # A NORMAL END IS NOT AN EMERGENCY (owner 2026-09-22 17:0x read the 17:00 DEADLINE alert as "broken again"). DEADLINE
  # and FINISHED still show a window, styled informational and titled as a normal end; every other state stays critical.
  case "$1" in
    DEADLINE|FINISHED|STOPPED) ( osascript -e "display alert \"brother.loop $1: normal end\" message \"${msg}\" as informational" >/dev/null 2>&1 & ) >/dev/null 2>&1 || true;;
    *) ( osascript -e "display alert \"brother.loop $1\" message \"${msg}\" as critical" >/dev/null 2>&1 & ) >/dev/null 2>&1 || true;;
  esac
}
# THE END REPORT RUNS DETACHED (U12, objection 18): in its own session, stdin from /dev/null, its output to $REPORT only,
# after a delay (BROTHER_REPORT_DELAY_S, default 60), so it holds no descriptor of the driver's caller (the pair
# launcher reads the driver's output to its end) and never competes with the next run's setup. It reads the log and
# never writes it: the receipt's digest of the log stays true. Arguments: REPORT LOG ALARM RUN_DIR CODE_ROOT DELAY.
# EXIT 3 IS A REPORT WITH A COST UNKNOWN (Lane G, 2026-09-27): loop_report.py wrote the report but a cost was null,
# missing, NaN or negative. The report is kept with its sections and the alarm says a cost is unknown, so it never
# reads as a clean spend; any other non zero exit is still a report that failed, named with its exit code.
REPORT_JOB='sleep "$6"
python3 -B "$HOME/.claude/bin/loop_report.py" --run "$2" > "$1" 2>&1; rc=$?
if [ "$rc" = 0 ] || [ "$rc" = 3 ]; then
  [ "$rc" = 3 ] && echo "report: written, but a cost is unknown (NO-DATA), so it is not a clean spend: see $1" >> "$3"
  { echo; python3 -B "$HOME/.claude/bin/salvage.py" list 2>&1 | head -60; } >> "$1" 2>/dev/null
  { echo; echo "== learning journal ($4)"; (cd "$5" && python3 -B -m plugin.runtime.brother.core.dream_report "$4" 2>&1 | head -40); } >> "$1" 2>/dev/null
else
  echo "report: FAILED to generate (exit $rc), see $1" >> "$3"
fi'
raise() {                       # raise <STATE> <one line reason>
  # ONE END PER RUN (audit E3, 2026-09-27): a signal arriving while this end runs is noted and the end completes once.
  # It used to start a second end inside the first (a second ending, settle and receipt). A trap, not an ignore: an
  # ignored signal is inherited by every child started here, the detached report job included.
  END_SIGNALLED=""; trap 'END_SIGNALLED=1' INT TERM HUP
  # A FAILED END EXITS 1 WHATEVER ITS STATE, and says why: END_NOTE is appended to the reason the alarm, the receipt and
  # the heartbeat carry. Sources: a pass fault (audit E4), the ending marker, the runner stop (audit E2), the end
  # snapshot, the receipt. The pair launcher's handoff refuses RC on any exit but 0.
  END_FAILED=0; END_NOTE=""
  # ONE END PER RUN, CLAIMED BEFORE ANY WRITE (2026-10-04: a second end appended to the driver log after the receipt
  # had hashed it). Claim exit 3: another end owns this run, so this one writes nothing to the run log, only a side note,
  # and exits 46 (its own code: never 0, which a handoff reads as a clean end).
  # Exit 4: the owner is gone without a receipt: NO-DATA, said in the same side note, exit 1. Anything else: the
  # claim is unknown, so this end runs and fails (exit 1) saying why.
  if [ -n "${BROTHER_RUN_DIR:-}" ]; then
    CLAIM_OUT=$(python3 -B "${LOOP_DIR}/loop_receipt.py" end-claim --run-dir "$BROTHER_RUN_DIR" --pid "$$" 2>&1); CLAIM_RC=$?
    case "$CLAIM_RC" in
      0) ;;
      3) printf '%s %s %s: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$CLAIM_OUT" "$1" "$2" >> "$BROTHER_RUN_DIR/end-duplicates.log" 2>/dev/null; exit 46;;
      4) printf '%s %s %s: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$CLAIM_OUT" "$1" "$2" >> "$BROTHER_RUN_DIR/end-duplicates.log" 2>/dev/null; exit 1;;
      *) END_FAILED=1; END_NOTE="${END_NOTE}; END FAILED: the end claim could not be taken (exit ${CLAIM_RC}: ${CLAIM_OUT})";;
    esac
  fi
  if [ "${PASS_FAULTS:-0}" -gt 0 ]; then
    END_FAILED=1; END_NOTE="${END_NOTE}; END FAILED: ${PASS_FAULTS} pass fault(s), the last ${LAST_FAULT}"
  fi
  # THE END SEQUENCE (U5, U8; Lane R), in this order and before the end clock is read, so every call admitted in the
  # window is settled inside it. S6 ENDING: the ending marker, taken under both ledger locks; from it on every
  # registration for this run refuses, whatever the end state. A proof run whose barrier cannot be recorded fails its
  # end (exit 1): calls could still register after it.
  ENDING_OUT=$(python3 -B "${LOOP_DIR}/loop_receipt.py" proof-ending --run-dir "$BROTHER_RUN_DIR" --state "$1" 2>&1) \
    || { END_FAILED=1; ENDING_OUT="ENDING FAILED: ${ENDING_OUT}"; END_NOTE="${END_NOTE}; END FAILED: the ending marker was not recorded"; }
  printf '%s\n' "$ENDING_OUT" | tee -a "$LOG"
  # S7 SETTLE: wait for this run's open reservations and pending Claude calls; leftovers are named (exit 2, logged) and
  # the run still ends, the receipt reading them as NO-DATA. An owner stop or a kill gets 3 s, not 120: stop_loop.sh
  # KILLs a driver still alive 12 s after its TERM, and a driver killed inside its end writes no receipt at all.
  case "$1" in STOPPED|INTERRUPTED) SETTLE_S=3;; *) SETTLE_S=120;; esac
  SETTLE_OUT=$(python3 -B "${LOOP_DIR}/loop_receipt.py" proof-settle --run-dir "$BROTHER_RUN_DIR" --max-seconds "$SETTLE_S" 2>&1)
  echo "SETTLE exit $?: ${SETTLE_OUT}" | tee -a "$LOG"
  # S8 STOP, decided once, here (U8, B5-22): EVERY end stops the detached runners before the end clock, except FINISHED,
  # which has none by definition (audit E3: a TERM to the driver alone has nobody else to stop its runners, and detached
  # work outlived the receipt). BLOCKED, STALLED and UNPRODUCTIVE used to leave them "to finish work someone will read";
  # measured 2026-09-27, a BLOCKED end left 21 paid bridge calls running after its receipt, and in a proof pair nobody
  # reads them (RC never starts after such an end). A stop that fails fails the end (audit E2: STOP INCOMPLETE ended
  # DEADLINE at exit 0, which the handoff accepted).
  STOP_OK=1
  case "$1" in FINISHED) ;; *)
    if ! stop_runners; then
      STOP_OK=0; END_FAILED=1; END_NOTE="${END_NOTE}; END FAILED: the runner stop failed, so runners may still be alive"
    fi;;
  esac
  # DRAIN PAUSE CLEARED BEGIN: only the pause this driver wrote for its own deadline drain is removed, AFTER the runner stop above, so no runner
  # sees the pause vanish mid drain, and so the next run
  # does not start paused; an owner pause (any other text) is never touched.
  case "$(head -c 15 ~/.claude/evidence/LOOP-PAUSE.txt 2>/dev/null)" in "deadline drain:") rm -f ~/.claude/evidence/LOOP-PAUSE.txt;; esac
  # DRAIN PAUSE CLEARED END
  # THE RUN SCRATCH GOES ONLY WHEN THE STOP CONFIRMED NOTHING OF THE LOOP ALIVE (Lane C's item, 2026-09-27). The stop reads
  # ownership through loop_procs (the tool as the executable, or an interpreter's script or -m module) and exits 0 only
  # when nothing matching is left. The deadline end used to ask `pgrep -f unit_runner.py` instead, which matched any
  # command line that merely named the file and read a pgrep error as "no runner", which deleted the scratch.
  case "$1" in DEADLINE|DISK)
    if [ "$STOP_OK" = 0 ]; then echo "run scratch kept: a runner may still be alive" | tee -a "$LOG"; else rm -rf "$RUN_TMP"; fi;;
  esac
  RECEIPT_END=$(python3 -B "${LOOP_DIR}/loop_receipt.py" clock)
  if ! python3 -B "${LOOP_DIR}/loop_receipt.py" proof-end --run-dir "$BROTHER_RUN_DIR" --end "$RECEIPT_END"; then
    END_FAILED=1; END_NOTE="${END_NOTE}; END FAILED: the end snapshot was not taken"
  fi
  printf '%s\n' "LOOP $1 at $(date '+%Y-%m-%d %H:%M:%S %Z')" "$2${END_NOTE}" \
    "passes: ${PASS:-0} | log: ${LOG}" > "$ALARM"
  cat "$ALARM" >> "$HIST"; echo "" >> "$HIST"
  # THE END NOTE. A loop that ends should leave the standard report behind it, written from the
  # ledgers, not wait for someone to hand write one later. The report path is printed into the
  # alarm file itself, so whoever finds the alarm also finds the evidence without being told
  # where to look. It never fails the caller: an end that cannot be reported is still an end.
  REPORT=~/.claude/evidence/LOOP-REPORT-$(date '+%Y-%m-%d-%H%M%S').txt
  # THE REPORT IS THIS RUN'S, NOT THE LAST 24 HOURS' (2026-09-26): the dated RUN END line closes the window that
  # loop_report.py --run reads; every raise is followed by an exit, so this line is written once, at the true end.
  echo "RUN END $(date '+%Y-%m-%dT%H:%M:%S')" >> "$LOG"
  # The report (with what was paid for and did not land, and the learning journal's own report run from the code
  # root) is REPORT_JOB above, started detached; it can never fail or hold the end.
  REPORT_DELAY="${BROTHER_REPORT_DELAY_S:-60}"; case "$REPORT_DELAY" in ''|*[!0-9]*) REPORT_DELAY=60;; esac
  if python3 -B -c 'import subprocess, sys
subprocess.Popen(["bash", "-c", sys.argv[1]] + sys.argv[2:], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, start_new_session=True)' "$REPORT_JOB" report-later "$REPORT" "$LOG" "$ALARM" \
       "$BROTHER_RUN_DIR" "$CODE_ROOT" "$REPORT_DELAY" >/dev/null 2>&1; then
    echo "report: $REPORT" >> "$ALARM"
    echo "report scheduled: $REPORT (written in the background after ${REPORT_DELAY} s)" | tee -a "$LOG"
  else
    echo "report: FAILED to start, nothing will be written to $REPORT" >> "$ALARM"
  fi
  [ -n "$END_SIGNALLED" ] && echo "NOTE a signal arrived during the end; this end completes once" | tee -a "$LOG"
  echo "ALARM $1: $2${END_NOTE}" | tee -a "$LOG"
  # THE RECEIPT (AGENTS.md "The receipt contract", audit issue F23): a run that changes anything owes
  # a receipt, and 0 of 15 run directories held one on 2026-09-26. Written once, here, LAST of every
  # log write this function makes, through the one writer scripts/loop/loop_receipt.py: budget, spend,
  # landings and the log's own digest, each a number when readable and a named NO-DATA when not. IT IS
  # THE LAST LINE TO TOUCH $LOG, not merely the last line after RUN END: the digest is only as good as
  # the byte range it covers, and a digest taken before the report or ALARM lines were appended would
  # name a log that no longer matches by the time anyone reads the receipt. A field this end cannot
  # answer (SPENT is unset until the first pass) is handed through as an empty string, which the
  # writer turns into NO-DATA rather than a fabricated zero. Claude spend is not handed through at
  # all (U9): the writer tallies it after the final pass, from the ledger bytes the end retained.
  # A WRITE THAT FAILS IS REPORTED, NEVER SILENT, but never back onto $LOG: a log line naming the
  # failure would itself change the log after the digest inside the (failed) receipt was already
  # computed, so the outcome goes to the alarm (the channel every other terminal state already uses
  # for "a human should look") and to this process's own stdout, which the driver's caller and every
  # test harness both capture.
  RECEIPT_OUT=$(python3 -B "${LOOP_DIR}/loop_receipt.py" write \
    --run-dir "$BROTHER_RUN_DIR" --pid "$$" \
    --start "$RUN_START_ISO" --end "$RECEIPT_END" \
    --state "$1" --reason "$2${END_NOTE}" \
    --deadline "$STOP_HHMM" --budget "$BUDGET_USD" \
    --spent-before "${SPENT0:-}" --spent-after "${SPENT:-}" \
    --log-path "$LOG" --cwd "$WT" 2>&1)
  if [ $? -eq 0 ]; then
    echo "receipt: $RECEIPT_OUT" >> "$ALARM"
    echo "receipt written: $RECEIPT_OUT"
  else
    echo "receipt: RECEIPT FAILURE, ${RECEIPT_OUT:-loop_receipt.py gave no reason}" >> "$ALARM"
    END_FAILED=1; END_NOTE="${END_NOTE}; END FAILED: the receipt was not written"
    echo "RECEIPT FAILURE: ${RECEIPT_OUT:-loop_receipt.py gave no reason}"
  fi
  # The heartbeat and the desktop come last, when every failure of the end is known: a failed end reads ALARM on the
  # heartbeat (--end-failed) and is announced as critical, whatever its state (audit E2, E4, E5).
  if [ "$END_FAILED" = 1 ]; then HB_FAILED=--end-failed; SAY="$1 END FAILED"; else HB_FAILED=""; SAY="$1"; fi
  python3 -B ~/.claude/bin/loop_heartbeat.py write --state "$1" --reason "$2${END_NOTE}" --passes "${PASS:-0}" --log "$LOG" $HB_FAILED >/dev/null 2>&1 || true
  announce "$SAY" "$2${END_NOTE}"
  bash ~/.claude/bin/loop_guard.sh release >/dev/null 2>&1
  python3 -B "$CODE_ROOT/scripts/worktree_sentry.py" release >/dev/null 2>&1
  # THE FINISHER RUNS, ALWAYS (owner, 2026-09-22; docs/plan/loop-roles.json "after_a_run"): after every run it gives each
  # unlanded build one repair round and lands it through every gate, and nothing restarts before it has run. Nothing
  # started it (2026-09-28: run B ended three times, 107 grader passing builds unlanded, the finisher never ran). It is
  # started detached and waits for THIS driver to exit, so its own "nothing of the loop is alive" check holds. An owner
  # stop, a runner stop that failed, or no finisher chosen at intake skips it, said on the alarm and stdout only: the
  # receipt above stays the last write to $LOG.
  case "$1" in STOPPED|INTERRUPTED) FIN_SKIP="an owner stop ends everything";; *) FIN_SKIP="";; esac
  [ "$STOP_OK" = 1 ] || FIN_SKIP="the runner stop failed, so a runner may still be alive"
  # OFF BY PLAN E (step 2d, 2026-10-01): a detached finisher that pays, promotes and lands after the terminal receipt is
  # work outside the run's ownership and receipt. It runs only when BROTHER_FINISHER is exactly on.
  python3 -B "${LOOP_DIR}/loop_switches.py" BROTHER_FINISHER >/dev/null 2>&1 || FIN_SKIP="off by plan E (BROTHER_FINISHER is not on)"
  [ -n "${BROTHER_FINISHER_MODEL:-}" ] || FIN_SKIP="no finisher was chosen at intake"
  if [ -z "$FIN_SKIP" ]; then
    FIN_LOG="${LOG%.log}-finisher.log"
    FIN_JOB='while kill -0 "$1" 2>/dev/null; do sleep 2; done; cd "$4" || exit 1; exec python3 -B "$3/finish_run.py" --model "$2" >> "$5" 2>&1'
    if python3 -B -c 'import subprocess, sys
subprocess.Popen(["bash", "-c", sys.argv[1]] + sys.argv[2:], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, start_new_session=True)' "$FIN_JOB" finisher "$$" "$BROTHER_FINISHER_MODEL" \
         "$HOME/.claude/bin" "$WT" "$FIN_LOG" >/dev/null 2>&1; then
      echo "finisher: scheduled with ${BROTHER_FINISHER_MODEL} once this driver exits, log ${FIN_LOG}" >> "$ALARM"
      echo "FINISHER scheduled: ${BROTHER_FINISHER_MODEL}, log ${FIN_LOG}"
    else
      echo "finisher: FAILED to start; run finish_run.py --model ${BROTHER_FINISHER_MODEL} by hand" >> "$ALARM"
      echo "FINISHER FAILED to start"
    fi
  else
    echo "finisher: skipped, ${FIN_SKIP}" >> "$ALARM"
    echo "FINISHER skipped: ${FIN_SKIP}"
  fi
  [ "$END_FAILED" = 1 ] && exit 1
  return 0
}
# AN OWNER STOP IS NOT A CRASH (finding 19, 2026-09-26): stop_loop.sh writes one line "<driver pid> <reason>" per driver
# it is about to TERM into LOOP-STOP-REQUEST.txt. A signal is STOPPED only when that file names THIS driver's pid, so a
# stale request left by another run can never relabel a real kill; every other signal stays INTERRUPTED.
STOP_REQ=~/.claude/evidence/LOOP-STOP-REQUEST.txt
# A RUNNING PASS IS INTERRUPTIBLE TOO (X2 finding 2, 2026-09-27). The pass ran in the foreground and bash runs a trap only
# after its foreground command, so a TERM to the driver alone (the pair launcher's driver leads no group, so stop_loop.sh
# signals it alone) waited out the whole pass; a pass longer than the stop grace was KILLed first, with no end and no
# receipt (measured: driver exit -9, end sequence never entered). The pass now runs in the background, in its OWN process
# group, and the driver waits: `wait` returns at once on a trapped signal. The wrapper restores INT and QUIT, which bash
# ignores in a background command, so the pass and all it starts keep the dispositions they had in the foreground.
# NAMED LIMIT: a signal landing between the `&` and PASS_PID=$! (two adjacent commands) is not forwarded; that pass runs
# on as it did on every stop before this.
PASS_PID=""
run_pass() {                    # sets RC to the pass's exit code, read exactly as a foreground pass's
  python3 -B -c 'import os, signal, sys
os.setpgid(0, 0)
signal.signal(signal.SIGINT, signal.SIG_DFL); signal.signal(signal.SIGQUIT, signal.SIG_DFL)
os.execvp(sys.argv[1], sys.argv[1:])' bash ~/.claude/bin/loop_pass.sh >> "$LOG" 2>&1 &
  PASS_PID=$!
  wait "$PASS_PID"; RC=$?
  PASS_PID=""
}
# THE STOP IS FORWARDED TO THE PASS'S WHOLE GROUP, before any end step: the pass shell alone dies at once on TERM and
# orphans the step it runs (a landing, the pool), which then works on past the receipt. A group still alive 3 s after
# its TERM is KILLed, so the forward never holds the end past the stop grace. The pass's own EXIT trap (its heartbeat
# pulse) has run by then, so the end's terminal heartbeat is written last.
stop_pass() {
  local p="$PASS_PID" i=0
  [ -n "$p" ] || return 0
  PASS_PID=""
  kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null   # the pid alone only before the wrapper's setpgid
  while { kill -0 -- "-$p" || kill -0 "$p"; } 2>/dev/null && [ "$i" -lt 15 ]; do sleep 0.2; i=$((i + 1)); done
  if { kill -0 -- "-$p" || kill -0 "$p"; } 2>/dev/null; then
    kill -KILL -- "-$p" 2>/dev/null || kill -KILL "$p" 2>/dev/null
    echo "NOTE the running pass (pid $p) outlived its TERM by 3 s and was killed" | tee -a "$LOG"
  else
    echo "NOTE the running pass (pid $p) was stopped before the end" | tee -a "$LOG"
  fi
}
on_signal() {
  local why
  stop_pass                     # bash runs no trap inside this one: a second signal while the pass stops starts no end
  why=$(awk -v me="$$" '$1 == me { $1 = ""; sub(/^ /, ""); print; exit }' "$STOP_REQ" 2>/dev/null)
  if [ -n "$why" ]; then
    rm -f "$STOP_REQ" 2>/dev/null || true   # read once: a recycled pid in a later run can never match a stale request
    raise STOPPED "stopped on request: ${why}"; exit 0
  fi
  raise INTERRUPTED "the driver was killed or the shell exited"; exit 130
}
trap on_signal INT TERM HUP
# EVERY WAIT IS INTERRUPTIBLE (audit E3, bounded waits): bash runs a trap only after its foreground command ends, so a
# TERM arriving in a 10 s sleep waited the slice out before the end began, and stop_loop.sh KILLs a driver still alive
# 12 s after its TERM (measured 10.0 s before this). `wait` returns at once on a trapped signal; the orphaned sleep ends
# by itself and holds no descriptor of the driver's caller. A running pass is waited for the same way (run_pass).
nap() { sleep "$1" </dev/null >/dev/null 2>&1 & wait $!; }
echo "LOOP UNTIL ${STOP_HHMM} | gap ${GAP}s | log ${LOG}" | tee -a "$LOG"
# THE DEADLINE IS ABSOLUTE FOR THE RUNNERS TOO, AND SO IS THE MONEY. runner_pool starts every unit_runner detached in
# its own session (start_new_session), so the driver's exit left them building and spending past the stop hour,
# sometimes for five rounds, and an UNFUNDED end left them buying rounds (B5-22). raise() calls this once, after the
# ending marker and the settle, for DEADLINE, DISK, BUDGET, UNFUNDED, STOPPED and INTERRUPTED (U8, audit E3); see raise().
# Each runner is a session leader, so its whole group (the fan out, graders, probes) dies with one signal to the
# negative pid. Proven 2026-09-22 on a fake leader with a sleeper child: both dead, the 8 real runners untouched.
stop_runners() {
  # THE PATTERN LIVES IN ONE FILE (2026-09-22): bounded stages and fan outs run in their OWN sessions, so every kill
  # site must name them all. This branch had the full list while the restart script and a hand stop carried a
  # narrower copy, and 15 paid processes outlived a stop. stop_loop.sh is that one list; --runners-only leaves THIS
  # driver alive to write its own end note. ITS EXIT CODE IS STOP_LOOP'S, NEVER TEE'S (audit E2, 2026-09-27): the pipe
  # returned tee's 0 for STOP INCOMPLETE (exit 1) and NO-DATA (exit 2); raise() reads this one.
  bash ~/.claude/bin/stop_loop.sh --runners-only 2>&1 | tee -a "$LOG"
  return "${PIPESTATUS[0]}"
}
REPORT_PASSES="${REPORT_PASSES:-1,3,6,10,15,20}"   # then every 5 (25, 30, ...); the owner's cadence, overridable per launch
digest_pass() {                 # digest_pass <n>: 0 when pass n is a scheduled digest pass
  local n="$1"
  case ",${REPORT_PASSES}," in *",${n},"*) return 0;; esac
  if [ "$n" -ge 20 ] && [ $(( n % 5 )) -eq 0 ]; then return 0; fi
  return 1
}
if [ -f ~/.claude/evidence/LOOP-DIGEST.md ]; then mv ~/.claude/evidence/LOOP-DIGEST.md ~/.claude/evidence/LOOP-DIGEST-$(date '+%Y%m%d-%H%M%S').md; fi
# Setup and worktree acquisition never count toward an unattended proof.
WORK_START_ISO=$(python3 -B "${LOOP_DIR}/loop_receipt.py" proof-work-start --run-dir "$BROTHER_RUN_DIR" --deadline-epoch "$STOP_EPOCH")
if [ $? -ne 0 ]; then
  raise INTERRUPTED "proof work start refused: deadline or frozen boundary invalid after setup"; exit 2
fi
RUN_START_ISO="$WORK_START_ISO"
PASS=0
PASS_FAULTS=0; LAST_FAULT=""; PASS_FAULT_LIMIT=3   # audit E4: unexpected pass exits, counted; the third ends the run
START_EPOCH=$(date +%s)   # the pulse counts only what THIS run did
WARNED=""                  # warning kinds already announced, so a standing warning rings once and not every pass
# THE START NOTE. There was none: raise() is only ever called on a terminal state, so a loop
# beginning was silent by construction and "the start notes did not trigger" was exactly right.
# A start note is not a failure, so it goes to the history and the desktop but never to $ALARM,
# whose presence means something needs a human.
printf '%s\n' "LOOP STARTED at $(date '+%Y-%m-%d %H:%M:%S %Z')" \
  "running passes until ${STOP_HHMM}, gap ${GAP}s" "log: ${LOG}" >> "$HIST"
echo "" >> "$HIST"
python3 -B ~/.claude/bin/loop_heartbeat.py write --state STARTED --reason "running passes until ${STOP_HHMM}" --passes 0 --log "$LOG" >/dev/null 2>&1 || true
announce STARTED "running passes until ${STOP_HHMM}, gap ${GAP}s"
echo "NOTE STARTED: running until ${STOP_HHMM}" | tee -a "$LOG"
echo "RUN START ${RUN_START_ISO}" | tee -a "$LOG"   # dated, so loop_report.py --run can bound this run (a log name holds HHMM only)
while :; do
  # THE RUN'S BUDGET AND DEADLINE MAY CHANGE MID RUN, only through the intake (owner 2026-09-22). Re-read both every pass,
  # with `status --running`: the START rules (a pair record's remaining coverage, a plain record's age) never gate this
  # read (audit E1, 2026-09-27: seven hours into a pair the start check failed, no BUDGET_USD line came back, and a cut
  # from 10.00 to 0.50 USD was ignored with no intervention recorded). AN UNREADABLE BUDGET HOLDS: no pass runs until it
  # reads again (below, after the deadline check); it used to keep the last figure, which is how a cut went unseen.
  # Each figure is compared with the value the intake gave at THIS run's start (INTAKE_DEADLINE0, the budget as last
  # adopted), never with the driver's argument; every change is recorded as an intervention (U1, U11).
  _SAYS=$(python3 -B ~/.claude/bin/loop_intake.py status --running 2>/dev/null)
  _B=$(printf '%s\n' "$_SAYS" | sed -n 's/^BUDGET_USD \([0-9][0-9.]*\)$/\1/p' | head -1)
  INTAKE_HOLD=""
  if [ -z "$_B" ]; then
    INTAKE_HOLD=$(printf '%s\n' "$_SAYS" | head -1 | tr -d '"\\' | cut -c1-160); [ -n "$INTAKE_HOLD" ] || INTAKE_HOLD="loop_intake.py status --running gave no answer"
  elif [ "$_B" != "$BUDGET_USD" ]; then
    python3 -B "${LOOP_DIR}/loop_receipt.py" proof-event --run-dir "$BROTHER_RUN_DIR" --kind budget-change --detail "intake changed the budget from ${BUDGET_USD} to ${_B} USD" || { raise INTERRUPTED "proof intervention recording failed"; exit 2; }
    echo "NOTE budget changed by the intake from ${BUDGET_USD} to ${_B} USD" | tee -a "$LOG"
    BUDGET_USD=$_B
    run_budget_write || { raise INTERRUPTED "the budget change could not be written to the run's own record"; exit 2; }
  fi
  _D=$(intake_deadline "$_SAYS")
  if [ -n "$_D" ] && [ "$_D" != "$INTAKE_DEADLINE0" ]; then
    python3 -B "${LOOP_DIR}/loop_receipt.py" proof-event --run-dir "$BROTHER_RUN_DIR" --kind deadline-change --detail "intake changed the deadline from ${INTAKE_DEADLINE0:-none} to ${_D}" || { raise INTERRUPTED "proof intervention recording failed"; exit 2; }
    INTAKE_DEADLINE0=$_D
    case "$_D" in [0-9][0-9][0-9][0-9]-*) _DD=$_D;; *) _DD="$(date +%Y-%m-%d) ${_D}";; esac
    _E=$(date -j -f "%Y-%m-%d %H:%M:%S" "${_DD}:00" +%s 2>/dev/null) && { STOP_HHMM=${_D##* }; STOP_EPOCH=$_E; export BROTHER_STOP_HOUR=$(( 10#${STOP_HHMM%%:*} + ( 10#${STOP_HHMM##*:} > 0 ? 1 : 0 ) )); echo "NOTE deadline changed by the intake to ${_D}" | tee -a "$LOG"; }
    run_budget_write || { raise INTERRUPTED "the deadline change could not be written to the run's own budget record"; exit 2; }
  fi
  NOW=$(date +%s)
  if [ "$NOW" -ge "$STOP_EPOCH" ]; then
    raise DEADLINE "the wall clock deadline ${STOP_HHMM} arrived; this is a normal end, nothing is wrong"
    exit 0
  fi
  # DEADLINE DRAIN BEGIN (plan E step 1, 2026-10-01): 106 of the 134 live dead-runner runs on this machine died within
  # 30 minutes of a driver start, killed mid round by an end or a restart. A runner already leaves cleanly at its next
  # hold_gate when the pause file exists (PAUSED, outputs kept, re-seated on resume), so the driver pauses BEFORE the
  # deadline and the end then has only stragglers to stop. Written once, tagged, removed by the end path below.
  # A run that STARTS inside the lead never drains (it would pause before its first pass and do nothing): the time left at
  # the first look is kept, and the drain arms only for a run that began with more time than the lead.
  : "${DRAIN_FIRST_LEFT:=$(( STOP_EPOCH - NOW ))}"
  if [ "${DEADLINE_DRAIN:-0}" = 0 ] && [ "$DRAIN_FIRST_LEFT" -gt "${BROTHER_DRAIN_LEAD_S:-1200}" ] && [ $(( STOP_EPOCH - NOW )) -le "${BROTHER_DRAIN_LEAD_S:-1200}" ] && [ ! -e ~/.claude/evidence/LOOP-PAUSE.txt ]; then
    if printf 'deadline drain: %s in %ss, written %s\n' "${STOP_HHMM}" "$(( STOP_EPOCH - NOW ))" "$(date '+%Y-%m-%d %H:%M:%S %Z')" > ~/.claude/evidence/LOOP-PAUSE.txt; then
      DEADLINE_DRAIN=1; echo "$(date '+%H:%M:%S') DEADLINE DRAIN: paused $(( STOP_EPOCH - NOW ))s before ${STOP_HHMM}; runners leave at their next checkpoint" | tee -a "$LOG"
    else
      DEADLINE_DRAIN=1; echo "$(date '+%H:%M:%S') NOTE: the deadline drain could not write the pause file; the end will stop runners as before" | tee -a "$LOG"
    fi
  fi
  # DEADLINE DRAIN END
  if [ -n "$INTAKE_HOLD" ]; then
    if [ "${INTAKE_HELD:-0}" = 0 ]; then
      INTAKE_HELD=1; echo "$(date '+%H:%M:%S') HOLD: the intake's budget cannot be read (${INTAKE_HOLD}); no pass runs until it can, and the deadline keeps counting" | tee -a "$LOG"
      announce HELD "the intake's budget cannot be read; no pass runs until it can"
    fi
    bash ~/.claude/bin/loop_guard.sh renew >/dev/null 2>&1
    nap 10; continue
  fi
  if [ "${INTAKE_HELD:-0}" = 1 ]; then INTAKE_HELD=0; echo "$(date '+%H:%M:%S') the intake's budget reads again: ${BUDGET_USD} USD" | tee -a "$LOG"; fi
  find "$RUN_TMP" -mindepth 1 -maxdepth 1 -mmin +180 -exec rm -rf {} + 2>/dev/null
  DISK_WHY=$(disk_hold)
  if [ -n "$DISK_WHY" ]; then
    raise DISK "${DISK_WHY}; the run stops before a write fails half way (it died silently on a full disk on 2026-09-23)"; exit 3
  fi
  # Funding is read BEFORE spending, never after: an unfunded pass changes nothing and hides the real reason.
  # EVERY ANSWER NAMES ITS CAUSE (owner, 2026-09-27: mistaken UNFUNDED edge cases handled properly). burn_guard prints a
  # FUNDING line: SPENT (the run's budget is committed), PROVIDER-LOW (OpenRouter itself cannot pay), EXPIRED (the run's
  # budget ended at its deadline) each end the run with that reason. NO-DATA, or no FUNDING line at all, is an UNKNOWN:
  # it HOLDS (no pass runs, the deadline keeps counting) and ends the run only after FUND_HOLD_MAX_S of unbroken
  # unknowns. The old branch read two zeros as "the money is spent", and a run that stops for good loses its budget.
  FUND=$(python3 ~/.claude/bin/burn_guard.py 2>/dev/null); LANES=$(printf '%s\n' "$FUND" | tail -1)
  case "$LANES" in
    ''|*[!0-9]*|0)
      # ONE unreadable read must not end a seven hour run (review 2026-09-22): read again ten seconds later.
      nap 10; FUND=$(python3 ~/.claude/bin/burn_guard.py 2>/dev/null); LANES=$(printf '%s\n' "$FUND" | tail -1);;
  esac
  FSTATE=$(funding_of "$FUND"); FWHY=$(funding_why "$FUND"); [ -n "$FWHY" ] || FWHY="burn_guard named no cause"
  case "$LANES" in
    ''|*[!0-9]*|0)
      case "$FSTATE" in
        SPENT) raise UNFUNDED "the run's OpenRouter budget is spent: ${FWHY}"; exit 3;;
        PROVIDER-LOW) raise UNFUNDED "OpenRouter itself cannot pay: ${FWHY}"; exit 3;;
        EXPIRED) raise UNFUNDED "the run's budget has expired: ${FWHY}"; exit 3;;
        *)
          if [ -z "${FUND_HELD_SINCE:-}" ]; then
            FUND_HELD_SINCE=$(date +%s)
            echo "$(date '+%H:%M:%S') HOLD: funding cannot be read (${FWHY}); no pass runs until it can, and the deadline keeps counting" | tee -a "$LOG"
            announce HELD "funding cannot be read; no pass runs until it can"
          fi
          if [ $(( $(date +%s) - FUND_HELD_SINCE )) -ge "${FUND_HOLD_MAX_S:-900}" ]; then
            raise UNFUNDED "funding could not be read for $(( ${FUND_HOLD_MAX_S:-900} / 60 )) minutes running (${FWHY}); nothing may be spent on money nobody can measure"; exit 3
          fi
          bash ~/.claude/bin/loop_guard.sh renew >/dev/null 2>&1
          nap "${FUND_HOLD_NAP_S:-30}"; continue;;
      esac;;
  esac
  if [ -n "${FUND_HELD_SINCE:-}" ]; then FUND_HELD_SINCE=""; echo "$(date '+%H:%M:%S') funding reads again: ${FWHY}" | tee -a "$LOG"; fi
  SPENT=$(money_of "$FUND"); CLAUDE_TALLY=$(claude_tally)
  CLAUDE_SPENT=$(claude_spent "$CLAUDE_TALLY"); CLAUDE_NOTE=$(claude_note "$CLAUDE_TALLY")
  [ -n "$CLAUDE_NOTE" ] && CLAUDE_NOTE=" (${CLAUDE_NOTE})"
  if [ -z "$SPENT" ]; then
    raise UNFUNDED "burn_guard printed no MONEY line, so the spend against the ${BUDGET_USD} USD budget is unknown and nothing may be spent"; exit 3
  fi
  case "$CLAUDE_NOTE" in
    *NO-DATA*) CLAUDE_SPENT=""
      # A PROOF ACCEPTS ONLY KNOWN COST (prerequisite 4, decided 2026-09-28 on the owner's delegation): a plain run
      # reports an unreadable Claude tally as NO-DATA and continues (owner, 2026-09-27); a proof phase stops here,
      # before this pass pays anything nobody could then count.
      if [ -n "${BROTHER_PROOF_PHASE:-}" ]; then
        raise UNFUNDED "proof ${BROTHER_PROOF_PHASE}: the Claude calls ledger cannot be tallied${CLAUDE_NOTE}; a proof accepts only known cost"; exit 3
      fi;;
  esac
  # BUDGET SOURCES ARE SEPARATE (owner, 2026-09-27: "100 openrouter budget and 20M Claude tokens"; "different runs ...
  # can have different budget sources"). BUDGET_USD bounds OpenRouter money and is compared with OpenRouter spend alone;
  # Claude's cost is measured and reported beside it, bounded by its own budget (the session spend guard), and an
  # unreadable Claude tally is reported as NO-DATA rather than ending an OpenRouter funded run.
  echo "BUDGET  this run: spent $(python3 -c 'import sys; print("%.2f" % (float(sys.argv[1]) - float(sys.argv[2])))' "$SPENT" "$SPENT0") of ${BUDGET_USD} USD | remaining $(python3 -c 'import sys; print("%.2f" % (float(sys.argv[3]) - float(sys.argv[1]) + float(sys.argv[2])))' "$SPENT" "$SPENT0" "$BUDGET_USD") USD | deadline ${STOP_HHMM} | OpenRouter only; Claude ${CLAUDE_SPENT:-NO-DATA} USD on its own budget${CLAUDE_NOTE}" | tee -a "$LOG"
  if python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) - float(sys.argv[2]) >= float(sys.argv[3]) else 1)' "$SPENT" "$SPENT0" "$BUDGET_USD" 2>/dev/null; then
    raise BUDGET "the run's OpenRouter budget of ${BUDGET_USD} USD is spent (${SPENT0} to ${SPENT} USD since the start); the owner set it at intake"; exit 3
  fi
  # PAUSE, NOT STOP (owner, 2026-09-22: pause before the internet drops or before going out, resume later, nothing
  # broken, no tokens spent). All of the loop's state is on disk and re-read every pass, so a pause is simply: no new
  # pass while ~/.claude/evidence/LOOP-PAUSE.txt exists. Runners already in flight finish their current round (bounded
  # by their own timeouts) and write their STATUS; the pool reads it after the resume. The lease is renewed so no
  # second driver starts, the heartbeat says PAUSED, the deadline keeps counting (a pause is not an extension), and
  # nothing is dispatched. Remove the file and the next pass runs as if nothing happened. stop_loop.sh --pause / --resume.
  if ! control_clear; then
    if [ "${PAUSED:-0}" = 0 ]; then
      python3 -B "${LOOP_DIR}/loop_receipt.py" proof-event --run-dir "$BROTHER_RUN_DIR" --kind pause --detail "owner pause" || { raise INTERRUPTED "proof intervention recording failed"; exit 2; }
      PAUSED=1; echo "$(date '+%H:%M:%S') PAUSED: ${CONTROL_WHY}" | tee -a "$LOG"
      python3 -B ~/.claude/bin/loop_heartbeat.py write --state PAUSED --reason "paused by ${CONTROL_WHY}" --passes "${PASS:-0}" --log "$LOG" >/dev/null 2>&1 || true
      announce PAUSED "no pass runs until owner controls are readable and clear; the deadline keeps counting"
    fi
    bash ~/.claude/bin/loop_guard.sh renew >/dev/null 2>&1
    nap 10; continue
  fi
  if [ "${PAUSED:-0}" = 1 ]; then PAUSED=0; echo "$(date '+%H:%M:%S') RESUMED" | tee -a "$LOG"; announce RESUMED "passes continue from the state on disk"; fi
  bash ~/.claude/bin/loop_guard.sh renew >/dev/null 2>&1
  PASS=$((PASS + 1))
  echo "===== pass ${PASS} at $(date '+%H:%M:%S') (lanes ${LANES}) =====" >> "$LOG"
  # THE LOGIN IS KEPT FRESH OUTSIDE THE SEATS (owner ruling A, 2026-10-05). A native seat may read this machine's Claude
  # login but not write it, so it cannot refresh an expired access token: proof pair RB's every seat answered 401, parked
  # CONFIG_WAIT "Not logged in", and the run ended UNPRODUCTIVE in 4.5 minutes. Here, in the driver and never in a seat,
  # one tiny unsandboxed call of the same program on the cheapest Claude model lets the CLI refresh it (native_worker.py
  # refresh, bounded by its own timeout; SKIPPED when no seat runs). Cost: one minimal call per pass. It never stops the
  # pass: a failure is one REFRESH FAILED line with its class (never the login itself) and one alarm, and the seats'
  # CONFIG_WAIT already holds every build while the login stays stale. Any answer that is not OK or SKIPPED is FAILED.
  # BOUNDED FROM OUTSIDE (review 2026-10-05): the call's own 90 s cap does not cover the Claude ledger's lock, a blocking
  # flock with no time limit, so a writer stuck on it would stall the driver here. macOS has no timeout(1): perl's alarm
  # kills the refresh at BROTHER_REFRESH_BOUND_S (default 150, above the call's 90 s plus the ledger's 30 s grace) and
  # exit 142 reads REFRESH FAILED timeout. Output goes to a file, never a pipe: a grandchild that outlives the kill would
  # hold a pipe open and the wait would last as long as it does.
  REFRESH_OUT="${RUN_TMP}/refresh.out"
  REFRESH_BOUND=150; case "${BROTHER_REFRESH_BOUND_S:-}" in ''|*[!0-9]*|0*) ;; *) REFRESH_BOUND=$BROTHER_REFRESH_BOUND_S;; esac   # all digits and above 0, else 150: alarm 0 would cancel the bound
  { perl -e 'alarm shift; exec @ARGV' "$REFRESH_BOUND" python3 -B ~/.claude/bin/native_worker.py refresh </dev/null >"$REFRESH_OUT" 2>/dev/null; } 2>/dev/null
  REFRESH_RC=$?
  REFRESH_SAYS=$(grep -m1 '^REFRESH ' "$REFRESH_OUT" 2>/dev/null)
  [ "$REFRESH_RC" = 142 ] && REFRESH_SAYS="REFRESH FAILED timeout: no answer within ${REFRESH_BOUND} s"
  case "$REFRESH_SAYS" in "REFRESH OK"*|"REFRESH SKIPPED"*|"REFRESH FAILED"*) ;; *) REFRESH_SAYS="REFRESH FAILED no-answer: the refresh tool printed no REFRESH line";; esac
  echo "$(date '+%H:%M:%S') ${REFRESH_SAYS}" | tee -a "$LOG"
  case "$REFRESH_SAYS" in
    "REFRESH FAILED"*) [ "${REFRESH_WARNED:-0}" = 1 ] || { REFRESH_WARNED=1; announce LOGIN "${REFRESH_SAYS}: native seats hold CONFIG_WAIT until a plain claude call succeeds"; };;
    *) REFRESH_WARNED=0;;
  esac
  run_pass
  # THE PULSE, after EVERY pass including the last one. One row of counts for this run (landed, gate approvals,
  # parked sub units) and a WARN the moment a gate has never approved, READY builds are not landing, the pool is
  # draining, or the run is behind its own base rate. It NEVER stops the run and its exit code is never read: the
  # night of 2026-09-22 made 40 passes and landed nothing while every one of those numbers sat unread on disk.
  RUN_SPENT=$(python3 -c 'import sys; print("%.2f" % (float(sys.argv[1]) - float(sys.argv[2]) + float(sys.argv[3])))' "${SPENT:-0}" "${SPENT0:-0}" "${CLAUDE_SPENT:-0}" 2>/dev/null || true)
  PULSE_OUT=$(python3 -B ~/.claude/bin/pass_pulse.py --start "$START_EPOCH" --passes "$PASS" --log "$LOG" --spent "${RUN_SPENT}" 2>&1) || true
  # THE PLAYBOOK BEHIND THE WATCH (P3, 2026-09-24): a watch class is acted on ONCE by a deterministic check whose
  # verdict is logged; an ESCALATE names a gate defect for the owner's warning line. Best effort: a missing tool
  # never ends the run.
  WATCH_SAYS=$(python3 -B ~/.claude/bin/loop_watch.py --log "$LOG" --lanes "${BROTHER_LANES:-6}" 2>/dev/null | tail -1)
  case "$WATCH_SAYS" in
    ACT\ *)
      WK=$(printf '%s\n' "$WATCH_SAYS" | sed -n 's/^ACT \([a-z]*\): .*new warning kind \([A-Z-]*\).*/\1:\2/p'); [ -z "$WK" ] && WK=$(printf '%s\n' "$WATCH_SAYS" | sed -n 's/^ACT \([a-z]*\):.*/\1/p')
      case " ${ACTED_KINDS:-} " in *" $WK "*) ;; *)
        ACTED_KINDS="${ACTED_KINDS:-} $WK"
        PB=$(python3 -B ~/.claude/bin/watch_act.py $(printf '%s\n' "$WATCH_SAYS" | sed -n 's/^ACT \([a-z]*\): \(.*\)$/\1 \2/p') 2>/dev/null) || true
        [ -n "$PB" ] && { echo "PLAYBOOK $PB" | tee -a "$LOG"; case "$PB" in ESCALATE*) announce "GATE-DEFECT" "$PB";; esac; };;
      esac;;
  esac
  printf '%s\n' "$PULSE_OUT" >> "$LOG"
  # THE COST PER LANDING WARNS, IT NEVER ENDS A RUN (2026-09-25). As a stop (H6, 2026-09-24) it ended the 08:56 run at
  # 10:10 after three landings and the machine sat idle until 16:45. The pulse still prints WARN COST-PER-LANDING and the
  # warning rings once; the absolute budget is what ends a run on money.
  # THE DIGEST TO THE OWNER FOLLOWS HIS CADENCE, THE MEASUREMENT DOES NOT (owner, 2026-09-22: "only have it in the first
  # tour then the third 6th and 10th 15th 20th and each five after that ... to save context"). The pulse row is measured
  # every pass and costs nothing to read later; the digest file a human or a session reads is appended only on the
  # scheduled passes. A WARN, a LANDED or an ALARM still rings at once through announce(): a schedule that waits for pass
  # 6 to say "the checker never approves" is last night again, only shorter.
  if digest_pass "$PASS"; then
    { echo "## pass $PASS at $(date '+%H:%M:%S')"; printf '%s\n' "$PULSE_OUT" | grep -E '^(PULSE|WARN)'; grep -E '^(READY   |LANDED |NEEDFACT|STALLED|COUNCIL)' "$LOG" | tail -5; echo; } >> ~/.claude/evidence/LOOP-DIGEST.md
  fi
  for KIND in $(printf '%s\n' "$PULSE_OUT" | sed -n 's/^WARN \([A-Z-]*\):.*/\1/p'); do
    case " $WARNED " in
      *" $KIND "*) ;;
      *) WARNED="$WARNED $KIND"; announce "WARN" "$(printf '%s\n' "$PULSE_OUT" | grep -m1 "^WARN $KIND:" | cut -c6-)";;
    esac
  done
  # DRAINING IS NOT UNPRODUCTIVE (objection 10): near a proof run's deadline every call whose own timeout would outlive
  # it is refused before any row exists, so a pass that lands, closes and starts nothing is the expected shape. While
  # proof_ledger.draining() says so, exits 43 and 45 are not terminal: the driver sleeps to the deadline and ends
  # DEADLINE. Outside a proof, or when the question cannot be answered, they stay terminal.
  DRAIN=0
  case "$RC" in 43|45)
    if python3 -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import proof_ledger; sys.exit(0 if proof_ledger.draining() else 1)' "$LOOP_DIR" >/dev/null 2>&1; then
      DRAIN=1; echo "$(date '+%H:%M:%S') DRAINING: pass exit ${RC} is not terminal while the run drains; sleeping to the deadline" | tee -a "$LOG"
    fi;;
  esac
  # A PAUSE IS NOT UNPRODUCTIVE (2026-09-28, run B): an owner pause written while a pass was running held every runner
  # that pass tried to start, the pass exited 45, and the driver ended the whole run at 12:37 instead of waiting. While
  # an owner pause or hold is in force (or its reader cannot say it is clear), exit 45 is that control working, not a
  # refusal upstream: the gap below sees the control and the pause handling above waits for the resume.
  if [ "$DRAIN" = 0 ] && [ "$RC" = 45 ] && ! control_clear; then
    DRAIN=2; echo "$(date '+%H:%M:%S') HELD: pass exit 45 is not terminal while an owner control is in force (${CONTROL_WHY})" | tee -a "$LOG"
  fi
  [ "$DRAIN" != 0 ] || case "$RC" in
    0) ;;
    42) raise FINISHED "every unit in scope is DONE with evidence; there is nothing left to build"; exit 42;;
    45) raise UNPRODUCTIVE "a pass landed nothing, closed nothing, started nothing, and no runner is alive; something upstream is refusing and repeating it changes nothing"
        exit 45;;
    43) raise STALLED "work remains but NOT ONE sub unit is admissible; this needs a decision, not another pass"; exit 43;;
    44) # BLOCKED is not STALLED. Stalled means nothing is admissible; blocked means the loop cannot even try,
        # because unpushed commits or unattributable changes make every landing refuse on parity. Measured
        # 2026-09-21: this state ran 22 consecutive passes at exit 0 with zero runners alive and nobody saw it.
        # THE ALARM QUOTES THE PASS'S OWN REASON. A fixed sentence about hub was shown for a digest crash at 14:10 on
        # 2026-09-22 and sent the owner looking at the wrong thing. The pass prints one "LOOP BLOCKED: <why>" line.
        WHY=$(grep -E '^LOOP BLOCKED: ' "$LOG" | tail -1 | cut -c14-220)
        raise BLOCKED "${WHY:-the pass exited 44 without printing its reason}. NOTHING further will run until a human looks"
        exit 44;;
    *)  # AN UNEXPECTED PASS EXIT IS A FAULT, NEVER AN ORDINARY PASS (audit E4, 2026-09-27). A pass exits 0, or 42 to 45 by
        # name; a crash (1), a usage error (2), a script that cannot run (126), a missing command (127) or a KILL (137)
        # fell through as a pass that ran, and a later deadline returned 0 with a clean DEADLINE receipt. Each fault is
        # logged and counted, the run's end is then a failure whatever its state (raise reads PASS_FAULTS), and the pass
        # is retried until the third fault, which ends the run BLOCKED: the same failing pass repeated changes nothing.
        PASS_FAULTS=$((PASS_FAULTS + 1)); LAST_FAULT="pass ${PASS} exited ${RC}"
        echo "$(date '+%H:%M:%S') PASS FAULT: pass ${PASS} exited ${RC} (a pass exits 0 or 42 to 45); fault ${PASS_FAULTS} of ${PASS_FAULT_LIMIT}, and the run's end will be a failure" | tee -a "$LOG"
        if [ "$PASS_FAULTS" -ge "$PASS_FAULT_LIMIT" ]; then
          raise BLOCKED "the pass faulted ${PASS_FAULTS} times (the last: ${LAST_FAULT}). NOTHING further will run until a human looks"
          exit 44
        fi;;
  esac
  echo "$(date '+%H:%M:%S') pass ${PASS} done (rc ${RC})" | tee -a "$LOG"
  # Sleep in short slices so the deadline is honoured even mid gap. A draining run sleeps to its deadline.
  # A PAUSE OR HOLD INSIDE THE GAP IS SEEN (U11, B5-03): every slice looks for either file, and when one exists asks
  # the one reader, which records it in the run's history; a held run leaves the gap for the pause handling above.
  END=$(( $(date +%s) + GAP )); [ "$DRAIN" = 1 ] && END=$STOP_EPOCH
  EVD="$HOME/.claude/evidence"
  while [ "$(date +%s)" -lt "$END" ] && [ "$(date +%s)" -lt "$STOP_EPOCH" ]; do
    nap 10
    if [ -e "$EVD/LOOP-PAUSE.txt" ] || [ -L "$EVD/LOOP-PAUSE.txt" ] || [ -e "$EVD/LOOP-HOLD.txt" ] || [ -L "$EVD/LOOP-HOLD.txt" ]; then
      control_clear || break
    fi
  done
done

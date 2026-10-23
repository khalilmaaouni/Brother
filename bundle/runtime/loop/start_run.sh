#!/bin/bash
# ONE COMMAND FOR A PLAIN brother.loop RUN (the proof pair has proof_pair.sh). Every step below is a trap a launch hit on
# 2026-09-27, checked in order; a refusal prints "START REFUSED [<step>]" with the fix, and nothing is started.
# usage, from the launch worktree:
#   start_run.sh --deadline HH:MM --budget-usd N [--scope REGEX] [--gap SECONDS] [--finisher M] [--documenter M] [--checker M] [--worker M] [--adversary M]
#                [--lift-hold "<the owner's words>"] [--dry]
#   --plan docs/plan/X.json names the plan the run reads: plan_path.py validates it here, the intake exports it as BROTHER_WBS.
#   A value that is empty, only whitespace, or starts with -- is refused ("<option> needs a value"), for every option.
#   0 POWER    power_rule.py, the driver's own rule: without AC, a deadline beyond BROTHER_POWER_WINDOW_S refuses.
#   1 TREE     this directory is a git worktree on a branch with an upstream, clean and equal to it. An upstream-less
#              branch read NO-DATA at intake, and a dirty or unpushed tree is refused by the driver's first pass.
#   2 PINS     BROTHER_CLAUDE_BIN and BROTHER_CODEX_BIN from model_router unless already set; each an executable file.
#   3 CONTROLS one launch at a time (the lock $STARTS/.launch-lock holds the launcher's pid; a dead pid's lock is
#              removed with a WARN). A controls-held-* folder from an earlier launch that still holds a hold or pause
#              and is not marked .started refuses: those controls are NOT in force (a SIGKILL cannot be trapped).
#              A HOLD or PAUSE refuses, unless --lift-hold carries the owner's words. The words are the authority, never
#              this tool. The lift itself waits for step 3c, after every check that does not need it.
#   3b LIVE    one loop at a time: a loop already alive refuses, and so does a live check that cannot answer.
#   3c LIFT    the hold and pause are recorded with the owner's words beside this start's log and moved aside. The intake
#              reports a hold as HELD (never READY), so the lift comes before it; any refusal from here on moves them back
#              exactly as found, so a refused start never leaves a hold or pause lifted.
#   4 INTAKE   prepare runs from THIS checkout's scripts/loop/loop_intake.py (the deployed copy cannot see the parity
#              test), with the pins, the deadline and the budget; it must exit 0 and its last INTAKE line must be INTAKE READY.
#   5 DRIVER   started detached in its own session with the intake's launch-env.sh sourced (the plain driver does not
#              source it), BROTHER_LAUNCH_WORKTREE set to this checkout, and its first line awaited: LOOP UNTIL, or the
#              refusal it printed instead.
# 1.1.6 S2, BOTH HOSTS AND THE NIGHT'S REBOOT:
#   3d BOOT    kern.boottime (seam START_RUN_BOOTTIME) is compared with the newest earlier launch record
#              ($STARTS/launch-<stamp>.json, written at STARTED): a different one prints "the last run <stamp> was
#              killed by a reboot at <boot>" and marks that record delivered=false. Reporting: it never refuses.
#   4b SEAT    a SEAT line from the intake (a signed out claude or codex, loop_intake.seat_gate) refuses the arm after
#              one row per transport in notify-request.jsonl (loop_intake.notify_once) under $BROTHER_RUN_DIR when the
#              launch names one, else $STARTS/notify-<day>.
#   4c GRANT   with --grant-tokens N only: scripts/loop_budget_grant.py (seam START_RUN_GRANT) writes the spend guard
#              project raise for this checkout's slug until the deadline, with the intake record's owner_words; a
#              refused grant refuses the start.
#   5b HOLDER  on the loop token lane (BROTHER_LOOP_TOKEN=on) only, the holder STARTS the driver (S1.b): step 5 runs
#              brother_login.py hold --run $STARTS/hold-<stamp> -- <the driver command> (seam START_RUN_HOLDER), which
#              reads the login once and hands the driver the one end of an unnamed socket pair (pass_fds); runner_pool
#              and unit_runner hand it on to native_worker, whose seats ask it (brother_login.take_hold). The holder's pid
#              goes in <the driver's run folder>/login-holder.pid, which stop_loop.sh reaps. A holder that cannot read
#              the login starts nothing and the launch refuses [holder]. Off the token lane step 5 is unchanged.
#              The driver leads its own process group on both lanes (the holder starts it in its own session,
#              S2 round 1), so stop_loop.sh signals the driver's whole group, a foreground refresh included.
# 1.1.7 LR1.c, THE WATCHER:
#   5c WATCH   after the driver's first line, scripts/loop/run_watch.py (seam START_RUN_WATCH) is started detached beside
#              the driver (its own session, output to <run folder>/watcher.log) over the driver's own log and heartbeat
#              ($EVD/LOOP-HEARTBEAT.json); its pid goes in <run folder>/watcher.pid (the driver's run folder, else a
#              watch-<stamp> folder beside the launch records). A watcher that is missing or does not stay alive for a
#              second stops the driver's group and refuses the launch [watch]: a run nobody watches is not started.
# 1.1.7 LR1.d, THE LANDING CANARY:
#   4d CANARY  after the intake (and the grant), this checkout's scripts/loop/loop_canary.py (seam START_RUN_CANARY; a
#              file that does not end in .py runs under bash) replays the known good build AND sends one tiny real change
#              through the live judge (its landing stage, asked for with --landing: the intake and the deploy run the
#              canary bare, replay only). Exit 0 starts; exit 1 refuses [canary] with the named row (its
#              last line); exit 3 refuses [canary] NO-DATA; any other exit refuses too. Its output is in
#              $STARTS/canary-<stamp>.log. It is a minute-scale replay plus the whole gate on one scratch change, so the
#              launch waits for it; a signal stops it with the intake's own handler.
# 1.1.7 LR1.b, THE CLOCK ROWS:
#   1b CLOCK   after the tree and before the pins, the close ceremony pack and the restore drill record must still be
#              fresh at the deadline (scripts/close_ceremony_check.py and scripts/acc6_restore_evidence.py, expiry_errors);
#              an expiry, a missing record or a helper that cannot run refuses [clock] with the refresh command.
# The run's money is its own: the driver writes <run dir>/money/run-budget.json from the intake's budget and deadline,
# and nothing carries over from another run. Seams for tests: START_RUN_INTAKE, START_RUN_DRIVER, START_RUN_EVIDENCE.
# TIMING (LA1.a, reporting only): one `TIME <step> <seconds>` line per step, then, after STARTED, the wait for the
# first claimed unit (START_RUN_TIMING_WAIT_S, default 90) and `LAUNCH command to first claimed unit <s> s`, SLOW
# past START_RUN_SLOW_S (default 180), with $STARTS/timing-<stamp>.json. A timer or writer failure prints a
# `TIME NO-DATA` line and never changes a verdict or an exit code; no timing line starts with REFUSED or NO-DATA.
set -u
LDIR=$(cd "$(dirname "$0")" && pwd)   # this file's own folder, absolute before the cd into the checkout (1.1.6 S2)
clock_now() { local n; n=$(date +%s 2>/dev/null) || n=""; case "$n" in ''|*[!0-9]*) n="";; esac; echo "$n"; }
T0=$(clock_now); TPREV=$T0; TSTEPS=""
tmark() {
  local n; n=$(clock_now)
  if [ -n "$n" ] && [ -n "$TPREV" ]; then echo "TIME $1 $((n - TPREV))"; TSTEPS="$TSTEPS $1=$((n - TPREV))"
  else echo "TIME NO-DATA $1: the clock could not be read"; fi
  TPREV=$n
  return 0
}
# SAVED is set by step 3c once the hold and pause are moved aside; from then on every refusal moves them back first.
SAVED=""
restore_controls() {
  [ -n "$SAVED" ] || return 0
  local f kept="" stuck=""
  for f in "$SAVED"/*; do
    [ -e "$f" ] || [ -L "$f" ] || continue
    # mv -n: a hold written by someone else since the lift is newer than this one and is never overwritten
    mv -n "$f" "$EVD/" 2>/dev/null
    if [ -e "$f" ] || [ -L "$f" ]; then
      # 2026-10-11: two different reasons, two different messages: a newer control already in place, or a move that
      # failed with nothing in the way (an unwritable folder), which leaves that control out of force.
      if [ -e "$EVD/${f##*/}" ] || [ -L "$EVD/${f##*/}" ]; then kept="$kept $f"; else stuck="$stuck $f"; fi
    fi
  done
  echo "restored after a refused start $(date '+%Y-%m-%d %H:%M:%S %Z')${kept:+; NOT restored, a newer file is in place:$kept}${stuck:+; NOT restored, the move back failed:$stuck}" >> "$LIFTREC" 2>/dev/null
  [ -z "$kept" ] || echo "WARN controls: a newer hold or pause is in place; the lifted one stays in$kept"
  [ -z "$stuck" ] || echo "WARN controls: could not move back$stuck to $EVD; that control is NOT in force until it is moved back by hand"
  [ -n "$kept$stuck" ] || echo "OK   controls restored exactly as found"
  SAVED=""
}
refuse() { restore_controls; echo "START REFUSED [$1]: $2"; python3 -B "$(dirname "$0")/../jev_start_refusal.py" "$1" "$2" 2>/dev/null || true; exit 2; }
# 2026-10-11 round 3: INT, TERM, HUP, ALRM (the estate's `perl -e 'alarm N'`) and QUIT stop the intake's whole process
# group and put the hold and pause back as found. A closed stdout (`start_run.sh | head`) only silences the launcher:
# the PIPE handler sends later output to /dev/null instead of letting the first write kill it mid way (a handler, not
# an ignore, so the children keep the default SIGPIPE). LOCK is set once this launch owns the launch lock.
IPID=""; PID=""; LOCK=""
on_signal() {
  if [ -n "$IPID" ]; then
    kill -TERM -- "-$IPID" 2>/dev/null; kill -TERM "$IPID" 2>/dev/null
    # LR1 review M3: the canary (step 4d) unwinds its worktrees on a TERM, so it is given up to 90 seconds to do so (its judge child is told TERM and has loop_canary.CHILD_GRACE_S = 40 s, plus 10 s to reap, plus its own two git removals); what
    # still stands after that is killed (bash reaps the child as soon as it ends, so kill -0 stops answering; no ps, which a
    # sandbox can refuse)
    G=${START_RUN_CANARY_GRACE_S:-90}; case "$G" in ""|*[!0-9]*) G=90;; esac   # the seam is for tests; a bad value is the default
    W=0   # a counter, not `seq 1 N`: BSD seq counts DOWN for N below 1
    while [ "$W" -lt $((G * 2)) ]; do
      kill -0 "$IPID" 2>/dev/null || break
      sleep 0.5
      W=$((W + 1))
    done
    kill -KILL -- "-$IPID" 2>/dev/null
  fi
  refuse signal "interrupted${PID:+; the driver pid $PID may still be running and honours the restored hold}"
}
release_lock() { [ -z "$LOCK" ] || { rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null; }; }
trap 'exec >/dev/null 2>&1' PIPE
DEADLINE=""; BUDGET=""; SCOPE="."; PLAN=""; GAP=240; FIN=opus55; DOC=astra; CHK=sonnet; WRK=""; ADV=""; WORDS=""; DRY=0; GRANT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --deadline|--budget-usd|--gap|--scope|--plan|--finisher|--documenter|--checker|--worker|--adversary|--lift-hold|--grant-tokens)
      [ $# -ge 2 ] || refuse args "$1 needs a value"
      # 2026-10-11: an empty value, or one that is itself an option, is not a value: `--lift-hold --dry` used to store
      # "--dry" as the owner's words, lift the hold and start a REAL run, and an unquoted empty variable did the same.
      # Round 3: a value that is only whitespace is empty too (docs/plan/specs/fixes/FX-06.md says so for --lift-hold).
      V=${2//[[:space:]]/}
      case "$V" in ""|--*) refuse args "$1 needs a value, not '$2'";; esac;;
  esac
  case "$1" in
    --deadline) DEADLINE=$2; shift 2;;
    --budget-usd) BUDGET=$2; shift 2;;
    --gap) GAP=$2; shift 2;;
    --scope) SCOPE=$2; shift 2;;
    # 2026-10-11 (LP1.c): accepted now that the plan selector (LP2) is on the line. Given twice is refused (never a last
    # one wins); the value is validated by plan_path.py below and reaches the intake, which exports BROTHER_WBS.
    --plan) [ -z "$PLAN" ] || refuse args "--plan given twice"; PLAN=$2; shift 2;;
    --finisher) FIN=$2; shift 2;;
    --documenter) DOC=$2; shift 2;;
    --checker) CHK=$2; shift 2;;
    --worker) WRK=$2; shift 2;;        # 2026-09-30: the worker pin (BROTHER_PIN_MODEL) as an intake choice, e.g. opus55 for a Claude only run
    --adversary) ADV=$2; shift 2;;     # 2026-09-30: the adversary seats (BROTHER_ADVERSARY_MODEL) as an intake choice
    --lift-hold) WORDS=$2; shift 2;;
    --grant-tokens) GRANT=$2; shift 2;;   # 1.1.6 S2: the spend guard raise, in tokens; loop_budget_grant.py validates it
    --dry) DRY=1; shift;;
    *) refuse args "unknown argument $1 (see the usage at the top of this file)";;
  esac
done
# ONE DATED DEADLINE FOR BOTH TOOLS: the driver reads a past HH:MM as tomorrow, the intake refuses it as "not in the
# future" (2026-09-27 16:37), so HH:MM becomes "YYYY-MM-DD HH:MM" here, tomorrow when it has already passed today.
case "$DEADLINE" in
  [0-2][0-9]:[0-5][0-9])
    if [ "$(date +%H:%M)" \< "$DEADLINE" ]; then DEADLINE="$(date +%Y-%m-%d) $DEADLINE"; else DEADLINE="$(date -v+1d +%Y-%m-%d) $DEADLINE"; fi;;
  [0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]\ [0-2][0-9]:[0-5][0-9]) ;;
  *) refuse args "--deadline HH:MM or \"YYYY-MM-DD HH:MM\" is required";;
esac
echo "OK   deadline $DEADLINE"
case "$BUDGET" in ''|*[!0-9.]*|.*|*.*.*) refuse args "--budget-usd N (a positive number) is required";; esac
case "$GAP" in ''|*[!0-9]*) refuse args "--gap is a whole number of seconds";; esac
if [ -n "$PLAN" ]; then   # the one selector's own verdict, before anything starts (a missing plan_path.py refuses too)
  PERR=$(BROTHER_WBS="$PLAN" python3 -B "$(dirname "$0")/plan_path.py" 2>&1 >/dev/null) || refuse args "--plan: ${PERR#PLAN REFUSED: }"
  echo "OK   plan $PLAN"
fi
tmark args 2>/dev/null || true

# 0 POWER (LA3.a, 2026-10-12): the driver's power rule, asked BEFORE the tree and the intake. On 2026-10-11 a battery
# start ran the whole two minute intake and was refused by the driver four seconds after it. The same module answers
# for the driver (loop_until.sh), so the two never disagree; the driver keeps its own reading for a charger pulled in
# between. Refuses in --dry too. A rule that cannot run refuses: only its own 0 (start) and 2 (refuse) are answers.
PRULE="$(dirname "$0")/power_rule.py"
[ -f "$PRULE" ] || refuse power "the power rule could not run (no $PRULE)"
PWHY=$(python3 -B "$PRULE" check --deadline "$DEADLINE" 2>&1); PRC=$?
case "$PRC" in
  0) echo "OK   power  ${PWHY:-on AC}";;
  # L12 (2026-10-16): on battery the remedy is read first. The rule and its words stay power_rule.py's; its own
  # `source` reading names the battery, so no text of the rule is matched here.
  2) [ "$(python3 -B "$PRULE" source 2>/dev/null)" = battery ] && PWHY="plug in the charger first: $PWHY"
     refuse power "${PWHY:-the power rule refused without a reason}";;
  *) refuse power "the power rule could not run (exit $PRC): $(printf '%s' "$PWHY" | tr '\n' ' ' | cut -c1-160)";;
esac
tmark power 2>/dev/null || true

# 1 TREE
REPO=$(git rev-parse --show-toplevel 2>/dev/null) || refuse tree "not inside a git checkout; cd into the launch worktree"
cd "$REPO" || refuse tree "cannot enter $REPO"
BRANCH=$(git branch --show-current)
[ -n "$BRANCH" ] || refuse tree "detached HEAD; check out a named branch"
UP=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null) || refuse tree "branch $BRANCH has no upstream: git push -u <remote> $BRANCH:$BRANCH"
git fetch -q "${UP%%/*}" 2>/dev/null || refuse tree "cannot fetch ${UP%%/*}"
# LA3.b: the sha this fetch left and when, passed to the intake so it does not fetch a second time (it fetches anyway
# when the upstream has moved since, or the reading is older than its bound).
UPSHA=$(git rev-parse "$UP" 2>/dev/null); FETCHED_AT=$(clock_now)
DIRTY=$(git status --porcelain | wc -l | tr -d ' ')
[ "$DIRTY" = 0 ] || refuse tree "$DIRTY changed path(s); commit and push them, or move them out of the launch worktree"
AHEAD=$(git rev-list --count "$UP..HEAD"); BEHIND=$(git rev-list --count "HEAD..$UP")
[ "$AHEAD" = 0 ] || refuse tree "$AHEAD unpushed commit(s): git push"
[ "$BEHIND" = 0 ] || refuse tree "$BEHIND commit(s) behind $UP: git merge --ff-only $UP"
echo "OK   tree   $BRANCH clean and equal to $UP"
tmark tree 2>/dev/null || true

# 1b CLOCK (1.1.7 LR1.b): a record the whole gate reads against the clock must still be fresh when the run ENDS, not
# only when it starts. 2026-10-21: three standing exceptions expired at midnight and every tree's gate went red for a
# reason no build caused. The close ceremony pack and the restore drill record are asked, from this launcher's own
# checkout, for their expiry before the deadline (close_ceremony_check.expiry_errors, acc6_restore_evidence.expiry_errors);
# an expiry, a drill record that is missing or unreadable, or a helper that crashes refuses [clock] with the first line
# and nothing starts. THE CLOCK RULE IS THE HUB'S ESTATE LAW, so it is NOT a refusal where it cannot apply: a route whose
# folder holds no such helpers (the plugin's bundle/runtime/loop) and a handover root that does not exist (another
# user's machine) each print a NOTE and go on. SEAMS: START_RUN_HANDOVER_ROOT (the pack folder), START_RUN_CLOCK_ROWS=off.
# It is off ONLY for START_RUN_CLOCK_ROWS=off or a START_RUN_EVIDENCE under a temp or scratch folder (the launcher suites'
# own throwaway evidence), never for any other value; off prints a NOTE, never a silent skip. The helper's stdout is the
# verdict; its stderr is dropped, so a stray warning is never read as a refusal.
CLK=on
case "${START_RUN_EVIDENCE:-}" in
  /tmp/*|/private/tmp/*|/var/folders/*|/private/var/folders/*|"$HOME"/.claude/brother-scratch/*) CLK=off;;
esac
[ -z "${TMPDIR:-}" ] || case "${START_RUN_EVIDENCE:-}" in "${TMPDIR%/}"/*) CLK=off;; esac
[ "${START_RUN_CLOCK_ROWS:-}" != off ] || CLK=off
if [ "$CLK" = off ]; then
  echo "NOTE clock: the close pack and the restore drill were NOT checked against the deadline (START_RUN_CLOCK_ROWS=off or a test evidence folder)"
elif ! { [ -f "$LDIR/../close_ceremony_check.py" ] && [ -f "$LDIR/../acc6_restore_evidence.py" ] && [ -f "$LDIR/../handover_pack_scan.py" ]; }; then
  echo "NOTE clock: not checked on this route (helpers absent beside $LDIR)"
else
  CLKOUT=$(python3 -B - "$LDIR/.." "$REPO" "$DEADLINE" "${START_RUN_HANDOVER_ROOT:-}" <<'PY' 2>/dev/null
import datetime, os, sys
here, repo, dl, hroot = sys.argv[1:5]
sys.path.insert(0, os.path.abspath(here))
try:
    import close_ceremony_check as cc
    import acc6_restore_evidence as ar
    deadline = datetime.datetime.strptime(dl, "%Y-%m-%d %H:%M").timestamp()
    root = hroot or cc.ROOT
    lines = []
    if os.path.isdir(root):
        lines += cc.expiry_errors(root, deadline)
    else:
        print("NOTE clock: no handover root at %s (another user's machine), the close pack was not checked" % root)
    lines += ar.expiry_errors(repo, deadline)
except Exception as exc:
    lines = ["clock rows could not be checked (%s: %s); the helpers are scripts/close_ceremony_check.py and scripts/acc6_restore_evidence.py"
             % (type(exc).__name__, exc)]
print("\n".join(lines))
PY
  ) || CLKOUT="clock rows could not be checked (the helper process failed)"
  printf '%s\n' "$CLKOUT" | grep '^NOTE ' || true
  CLKOUT=$(printf '%s\n' "$CLKOUT" | awk 'NF && $0 !~ /^NOTE /' | head -1)
  [ -z "$CLKOUT" ] || refuse clock "$CLKOUT"
  echo "OK   clock  the close pack and the restore drill outlast the deadline $DEADLINE"
fi
tmark clock 2>/dev/null || true

# 2 PINS
for WHICH in claude codex; do
  VAR=BROTHER_CLAUDE_BIN; [ "$WHICH" = codex ] && VAR=BROTHER_CODEX_BIN
  CUR=${!VAR:-}
  if [ -z "$CUR" ]; then
    CUR=$(python3 -B -c "import sys; sys.path.insert(0, 'scripts/loop'); import model_router; print(model_router.${WHICH}_bin())" 2>/dev/null | awk 'NF' | tail -1)
  fi
  [ -f "$CUR" ] && [ -x "$CUR" ] || refuse pins "$VAR resolves to '${CUR:-nothing}', not an executable file; set it to the real program"
  export "$VAR=$CUR"
  echo "OK   pins   $VAR=$CUR"
done
# L11 (2026-10-16): whether the pinned Claude program is signed in, and which installed one is, with the remedy. REPORTING
# ONLY: the refusal stays the intake's proof (start-real2 to 6 on 10-14 were refused there, rightly, after up to 51 s).
# `<program> auth status` prints JSON with loggedIn; anything else is "cannot tell", never "signed in".
python3 -B - "$BROTHER_CLAUDE_BIN" "$(dirname "$0")" <<'PY' 2>/dev/null || echo "WARN signin could not tell whether BROTHER_CLAUDE_BIN=$BROTHER_CLAUDE_BIN is signed in (the check failed); the intake's proof decides"
import json, os, subprocess, sys
pinned, here = sys.argv[1], os.path.abspath(sys.argv[2])
sys.path.insert(0, here)
import loop_switches as SW
if SW.on("BROTHER_LOOP_TOKEN"):
    print("OK   signin BROTHER_LOOP_TOKEN=on: seats run on the loop login, which the intake's seat probe proves")
    sys.exit(0)
def signed_in(prog):
    """True or False as the program says, else the reason it cannot tell."""
    try:
        r = subprocess.run([prog, "auth", "status"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True, timeout=20)
        v = json.loads(r.stdout).get("loggedIn")
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError) as exc:
        return "auth status gave no answer (%s)" % type(exc).__name__
    return v if isinstance(v, bool) else "auth status named no loggedIn (exit %d)" % r.returncode
state = signed_in(pinned)
if state is True:
    print("OK   signin BROTHER_CLAUDE_BIN=%s is signed in" % pinned)
    sys.exit(0)
if state is not False:
    print("WARN signin could not tell whether BROTHER_CLAUDE_BIN=%s is signed in (%s); the intake's proof decides" % (pinned, state))
    sys.exit(0)
other = None
try:
    sys.path.insert(0, os.path.dirname(here))
    import brother_paths
    for cand in brother_paths.claude_candidates():
        if os.path.realpath(cand) != os.path.realpath(pinned) and signed_in(cand) is True:
            other = cand
            break
except (ImportError, OSError, ValueError):
    pass   # sbe: allow-silent which other program is signed in is a hint; the line below still names the pin and its remedy
if other:
    print("WARN signin BROTHER_CLAUDE_BIN=%s is not signed in; %s is: sign in to %s, or set BROTHER_CLAUDE_BIN=%s" % (pinned, other, pinned, other))
else:
    print("WARN signin BROTHER_CLAUDE_BIN=%s is not signed in, and no other installed claude program reads signed in: sign in to %s" % (pinned, pinned))
PY
tmark pins 2>/dev/null || true

# 3 CONTROLS
EVD=${START_RUN_EVIDENCE:-$HOME/.claude/evidence}
HOLD="$EVD/LOOP-HOLD.txt"; PAUSE="$EVD/LOOP-PAUSE.txt"
# Round 3 B2: the pid makes the stamp unique, so two launches in one second never share an intake or driver log (a
# refused driver used to read the other launch's LOOP UNTIL line and print STARTED).
STAMP=$(date +%Y%m%d-%H%M%S)-$$; STARTS="$EVD/loop-starts"; mkdir -p "$STARTS" || refuse controls "cannot create $STARTS"
# The launch lock, taken before the leftover check and the live check, released on every exit by the EXIT trap. The
# traps go in first, so no signal can land between taking the lock and being able to release it.
trap release_lock EXIT
trap on_signal INT TERM HUP ALRM QUIT
LOCKDIR="$STARTS/.launch-lock"
if mkdir "$LOCKDIR" 2>/dev/null; then LOCK=$LOCKDIR; else
  [ -d "$LOCKDIR" ] || refuse launch "cannot create the launch lock $LOCKDIR"
  OTHER=$(cat "$LOCKDIR/pid" 2>/dev/null)
  # an empty pid can be a launch between its mkdir and its pid write: read once more before judging it
  [ -n "$OTHER" ] || { sleep 1; OTHER=$(cat "$LOCKDIR/pid" 2>/dev/null); }
  case "$OTHER" in ''|*[!0-9]*) refuse launch "the launch lock $LOCKDIR names no pid ('$OTHER'); if no other start_run.sh is running, remove that folder by hand";; esac
  ! kill -0 "$OTHER" 2>/dev/null || refuse launch "another launch is in progress (pid $OTHER holds $LOCKDIR); wait for it to finish"
  echo "WARN launch: removing a stale launch lock, pid $OTHER is not alive ($LOCKDIR)"
  # ponytail: two launches judging the same stale lock at once can race here; the loser's mkdir refuses below.
  rm -f "$LOCKDIR/pid"
  rmdir "$LOCKDIR" 2>/dev/null && mkdir "$LOCKDIR" 2>/dev/null || refuse launch "cannot replace the stale launch lock $LOCKDIR (another launch may have just taken it)"
  LOCK=$LOCKDIR
fi
echo "$$" > "$LOCK/pid" || refuse launch "cannot write $LOCK/pid"
# Round 3 B1: a SIGKILL cannot be trapped, so a launch killed after its lift leaves the hold and pause in its
# controls-held-* folder, out of force. A successful start marks its folder .started and is skipped.
for d in "$STARTS"/controls-held-*; do
  [ -d "$d" ] && [ ! -e "$d/.started" ] || continue
  for f in "$d/LOOP-HOLD.txt" "$d/LOOP-PAUSE.txt"; do
    if [ -e "$f" ] || [ -L "$f" ]; then
      refuse controls "$d still holds a hold or pause from an earlier launch that never started: those controls are NOT in force; move them back to $EVD by hand (where $EVD already has a newer one, decide which stays), then start again"
    fi
  done
done
LIFT=0; LIFTREC="$STARTS/controls-lifted-$STAMP.txt"
if [ -e "$HOLD" ] || [ -L "$HOLD" ] || [ -e "$PAUSE" ] || [ -L "$PAUSE" ]; then
  [ -n "$WORDS" ] || refuse controls "a HOLD or PAUSE is in force ($(cat "$HOLD" "$PAUSE" 2>/dev/null | head -1 | cut -c1-120)); lift it only on the owner's word: --lift-hold \"<his words>\""
  LIFT=1
  if [ "$DRY" = 1 ]; then echo "DRY  controls would be lifted on: $WORDS"
  else echo "OK   controls in force; lifted on the owner's words once every other check has passed"; fi
else
  echo "OK   controls clear"
fi
# 3b LIVE (2026-10-10): nothing in the driver refuses a second instance, and two drivers over one plan double the spend
# and race the landing. Which processes are the loop's is decided in ONE place (loop_procs.py, through stop_loop.sh
# --dry: 0 nothing alive, 1 something alive, 2 unreadable). Only 0 starts; an answer that could not be read refuses.
tmark controls 2>/dev/null || true
LIVE=${START_RUN_LIVE_CHECK:-$REPO/scripts/loop/stop_loop.sh}
LOUT=$(bash "$LIVE" --dry 2>&1)
LRC=$?; LIVE_AT=$(clock_now)
case "$LRC" in
  0) echo "OK   live   no loop is running";;
  1) refuse live "a loop is already running; stop it first with scripts/loop/stop_loop.sh --reason: $(printf '%s' "$LOUT" | tr '\n' ' ' | cut -c1-200)";;
  *) refuse live "the live check could not answer (exit $LRC): $(printf '%s' "$LOUT" | tr '\n' ' ' | cut -c1-200)";;
esac
tmark live 2>/dev/null || true
if [ "$DRY" = 1 ]; then echo "DRY  would prepare the intake and start: loop_until.sh $DEADLINE $GAP"; exit 0; fi

# 3d BOOT (1.1.6 S2): a reboot kills the driver and nothing relaunches it, so the next launch is where it is seen.
BOOT=${START_RUN_BOOTTIME-$(sysctl -n kern.boottime 2>/dev/null)}
BOOT=$(printf '%s' "$BOOT" | tr -d '\n' | cut -c1-200)
[ -n "$BOOT" ] || echo "NO-DATA boot: kern.boottime could not be read; this launch's record carries no boot time"
python3 -B - "$LDIR" "$STARTS" "$BOOT" <<'PY' 2>/dev/null || echo "NO-DATA reboot: the earlier launch records could not be compared"
import sys
sys.path.insert(0, sys.argv[1])
import loop_intake as LI
note = LI.reboot_note(sys.argv[2], sys.argv[3])
if note:
    print("NOTE reboot: %s" % note)
if "killed by a reboot" in note:
    try:
        path = LI.mark_not_delivered(sys.argv[2], sys.argv[3])
        if path:
            print("NOTE reboot: that run is not counted delivered (%s)" % path)
    except OSError as exc:
        print("WARN reboot: that run could not be marked not delivered (%s)" % type(exc).__name__)
PY

# 3c LIFT, the last step before the intake (which reads a hold as HELD); every later refusal moves both back.
if [ "$LIFT" = 1 ]; then
  # 2026-10-11 review F5: the record is written with noclobber, so a launch in the same second refuses here instead of
  # truncating the other launch's record before its mkdir below would have refused.
  ( set -C; { echo "lifted $(date '+%Y-%m-%d %H:%M:%S %Z') on the owner's words: $WORDS"; echo "--- the hold and pause as found:"; for f in "$HOLD" "$PAUSE"; do [ -e "$f" ] && cat "$f"; done; true; } > "$LIFTREC" ) \
    || refuse controls "cannot record the lift in $STARTS"
  mkdir "$STARTS/controls-held-$STAMP" || refuse controls "cannot create $STARTS/controls-held-$STAMP"
  SAVED="$STARTS/controls-held-$STAMP"
  for f in "$HOLD" "$PAUSE"; do
    if [ -e "$f" ] || [ -L "$f" ]; then mv "$f" "$SAVED/" 2>/dev/null || refuse controls "cannot remove the hold or pause"; fi
  done
  echo "OK   controls lifted on the owner's words, kept in $LIFTREC"
fi
tmark lift 2>/dev/null || true

# 4 INTAKE, from this checkout's copy
INTAKE=${START_RUN_INTAKE:-$REPO/scripts/loop/loop_intake.py}
[ -f "$INTAKE" ] || refuse intake "no $INTAKE in this checkout"
ILOG="$STARTS/intake-$STAMP.log"
# THE SCOPE IS ALWAYS NAMED (2026-09-27): with none given the pool fell back to an old hard coded list and never admitted
# 18 fully specified units; the default is the whole plan.
# The intake runs in the background in its own process group (the launcher waits on it, because bash defers a trap
# until a foreground child ends), so a signal stops the intake and everything it started (round 3 N1).
# LA3.b: the launcher's fetch and live readings ride along; the intake ignores any it cannot read and checks again.
START_RUN_UPSTREAM_SHA="$UPSHA" START_RUN_FETCHED_AT="$FETCHED_AT" START_RUN_LIVE_RC="$LRC" START_RUN_LIVE_AT="$LIVE_AT" \
  perl -e 'setpgrp(0, 0); exec @ARGV or die "exec $ARGV[0]: $!\n"' python3 -B "$INTAKE" prepare finisher="$FIN" documenter="$DOC" checker="$CHK" worker="$WRK" adversary="$ADV" --deadline "$DEADLINE" --budget-usd "$BUDGET" --scope "$SCOPE" ${PLAN:+--plan "$PLAN"} \
  --words "start_run.sh: deadline $DEADLINE, budget $BUDGET USD${WORDS:+; owner: $WORDS}" > "$ILOG" 2>&1 &
IPID=$!
wait "$IPID"
IRC=$?
IPID=""
tmark intake 2>/dev/null || true
# READY is printed before the record is written and can still be overturned (loop_intake.py appends INTAKE NOT READY
# or INTAKE NO-DATA after it), so the verdict is the exit code AND the last INTAKE line, never the first READY.
ILAST=$(grep '^INTAKE ' "$ILOG" | tail -1)
# 4b SEAT (1.1.6 S2): a signed out seat of either host refuses the arm, after one notify row per transport.
SEATS=$(grep '^SEAT ' "$ILOG")
if [ -n "$SEATS" ]; then
  NDIR=${BROTHER_RUN_DIR:-$STARTS/notify-$(date +%Y%m%d)}
  NOTE=$(python3 -B - "$LDIR" "$NDIR" "$SEATS" <<'PY' 2>&1
import os, sys, time
sys.path.insert(0, sys.argv[1])
import loop_intake as LI
d = sys.argv[2]
try:
    os.makedirs(d, exist_ok=True)
    said = []
    for line in sys.argv[3].splitlines():
        parts = line.split()
        if len(parts) > 1:
            wrote = LI.notify_once(d, "seat-signed-out:" + parts[1], "seat", time.time())
            said.append("%s %s" % (parts[1], "row written" if wrote else "row already there"))
    print("notify %s: %s" % (os.path.join(d, LI.NOTIFY_FILE), ", ".join(said)))
except OSError as exc:
    print("the notify row could not be written under %s (%s)" % (d, type(exc).__name__))
PY
)
  refuse seat "$(printf '%s' "$SEATS" | sed 's/^SEAT *//' | tr '\n' ' ' | cut -c1-400)(${NOTE:-the notify step gave no answer}; full: $ILOG)"
fi
[ "$IRC" = 0 ] && [ "$ILAST" = "INTAKE READY" ] || refuse intake "not READY (exit $IRC, last verdict: ${ILAST:-none}); the refusing rows: $(grep -E '^(REFUSED|NO-DATA)' "$ILOG" | head -3 | cut -c1-160 | tr '\n' ' ') (full: $ILOG)"
echo "OK   intake READY ($ILOG)"

# 4c GRANT (1.1.6 S2, SG1): only when asked. The words are the intake record's own owner_words; the seats are new
# sessions, so the spend at grant is zero; the slug is the spend guard's project_slug rule for this checkout.
if [ -n "$GRANT" ]; then
  GOUT=$(python3 -B - "$EVD/loop-intake/CURRENT.json" "$DEADLINE" "$REPO" "$GRANT" "${START_RUN_GRANT:-$LDIR/../loop_budget_grant.py}" <<'PY' 2>&1
import datetime, json, subprocess, sys
rec_path, deadline, repo, tokens, tool = sys.argv[1:6]
try:
    with open(rec_path, encoding="utf-8") as fh:
        words = json.load(fh).get("owner_words")
except (OSError, ValueError, AttributeError) as exc:
    print("the intake record %s cannot be read (%s)" % (rec_path, type(exc).__name__))
    sys.exit(2)
if not isinstance(words, str) or not words.strip():
    print("the intake record %s carries no owner_words" % rec_path)
    sys.exit(2)
until = datetime.datetime.strptime(deadline, "%Y-%m-%d %H:%M").astimezone().isoformat(timespec="seconds")
slug = repo.replace("/", "-").replace(".", "-")
try:
    r = subprocess.run([sys.executable, "-B", tool, "--words", words, "--tokens", tokens, "--until", until,
                        "--project-slug", slug, "--spent", "0"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
except (OSError, subprocess.SubprocessError) as exc:
    print("%s could not run (%s)" % (tool, type(exc).__name__))
    sys.exit(2)
print(" ".join(r.stdout.split())[-400:])
sys.exit(r.returncode)
PY
  ) || refuse grant "loop_budget_grant did not write the raise: $(printf '%s' "$GOUT" | tr '\n' ' ' | cut -c1-300)"
  echo "OK   grant  $GOUT"
fi

# 4d CANARY (1.1.7 LR1.d): the whole path on one tiny real change, before the first pass. Run in its own process group
# like the intake, so a signal stops it. Only exit 0 goes on; 1 names the refusing row, 3 is NO-DATA, anything else is a
# canary that did not answer: every one of them refuses, never a start.
CANARY=${START_RUN_CANARY:-$REPO/scripts/loop/loop_canary.py}
[ -f "$CANARY" ] || refuse canary "no $CANARY in this checkout"
CLOG="$STARTS/canary-$STAMP.log"
case "$CANARY" in *.py) CCMD=(python3 -B "$CANARY" --landing);; *) CCMD=(bash "$CANARY" --landing);; esac
# THE ONE SCRATCH ROOT (LR1, 2026-10-21; the rule loop_until.sh states: $BROTHER_SCRATCH/run-<stamp>): the canary and every
# child it starts write under it, never in the caller's TMPDIR or the system temp folder (on macOS /tmp is a link to
# /private/tmp, so a path spelled through it no longer equals the same path resolved, which refused every real launch).
CTMP="${BROTHER_SCRATCH:-$HOME/.claude/brother-scratch}/run-$STAMP"
mkdir -p "$CTMP" || refuse canary "cannot create its scratch directory $CTMP"
TMPDIR="$CTMP" perl -e 'setpgrp(0, 0); exec @ARGV or die "exec $ARGV[0]: $!\n"' "${CCMD[@]}" > "$CLOG" 2>&1 &
IPID=$!
wait "$IPID"
CRC=$?
IPID=""
rm -rf "$CTMP"   # our own directory, named above and made here; the driver prunes the root by age as well
CLAST=$(awk 'NF' "$CLOG" | tail -1 | cut -c1-300)
case "$CRC" in
  0) # exit 0 is not enough (LR1 review): an older loop_canary.py ignores --landing and exits 0 after the replay alone, so
     # the last line must be the one only a landing stage that reached WOULD LAND prints (loop_canary.LANDING_OK)
     case "$CLAST" in
       "CANARY WOULD LAND: the replay and the landing stage"*) echo "OK   canary $CLAST ($CLOG)";;
       *) refuse canary "the canary exited 0 without the landing stage's line (an older loop_canary.py ignores --landing); its last line: ${CLAST:-none} (full: $CLOG)";;
     esac;;
  1) refuse canary "${CLAST:-the canary refused and said nothing} (full: $CLOG)";;
  3) echo "NOTE canary: ${CLAST:-no reason} (full: $CLOG)"; refuse canary "NO-DATA";;
  *) refuse canary "the canary exited $CRC: ${CLAST:-no output} (full: $CLOG)";;
esac

# THE FIRST CLAIMED UNIT AND THE TIMING RECORD (LA1.a). The loop log's name carries only %Y%m%d-%H%M, so a relaunch in
# the same minute appends to an older run's log: only a START row after the LAST `RUN START` line is this launch's.
# Exits non zero when the record could not be written; the caller ignores it (`|| true`): reporting only.
launch_timing() {
  python3 -B - "$1" "${START_RUN_TIMING_WAIT_S:-90}" "${START_RUN_SLOW_S:-180}" "$T0" "$STAMP" "$TSTEPS" "$ILOG" "$STARTS/timing-$STAMP.json" <<'PY'
import json, os, re, sys, time
log, wait_s, slow_s, t0, stamp, steps, ilog, out = sys.argv[1:9]
whole = lambda v, d: int(v) if re.fullmatch(r"[0-9]+", v or "") else d
wait_s, slow_s, t0 = whole(wait_s, 90), whole(slow_s, 180), whole(t0, None)
ROW = re.compile(rb"^[A-Za-z0-9_.-]+ +[A-Za-z0-9_.-]+ +START *$")
def seen():
    try:
        with open(log, "rb") as f:
            rows = f.read().splitlines()
    except OSError:
        return False
    starts = [i for i, l in enumerate(rows) if l.startswith(b"RUN START")]
    return bool(starts) and any(ROW.match(l) for l in rows[starts[-1] + 1:])
try:
    end = time.monotonic() + wait_s
    found = seen()
    while not found and time.monotonic() < end:
        time.sleep(1)
        found = seen()
    total = int(time.time()) - t0 if found and t0 is not None else None
    slow = total is not None and total > slow_s
    if total is not None:
        print("LAUNCH command to first claimed unit %d s%s" % (total, " SLOW (over %d s)" % slow_s if slow else ""))
    elif found:
        print("TIME NO-DATA: a START row was seen but the launch clock could not be read")
    else:
        print("TIME NO-DATA: no START row within %d s (log %s)" % (wait_s, log))
    launcher = {}
    for pair in steps.split():
        k, _, v = pair.partition("=")
        launcher[k] = int(v) if re.fullmatch(r"-?[0-9]+", v) else None
    intake = {}
    try:
        with open(ilog, encoding="utf-8", errors="replace") as f:
            for l in f:
                m = re.match(r"^TIME ([A-Za-z0-9_]+) ([0-9]+(?:\.[0-9]+)?)$", l.rstrip("\n"))
                n = re.match(r"^TIME NO-DATA ([A-Za-z0-9_]+):", l)
                if m: intake[m.group(1)] = float(m.group(2))
                elif n: intake[n.group(1)] = None
    except OSError:
        pass   # sbe: allow-silent an unreadable intake log leaves the intake steps empty; the record still says what it saw
    rec = {"schema": "launch-timing-v1", "stamp": stamp, "launcher": launcher, "intake": intake, "total_s": total, "slow": slow}
    tmp = "%s.tmp-%d" % (out, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1)
        os.replace(tmp, out)
    except OSError:
        try: os.unlink(tmp)
        except OSError: pass   # sbe: allow-silent the temp file may never have been made
        print("TIME NO-DATA: the timing record could not be written")
        sys.exit(3)
except Exception as exc:   # sbe: allow-silent reporting only: a timer that breaks says so and the launch stands
    print("TIME NO-DATA: the launch timing could not be computed (%s)" % type(exc).__name__)
    sys.exit(3)
PY
}

# 5b HOLDER (1.1.6 S2): the driver's run folder is found by the DRIVER's pid, DPID (loop_until.sh names it
# run-<stamp>-<its pid>, and the spawner execs the driver). Off the token lane DPID is PID; on it PID is the holder,
# which started the driver as its one child. Prints the folder and returns 0, or prints why and returns 1.
run_folder() {
  local runs="${BROTHER_RUNS_ROOT:-$HOME/.claude/evidence/loop-runs}" run="" d
  for d in "$runs"/run-*-"$DPID"; do
    [ -d "$d" ] || continue
    [ -z "$run" ] || { echo "two run folders end in the driver pid $DPID under $runs"; return 1; }
    run=$d
  done
  [ -n "$run" ] || { echo "no run folder of the driver pid $DPID under $runs"; return 1; }
  echo "$run"
}
# The holder is already serving: it read the login before it started the driver, DPID (its one child, named by the
# caller in this shell, since start_holder runs in a command substitution). This writes the holder's pid and run folder
# into the driver's run folder for stop_loop.sh. Prints the OK line and returns 0, or prints why and returns 1 (the
# caller stops the holder's whole group and refuses).
start_holder() {
  if [ -z "$HOLDER" ]; then echo "OK   holder not started: BROTHER_LOOP_TOKEN is not on, so no seat reads the saved login"; return 0; fi
  local run
  case "$DPID" in ''|*[!0-9]*|"$PID") echo "the login holder pid $PID names no one driver it started ('$(echo $DPID)')"; return 1;; esac
  run=$(run_folder) || { echo "$run"; return 1; }
  { printf '%s %s\n' "$PID" "$HDIR" > "$run/login-holder.pid.tmp" && mv -f "$run/login-holder.pid.tmp" "$run/login-holder.pid"; } 2>/dev/null \
    || { echo "the holder's pid file under $run could not be written"; return 1; }
  kill -0 "$PID" 2>/dev/null || { echo "the login holder pid $PID ended before its pid file was written"; return 1; }
  echo "OK   holder pid $PID serves the run's login for $run (it started the driver pid $DPID)"
}
# 5c WATCH (1.1.7 LR1.c): arm the watcher beside the driver DPID over its own log and heartbeat. Prints the OK line and
# returns 0, or prints why and returns 1 (the caller stops the driver's group and refuses [watch]).
arm_watcher() {
  local watch=${START_RUN_WATCH:-$LDIR/run_watch.py} logp run wpid
  [ -f "$watch" ] || { echo "no watcher $watch"; return 1; }
  logp=$(grep -m1 '^LOOP UNTIL' "$DLOG" | sed -n 's/.*| log //p')
  [ -n "$logp" ] || { echo "the driver's log path could not be read from its first line"; return 1; }
  run=$(run_folder 2>/dev/null) || run="$STARTS/watch-$STAMP"
  mkdir -p "$run" 2>/dev/null || { echo "cannot create the watcher's folder $run"; return 1; }
  wpid=$(python3 - "$watch" "$DPID" "$logp" "$EVD/LOOP-HEARTBEAT.json" "$run" <<'PY'
import os, subprocess, sys
watch, pid, log, hb, run = sys.argv[1:6]
out = open(os.path.join(run, "watcher.log"), "ab")
p = subprocess.Popen([sys.executable, "-B", watch, "--pid", pid, "--log", log, "--heartbeat", hb, "--run-dir", run],
                     stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
print(p.pid)
PY
  ) || { echo "the watcher could not be spawned"; return 1; }
  sleep 1
  kill -0 "$wpid" 2>/dev/null || { echo "the watcher pid $wpid ended within a second: $(tail -2 "$run/watcher.log" 2>/dev/null | tr '\n' ' ' | cut -c1-160)"; return 1; }
  printf '%s\n' "$wpid" > "$run/watcher.pid" 2>/dev/null || { kill "$wpid" 2>/dev/null; echo "the watcher's pid file under $run could not be written"; return 1; }
  echo "OK   watch  pid $wpid watches the driver pid $DPID ($run)"
}
# The launch record (1.1.6 S2): written at STARTED, read by the next launch's boot step. Reporting only.
launch_record() {
  local run
  run=$(run_folder 2>/dev/null) || run=""
  python3 -B - "$STARTS/launch-$STAMP.json" "$STAMP" "$BOOT" "$DPID" "$run" <<'PY' || echo "WARN boot: the launch record could not be written; the next launch cannot tell a reboot"
import json, os, sys, time
path, stamp, boot, pid, run = sys.argv[1:6]
rec = {"schema": "launch-v1", "stamp": stamp, "boot": boot or None, "driver_pid": int(pid), "run_dir": run or None,
       "at": time.time()}
tmp = "%s.tmp-%d" % (path, os.getpid())
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(rec, fh, indent=1)
os.replace(tmp, path)
PY
}

# 5 DRIVER, detached, with the intake's launch settings
DRIVER=${START_RUN_DRIVER:-$HOME/.claude/bin/loop_until.sh}
ENVF="$EVD/loop-intake/launch-env.sh"
[ -f "$ENVF" ] || refuse driver "no $ENVF: the intake did not write its launch settings"
DLOG="$STARTS/driver-$STAMP.log"
HOLDER=""; HDIR=""
if [ "${BROTHER_LOOP_TOKEN:-}" = on ]; then   # 5b: the holder starts the driver; its run folder is this launch's own
  HOLDER=${START_RUN_HOLDER:-$LDIR/brother_login.py}
  HDIR="$STARTS/hold-$STAMP"
  [ -f "$HOLDER" ] || refuse holder "no login holder $HOLDER"
  mkdir "$HDIR" 2>/dev/null || refuse holder "cannot create the holder's run folder $HDIR"
fi
PID=$(python3 - "$DRIVER" "$ENVF" "$DEADLINE" "$GAP" "$DLOG" "$REPO" "$HOLDER" "$HDIR" <<'PY'
import os, subprocess, sys
driver, envf, deadline, gap, log, repo, holder, hdir = sys.argv[1:9]
out = open(log, "ab")
run = ["bash", driver, deadline, gap]
if holder:   # 1.1.6 S2: the holder reads the login once, then starts the driver with the descriptor (S1.b)
    run = [sys.executable, "-B", holder, "hold", "--run", hdir, "--"] + run
# S2 round 3 (DeepSeek lane L4): the launch settings are sourced BEFORE the holder starts, so no child of that prelude
# ever holds its descriptor; the prelude's bash then becomes the holder (or the driver) by exec, the same pid.
cmd = ["bash", "-c", 'source "$1" && shift && exec "$@"', "start_run", envf] + run
p = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=repo,
                     start_new_session=True, env=dict(os.environ, BROTHER_LAUNCH_WORKTREE=repo))
print(p.pid)
PY
) || refuse driver "the driver could not be spawned"
DPID=$PID
for _ in $(seq 1 120); do
  if grep -q '^LOOP UNTIL' "$DLOG" 2>/dev/null; then
    tmark driver 2>/dev/null || true
    [ -z "$HOLDER" ] || DPID=$(pgrep -P "$PID" 2>/dev/null)   # the holder's one child is the driver
    if ! HOUT=$(start_holder); then
      kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null
      # S2 round 1: the holder starts the driver in the driver's own group, so that group is stopped too
      case "$DPID" in ''|*[!0-9]*|"$PID") ;; *) kill -TERM -- "-$DPID" 2>/dev/null || kill -TERM "$DPID" 2>/dev/null;; esac
      refuse holder "$HOUT; the run's process group $PID was stopped"
    fi
    echo "$HOUT"
    if ! WOUT=$(arm_watcher); then
      kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null
      case "$DPID" in ''|*[!0-9]*|"$PID") ;; *) kill -TERM -- "-$DPID" 2>/dev/null || kill -TERM "$DPID" 2>/dev/null;; esac
      refuse watch "$WOUT; the run's process group $PID was stopped"
    fi
    echo "$WOUT"
    echo "OK   driver pid $DPID: $(grep -m1 '^LOOP UNTIL' "$DLOG")"
    echo "STARTED: watch with: tail -f $(grep -m1 '^LOOP UNTIL' "$DLOG" | sed -n 's/.*| log //p')"
    trap - INT TERM HUP ALRM QUIT
    # the held copies stay on purpose after a successful start; the mark tells the next launch they are not leftovers
    if [ -n "$SAVED" ]; then
      { : > "$SAVED/.started"; } 2>/dev/null || echo "WARN controls: could not mark $SAVED as a successful start; the next start refuses on it until $SAVED/.started exists"
    fi
    launch_record
    launch_timing "$(grep -m1 '^LOOP UNTIL' "$DLOG" | sed -n 's/.*| log //p')" || true
    exit 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    [ -z "$HOLDER" ] || refuse holder "the login holder (which starts the driver) exited before LOOP UNTIL: $(tail -3 "$DLOG" | tr '\n' ' ' | cut -c1-240)"
    refuse driver "the driver exited before it started: $(tail -3 "$DLOG" | tr '\n' ' ' | cut -c1-240)"
  fi
  sleep 1
done
refuse driver "no LOOP UNTIL line within 120 s; the driver pid $PID is still alive and honours the restored hold, stop it with scripts/loop/stop_loop.sh --reason (log $DLOG)"

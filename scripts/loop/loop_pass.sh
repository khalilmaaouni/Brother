#!/bin/bash
# ONE loop pass: digest, land every READY build in one batch, refill the runner pool. At most about 35 lines out.
# usage (any cwd): loop_pass.sh      Exit 0 always; each tool prints its own verdict and the landing's exit is echoed.
# owner order 2026-09-20, maximum width: model stage is bounded by nothing (cents, no CPU), machine stage by cores, landing stays serial
# ADAPTIVE SIZING, one knob per stage, each bound by its own resource (the three stage law). Fixed numbers
# cannot be right twice: 8 slots is reckless on a machine already at load 7 and wasteful on an idle one. Every
# unknown returns the FLOOR, so an unreadable load, core count or money cap NARROWS the loop rather than
# widening it. An explicit env var still wins, so a human can pin any knob for one run.

# THE PULSE, on EVERY exit path including ones added after this line. Wiring it at each `exit`
# by hand is exactly the mistake fixed earlier tonight, where a productivity guard sat after a
# sibling early exit and was walked straight past. A trap cannot be bypassed by a new branch.
# LANDED_N is set late in the script, so an early exit reports 0, which is the truth for it.
pulse() { python3 -B ~/.claude/bin/loop_heartbeat.py write --state PASS \
    --reason "pass exited $1" --landed "${LANDED_N:-0}" >/dev/null 2>&1 || true; }
trap 'pulse $?' EXIT
# LIVE RUNNERS ARE READ AS A FACT OR NOT AT ALL (2026-09-27, the class Lane L2 fixed in loop_done.py, finding 9): both
# counts below were `ps -eo command | grep -c`, which ignores ps's exit status, so a failed or refused read counted as
# zero live runners, and zero is what turns a quiet pass into exit 45 UNPRODUCTIVE, which ends the run. A read that
# exits nonzero, or a table with no row past its header (a real one always lists ps itself), prints nothing and returns
# 1; each caller then says NO-DATA and holds its verdict: exit 0, and the next pass reads again.
# ONE OWNERSHIP RULE (X2 finding 7, 2026-09-27): the count is loop_procs', the program position a stop signals by, never
# a grep of every command line; a viewer whose ARGUMENT named unit_runner.py was counted live here while a stop left it
# alone, and it kept a pass that did nothing from exiting 45. loop_procs exits 2 on a refused, empty or malformed table.
live_runners() {
  local n
  n=$(python3 -B ~/.claude/bin/loop_procs.py unit-runners 2>/dev/null) || return 1
  case "$n" in ''|*[!0-9]*) return 1;; esac
  echo "$n"
}
NO_TABLE="NO-DATA: the process table could not be read, so whether a runner is alive is unknown; this pass holds its verdict and the next pass reads again"

# THE WORKTREE COMES FROM THE DRIVER (BROTHER_LAUNCH_WORKTREE) OR IS THE CURRENT DIRECTORY, and the remote and branch come
# from that checkout's configured upstream. Neither is a literal: a literal path and branch were true on one laptop and
# false in every other clone (owner, 2026-09-22: fix it at the root, for anyone who downloads the repository).
WT="${BROTHER_LAUNCH_WORKTREE:-$PWD}"
# THE CODE ROOT (U3, B5-04 and B5-08): the six scripts this pass runs by path (sizing, reprobe, diagnosis, the
# worktree fingerprint twice, the closer) come from BROTHER_CODE_ROOT, the frozen candidate the pair launcher names,
# never from the landing tree; their DATA stays on the landing tree (each runs with cwd $WT and reads docs/plan from
# it). A set value that is not an absolute directory, or a proof phase with none, runs nothing: frozen code never falls
# back to the landing tree.
# (C) OUTSIDE A PROOF, WITH NOTHING SET, THE LANDING TREE IS NEVER THE CODE ROOT (review 17 finding 4, 2026-10-03,
# executed: CODE_ROOT fell back to the launch worktree, so diag_round.py and worktree_sentry.py ran unsandboxed from the
# tree every landing writes into). CODE_ROOT stays empty there: the sizing eval and the diagnostician do not run (each
# says so), and the fingerprint runs the tree's sentry ISOLATED AND BOXED through the deployed lander
# (land_batch.py --boxed: it reads the tree and writes only its own temp directory, no network).
if [ -n "${BROTHER_CODE_ROOT:-}" ]; then
  case "$BROTHER_CODE_ROOT" in /*) ;; *) echo "LOOP BLOCKED: BROTHER_CODE_ROOT ${BROTHER_CODE_ROOT} is not an absolute path"; exit 44;; esac
  [ -d "$BROTHER_CODE_ROOT" ] || { echo "LOOP BLOCKED: BROTHER_CODE_ROOT ${BROTHER_CODE_ROOT} is not a directory"; exit 44; }
  CODE_ROOT="$BROTHER_CODE_ROOT"
elif [ -n "${BROTHER_PROOF_PHASE:-}" ]; then
  echo "LOOP BLOCKED: a proof phase runs frozen code only, and BROTHER_CODE_ROOT is not set"; exit 44
else
  CODE_ROOT=""
fi
sentry() {   # the worktree fingerprint: the frozen code root's sentry, else the landing tree's own, boxed
  if [ -n "$CODE_ROOT" ]; then python3 -B "$CODE_ROOT/scripts/worktree_sentry.py" "$@"
  else python3 -B ~/.claude/bin/land_batch.py --boxed "$WT/scripts/worktree_sentry.py" "$@"; fi
}
# FROZEN BY PLAN E (step 2f, 2026-10-01): history based sizing moved the settings between passes, so two runs on the same
# plan could behave differently. The declared launch settings hold unless BROTHER_TUNING=on.
if python3 -B ~/.claude/bin/loop_switches.py BROTHER_TUNING >/dev/null 2>&1; then
  if [ -n "$CODE_ROOT" ]; then eval "$(cd "$WT" && python3 -B "$CODE_ROOT/scripts/adaptive_sizing.py" --env 2>/dev/null)" || true
  else echo "SIZING  adaptive sizing skipped: no frozen code root (BROTHER_CODE_ROOT), and the landing tree's code never runs unboxed"; fi
fi
# AN EXPLICIT OWNER OVERRIDE BEATS THE ADAPTIVE FIGURE (owner 2026-09-22: optimise lanes for yield). Measured runs 3 and 4:
# the machine stage never bound (load 4.9 of 8 cores at 10 runners) and the floor was builds reaching READY (grader PASS
# 46 percent, probes clean 41 percent at 3 workers per round); the runner's own default and the measured pass@k plateau
# is 5. BROTHER_WORKERS_PER_ROUND, when set at launch, replaces the adaptive width; unset, nothing changes.
[ -n "${BROTHER_WORKERS_PER_ROUND:-}" ] && case "$BROTHER_WORKERS_PER_ROUND" in ''|*[!0-9]*) ;; *) WORKERS_PER_ROUND=$BROTHER_WORKERS_PER_ROUND;; esac
# BROTHER_LANES, when set at launch, is the owner's number of units in flight (owner 2026-09-22 18:0x: "I would like 6 lanes").
[ -n "${BROTHER_LANES:-}" ] && case "$BROTHER_LANES" in ''|*[!0-9]*) ;; *) BROTHER_WIP=$BROTHER_LANES;; esac
export LOCAL_SLOTS=${LOCAL_SLOTS:-8} WORKERS_PER_ROUND=${WORKERS_PER_ROUND:-6}
echo "SIZING  LOCAL_SLOTS=${LOCAL_SLOTS} BROTHER_WIP=${BROTHER_WIP:-5} WORKERS_PER_ROUND=${WORKERS_PER_ROUND} PIN ${BROTHER_PIN_MODEL:-none} LANES ${BROTHER_LANES:-default} SCOPE ${BROTHER_SCOPE:-all}"   # 3 is the proven shape and costs 40 percent less per build than 5, which buys more lanes from the same money
cd "$WT" || { echo "LOOP BLOCKED: launch worktree missing; a pass with no tree is not an ordinary pass"; exit 44; }
UP=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)
case "$UP" in
  */*) REMOTE="${UP%%/*}"; UPBRANCH="${UP#*/}";;
  *) echo "LOOP BLOCKED: the launch branch has no upstream, so no landing knows where to push: git branch --set-upstream-to <remote>/<branch>"; exit 44;;
esac
# CAN WE STOP? Asked BEFORE anything is spent. Until 2026-09-21 nothing this script printed ever meant
# "finished": brother_pass's NOTHING rung means "nothing actionable right now", which a landing or a diagnosed
# fact clears, and this script exited 0 unconditionally. So an unattended run had no way to end and would keep
# waking and paying long after the last unit closed. loop_done exits 0 only when every in scope unit reads DONE
# WITH evidence, nothing is READY and unlanded, and no runner is alive; every unknown fails toward WORKING,
# because stopping early abandons real work while one more pass costs cents.
# STAGE 0, RECONCILE, AND IT IS FIRST FOR A REASON. Measured 2026-09-21 on a live run: a landing committed,
# its push was REFUSED by the hermetic gate, and land_batch left the commit local. From that moment every later
# landing refused with "local <sha> is not hub <sha> before landing", the refill guard then skipped the pool,
# runners drained to ZERO, and the loop ran 22 further passes that each exited 0 while doing nothing at all. One
# refused push deadlocked the whole loop, silently, and no stage could see it because no stage looked.
#
# A local branch AHEAD of hub with a CLEAN tree is not a decision, it is an unpushed push, so this retries it.
# A push that is refused AGAIN is a real blocker: it exits 44 rather than 0, because a pass that cannot make
# progress must never look like a pass that did.
# STALE SANDBOXES ARE SWEPT (review 2026-09-22): grade_build cleans its temp trees in a finally that a SIGKILL
# skips. Only the grader's own prefixes under the temp dir, only older than three hours; a grade never takes that.
python3 - <<'PYSWEEP'
import glob, os, shutil, tempfile, time
n = 0
for d in glob.glob(os.path.join(tempfile.gettempdir(), "grade-build-*")) + glob.glob(os.path.join(tempfile.gettempdir(), "grade-mut-*")):
    try:
        if os.path.isdir(d) and time.time() - os.path.getmtime(d) > 3 * 3600: shutil.rmtree(d, ignore_errors=True); n += 1
    except OSError: pass
print("SWEEP   %d stale sandbox(es) removed" % n)
PYSWEEP
git fetch -q "$REMOTE" 2>/dev/null
AHEAD=$(git rev-list --count "$UP"..HEAD 2>/dev/null || echo 0)
DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [ "${AHEAD:-0}" -gt 0 ] && [ "${DIRTY:-1}" -eq 0 ]; then
  echo "RECONCILE local is ${AHEAD} commit(s) ahead of ${REMOTE} with a clean tree: pushing before anything else"
  # A GATE REFUSAL AND A DROPPED CONNECTION ARE NOT THE SAME FAILURE, and until 2026-09-21 this treated them
  # identically: one push, and if the branch was still ahead, exit 44 as BLOCKED forever. A transient blip then
  # halted an entire unattended run, and nothing cleared the alarm when the network came back. Measured that
  # evening: the board carried "the tree cannot reach hub" while hub already held the exact local HEAD, so the
  # run had been stopped by a failure that had already resolved itself.
  #
  # The estate already draws this line for model calls in or_fanout: a timeout or transport error can differ
  # next time and is retried, while a refusal by one of our own gates is deterministic and is reported at once.
  # The push path never learned it. It does now, and the distinction is read from the push's OWN words rather
  # than guessed: a gate that refuses SAYS so, and anything else is treated as transport and retried twice with
  # a pause. A gate refusal still blocks immediately, because retrying it cannot change the answer.
  # THROUGH THE LANDER, NEVER PLAIN git push (review 14 finding 3, 2026-10-02): the installed pre-push hook execs the
  # TREE's scripts/pre_push_hook.sh, so a plain push here ran just landed code unsandboxed. land_batch.py --push runs
  # the push gate from a frozen copy of hub's head and pushes with hooks off; a gate refusal is one BLOCK line.
  # UNDER THE RUN'S OWN TMPDIR, NEVER THE HOST'S (2026-10-03): macOS `mktemp -t` puts the file in the Darwin user temp
  # folder whatever TMPDIR says, which the landing sandbox denies, so the reconcile push through the lander died with an
  # empty log path (measured under the pre-push gate: "mkstemp failed on /var/folders/...: Operation not permitted").
  PUSHLOG=$(mktemp "${TMPDIR:-/tmp}/brother-push.XXXXXX")
  PUSH_TRIES=0
  while :; do
    python3 ~/.claude/bin/land_batch.py --push >"$PUSHLOG" 2>&1
    git fetch -q "$REMOTE" 2>/dev/null
    STILL=$(git rev-list --count "$UP"..HEAD 2>/dev/null || echo 0)
    [ "${STILL:-0}" -eq 0 ] && break
    if grep -qE 'pre-push: REFUSED|^BLOCK |rejected|non-fast-forward|protected branch|^REFUSED: the upstream' "$PUSHLOG"; then
      echo "LOOP BLOCKED: the push GATE refused, which a retry cannot change. Its own verdict:"
      grep -E 'pre-push: REFUSED|^BLOCK |rejected|non-fast-forward|protected branch|^REFUSED: the upstream' "$PUSHLOG" | head -4
      rm -f "$PUSHLOG"; exit 44
    fi
    PUSH_TRIES=$((PUSH_TRIES + 1))
    if [ "$PUSH_TRIES" -ge 3 ]; then
      echo "LOOP BLOCKED: ${STILL} commit(s) still cannot reach ${REMOTE} after 3 attempts, and the push named no gate"
      echo "              refusal, so this is transport and it did not recover. Last lines:"
      tail -3 "$PUSHLOG"
      rm -f "$PUSHLOG"; exit 44
    fi
    echo "RECONCILE push attempt ${PUSH_TRIES} did not land and named no gate refusal: treating as transport, retrying"
    sleep $((PUSH_TRIES * 10))
  done
  rm -f "$PUSHLOG"
  echo "RECONCILE parity restored"
fi
if [ "${DIRTY:-0}" -gt 0 ] && ! pgrep -f "land_batch.py" >/dev/null 2>&1; then
  # Dirty with NO writer running is unattributable work, which a landing will refuse anyway. Say so once.
  echo "LOOP BLOCKED: ${DIRTY} changed path(s) with no landing in flight; a landing cannot attribute them: $(git status --porcelain | head -5 | tr '\n' ' ')"
  exit 44
fi
python3 -B ~/.claude/bin/loop_done.py --scope "$BROTHER_SCOPE"; DONE=$?
if [ $DONE -eq 0 ]; then
  echo "LOOP FINISHED: stop the loop, there is nothing left in scope"
  exit 42          # 42 is the agreed 'stop the loop' code; 0 still means an ordinary pass ran
fi
if [ $DONE -eq 3 ]; then
  # STALLED is neither finished nor workable: unfinished work exists and not one sub unit is admissible.
  # Spinning here burns money for nothing and a false FINISHED would be worse, so the loop stops and SAYS SO.
  echo "LOOP STALLED: work remains but nothing is admissible. This needs a decision or a rubric change."
  exit 43
fi
# FOREIGN WRITE DETECTION, and it needs nobody's cooperation. The tree's own state is fingerprinted here and
# checked again immediately before the landing stage. A change this pass did not make is a foreign write, named
# deterministically rather than discovered as an unattributable diff at commit time.
# RE-PROBE BEFORE THE DIGEST, owner review 2026-09-22. A lane whose adversaries timed out ends READY-UNPROBED and
# nothing in this pass ever re-probed it: scripts/probe_round.py existed, was registered and tested, and no rung
# called it, so a grader PASS build was thrown away for a fresh runner every time. Two of eight lanes ended that
# way tonight within 25 minutes. probe_round writes only under the run folders, so the fingerprint above stands.
# DETACHED (owner, 2026-09-22 03:5x: "why is it taking so long"): run synchronously, this rung held the whole pass
# for its fan out wait (three times the 600 s timeout) plus lane by lane probe execution, so the checker, the
# landing and the refill waited half an hour behind it. It now runs in its own session, one instance at a time;
# its promotions are read by the next pass. No stage waits for its slowest member.
# RETIRED 2026-09-27 (owner: a silent probe no longer blocks a landing): the runner no longer writes READY-UNPROBED, so the
# re-probe has no new input; the historical ones are regraded by the finisher and handed to the landing gates (or
# re-probed first, under BROTHER_FINISHER_REGRADE_PROBES).
echo "REPROBE  retired: a silent probe goes to the landing gates"
# THE DIAGNOSTICIAN LANE (design section 4, "EXHAUSTED with no change"): a cheap model names ONE fact plus the grep that
# proves it, diag_apply runs the grep, and only a proven fact restarts the runner with RUNNER_HINT. Detached like the
# reprobe, at most 6 lanes a pass, and diag_brief asks each stuck run ONCE (its DIAGNOSED marker), so the spend is bounded
# by the number of stuck runs, never by the number of passes.
if [ -z "$CODE_ROOT" ]; then echo "DIAGNOSE skipped: no frozen code root (BROTHER_CODE_ROOT); the diagnostician calls models, so it cannot run boxed, and the landing tree's code never runs unboxed"
elif pgrep -f "scripts/diag_round\.py" >/dev/null 2>&1; then echo "DIAGNOSE already running from an earlier pass"; else
  python3 -c "import subprocess,sys,os; subprocess.Popen([sys.executable,'-B',sys.argv[1],'--limit','6'], stdout=open(os.path.expanduser('~/.claude/evidence/diag-round.log'),'a'), stderr=subprocess.STDOUT, start_new_session=True)" "$CODE_ROOT/scripts/diag_round.py"
  echo "DIAGNOSE started detached, log ~/.claude/evidence/diag-round.log"
fi
# SALVAGE BEFORE THE DIGEST READS WHAT IS READY (2026-09-22): a sub unit parked as EXHAUSTED or quarantined while a
# build of it sits on disk that is grader PASS, probe CLEAN and still applies is paid work thrown away. 15 such builds
# were found by hand the morning after a night that landed nothing. salvage.py marks the newest one per sub unit READY
# and keeps the old STATUS beside it; the landing gates below still decide. It never fails the pass.
# OFF BY PLAN E (step 2e, 2026-10-01): an old PASS promoted to READY without regrading against today's tree reached
# landing graded on a tree that no longer exists (ACC3.b salvaged, then refused). Runs only when BROTHER_SALVAGE=on.
if python3 -B ~/.claude/bin/loop_switches.py BROTHER_SALVAGE >/dev/null 2>&1; then
  SALV=$(python3 -B ~/.claude/bin/salvage.py promote 2>&1); SRC=$?
  printf '%s\n' "$SALV" | tail -3
  [ "${SRC:-0}" -ne 0 ] && echo "SALVAGE exit=$SRC: promotion crashed; clean builds from before stay unpromoted this pass"
else
  echo "SALVAGE off by plan E (BROTHER_SALVAGE is not on)"
fi
D=$(python3 ~/.claude/bin/pass_digest.py --no-fetch); DRC=$?; echo "$D"
if [ "${DRC:-0}" -ne 0 ]; then
  echo "LOOP BLOCKED: pass_digest exit=$DRC; without the digest this pass cannot see what is READY, so it lands nothing and starts nothing"
  exit 44
fi   # this pass fetched hub at reconcile seconds ago; a second round trip is 1 s of nothing
READY=$(echo "$D" | grep -E '^ +/.*-build\.json$' | tr -d ' ')
# THE CHECKER IS GONE (owner decision 2026-10-02, docs/decisions/remove-checker-2026-10-02.json): no measured unique catch
# in its history, and tonight's gate sent back 6 of 6 grader and probe clean builds. Nothing runs between READY and the snapshot.
FP_BEFORE=$(sentry snapshot 2>/dev/null); FPRC=$?
if [ -n "$READY" ]; then
  # one build per unit per batch: siblings patch the same files
  PICK=$(echo "$READY" | python3 -c "
import sys,re,os
seen=set()
for p in sys.stdin.read().split():
    u=re.split(r'[.-]',os.path.basename(p))[0]
    if u not in seen: seen.add(u); print(p)")
  # AN UNREADABLE FINGERPRINT IS NOT AN UNCHANGED TREE (Lane W, safety). A failed sentry read gives an empty snapshot,
  # and this check used to be skipped on exactly that, so a tree nobody could fingerprint was landed on. It refuses.
  if [ "${FPRC:-1}" -ne 0 ] || [ -z "${FP_BEFORE:-}" ]; then
    echo "LOOP BLOCKED: the worktree fingerprint could not be taken (sentry exit ${FPRC:-unknown}), so a foreign write cannot be ruled out; nothing is landed"
    exit 44
  fi
  if ! sentry verify "$FP_BEFORE" >/dev/null 2>&1; then
    echo "FOREIGN WRITE before landing: this tree changed and this pass did not change it."
    echo "                  Refusing to land work that cannot be attributed. Look before continuing."
    exit 44
  fi
  LANDED_LINE=$(python3 ~/.claude/bin/land_batch.py $PICK); LB=$?
  echo "$LANDED_LINE"; echo "land_batch exit=$LB"
  # a refused landing leaves builds applied but uncommitted: a runner briefed from that tree builds on sand
  # A REFUSED LANDING IS NOT A SUCCESSFUL PASS, and this line used to exit 0.
  #
  # Found by RUNNING the loop rather than reading it, 2026-09-21 19:42. The pass landed nothing, closed
  # nothing, started nothing, had ZERO runners alive, and exited 0. The productivity assertion added earlier
  # the same evening sits AFTER the pool refill, so this early exit walked straight past it. That is the exact
  # failure it was written to prevent (22 consecutive passes at exit 0 doing nothing) reappearing through a
  # path the fix did not cover. A guard that a sibling code path can bypass is not a guard.
  #
  # Exit 45 UNPRODUCTIVE, which the driver raises as its own alarm state, so a refused landing now reaches a
  # human in seconds instead of looking like an ordinary pass.
  # A HOLD IS NOT A REFUSAL (2026-09-22, first pass of the checker): three builds vetoed, land_batch printed
  # "nothing landed" and exited 1, and this branch then skipped the refill as if the tree needed a decision. With
  # a strict checker that starves the pool: no new runner until a pass happens to find nothing READY at all.
  # Only a REFUSED landing (gates red, stray paths, parity) means the tree needs a human; held builds do not.
  if [ $LB -ne 0 ] && ! printf '%s' "${LANDED_LINE:-}" | grep -q '^nothing landed'; then
    echo "runner pool NOT refilled: landing refused, tree needs a decision first"
    if ! LIVE_NOW=$(live_runners); then echo "$NO_TABLE"; exit 0; fi
    if [ "${LIVE_NOW:-0}" -eq 0 ]; then
      echo "PASS DID NOTHING: the landing was refused and no runner is alive, so this pass changed nothing."
      exit 45
    fi
    exit 0        # runners still working: the pass was quiet, not unproductive
  fi
fi
# CLOSE WHAT IS FINISHED, BEFORE REFILLING. Measured 2026-09-21 on a live run: this script had no call to
# close_unit at all, so across 34 passes and 2.5 hours it landed sub units while FOUR units sat with every sub
# unit in and nothing closing them. Units DONE read 10 the whole time. Whole units closed is the number the
# owner watches, and it was the one number the loop could not move. close_unit is safe to run every pass: it is
# idempotent, it refuses any unit whose sub units are not all landed, and it refuses a done_check that is prose
# rather than a command, so a pass that closes nothing costs one command and says so.
# THROUGH THE LANDER, NEVER THE TREE'S CLOSER (review 15 finding 2, 2026-10-03): this ran "$CODE_ROOT/scripts/close_unit.py",
# and outside a proof CODE_ROOT is the launch worktree, so the tree's closer (and the plan_store, provenance and plan_lint
# it imports) ran unsandboxed every pass, with every done check bare in the tree. land_batch.py --close runs the closer
# from a frozen copy of hub's head, each done check under the sandbox in a disposable copy, and commits and pushes the
# closure with the frozen checks and no hook.
CLOSED_ALL=$(python3 ~/.claude/bin/land_batch.py --close 2>&1); CRC=$?          # 0 closed something, 1 nothing to close, 2 NO-DATA
# EVERY verdict line, never the last six (2026-09-28: L0's "not a shape this tool will execute" refusal was the
# seventh line from the end, so the run log never showed why a closable unit stayed open)
CLOSED=$(printf '%s\n' "$CLOSED_ALL" | grep -E '^CLOSED|^CLOSE-RED|^CLOSE-SKIP|^CLOSE-SCOPE|DONE:|NOT CLOSED|not closable|not in the plan')
case "${CRC:-0}" in 0|1) ;; *) echo "CLOSE exit=$CRC: $(printf '%s\n' "$CLOSED_ALL" | tail -1 | cut -c1-140)";; esac
echo "$CLOSED" | cut -c1-160
# A closure edits the plan file, so it is committed NOW rather than left dirty: the next pass lands from a
# clean tree, and a landing refuses a tree it cannot attribute. BY THE LANDER (review 14 finding 3, 2026-10-02):
# a plain `git commit` and `git push` here ran the installed hooks, which exec the TREE's scripts
# (self_check_staged.py, pre_push_hook.sh): just landed code, unsandboxed. land_batch.py --push stages the plan
# alone, runs both checks from a frozen copy of hub's head, commits and pushes with hooks off; a refused check
# leaves the plan change as it was, and the next pass says so (dirty with no landing in flight).
if ! git diff --quiet -- docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json 2>/dev/null; then
  CLOSE_PUSH=$(python3 ~/.claude/bin/land_batch.py --push 2>&1); CPRC=$?
  echo "closure $(printf '%s\n' "$CLOSE_PUSH" | tail -1 | cut -c1-160)"
  [ "${CPRC:-1}" -ne 0 ] && echo "closure did not land (lander exit ${CPRC}): the plan change stays as the closer left it and the next pass reports it"
fi
POOL_ALL=$(python3 ~/.claude/bin/runner_pool.py 2>&1); PRC=$?
POOL=$(printf '%s\n' "$POOL_ALL" | grep -E 'START|^started' | cut -c1-160)
[ "${PRC:-0}" -ne 0 ] && echo "POOL exit=$PRC: $(printf '%s\n' "$POOL_ALL" | tail -1 | cut -c1-140)"
echo "$POOL"

# RC-7, A FAILURE THAT RETURNS SUCCESS. Measured 2026-09-21: this script ran 22 consecutive passes at exit 0
# with ZERO runners alive, landing nothing, closing nothing, starting nothing, and every one of those passes
# was indistinguishable from a working one. A refused push had left a commit local; every later landing refused
# on parity; the refill guard skipped the pool; runners drained to zero. Nobody could see it because "the pass
# ran" and "the pass did something" were the same exit code.
#
# So a pass now ASSERTS ITS OWN PRODUCTIVITY. Landed, closed and started are counted from what the tools
# actually printed. A pass that did none of the three AND has no live runner to show for it exits 45, because
# repeating it changes nothing and only a human can say why.
#
# A quiet pass is NOT a failure on its own: a loop with every lane busy and nothing ready to land is working
# exactly as intended, so a live runner is enough to call the pass productive. The failure is doing nothing
# with nothing running.
STARTED=$(printf '%s' "$POOL" | grep -oE '^started [0-9]+' | grep -oE '[0-9]+' | head -1)
LIVE=$(live_runners) || LIVE=unknown
LANDED_N=$(printf '%s' "${LANDED_LINE:-}" | grep -c 'LANDED' || true)
CLOSED_N=$(printf '%s' "${CLOSED:-}" | grep -c '^CLOSED  [1-9]' || true)
if [ "$LIVE" = unknown ] && [ "${STARTED:-0}" -eq 0 ] && [ "${LANDED_N:-0}" -eq 0 ] && [ "${CLOSED_N:-0}" -eq 0 ]; then
  echo "$NO_TABLE"; exit 0      # only the live count could decide this pass, and it is unknown
fi
if [ "${STARTED:-0}" -eq 0 ] && [ "$LIVE" = 0 ] && [ "${LANDED_N:-0}" -eq 0 ] && [ "${CLOSED_N:-0}" -eq 0 ]; then
  echo "PASS DID NOTHING: nothing landed, nothing closed, nothing started, and no runner is alive."
  echo "                  Repeating this changes nothing. Something upstream is refusing; look at it."
  exit 45
fi

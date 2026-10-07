#!/usr/bin/env python3
"""Drive ONE sub unit from brief to a build that is grader PASS and probe CLEAN, with automatic repair rounds. No human inside a round.
usage (repo root): unit_runner.py <unit> <sub> [max rounds=3]
Why one sub unit at a time per unit: sub units of a unit patch the same files, so builds made in parallel against one base stop applying
the moment a sibling lands (measured 2026-09-20). The brief is therefore built FRESH from the current tree at round 0.
Round: WORKERS_PER_ROUND workers, 5 by default (never waits for a straggler) -> first grader PASS wins -> two adversaries write executable probes -> CLEAN ends the run.
A grader FAIL or a DIRTY probe verdict builds the next round's brief from the machine's own lines plus the best previous build.
Writes <run dir>/STATUS: READY <build.json> (a silent probe no longer blocks: READY-UNPROBED retired 2026-09-27) | EXHAUSTED <why> | WITHHELD <why>. Landing stays with the orchestrator (gates, plan evidence, commit)."""
import atexit, fcntl, glob, json, os, re, subprocess, sys, time, signal
# THE HOLD REACHES THIS ROUTE (review item 1, 2026-09-26): a pause or the owner's HOLD stops it before any model seam
# loads or anything is written; loop_hold.py is the one reader. Its own selftest still runs while held.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loop_hold as _LH  # noqa: E402
_LH.gate(where="unit_runner start")
BIN = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BIN)
import probe_build as P
import brief_fit
import stage_log, fanout_verdict as FV, repair_advisor as RA, self_check as SC, build_plan as BP, worker_mix as WM, ev_gate as EV
import bounded as B  # module scope: the script body below calls it (2026-09-22: bound inside build_models, every runner died at grade)
import plan_store  # the one landed test every loop reader shares: plan_store.sub_landed
import grade_build  # FX-09: the one reader of BROTHER_BRIEF_SCREEN, the switch that swaps RULES' screen sentence below
import mix_advice as MA  # H7.b (REQ-H-CHEAPEST): the advice line's cheapest valid model feeds side_model below; top level and unguarded, so a missing module refuses at startup instead of quietly reading as off
# ROUND CAP, raised from 3 to 5 on 2026-09-21 by two independent measurements agreeing.
# Ours: over 166 runs the rounds-used distribution is mean 2.43, median 3, max 4, with 97 runs at
# exactly 3 and a cliff immediately after. Mean BELOW median is the opposite of a heavy tail, so the
# 39 percent exhaustion rate was largely this cap, not the difficulty of the work: restart theory
# (Luby; Gomes et al) says restarts only help under heavy tails, and we do not have one.
# External: the SWE-agent pass@k curve rises 17.94 to 32.67 percent over six attempts and flattens
# around k=5 to 6, so five is where the gain stops paying. Above that we would buy tokens, not passes.
unit, sub = sys.argv[1:3]; max_rounds = int(sys.argv[3] if len(sys.argv) > 3 else os.environ.get("MAX_ROUNDS", "5"))
ROUND_CAP_LOW_PASS = 3      # H7 (2026-09-24): rounds a sub unit may buy while its grades mostly fail
LOW_PASS_RATE = 0.30        # below this share of PASS grades the cap applies
MIN_GRADES_FOR_CAP = 6      # fewer grades than this say nothing about a rate


def adaptive_rounds(default_rounds, passes, total):
    """The round cap for THIS sub unit from its own grade history. Measured 2026-09-24: run 3 bought 15 rounds of
    D3.7 against a landed code defect at 14.47 USD for 0 landings; the pass@k curve flattens at 5 to 6 attempts
    only when attempts can pass. Under LOW_PASS_RATE over MIN_GRADES_FOR_CAP grades or more, the cap is
    ROUND_CAP_LOW_PASS; a caller's smaller cap always wins; hostile counts read as no history."""
    try:
        passes, total = int(passes), int(total)
    except (TypeError, ValueError):
        return default_rounds
    if total < MIN_GRADES_FOR_CAP or passes < 0 or passes > total:
        return default_rounds
    return min(default_rounds, ROUND_CAP_LOW_PASS) if passes / float(total) < LOW_PASS_RATE else default_rounds


def grade_history(root, sub_id, since=0.0):
    """(passes, total) over every grade text of this sub unit under root (unit-runs/<sub>-HHMMSS/round*/grades/*.txt),
    the verdict being the first line that starts PASS or FAIL. Unreadable pieces count as nothing, never as a pass.
    since: runs last touched at or before it are left out (a run before the latest new fact priced a different problem)."""
    passes = total = 0
    try:
        runs = [d for d in os.listdir(root) if d.startswith(sub_id + "-")]
    except OSError:
        return 0, 0
    for d in runs:
        base = os.path.join(root, d)
        try:
            if since and os.path.getmtime(base) <= since:
                continue
            rounds = [r for r in os.listdir(base) if r.startswith("round")]
        except OSError:   # sbe: allow-silent grade history is a rate for the adaptive round cap; an unreadable folder is left out of the count
            continue
        for r in rounds:
            gdir = os.path.join(base, r, "grades")
            try:
                names = [n for n in os.listdir(gdir) if n.endswith(".txt")]
            except OSError:   # sbe: allow-silent grade history is a rate for the adaptive round cap; an unreadable folder is left out of the count
                continue
            for n in names:
                try:
                    with open(os.path.join(gdir, n), encoding="utf-8", errors="replace") as fh:
                        verdict = next((l for l in fh if l.startswith(("PASS", "FAIL"))), None)
                except OSError:   # sbe: allow-silent grade history is a rate for the adaptive round cap; an unreadable folder is left out of the count
                    continue
                if verdict is None:
                    continue
                total += 1
                passes += verdict.startswith("PASS")
    return passes, total


RUN_NAME_TRIES = 60   # consecutive seconds a run folder name may move forward past taken ones before the runner refuses


def claim_run_dir(root, sub_id, now, tries):
    """Create this run's folder <sub>-HHMMSS under root EXCLUSIVELY and return its path; None when `tries` consecutive
    seconds from `now` are all taken. RR lane C, 2026-09-27: the folder was made with exist_ok, so a later run at the
    same HHMMSS (any later day) reused an old folder and its grade and probe stages read that run's PASS and CLEAN as
    their own (unverified bytes marked READY), and two runners in one second shared a folder and a PID file. The name
    format is kept because ten readers parse it; a clash moves to the next free second instead, and an existing folder
    is never entered. Any mkdir failure other than a taken name propagates: an unknown refuses, it never reuses."""
    for i in range(tries):
        path = os.path.join(root, "%s-%s" % (sub_id, time.strftime("%H%M%S", time.localtime(now + i))))
        try:
            os.mkdir(path)
        except FileExistsError:   # sbe: allow-silent a taken name moves to the next second; running out of tries returns None, which the caller refuses
            continue
        return path
    return None


plan = json.load(open("docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", encoding="utf-8"))
# THE ROUND CAP READS HISTORY SINCE THE LAST NEW FACT (2026-09-27), the same boundary the value gate uses: the spec's last
# change, or now for a restart carrying a proven fact. Lifetime history capped repaired sub units at 3 rounds for failures
# of a spec or a brief that no longer exists.
try: _fact = os.path.getmtime(next((u for u in plan["units"] if u["id"] == unit), {}).get("spec") or "")
except (OSError, TypeError): _fact = 0.0
if os.environ.get("RUNNER_HINT"): _fact = time.time()
_gp, _gt = grade_history(os.path.expanduser("~/.claude/evidence/unit-runs"), sub, since=_fact)
_cap = adaptive_rounds(max_rounds, _gp, _gt)
if _cap != max_rounds:
    print("ROUNDS  capped at %d (was %d): %d of %d grades of %s passed since its last new fact" % (_cap, max_rounds, _gp, _gt, sub)); max_rounds = _cap
spec = next(u["spec"] for u in plan["units"] if u["id"] == unit)
_runs_root = os.path.expanduser("~/.claude/evidence/unit-runs")
# ONE LIVE RUNNER PER SUB UNIT (RR lane C, 2026-09-27): the pool and diag_apply each decide "no runner is alive" from
# their own read and can both start one; two runners of one sub unit then each bought a brief and a round. This lock is
# the one place every starter routes through. It is taken BEFORE any folder exists, so a refused runner leaves nothing
# a reader could mistake for a run, and the kernel releases it when this process ends, however it ends. A lock that
# cannot be taken for any reason refuses: an unknown never starts a second runner.
try:
    os.makedirs(_runs_root, exist_ok=True)
    _lock_fd = os.open(os.path.join(_runs_root, sub + ".lock"), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    run = claim_run_dir(_runs_root, sub, time.time(), RUN_NAME_TRIES)
except OSError as _exc:
    print("REFUSED %s: another runner of this sub unit holds its lock, or the lock or run folder cannot be made (%s: %s); "
          "nothing was started" % (sub, type(_exc).__name__, _exc), flush=True); sys.exit(2)
if run is None:
    print("REFUSED %s: %d consecutive run folder names are taken; no old folder is reused" % (sub, RUN_NAME_TRIES), flush=True); sys.exit(2)
# EVERY CLAUDE CALL THIS RUNNER CAUSES IS TAGGED WITH ITS FOLDER (2026-10-04): claude_ledger.start reads this, so a
# sub unit's native session, its retries and its helper calls are billed to it by identity, never by a time window
# THE TAG CARRIES THE CLAIM TIME (review 2026-10-04): a folder name has no date, so a pruned folder's name reused on a
# later day would inherit the old run's rows; RUN-TAG holds the exact tag the ledger rows carry, read by native_usd
_unit_run_tag = "%s@%d" % (os.path.basename(run), int(time.time()))
try:
    with open(os.path.join(run, "RUN-TAG"), "w", encoding="utf-8") as _fh:
        _fh.write(_unit_run_tag + "\n")
except OSError as _exc:   # no tag file means spend this run could never be attributed: refuse before any call is made
    print("REFUSED %s: the run tag could not be written in %s (%s: %s); nothing was started" % (sub, run, type(_exc).__name__, _exc), flush=True); sys.exit(2)
os.environ["BROTHER_UNIT_RUN"] = _unit_run_tag
# THIS RUNNER IS FINDABLE FROM ITS OWN FOLDER (R4, 2026-09-22): runner_pool counted live runners by grepping the
# machine wide process table, so runners of any other checkout counted against this pool and its guard suite
# could never pass while any runner was alive. A PID beside the STATUS names exactly this run's process.
try:
    with open(os.path.join(run, "PID"), "w") as _f: _f.write("%d\n" % os.getpid())
except OSError as _exc:
    # A folder with neither PID nor STATUS reads RUNNING to the pool and DEAD to salvage for ever: take it back.
    for _gone in (os.path.join(run, "PID"), run):
        try: (os.rmdir if _gone == run else os.remove)(_gone)
        except OSError: pass   # sbe: allow-silent best effort removal of this run's own empty folder; the refusal below is the result
    print("REFUSED %s: the PID file cannot be written (%s)" % (sub, _exc), flush=True); sys.exit(2)
# The private-name list is NOT read here. It was, into a module level `names` that nothing ever used, and the
# single reader of that list is grade_build.private_hits, reached through private() below. A dead read still
# costs what a live one costs: it ran at IMPORT time with no guard, so this module could not even be imported
# on a machine whose HOME lacks the file, and the runner's behaviour depended on the developer's home directory
# rather than on the tree. private_hits owns the read, and it counts an unreadable list as a hit, which is the
# direction an unknown must fail in.


def write_status(path, text):
    """Write text to path atomically: path + ".tmp" then os.replace; a reader sees the old text or the new one, never a torn one. Only os and builtins are used, so this body can be lifted by a test. Unknown input REFUSES: a path that is not a non-empty str, or a text that is not a str, raises ValueError naming it; an OSError from the write, the flush, the fsync or the replace is re-raised as OSError naming the path and the cause, and the stale .tmp is left for the next write to overwrite."""
    if not isinstance(path, str) or not path:
        raise ValueError("write_status path is not a non-empty str: %r" % (path,))
    if not isinstance(text, str):
        raise ValueError("write_status text is not a str: %r" % (text,))
    tmp = path + ".tmp"
    # ONE LOCK FOR EVERY STATUS WRITER (RR lane C, 2026-09-27): a run's STATUS has five writers, and salvage, the pool's
    # reconcile and the checker each decide on the word they read; the write waits for the runs root's lock they take.
    try:
        import fcntl   # here, not at the top: tests lift this body with only os, re and sys in scope
        with open(os.path.join(os.path.dirname(os.path.dirname(path)), ".status.lock"), "a") as lock:   # outside every run folder, so no run's mtime moves
            fcntl.flock(lock, fcntl.LOCK_EX)   # released when the file closes
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
    except OSError as exc:
        raise OSError("write_status failed for %r: %s" % (path, exc)) from exc


def hold_gate(where):
    """Before each paid stage: a pause or HOLD that arrived mid run stops the runner here, with PAUSED as its status
    word (not parked: the pool re-seats the sub unit on resume) and every build, grade and probe already on disk kept."""
    why = _LH.reason()
    if why:
        status("PAUSED before %s: %s; outputs already on disk are kept" % (where, why)); print("HELD at %s: %s" % (where, why), flush=True); sys.exit(_LH.HELD_EXIT)


def status(text):
    write_status(os.path.join(run, "STATUS"), text + "\n"); print(text, flush=True)


def private(text):
    return P.G.private_hits(text)


# EVERY VERDICT THE PROBE STAGE CAN PRODUCE, and what this runner does with each. This is DATA on purpose.
# A chain of ifs has a last branch, and a word nobody has thought of yet falls into it; here a word that is not
# a KEY is not a verdict this runner knows, and the lookup's default answers for it. There is exactly one
# admitting word. Everything else, known or unknown, does not admit.
#
# ADMIT   the build may be called READY.
# REFUSE  the build is rejected: a repair round is built from the findings, and the run is a rejection.
# UNKNOWN nothing was learned. It does NOT admit, and it is NOT recorded as a rejection: counting an unknown
#         as a failure is the same defect wearing the other coat, and this estate was bitten by exactly that
#         in its prediction ledger on 2026-09-21.
PROBE_VERDICT = {"CLEAN": "ADMIT", "DIRTY": "REFUSE", "NO-DATA": "UNKNOWN"}
UNKNOWN_VERDICT = "UNKNOWN"  # the default, named so a mutation to it is visible in a diff


def probe_outcome(verdict, finds):
    """ADMIT, REFUSE or UNKNOWN for what the probe stage left on disk. The one decision between a red team
    finding and a READY build, and a FUNCTION so a test can drive it with values.

    Measured 2026-09-21: the gate that guarded this walked the file's syntax tree and asserted the READY exit
    sat inside an `if` whose unparsed test mentioned "finds" and "verdict". That is a property of the TEXT.
    Replacing `finds = sorted(set(finds))` one line upstream with `finds = []` left the gate printing PASS
    twice at exit 0 while a DIRTY verdict carrying a CRASH finding took the READY exit.

    Measured 2026-09-22, the defect this rewrite closes: the predecessor asked only whether the verdict string
    was empty or the literal "NO-DATA", so probe_admits("DIRTY", []) returned True, and so did "BANANA".
    An unrecognised word and a hostile word both admitted.

    FOUR GUARDS, DELIBERATELY INDEPENDENT so a fixture can trip exactly one and each is therefore proved.
    A fixture that trips two proves neither, which was measured on this module's siblings the same night."""
    # (1) A verdict that is not text cannot be normalised. FAIL DIRECTION: unknown, never the safe case. This
    #     also keeps .strip() from raising on a list or a None the caller read out of a broken log.
    if not isinstance(verdict, str):
        return UNKNOWN_VERDICT
    # (2) Findings we cannot read are findings we cannot count. FAIL DIRECTION: unknown, NOT a rejection, since
    #     an unreadable finding set is not evidence that anything was actually wrong.
    if not isinstance(finds, (list, tuple, set)):
        return UNKNOWN_VERDICT
    # (3) EVIDENCE OUTRANKS THE WORD. A CRASH or WRONG-ACCEPT? line was observed by an executed probe, so it
    #     refuses whatever the summary verdict says, including CLEAN.
    if len(finds) > 0:
        # ONLY AN EXECUTED DEFECT REFUSES (owner, 2026-09-27: "we want gates that land stuff, not gates that just block
        # everything"). A CRASH line is a raw exception that reached the caller: proven. A WRONG-ACCEPT? line is the red
        # team's READING of the specification, which the probe log itself says to check: advisory, kept beside the build.
        # Any other shape is unknown content and still refuses. Measured: 91 builds that passed the grader died here.
        if all(isinstance(f, str) and f.startswith("WRONG-ACCEPT?") for f in finds):
            return "ADMIT"
        return "REFUSE"
    # (4) The table. FAIL DIRECTION: any word not in it, including "" and every word coined after today, is an
    #     unknown, and an unknown never proceeds.
    return PROBE_VERDICT.get(verdict.strip(), UNKNOWN_VERDICT)


# H7.b (REQ-H-CHEAPEST): the planner and the repair advisor run on the owner's own model when the owner named one,
# else on the advice line's cheapest VALID model, else on nothing at all. The two constants below use only `re` and
# literals: scripts/test_unit_runner_probe_gate.py lifts this module's functions and its ALL CAPS constants out of the
# parsed source and executes them with only os, re and sys in scope, so a constant that read anything else would
# break every case in that file.
SIDE_ROLES = {"plan": "BROTHER_BUILD_PLAN_MODEL", "repair": "BROTHER_REPAIR_ADVISOR_MODEL"}
MODEL_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}\Z")


def side_model(role, advice, env_value):
    """The model the plan or the repair side call runs on, or the word off when no call is wanted. Pure and total:
    it reads no environment, no file and no clock, and every hostile input ends in off rather than in a raise or in
    a model nobody chose. THE PRECEDENCE IS THE OWNER, THEN THE MEASURED ADVICE, THEN OFF. An owner value that is not
    a bare model name is refused rather than passed on, and it NEVER falls through to the advice: a typo must be
    visible, not silently replaced by a different model, which is the same defect the pin check in bridge_models
    exists for. The word off is a legitimate owner value and comes back verbatim, which is how the owner turns one
    side off without the other."""
    if not isinstance(role, str) or role not in SIDE_ROLES:
        return "off"  # the isinstance check comes first, so an unhashable role can never raise on the lookup
    if env_value is not None and not isinstance(env_value, str):
        return "off"  # a bool, a number, a list, bytes or NaN is not a model name
    env = env_value.strip() if isinstance(env_value, str) else ""
    if env:
        return env if MODEL_NAME.match(env) else "off"
    if not isinstance(advice, dict):
        return "off"
    cheap = advice.get("cheapest")
    if isinstance(cheap, str) and cheap != "off" and MODEL_NAME.match(cheap):
        return cheap
    return "off"


def side_advice():
    """A dict whose cheapest key holds a model name or None, read from the newest runs that hold a decision journal,
    or None when there is no such run at all. The journal read costs one subprocess per run and it is paid ONLY when
    at least one side is unset, so an owner who named both models never waits for it. A run that cannot be read is
    skipped by name on stderr inside mix_advice.gather, and the advice then names no valid model, which reads as off:
    fail closed, never a guess."""
    runs = MA.newest_runs(MA.RUNS, 3)
    if not runs:
        return None
    return {"cheapest": MA.cheapest_valid(MA.gather(runs))}


def repair_base(best, rejected, isfile=os.path.isfile):
    """The build the next repair brief starts from after a grader NO-PASS: the best build that passed the grader THIS
    RUN when it still exists, else the first rejected one, else nothing. (2026-09-22: starting from a rejected build made
    the sequence PASS+DIRTY, NO-PASS, PASS+DIRTY, NO-PASS run to exhaustion on 8 of 11 sub units.)"""
    if best and isfile(best): return best, True
    return (rejected[0] if rejected else ""), False


def judged(wd, sub=None):
    """True only when EVERY paid build of this round (<wd>/out/<name>-build.json, or <name>.json) has its OWN completed
    grade: grades/<name>.txt (grade_one.sh renames .part only after the grader returned) whose last exit= line is exit=0
    or exit=1. One completed FAIL beside a build whose grade timed out, faulted (exit 3), never finished (.part) or is
    missing is NOT a judged round (owner 2026-10-03: that still bought a repair round and lost the unjudged build). A
    grade file naming no build of this round is ignored. No build, or an unreadable grade, is not judged."""
    # the same set grade_lane.sh grades (review 2026-10-03 finding 5): out/<sub>-r*-build.json when the sub is named
    builds = glob.glob(os.path.join(wd, "out", (sub + "-r*-build.json") if sub else "*.json"))
    if not builds:
        return False
    for b in builds:
        name = os.path.basename(b)[:-len(".json")]
        name = name[:-len("-build")] if name.endswith("-build") else name
        try:
            with open(os.path.join(wd, "grades", name + ".txt"), encoding="utf-8", errors="replace") as fh:
                codes = re.findall(r"^exit=(\d+)\s*$", fh.read(), re.M)
        except OSError:
            return False
        if not codes or codes[-1] not in ("0", "1"):
            return False
    return True


def graded_pass(rc, log_text, sub_id):
    """The build name <sub>-rN the grade stage passed, or None. Decided on grade_lane.sh's EXIT CODE and the LAST verdict
    line of its log only (RR lane C, 2026-09-27, the rule lane F4 gave grade_lane.sh): this searched the log for
    "PASS <name>" anywhere and ignored the exit code, so a PASS line from a grade that exited 1, a PASS under a later
    NO-PASS, and a PASS naming a path outside this round all reached the probe stage. A verdict line is grade_lane.sh's
    "<lane> PASS <name>" or "<lane> NO-PASS", or a stage FAIL line bounded.py appends; the last one must be exactly this
    sub unit's PASS of one of its own builds. Pure: only re is used, so tests lift it with os, re and sys in scope."""
    if rc != 0 or not isinstance(log_text, str) or not isinstance(sub_id, str):
        return None
    verdicts = [l.strip() for l in log_text.splitlines() if re.match(r"^\S+ (PASS \S+|NO-PASS)\s*$|^FAIL\b", l.strip())]
    m = re.fullmatch(re.escape(sub_id) + r" PASS (" + re.escape(sub_id) + r"-r\d+)", verdicts[-1]) if verdicts else None
    return m.group(1) if m else None


def probe_admits(verdict, finds):
    """True only for the one admitting outcome. Kept as its own name because scripts/test_unit_runner_probe_gate.py
    drives this signature and asserts the READY exit sits inside a guard bound to a call of it."""
    return probe_outcome(verdict, finds) == "ADMIT"


def previous_reason(sub_id, runs_dir=None, exclude=None, listdir=None, read=None, mtime=None):
    """Why the PREVIOUS run of this sub unit was rejected, in its own words, or "" when there is nothing to learn.

    THE READER THAT DID NOT EXIST, measured 2026-09-21. When land_batch.py quarantines a build it writes the
    reason into that run's STATUS file, in its own comment, "so the next runner knows what to fix". Nothing read
    it. This runner took its only hint from RUNNER_HINT in the ENVIRONMENT, and the single writer of that
    variable is scripts/loop/diag_apply.py, which is not in the pass ladder: brother_pass runs probe_round,
    diag_round and runner_pool, and runner_pool starts this script with a plain environment. So a quarantined
    sub unit was rebuilt from a brief identical to the one that produced the build the gates had just dropped,
    and the most informative fact the loop owns about this sub unit was written to a file nobody opened.

    Only QUARANTINE, EXHAUSTED and WITHHELD carry a lesson: READY and READY-UNPROBED describe a build that was
    not rejected, so they teach nothing and return "". Newest is decided by MODIFICATION TIME, never by the
    HHMMSS folder stamp, which wraps at midnight and silently reorders a night's work (the same defect already
    fixed in pass_digest, runner_pool and loop_done). This run's own folder is excluded, or a runner would read
    the status it just wrote. Anything unreadable is "" and never raises: a missing lesson degrades the brief,
    it must never stop a build."""
    listdir = listdir or (lambda d: sorted(os.listdir(d)))
    read = read or (lambda q: open(q, encoding="utf-8").read())
    mtime = mtime or os.path.getmtime
    runs_dir = os.path.dirname(run) if runs_dir is None else runs_dir
    mine = (run if exclude is None else exclude).rstrip("/")
    try:
        entries = listdir(runs_dir)
    except OSError:
        return ""
    newest = (None, "")
    for name in entries:
        path = os.path.join(runs_dir, name)
        if path.rstrip("/") == mine or re.sub(r"-\d{6}$", "", name) != sub_id:
            continue
        try:
            text, when = read(os.path.join(path, "STATUS")).strip(), mtime(path)
        except OSError:  # sbe: allow-silent a sibling run with no STATUS file yet is still running, so it cannot be the newest finished one
            continue
        if newest[0] is None or when >= newest[0]:
            newest = (when, text)
    word = newest[1].split()[:1]
    return newest[1][:900] if word and word[0] in ("QUARANTINE", "EXHAUSTED", "WITHHELD") else ""


# THE CODE ROOT (U3, B5-08): the fan out and the failure ledger are CODE and run from the tree the launcher froze
# (BROTHER_CODE_ROOT), never from the launch worktree this runner works in; the data they read and write stays where it
# was. A proof phase with no code root starts nothing (model_router.code_root refuses it).
import model_router as _MR0
try:
    code_root = _MR0.code_root()
except _MR0.Refused as _exc:
    status("BLOCKED no code root to run the fan out from: %s" % _exc); sys.exit(2)
base = os.path.join(run, "brief-base.md")
r = subprocess.run([sys.executable, os.path.join(BIN, "build_brief.py"), unit, sub, spec, base], capture_output=True, text=True, timeout=300)
if r.returncode != 0 or not os.path.isfile(base):
    status("WITHHELD brief could not be built: %s" % (r.stderr or r.stdout).strip()[-160:]); sys.exit(2)
# THE BRIEF GATE (owner 2026-09-24: clear briefs per unit and from the epic, ENFORCED): a brief that does not show the
# files its section names or the modules its named test imports, is over the dispatcher budget, carries no runnable
# done_check, or does not state the unit's WBS row is refused HERE, before eight workers are paid to guess against it.
# A refusal parks the sub unit WITHHELD with the property named as its fact; a changed brief_check readmits it.
chk = subprocess.run([sys.executable, os.path.join(BIN, "brief_check.py"), unit, sub, spec, base], capture_output=True, text=True, timeout=300)
# A NATIVE SESSION READS THE TREE ITSELF (2026-10-02, the first native run: FX-13.4 parked WITHHELD on "B1 NAMED SHOWN"):
# B1 to B3 judge the blind paste (files shown, imports shown, its size), so for a run that is native by intent (the switch
# on and the pinned worker on the claude transport) a refusal on those alone is waived and said; B4, B5 and any NO-DATA
# still park the sub unit. A round that would then run blind on the waived brief is refused at the round (below).
import loop_switches as _SW0


def native_intent(env=None):
    """True when this run builds with Claude native sessions: loop_switches.native_on and BROTHER_PIN_MODEL a registry
    model on the claude transport (an unpinned build chain is bridge models only). An unreadable registry is False."""
    e = os.environ if env is None else env
    pin = e.get("BROTHER_PIN_MODEL")
    if not _SW0.native_on(e) or not pin:
        return False
    try:
        return (_MR0.registry().get(pin) or {}).get("transport") == "claude"
    except Exception:   # sbe: allow-silent an unreadable registry is not native: the brief gate then refuses as before
        return False


_gate_waived = ""
if chk.returncode != 0:
    why = " | ".join(l for l in chk.stdout.splitlines() if "REFUSED" in l or "NO-DATA" in l) or (chk.stderr or "").strip()[-200:]
    _on = next((l.split("REFUSED on ", 1)[1] for l in chk.stdout.splitlines() if l.startswith("BRIEF ") and "REFUSED on " in l), "")
    _props = [x.strip() for x in _on.split(",") if x.strip()]
    if chk.returncode == 1 and _props and all(x.split()[0] in ("B1", "B2", "B3") for x in _props) and native_intent():
        _gate_waived = ", ".join(_props)
        print("NATIVE  brief gate %s waived: a native session reads the tree itself (%s)" % (_gate_waived, why[:300]), flush=True)
    else:
        status("WITHHELD brief refused by brief_check (exit %d): %s" % (chk.returncode, why[:700])); sys.exit(2)
brief = open(base, encoding="utf-8").read()
# The brief learns from the LAST attempt at this sub unit, from two sources that rarely both exist: a
# diagnostician's verified fact in RUNNER_HINT, and the machine's own rejection reason left in the
# previous run's STATUS by land_batch. Before 2026-09-21 only the first was read and nothing in the pass
# ladder ever set it, so every repeat attempt was byte identical to the one that had just been dropped.
# The diagnostician's proven fact arrives as the unit's note (plan E step 2b), never as a restart: it is brief input and
# does not reset this sub unit's round history. RUNNER_HINT stays for a fact a human supplies by hand.
import diag_notes as _DN
hint = "\n".join(x for x in (os.environ.get("RUNNER_HINT", ""), _DN.note_for(unit, sub), previous_reason(sub)) if x)
# JEV INSIDE THE LOOP (owner 2026-09-22): its typed checklist on the spec becomes advice lines in the brief, one per
# flagged question. Advice only: Jev never decides whether the build runs, and a call that fails adds nothing.
try:
    _jev = subprocess.run([sys.executable, "-B", os.path.join(BIN, "jev_brief.py"), spec, os.path.join(run, "jev-spec.json")],
                          capture_output=True, text=True, timeout=330)
    if _jev.returncode == 0 and _jev.stdout.strip(): hint = "\n".join(x for x in (hint, _jev.stdout.strip()) if x)
    print("JEV     %s" % ("%d flag(s)" % _jev.stdout.count("\n- ") if _jev.returncode == 0 else "NO-DATA: " + (_jev.stderr.strip() or "no answer")[:100]))
except (OSError, subprocess.SubprocessError):
    print("JEV     NO-DATA: the checklist could not run")
# What this estate keeps getting wrong, most costly first, straight from the failure ledger. A failure that
# teaches nothing is paid for twice: seven landings died on ONE class before a human noticed and wrote the rule.
try:
    try:
        import probe_build as _PB; _probe_lessons = _PB.lessons()
    except Exception as _exc: _probe_lessons = ""; print("LESSONS NO-DATA: %s" % type(_exc).__name__, flush=True)   # sbe: allow-silent a lesson block is evidence for the brief, never a gate
    _led = subprocess.run([sys.executable, os.path.join(code_root, "scripts", "failure_ledger.py"), "rules"],
                          capture_output=True, text=True, timeout=60).stdout.strip()
    LESSONS = ("\nWHAT THIS ESTATE KEEPS GETTING WRONG, from its own failure ledger. Each line has already cost "
               "real builds, so treat them as hard rules:\n" + _led + "\n") if _led and "NO-DATA" not in _led else ""
    _led = (("\n" + _probe_lessons) if _probe_lessons else "") + _led
except (OSError, subprocess.SubprocessError):
    LESSONS = ""  # what earlier exhausted runs taught: a fact about the real tree the brief did not show


GRADE_LINE_MAX = 1700   # grade_build.failure_tail is 1500 chars plus its verdict prefix: the assertion must survive into the re-brief


def teach(evidence, ok=False, stage=""):
    """Write this lane's own outcome back to the ledger, so the lessons above can improve on their own.

    THE OPEN LOOP THIS CLOSES, measured 2026-09-21: the only call anywhere to failure_ledger.py was the `rules`
    read directly above. Nothing in the loop ever called `record`, so all nineteen rows in the ledger had been
    typed by a human and a brief's lessons could never learn from a lane. A ledger that is read and never
    written is a checklist, not a feedback loop, and it goes stale the day its author stops paying attention.

    The class is not passed in: `auto` hands the evidence to the ledger's own classifier, so a lane never has to
    know the taxonomy and a new signal added centrally reaches every lane at once. Successes are recorded too,
    because a failure count with no attempt count has no denominator. Never raises: a bookkeeping failure must
    not end a build that otherwise worked."""
    try:
        _r = subprocess.run([sys.executable, os.path.join(code_root, "scripts", "failure_ledger.py"), "record",
                        "ok:build" if ok else ("auto:" + stage if stage else "auto"), (evidence or "")[:400], "--unit", unit, "--sub", sub],
                       capture_output=True, text=True, timeout=60)
        if _r.returncode != 0:   # a ledger that cannot record is said, never swallowed: last night no lesson could ever improve because nothing wrote back
            print("LEDGER  NO-DATA: failure_ledger record exit %d: %s" % (_r.returncode, (_r.stderr or _r.stdout).strip().splitlines()[-1:] or ["no output"]), flush=True)
    except (OSError, subprocess.SubprocessError):
        pass
# H7.b (REQ-H-CHEAPEST): the two side models are decided ONCE, here, before any call site, and every call site below
# is gated on its own name. off means no call is made and a NOTE says which of the two reasons it was. Precedence:
# the owner's own value, else the advice line's cheapest valid model, else off. The journal is read only when at least
# one side is unset, so an owner who set both pays nothing for it.
_plan_env = os.environ.get(SIDE_ROLES["plan"])
_repair_env = os.environ.get(SIDE_ROLES["repair"])
_plan_owner = bool(_plan_env) and bool(_plan_env.strip())
_repair_owner = bool(_repair_env) and bool(_repair_env.strip())
# FX-11.6 (REQ-FX11-20): with BROTHER_BREAKER on, the roles file's planner and repair_advisor come before the advice
# line; off names nothing and every line below is today's. side_model itself is unchanged and stays pure.
import loop_roles as _LR  # noqa: E402  this directory's copy (BIN is on sys.path)
try:
    _role_seats, _role_why = _LR.side_seat_models(dict(os.environ))
except (ValueError, OSError) as _exc:
    _role_seats, _role_why = {"plan": None, "repair": None}, "%s: %s" % (type(_exc).__name__, str(_exc)[:200])
_side_advice_line = side_advice() if not (_plan_owner and _repair_owner) else None
_plan_model = side_model("plan", {"cheapest": _role_seats["plan"]} if _role_seats["plan"] else _side_advice_line, _plan_env)
_repair_model = side_model("repair", {"cheapest": _role_seats["repair"]} if _role_seats["repair"] else _side_advice_line, _repair_env)
for _side, _owner, _env, _model in (("plan", _plan_owner, _plan_env, _plan_model),
                                    ("repair", _repair_owner, _repair_env, _repair_model)):
    if _model != "off":
        if not _owner:
            os.environ.setdefault(SIDE_ROLES[_side], _model)
        continue
    if _owner:
        print("NOTE    %s side OFF: %s=%r is not a bare model name (letters, digits, . _ : / -, at most 64 characters, no spaces)" % (_side, SIDE_ROLES[_side], _env), flush=True)
    elif _role_why:
        print("NOTE    %s side OFF: the roles file names no %s (%s)" % (_side, "planner" if _side == "plan" else "repair_advisor", _role_why), flush=True)
    else:
        print("NOTE    %s side OFF: the advice line names no valid cheapest model" % _side, flush=True)
# THE PLAN BEFORE THE BUILD (owner 2026-09-22, piece 1): a Claude model writes the plan from the spec and the real files;
# it heads the round 0 brief so the worker executes instead of guessing. Advisory; None adds nothing.
if BP.enabled(sub) and _plan_model != "off":
    _plan = BP.plan(brief[:30000], brief[30000:110000], runners=", ".join(next((u.get("command_runners") or []) for u in plan["units"] if u["id"] == unit)) or "none for this unit")
    if _plan: brief = _plan + "\n" + brief; print("PLAN    %s" % _plan.splitlines()[0][:80], flush=True)
    else: print("PLAN    NO-DATA: no complete plan; the worker builds from the spec alone", flush=True)
if hint:
    brief += "\n\nLEARNED FROM REJECTED BUILDS OF THIS SUB UNIT, read before writing anything:\n" + hint + "\n"
def bridge_models(n):
    """The best bridge models for a build, best first, from the data driven registry.

    THE REGISTRY REFUSES, IT NEVER DEFAULTS. An earlier draft of this function ended by returning a
    hardcoded model name when the registry could not be read, which is the defect this estate names
    in its own laws: an unestablished value refuses. A build stage quietly reverting to a name
    somebody typed is a routing layer that has become decoration, and nobody would learn it had
    happened. An unreadable registry stops the round instead, the same direction every other
    unknown in this loop takes.

    The DEFAULT seats are bridge models: the fan out reserves OpenRouter money for them and that is the
    ledger the burn guard reads. A PINNED model may be on any transport the fan out speaks (bridge, claude,
    codex through or_fanout._run_off_bridge): the owner ruled 2026-09-22 that codex is a peer of the other
    models inside the loop and is used when a user names it. The privacy gate still runs at the wire."""
    pin = os.environ.get("BROTHER_PIN_MODEL")
    sys.path.insert(0, os.path.join(BIN))
    import model_router as R
    reg = R.registry()
    try:
        order = R.chain("build", R.PUBLIC, pin)
    except R.Refused as exc:   # a pin outside the transport allowlist, or a pin the registry refuses: named, never a traceback
        status("BLOCKED the build chain is refused: %s" % exc)
        sys.exit(2)
    bridge = [m for m in order if reg[m]["transport"] == "bridge" or m == pin]
    # A PIN THAT CANNOT BE HONOURED IS REFUSED, NEVER IGNORED. Measured 2026-09-21: pinning a first
    # party model here returned the bridge list unchanged, so the operator asked for one model,
    # got two others, and nothing said so. Silently doing something else is how an operator stops
    # trusting the tool, and it is the failure this router exists to remove, not to reproduce.
    if pin and (not bridge or bridge[0] != pin):
        status("BLOCKED pinned model %s cannot run this stage: it is not eligible for a public build (%s)"
               % (pin, reg.get(pin, {}).get("transport", "unknown")))
        sys.exit(2)
    if not bridge:
        status("BLOCKED no bridge model is eligible for a build")
        sys.exit(2)
    # A PIN IS EXCLUSIVE, NOT A PREFERENCE. Measured 2026-09-22 02:36: with BROTHER_PIN_MODEL=deepseek every
    # round0 still carried ['deepseek', 'muse'], because the pin only ordered the list and the round robin below
    # filled the other seats from it. An operator who names one model gets that model in every seat.
    if pin:
        bridge = [pin]
    # Two models, not five copies of one. Independent drafts from DIFFERENT models are what best
    # of N buys; five samples of one model are five samples of one model's blind spots.
    return bridge[:max(1, min(n, 2))]


# THE TWO REFUSALS THAT RECURRED ALL DAY (2026-09-22, grader histogram of the 21:2x reseats: 13 safety screen refusals for
# subprocess, urllib and run() outside the allow list; 5 "tests pass without the code") are named here, with the unit's own
# allow list from the plan, so a worker reads them before it writes a line.
_runners = ", ".join(next((u.get("command_runners") or []) for u in plan["units"] if u["id"] == unit))  # filled into RULES at its one use site: RULES stays a constant the probe gate test can lift
# FX-09: the hand typed screen sentence below is today's default (BROTHER_BRIEF_SCREEN unset or off) and it has drifted
# from the grader (it forbids shutil, which the grader allows). With the switch on, the brief carries THE GRADER'S SCREEN,
# generated from grade_build.py, and the sentence steps aside for a pointer to it. RULES_REST is unchanged either way.
SCREEN_HAND = ("\nTHE SAFETY SCREEN (the grader refuses the whole build): no file you write imports subprocess, urllib, socket, http, requests or shutil, and none calls run(), getattr() or exec() with a dynamic name, EXCEPT the paths this unit's plan names as command runners ({RUNNERS}) and there only with an argv whose EVERY element is a string constant (a computed path or value goes through cwd= or env=, never into argv; see RULE 10). A test file never imports subprocess. Your tests must FAIL without your code and PASS with it: a test that passes on the unchanged tree is refused.\n")
SCREEN_POINTER = ("\nTHE SAFETY SCREEN: the block THE GRADER'S SCREEN in the brief above is generated from the grader and is the whole rule; where anything else you read disagrees with it, it wins. This unit's plan names these command runners: {RUNNERS}. Your tests must FAIL without your code and PASS with it: a test that passes on the unchanged tree is refused.\n")
RULES_REST = ("\nRules the machines enforce: every suite also runs in an EXPORT copy of the repository with an empty HOME and WITHOUT docs/plan, data files outside the code, or any private file, so a test builds its own fixture in a temp folder; a test that must read a live repository document is decorated with unittest.skipUnless(os.path.isfile(PATH), reason) and an existing suite you touch keeps that property; the done_check suite is RED without your code and GREEN with it, on Python 3.9 AND 3.13; every EXISTING suite of a module you edit stays green; "
         "every mutation you name makes a NAMED test fail; hostile input (wrong type, None, NaN, a bool where a number belongs, bytes that are not utf-8, a directory or a missing file where a file belongs, an unhashable key, "
         "a record altered after it was parsed) is REFUSED with the module's own deliberate error or refusal value, never a raw interpreter exception; files are read as BYTES; one validation where all callers route through. "
         "Return ONE complete, self contained JSON build applying to the tree you were shown.\n")
RULES = SCREEN_HAND + RULES_REST            # constants only, so the probe gate test can still lift RULES
RULES_SCREEN = SCREEN_POINTER + RULES_REST  # the switch picks one of the two at the one use site below
try:
    _screen_on = grade_build.brief_screen_mode() == "on"
except ValueError as _exc:   # unreachable in practice: build_brief already exited 2 on the same value
    status("WITHHELD %s" % _exc); sys.exit(2)
# A STRAGGLER IS WAITED FOR, NEVER KILLED WHILE IT HOLDS A CALL (U5, objection 9). The cut grades what is on disk; the fan
# out keeps running and every call it started ends with its own terminal row. A fan out's calls end within twice its
# call timeout (a slot wait of up to one timeout, then the call itself: openrouter_dispatch.dispatch passes the same
# timeout to acquire_slot and to the bridge), so it is waited for up to that plus FANOUT_GRACE_S, then sent TERM (or_ask
# exits, dispatch writes ABANDONED, a terminal row), then KILL after TERM_GRACE_S. The wait runs when the runner exits.
# With BROTHER_ONE_DEADLINE=on (FX-10) the fan out's own deadline is twice the call timeout plus a settle margin
# (WM.deadlines "wave", passed as --deadline), the reaper fires after it, and a late answer is recorded in results.json.
FANOUT_GRACE_S = 60
#: THE GRADE STAGE'S LIMIT, seconds (2026-10-05, owner ruling proofs-unlandable.json option A). The proof pair carries no
#: GRADE_TIMEOUT (launch-env.sh and proof_pair.sh set none), so THIS default is the one the frozen pair runs. Measured alone
#: that day at load 3 to 4: one run of PR1.c's suite (test_precut_review.py, 91 tests of git fixtures and judge runs) took
#: 244 s (one green run; each grade stage holding it took 236 to 314 s), and a passing PR1.c grade runs it about nine times
#: (red, green, the 3.9 leg, six mutations), about 2450 s (an estimate from those stages, the 3.9 leg was not run);
#: 1800 cut both MG1.e and PR1.c. 3600 is that need plus about 45 percent for load. A failed green no longer pays for
#: its mutations (grade_build), so only a grade that can still pass spends this long.
GRADE_TIMEOUT_S = 3600
TERM_GRACE_S = 10
_fanouts = []


def reap_fanouts():
    """Wait out every fan out this runner started, each up to its own deadline, then TERM, then KILL."""
    for proc, deadline in _fanouts:
        while proc.poll() is None and time.time() < deadline:
            time.sleep(1)
        if proc.poll() is None:
            try: os.killpg(proc.pid, signal.SIGTERM)
            except OSError: pass
            end = time.time() + TERM_GRACE_S
            while proc.poll() is None and time.time() < end:
                time.sleep(0.5)
        if proc.poll() is None:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except OSError: pass
            proc.wait()


atexit.register(reap_fanouts)
note, best = "", None
native_note = ""   # the repair lines a native session reads: the note without its pasted build (native_worker starts from base)
# YIELD IS UNITS CLOSED (owner 2026-09-22 23:2x): the expected value gate values the LAST open sub unit of a unit three times
# a plain one, so rounds are spent where a unit closes and stop where they only add a sub unit at a poor pass rate.
_unit_rec = next((u for u in plan["units"] if u["id"] == unit), {})
_open_subs = [x for x in (_unit_rec.get("sub_units") or []) if not plan_store.sub_landed(x, _unit_rec.get("evidence") or "")]   # the one landed test (X3 finding 5)
_value = EV.landing_value(len(_open_subs))   # FX-13.4: the one landing value rule, shared with the plan lint
for rnd in range(max_rounds):
    # P(pass) starts from this sub unit's OWN record across runs (fix 5, 2026-09-22), never from a flat prior alone
    try: _since = os.path.getmtime(_unit_rec.get("spec") or "")
    except (OSError, TypeError): _since = 0.0
    # A PROVEN FACT IS NEW INFORMATION, LIKE A NEW SPEC (2026-09-27 17:4x): history before it cannot price it. Counting the
    # rounds that failed without it refused every hinted restart at round 0 (R4.2 restarted five times, 0 rounds each),
    # so every fact the diagnostician proved was thrown away. A hinted run is priced from its own rounds only.
    if os.environ.get("RUNNER_HINT"): _since = max(_since, os.path.getmtime(run) if os.path.isdir(run) else time.time())
    _hr, _hp = EV.history(sub, since=_since, exclude_run=os.path.basename(run))
    # PER ARM COST (owner 2026-09-23 07:5x): a round's cost is the configured mix priced per arm (BROTHER_ARM_COST, USD per build,
    # the Claude arm priced as subscription quota), so the EV gate sees what the Sonnet arm costs instead of one constant.
    # FX-13.4: the rule lives in ev_gate.round_cost_from_env, shared with the plan lint; None on an unreadable figure.
    _round_cost = EV.round_cost_from_env()
    _go, _ev = EV.should_continue(rnd + _hr, (1 if best else 0) + _hp, round_cost=_round_cost, value=_value)
    if not _go:
        teach("EXHAUSTED by the expected value gate after %d rounds: %s" % (rnd, _ev)); status("EXHAUSTED by the expected value gate after %d rounds: %s" % (rnd, _ev)); sys.exit(1)
    print("EV      round %d: %s" % (rnd, _ev), flush=True)
    wd = os.path.join(run, "round%d" % rnd)
    for d in ("prompts", "out", "fanout"):
        os.makedirs(os.path.join(wd, d), exist_ok=True)
    # The trim order is the point and it is tested in brief_fit.py: the repair NOTE carries the only
    # lines that differ between round N and round N+1, so the static text yields to it, lessons first.
    text = brief_fit.fit(brief, (RULES_SCREEN if _screen_on else RULES).replace("{RUNNERS}", _runners or "none for this unit"), LESSONS, note)
    if private(text) or len(text.encode()) > 199500:
        status("WITHHELD round %d brief: private term or %d bytes" % (rnd, len(text.encode()))); sys.exit(2)
    pf = os.path.join(wd, "prompts", sub + ".md"); open(pf, "w", encoding="utf-8").write(text)
    stage_log.enter("model", sub, unit)
    # THE MODEL IS CHOSEN, NOT TYPED IN. This read a single hardcoded name until 2026-09-21, so
    # "best available" meant "whatever somebody wrote here", a model going down stopped the stage,
    # and no new model could be used without a code change.
    #
    # Sensitivity is PUBLIC and that is ENFORCED upstream, not assumed: the private(text) check
    # above WITHHOLDS any brief carrying a private term before it can reach this line. The router
    # checks again at the wire, because one gate is a policy and two gates are a control.
    n_workers = int(os.environ.get("WORKERS_PER_ROUND", "5"))   # owner 2026-09-20: this stage is wide, it costs cents and no local CPU
    picks = bridge_models(n_workers)
    # MIXED ARMS (owner 2026-09-22, row 18): the round's builds are spread over the arms in BROTHER_WORKER_MIX (default
    # deepseek:5,muse:2,sonnet:1), a registry known model each; an unknown arm is dropped and the registry's picks fill it.
    try:
        import model_router as _MR
        _known = set(_MR.seatable_names())   # the stage gate applied here too: a shadow or retired row never seats through the mix
    except Exception as _exc:
        print("MIX     NO-DATA: registry unreadable (%s); the base picks stand" % type(_exc).__name__, flush=True); _known = set(picks)
    # PER UNIT CLASS ARMS (owner 2026-09-23 06:0x): docs and runner classes pass at 15 percent against code at 9, so the mix
    # shifts on the class's own record when it has one and on the whole ledger otherwise. The class is unit_ledger's.
    try:
        import unit_ledger as _UL; _cls = _UL.unit_classes(plan).get(unit, "code")
    except Exception: _cls = None   # sbe: allow-silent an unreadable class falls to the whole ledger's record
    _stats = WM.arm_stats(unit_class=_cls) if _cls else {}
    picks = WM.picks(n_workers, picks, _known, stats=_stats or WM.arm_stats()) or picks
    # THE EFFORT A/B (owner 2026-09-27, "A/B effort"): BROTHER_EFFORT_AB names the arms ("xhigh,high"); every round
    # carries both, alternating by build and round, so both arms work the same brief at the same moment.
    # THE LIVE CALIBRATION (owner 2026-09-28, "improve the calibration ... while I sleep"): a review writes
    # ~/.claude/evidence/loop-calibration.json ({"effort_ab": ["high", "high", "xhigh"]}, repeats weight an arm) and every
    # round re-reads it, so the arms move mid run without a restart. Absent, unreadable, or naming no known effort: the
    # arms the run started with stand, and the round says why.
    _efforts = ("minimal", "low", "medium", "high", "xhigh")
    _ab = [e for e in os.environ.get("BROTHER_EFFORT_AB", "").split(",") if e in _efforts]
    _cal = os.environ.get("BROTHER_CALIBRATION_FILE") or os.path.expanduser("~/.claude/evidence/loop-calibration.json")
    if os.path.exists(_cal):
        try:
            with open(_cal, encoding="utf-8") as _fh:
                _live = [e for e in (json.load(_fh).get("effort_ab") or []) if e in _efforts]
            if not _live:
                raise ValueError("no known effort named")
            _ab = _live
            print("EFFORT  arms from %s: %s" % (_cal, ",".join(_ab)), flush=True)
        except (OSError, ValueError, AttributeError, TypeError) as _exc:
            print("EFFORT  NO-DATA: %s unusable (%s); the run's own arms stand: %s" % (_cal, type(_exc).__name__, ",".join(_ab) or "none"), flush=True)
    jobs = [{"id": "%s-r%d" % (sub, i), "model": picks[i % len(picks)], "prompt_file": pf,
             "out": os.path.join(wd, "out", "%s-r%d-build.json" % (sub, i)),
             # "sensitivity" is EARNED by the private(text) screen above, never assumed: or_fanout
             # refuses an unlabelled job, because a caller that forgot to label its content is
             # exactly the caller whose content must not leave this machine.
             "estimated_cost": 0.02, "expect": "json", "sensitivity": "public",
             **({"effort": _ab[(i + rnd) % len(_ab)]} if _ab else {})}
            for i in range(n_workers)]
    # THE CLAUDE NATIVE WORKER (owner 2026-10-02, "wire the Claude-native worker into the loop"). With BROTHER_CLAUDE_NATIVE=on
    # and every arm on the claude transport, each job is a Claude Code session with real tools in a reset checkout of the
    # tree the grader grades (native_worker.py), briefed with the sub unit's spec section, the grader's contract and the
    # last round's lines, never the blind paste. Its build lands at the same out/<sub>-rN-build.json, so the cut, grading,
    # probes and landing are unchanged. Seats bound it (BROTHER_NATIVE_SEATS, default 2): a seat is a whole agent session.
    # Off, or any arm off the claude transport: the blind fan out below, exactly as before, and the round says which.
    import loop_switches as _SWN
    _native = False
    if _SWN.native_on():   # standard since 2026-10-02: off only with BROTHER_CLAUDE_NATIVE=off or no sandbox-exec
        try:
            _reg_n = _MR.registry()
            _native = all(_reg_n.get(j["model"], {}).get("transport") == "claude" for j in jobs)
        except Exception as _exc:
            print("NATIVE  NO-DATA: registry unreadable (%s); the blind fan out runs" % type(_exc).__name__, flush=True)
        if not _native:
            print("NATIVE  off this round: an arm is not on the claude transport (%s)" % ", ".join(sorted(set(j["model"] for j in jobs))), flush=True)
    if _gate_waived and not _native:
        status("WITHHELD round %d: the brief gate waived %s for a native session, and this round would run blind on that brief"
               % (rnd, _gate_waived)); sys.exit(2)
    if _native:
        import native_worker as _NW   # only when on: a run with the switch off never loads it
        try:
            _seats_n, _native_s = _NW.seats(), _NW.session_seconds()
        except ValueError as _exc:
            status("BLOCKED %s" % _exc); sys.exit(2)
        _head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        _section = None
        if os.path.isfile(spec):
            with open(spec, encoding="utf-8") as _fh:
                _section = _NW.spec_section(_fh.read(), sub)
        if _head.returncode != 0 or not _head.stdout.strip():
            status("BLOCKED round %d native: the graded tree's HEAD cannot be read" % rnd); sys.exit(2)
        if not _section:
            status("WITHHELD round %d native brief: %s names no section for %s" % (rnd, spec, sub)); sys.exit(2)
        _native_base = _head.stdout.strip()
        # THE PREVIOUS REFUSAL TOO (2026-10-02 10:13): FX-13.4's native build was refused at landing and the session that
        # re-seated it would never have learned why, since hint (RUNNER_HINT, the diagnostician's note, the last landing
        # refusal) reached only the blind brief. It heads the native note, as it heads the blind brief.
        _learned = ("LEARNED FROM REJECTED BUILDS OF THIS SUB UNIT, read before writing anything:\n" + hint) if hint else ""
        _ntext = _NW.brief(sub, _section, _NW.contract_text([r.strip() for r in _runners.split(",") if r.strip()]),
                           "\n\n".join(x for x in (_learned, native_note) if x))
        if private(_ntext):
            status("WITHHELD round %d native brief: private term" % rnd); sys.exit(2)
        _npf = os.path.join(wd, "prompts", sub + "-native.md")
        with open(_npf, "w", encoding="utf-8") as _fh:
            _fh.write(_ntext)
        jobs = [dict(j, prompt_file=_npf) for j in jobs[:min(len(jobs), _seats_n)]]
        print("NATIVE  on: %d session(s) on %d seat(s), base %s, brief %d bytes" % (len(jobs), _seats_n, _native_base[:9], len(_ntext.encode())), flush=True)
    print("MODELS  " + ", ".join("%s x%d" % (m, sum(1 for j in jobs if j["model"] == m)) for m in picks))
    jf = os.path.join(wd, "jobs.json"); json.dump(jobs, open(jf, "w"), indent=1)
    # THE FAN OUT WRITES BESIDE THE ROUND, NEVER INTO IT: its builds land in fanout/ and the cut copies the ones present
    # into out/, so a straggler that finishes after the cut can never change the set being graded. jobs.json keeps the
    # out/ paths every reader of the round uses (self_check, unit_ledger).
    jff = os.path.abspath(os.path.join(wd, "jobs-fanout.json"))
    json.dump([dict(j, out=os.path.abspath(os.path.join(wd, "fanout", os.path.basename(j["out"])))) for j in jobs], open(jff, "w"), indent=1)
    # PATIENCE FOLLOWS THE ROUND'S SLOWEST FAMILY (owner 2026-09-23): soft cut, hard cut and the fan out's own per call
    # timeout all come from WM.patience, never the DeepSeek tuned 150/480/420 constants. ONE DEADLINE (FX-10,
    # BROTHER_ONE_DEADLINE=on): patience is on the model's own clock (WM.measured) and the round cut, the call timeout, the
    # fan out's wave deadline and the reaper all come from WM.deadlines, the one source; off, today's arithmetic below.
    _one, _d, _block = WM.one_deadline(), None, {}
    try:
        _T = {m: v.get("transport") for m, v in _MR.registry().items()}
        if _one:
            _block = WM.measured([j["model"] for j in jobs], _T, registry=_MR.registry())
            _pat = WM.patience_of(_block)
            _d = WM.deadlines(_pat, B.knob("FANOUT_TIMEOUT_S", 420), FANOUT_GRACE_S)
        else:
            _pat = WM.patience([j["model"] for j in jobs], _T)
    except Exception as _exc:
        _pat, _d = max(WM.TRANSPORT_PATIENCE.values()), None; print("PATIENCE NO-DATA (%s): the slowest default %d s" % (type(_exc).__name__, _pat), flush=True)
    _soft, _hard = max(150, _pat), max(480, int(_pat * 1.5))
    # FANOUT_TIMEOUT_S is a LOWER bound on the per call timeout (a test or an operator can only lengthen it); the dispatcher
    # still refuses a bridge call under its own 300 s floor.
    _call_timeout = max(B.knob("FANOUT_TIMEOUT_S", 420), int(_pat * 1.5))
    _reap, _wave_argv = 2 * _call_timeout + FANOUT_GRACE_S, []
    if _d:
        _hard, _call_timeout, _reap, _wave_argv = _d["round"], _d["call"], _d["reap"], ["--deadline", str(_d["wave"])]
        print("PATIENCE soft %d s, round %d s, call %d s, wave %d s, reap %d s (one deadline; %s)" % (_soft, _d["round"], _d["call"], _d["wave"], _d["reap"],
              ", ".join("%s p75 %s s from %s" % (m, b.get("p75_s"), b.get("source")) for m, b in sorted(_block.items())) or "no model"), flush=True)
        for _line in WM.findings(_block):
            print(_line, flush=True)
        if _call_timeout < WM.DISPATCH_FLOOR_S:
            print("PATIENCE NO-DATA: the call deadline %d s is under the dispatcher's %d s floor; bridge calls are refused TimeoutTooLow"
                  % (_call_timeout, WM.DISPATCH_FLOOR_S), flush=True)
    else:
        print("PATIENCE soft %d s, hard %d s (slowest family in this round)" % (_soft, _hard), flush=True)
    hold_gate("round %d model fan-out" % rnd)
    if _native:   # one session per job, each up to its own cap after a seat wait of up to the same: the 2x bound
        # THE NATIVE CAP IS ITS OWN (owner 2026-10-02, "a longer cap"): FX-13.4's session ended at the 1800 s call timeout
        # with no result. A native session runs its own tests, so it gets BROTHER_NATIVE_SESSION_S (native_worker).
        # plus the seat's fix sessions after its landing fuzz (native_worker.FIX_ROUNDS x FIX_SESSION_S, 2026-10-05)
        _fix_s = _NW.FIX_ROUNDS * min(_native_s, _NW.FIX_SESSION_S)
        _hard, _reap = max(_hard, 2 * _native_s + _fix_s), max(_reap, 2 * _native_s + _fix_s + FANOUT_GRACE_S)
        print("NATIVE  session cap %d s, round cut %d s, reap %d s" % (_native_s, _hard, _reap), flush=True)
        _fan_argv = [sys.executable, "-B", os.path.join(BIN, "native_worker.py"), jff, "--repo", os.getcwd(), "--base", _native_base,
                     "--timeout", str(_native_s)]
    else:
        _fan_argv = [sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jff, "--workers", "99", "--timeout", str(_call_timeout), "--retries", "0"] + _wave_argv
    proc = subprocess.Popen(_fan_argv + ["--results", os.path.abspath(os.path.join(wd, "results.json"))], stdout=open(os.path.join(wd, "fanout.log"), "w"), stderr=subprocess.STDOUT,
                            start_new_session=True, cwd=code_root)
    t0 = time.time()
    _fanouts.append((proc, t0 + _reap))
    while proc.poll() is None and time.time() - t0 < _hard and len(glob.glob(os.path.join(wd, "fanout", "*.json"))) < 3:
        time.sleep(3)
        if len(glob.glob(os.path.join(wd, "fanout", "*.json"))) >= 2 and time.time() - t0 > _soft:
            break  # two builds in hand after 150 s: grade them, the third is a straggler
    # THE CUT: the builds present now are the round's graded set (review 2026-09-22: a fan out writing into out/ changed
    # the set under the grader). A straggler still running is left to finish; reap_fanouts waits for it at exit.
    for _src in sorted(glob.glob(os.path.join(wd, "fanout", "*.json"))):
        with open(_src, "rb") as _fi, open(os.path.join(wd, "out", os.path.basename(_src)), "wb") as _fo:
            _fo.write(_fi.read())
    stage_log.leave("model", sub, unit, ok=bool(glob.glob(os.path.join(wd, "out", "*.json"))))
    # A REFUSED ROUND IS NOT A FAILED ROUND (2026-09-22 18:49 to 18:59): when no build came back and every fan out record
    # was refused for money, the run ends UNFUNDED, a word the pool re-seats once money returns, never EXHAUSTED.
    if not glob.glob(os.path.join(wd, "out", "*.json")) and FV.all_refused_by_budget(os.path.join(wd, "results.json")) is True:
        status("UNFUNDED round %d: every worker call was refused by the dispatcher's budget; no build was made; re-seat when money returns" % rnd); sys.exit(4)
    # A CLAUDE LIMIT IS NOT A ROUND (2026-10-02, owner: "the final users won't accept bugs"): every native session ended in
    # a Claude error result, a usage limit or an outage. Nothing was built or learned, so no attempt is spent: the run ends
    # UNFUNDED (the quota is the money of a Claude plan) and the pool re-seats it after its cooldown, never EXHAUSTED.
    if _native and not glob.glob(os.path.join(wd, "out", "*.json")) and FV.all_claude_errors(os.path.join(wd, "results.json")) is True:
        status("UNFUNDED round %d: every native Claude session ended in a Claude error result (a usage limit or an outage); "
               "no build was made and no attempt was spent; re-seated after the cooldown" % rnd); sys.exit(4)
    # NO SEAT IS NOT A ROUND (review 2026-10-02): every native session waited for a seat in vain (another runner held
    # both) or met a Claude error, so nothing ran. No attempt is spent; the pool re-seats after its cooldown, as for a
    # drain, the other "nothing ran" ending (DRAINED, exit 5).
    if _native and not glob.glob(os.path.join(wd, "out", "*.json")) and FV.all_unseated(os.path.join(wd, "results.json")) is True:
        status("DRAINED round %d: no native session ran (no seat freed within the session cap); no build was made and no "
               "attempt was spent; re-seated after the cooldown" % rnd); sys.exit(5)
    # A CONFIGURATION FAULT IS NOT A ROUND (2026-09-30): every worker call came back CONFIG_WAIT, the program does not know
    # the model or its configuration breaker already holds it. Nothing was built, nothing was learned about the brief and no
    # attempt of this sub unit is spent: no grade, no lesson, no EV history. The pool re-seats it once the resolved program
    # changes (runner_pool.fact_time reads the program record); tonight 78 such calls parked 7 sub units EXHAUSTED in 6 min.
    if not glob.glob(os.path.join(wd, "out", "*.json")) and FV.all_config_wait(os.path.join(wd, "results.json")) is True:
        status("CONFIG_WAIT round %d: every worker call was refused because the program does not know its model; no build was made "
               "and no attempt was spent; re-seated when the resolved program changes (loop_intake.py prepare proves it)" % rnd); sys.exit(6)
    # A DRAINED ROUND IS NOT A ROUND (objection 10): every call was refused at registration because the proof run is
    # ending or near its deadline, so nothing was registered or spent; no grade, no lesson, and the pool re-seats it.
    if not glob.glob(os.path.join(wd, "out", "*.json")) and FV.all_refused_by_drain(os.path.join(wd, "results.json")) is True:
        status("DRAINED round %d: every worker call was refused because the proof run is draining; nothing was registered or spent; re-seat after the drain" % rnd); sys.exit(5)
    # THE WORKER CHECKS ITSELF FIRST (owner 2026-09-22, piece 3): the grader's safety screen over every build, one free
    # repair with the exact refusal at the head, no sandbox, no round spent. Static only; the sandbox grade still decides.
    try:
        # ONE DEADLINE FOR THE SELF CHECK TOO (2026-09-30 21:3x): SC.screen's own default is 480 s, so every repair a slow
        # family needed past 540 s was cut (12 of tonight's rounds, 'SELFCHECK fan out timed out after 540s') while the
        # build fan out, on the same models, waited the measured patience. It gets the same per call deadline.
        _u, _r = SC.screen(wd, timeout=_call_timeout)
        if _u: print("SELFCHK %d unsafe build(s) sent back once, %d came back safe" % (_u, _r), flush=True)
    except Exception as _exc:
        print("SELFCHK NO-DATA: %s" % type(_exc).__name__, flush=True)
    stage_log.enter("grade", sub, unit)
    _grc = B.run_bounded([os.path.join(BIN, "grade_lane.sh"), wd, sub], os.path.join(wd, "lane.log"), B.knob("GRADE_TIMEOUT", GRADE_TIMEOUT_S))
    lane = open(os.path.join(wd, "lane.log"), encoding="utf-8").read()
    hit = graded_pass(_grc, lane, sub)
    # A PAID ANSWER IS GRADED BEFORE ANOTHER ROUND IS BOUGHT (Codex and Fable, 2026-09-28): run A threw away 14 builds
    # that arrived after the cut, all in rounds with no PASS, while a repair round was paid on the same brief. On a
    # NO-PASS the round waits (bounded, LATE_WAIT_S) for its own fan out and grades whatever arrived since the cut. One
    # deadline (FX-10): the wait reaches the wave deadline, so every call the round paid for has ended before another round.
    if not hit:
        _late_until = time.time() + B.knob("LATE_WAIT_S", 240)
        if _d:
            _late_until = max(_late_until, t0 + _d["wave"])
        while proc.poll() is None and time.time() < _late_until:
            time.sleep(3)
        _late = [p for p in sorted(glob.glob(os.path.join(wd, "fanout", "*.json")))
                 if not os.path.exists(os.path.join(wd, "out", os.path.basename(p)))]
        if _late:
            for _src in _late:
                with open(_src, "rb") as _fi, open(os.path.join(wd, "out", os.path.basename(_src)), "wb") as _fo:
                    _fo.write(_fi.read())
            print("LATE    %d answer(s) arrived after the cut; graded before another round is bought" % len(_late), flush=True)
            _grc = B.run_bounded([os.path.join(BIN, "grade_lane.sh"), wd, sub], os.path.join(wd, "lane.log"), B.knob("GRADE_TIMEOUT", GRADE_TIMEOUT_S))
            lane = open(os.path.join(wd, "lane.log"), encoding="utf-8").read()
            hit = graded_pass(_grc, lane, sub)
    # A GRADE THAT JUDGED NOTHING IS NO-DATA, NEVER A REJECTION (owner 2026-10-03, CV1.b): every !graded_pass was read as
    # "REJECTED BY THE SANDBOX GRADER", taught as a code lesson and paid for with another model round, while two CV1.b
    # grades had hit GRADE_TIMEOUT with empty output and judged nothing. A round is a rejection only when a grade
    # COMPLETED with the grader's own verdict (grades/<build>.txt closing exit=0 or exit=1; exit 3 is the grader's own
    # fault, a .part is a grade that never finished). Otherwise: no repair call, no lesson, the paid builds stay in out/,
    # and the sub unit parks WITHHELD until a fact changes (GRADE_TIMEOUT, the grader, the machine), like any NO-DATA park.
    if not hit and not judged(wd, sub):
        stage_log.leave("grade", sub, unit, ok=None)   # nothing learned: never counted as a rejection
        status("WITHHELD round %d: NO-DATA, the grade judged nothing (grade stage exit %s, no completed verdict); "
               "builds kept in %s; no model repair and no lesson; re-seated when its spec, a judge tool, the resolved program or a "
               "diagnosis note changes (runner_pool fact rule); the kept builds are not re-graded automatically"
               % (rnd, _grc, os.path.join(wd, "out")))
        sys.exit(2)
    stage_log.leave("grade", sub, unit, ok=bool(hit))
    if not hit:
        lines = []
        for g in sorted(glob.glob(os.path.join(wd, "grades", "*.txt"))):
            lines += ["[" + os.path.basename(g)[:-4] + "] " + l[:GRADE_LINE_MAX] for l in open(g, encoding="utf-8").read().splitlines()
                      if re.match(r"^(GREEN|RED-|APPLY|FAIL|   -|NEIGHBOUR|\s+mutation .*(SURVIVED|NOT APPLY))", l)]
        # THE REPAIR STARTS FROM THE BEST BUILD, NEVER FROM A REJECTED ONE (2026-09-22). This handed the next round the first
        # rejected file, so after a DIRTY probe round the repair regressed at the grader and the round after that started
        # from THAT wreck: the sequence PASS+DIRTY, NO-PASS, PASS+DIRTY, NO-PASS ran to exhaustion on 8 of 11 sub units.
        # When a build has passed the grader this run, the brief carries it, with the rejected lines as the lesson.
        base, from_best = repair_base(best, sorted(glob.glob(os.path.join(wd, "out", "*.json"))))
        prev = open(base, encoding="utf-8").read() if base else ""
        advice = RA.advise("\n".join(lines), prev, brief[:8000]) if RA.enabled(sub) and _repair_model != "off" else None
        if advice: print("ADVISOR %s" % advice.splitlines()[0][:80], flush=True)
        note = (("\n\n" + advice) if advice else "") + ("\n\nROUND %d WAS REJECTED BY THE SANDBOX GRADER. Its own lines:\n" % rnd + "\n".join(lines) +
                ("\nRead the FIRST failing line. THE BUILD BELOW PASSED THE GRADER EARLIER THIS RUN: start from it, apply the smallest change that answers the lines above, change nothing else.\n" if from_best else
                 "\nRead the FIRST failing line, fix its cause, and check every mutation against your own tests. One of the rejected builds, to start from or discard:\n") + prev[:60000] + "\n")
        native_note = (("\n\n" + advice) if advice else "") + "ROUND %d WAS REJECTED BY THE SANDBOX GRADER. Its own lines:\n" % rnd + "\n".join(lines)
        teach("\n".join(lines)[:400], stage="grade")
        print("round %d: grader NO-PASS" % rnd, flush=True); continue
    best = os.path.join(wd, "out", hit + "-build.json")
    import loop_switches as _SW
    probes_off = not _SW.probes_on()
    # PROBES OFF (plan E step 2c): no generation and no wait. The stage still falls through to the same admit path, so a
    # checker the calibrator granted gate still runs, and every record says NOT RUN, never clean.
    probe_word = "probes NOT RUN (off by plan E)" if probes_off else "probes clean"
    stage_log.enter("probe", sub, unit)
    table = os.path.join(wd, "probes.table")
    if not probes_off:
        hold_gate("round %d probes" % rnd)
        B.run_bounded([sys.executable, os.path.join(BIN, "probe_wave.py"), wd, os.path.join(wd, "probes"), "^%s$" % re.escape(sub)],
                      table, B.knob("PROBE_TIMEOUT", 600) + 180)
    # FAIL DIRECTION AT THE READ ITSELF. No verdict file at all reads as "NO-DATA" (UNKNOWN). An EMPTY verdict
    # file strips to "", which is not a key in PROBE_VERDICT and is therefore UNKNOWN too. A half written file
    # holding a truncated word is UNKNOWN for the same reason. None of the three can reach the READY exit.
    verdict = "NOT RUN" if probes_off else (open(os.path.join(wd, "probes", "logs", sub + ".done")).read().strip() if os.path.isfile(os.path.join(wd, "probes", "logs", sub + ".done")) else "NO-DATA")
    finds = []
    for log in glob.glob(os.path.join(wd, "probes", "logs", sub + "-*.log")):
        for l in open(log, encoding="utf-8").read().splitlines():
            m = re.match(r"^(CRASH|WRONG-ACCEPT\?)\s+(\S+)\s+(\S+)\s?(.*)$", l)
            if m and (P.classify(m.group(3), "", m.group(4)) == "CRASH" if m.group(1) == "CRASH"
                      else not P.REFUSAL_VALUE.search(re.sub(r"^RETURNED\s+", "", (m.group(3) + " " + m.group(4)).strip()))):
                finds.append(l[:230])
    finds = sorted(set(finds))
    admit = True if probes_off else probe_admits(verdict, finds)
    outcome = "ADMIT" if probes_off else probe_outcome(verdict, finds)  # pure, so calling it twice costs nothing and keeps both names honest
    # THREE STATES, KEPT APART IN EVERYTHING THIS STAGE WRITES.
    # stage_log: ok=True admitted, ok=False rejected, ok=None nothing learned (leave() already defaults ok to
    # None, so the funnel counts an unknown in neither column instead of inflating the rejection count).
    # STATUS file: READY / EXHAUSTED-after-repair / READY-UNPROBED, three distinct first words.
    # probes/logs/<sub>.done: CLEAN / DIRTY / NO-DATA, written by probe_wave, three distinct words.
    stage_log.leave("probe", sub, unit, ok=True if outcome == "ADMIT" else (False if outcome == "REFUSE" else None),
                    note="%s -> %s" % (verdict, outcome))
    if admit:
        _advisory = [f for f in finds if f.startswith("WRONG-ACCEPT?")]
        if _advisory:
            try:
                with open(best + ".probe-notes.txt", "w", encoding="utf-8") as _f: _f.write("\n".join(_advisory) + "\n")
            except OSError as _exc: print("PROBE NOTES not written: %s" % _exc, flush=True)
        teach("grader PASS, %s at round %d" % (probe_word, rnd), ok=True)
        # THE CHECKER IS GONE (owner decision 2026-10-02, docs/decisions/remove-checker-2026-10-02.json): grader PASS and
        # the probes decide READY; nothing reviews the build again before landing.
        status("READY %s (round %d, grader PASS, %s%s)" % (best, rnd, probe_word, ", %d advisory probe note(s) beside the build" % len(_advisory) if _advisory else "")); sys.exit(0)
    if outcome == "UNKNOWN":
        # THE BRANCH IS TAKEN ON THE OUTCOME, NOT ON THE WORD "NO-DATA".
        # Before 2026-09-22 this compared verdict == "NO-DATA", so an unrecognised or truncated verdict word
        # fell through to the DIRTY branch below and was recorded as a REJECTION with an empty finding list:
        # an unknown counted as a failure, which is the mirror of admitting one.
        # AN ABSENT PROBE RESULT IS AN UNKNOWN, AND THIS IS THE STATE THE LOOP CLEARS BY ITSELF.
        # It is RETRIED, never held. Two stages clear it and the cheap one runs first: scripts/probe_round.py
        # re-probes this same build and promotes it to READY, and if that keeps returning nothing, runner_pool
        # starts a fresh runner that rebuilds the sub unit from the current tree. Nothing lands it meanwhile:
        # land_batch's gate admits a status whose FIRST WORD is exactly READY and no other.
        # What used to happen instead, measured 2026-09-21 at a 24 percent probe yield: runner_pool and
        # loop_done both matched the PREFIX "READY", so the unit was held forever as "a READY build waits to
        # land" while the only gate that could land it refused the same string. Both readers now compare the
        # status WORD, which is what land_batch and pass_digest always did.
        # Exit 1, not 0: no caller reads this code (runner_pool and diag_apply both Popen without waiting),
        # but a human reading $? must not be told an unknown succeeded.
        # A SILENT PROBE NO LONGER BLOCKS (owner, 2026-09-27, his yes to "a build that passed the grader, whose probe gave
        # no answer, goes to landing"): the landing's own suites on both Pythons, its fuzz and the spec's done check decide.
        # Measured before: READY-UNPROBED builds were re-probed or rebuilt, never landed, and landings fell to zero.
        status("READY %s (round %d, grader PASS, probe stage returned %r: no answer, so the landing gates decide)" % (best, rnd, verdict)); sys.exit(0)
    note = ("\n\nROUND %d PASSED THE GRADER AND WAS THEN REJECTED: a red team fired hostile inputs at it AS EXECUTED CODE and these came back wrong (CRASH = a raw interpreter exception reached the caller; "
            "WRONG-ACCEPT? = the red team says the specification blocks this input and the code returned a normal value: check each against the specification):\n" % rnd + "\n".join(finds) +
            "\nFix every real one at its source, add one test per finding class and one mutation per new guard, change nothing else. THE PREVIOUS BUILD:\n" + open(best, encoding="utf-8").read()[:80000] + "\n")
    native_note = "ROUND %d PASSED THE GRADER AND WAS THEN REJECTED: executed hostile inputs came back wrong:\n" % rnd + "\n".join(finds)
    teach("\n".join(finds)[:400], stage="probe")
    print("round %d: grader PASS, probes DIRTY (%d findings)" % (rnd, len(finds)), flush=True)
# THE FINISHER'S PRE-BRIEF AT PARK TIME (owner 2026-09-23 05:4x): the probe findings and the best build are on disk the moment a
# sub unit parks, and nothing read them until the finisher started hours later and briefed from scratch. One advisor call
# now, cached beside the run, so the finisher starts with a plan. Advisory text only; a failure here is a printed line.
try:
    if best and RA.enabled(sub) and _repair_model != "off":
        _pre = RA.advise(note[:6000] if note else "no findings recorded", open(best, encoding="utf-8").read()[:20000], brief[:8000])
        if _pre:
            with open(os.path.join(run, "FINISHER-BRIEF.md"), "w", encoding="utf-8") as _f: _f.write(_pre)
            print("PREBRIEF written for the finisher: %s" % _pre.splitlines()[0][:80], flush=True)
except Exception as _exc: print("PREBRIEF NO-DATA: %s" % type(_exc).__name__, flush=True)   # sbe: allow-silent advisory cache, never a gate
teach("EXHAUSTED after %d rounds; best grader pass: %s" % (max_rounds, best))
status("EXHAUSTED after %d rounds; best grader pass: %s" % (max_rounds, best)); sys.exit(1)

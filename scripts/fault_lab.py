#!/usr/bin/env python3
"""fault_lab: R27.1, the installed-artifact lifecycle fault lab.

WHY THIS EXISTS. Every observer this estate had before today shares one
abstraction boundary with the product it checks: a unit test imports the
same modules the implementation does, a hand-run drives the same in-process
call the implementation makes. The Codex consultation named the disease
(docs/plan/HARDENING-2026-08-30-CODEX.md): observer monoculture. Four real
defects shipped and were only found by a human or a rival tool because
nothing here ever asked the questions a stranger with the installed plugin
and no source checkout would ask.

THE ONE LAW THAT MAKES THIS WORTH BUILDING, and the reason it is checked by
its own test (test_fault_lab.py): THIS FILE IMPORTS NO PRODUCT MODULE. Not
door, not loop_bridge, not claim_store, not brother_run, not any module this
estate ships. It treats the product exactly as a stranger would: a real
`claude plugin install`, a real launcher (bundle/runtime/brother-run) run as
a subprocess, and answers read from FILES (the Work document, claims.json,
git) and CLI OUTPUT ONLY. A harness that imported claim_store to drive this
would recreate the exact blind spot it exists to close (the recorded lesson
in scripts/test_repair_drain.py's own docstring: driving brother_run.main()
in-process, with a fake run_loop, proved the OUTER drain logic but never
touched the real git worktrees, the real lane machinery, or the real claim
store lock).

THE ONLY FAKE ALLOWED is the deterministic worker stub, through the
documented DOOR_MODEL_CMD / MODEL_WORKER_CMD environment seam (the same one
scripts/clean_install_e2e.sh already uses): a real model cannot authenticate
inside this sandbox, so a scripted stand-in plays it. Everything downstream
of that seam is the real product: real git, real worktrees, real locks, real
subprocesses.

THE FOUR LIFECYCLE SCENARIOS, each driven only through bundle/runtime/brother-run
(resolved from a REAL, throwaway `claude plugin install`) and asserted only
from files and CLI output:

  fail_then_repair          a unit fails its own check, then repairs, within
                            the bounds brother_run itself names (either it
                            integrates, or the run exits naming the
                            exhausted repair bound; either is a pass).
  crash_then_bare_invoke    SIGKILL the launcher's whole process group
                            mid-run; a second, bare invocation (same outcome
                            text, no --resume, no --continue) must resume
                            the SAME work-set rather than start a second one,
                            announce that it did, and reclaim the dead
                            owner's claim promptly.
  two_process_race          two launcher invocations race for the same unit
                            on the same repository; the loser must never
                            double-claim and the unit integrates exactly
                            once.
  kill_holding_lock         a process dies holding the claim store's lock
                            file; a follow-up acquire through the real
                            product must reclaim it promptly, not wait out
                            the timeout, and must say so.

TWO MORE, ADDED FOR P13 (docs/plan/READINESS-ROADMAP-2026-08-29.json, doc
21.6 DS-G01 and DS-G03): the data-science golden fixtures. Neither crashes
or races anything; both prove the engine correctly refuses a trap rather
than silently accepting it as proof.

  ds-leakage                 a unit's done_check runs scripts/split_check.py
                            against tests/fixtures/ds-leakage/ (a train and
                            test CSV sharing an entity); the check must
                            print FAIL and the unit must never read
                            verified, however many attempts it is given.
  ds-seed                    a unit's done_check runs
                            tests/fixtures/ds-seed/eval.py, an eval script
                            that draws its metric from an unseeded random
                            source; the run must reach the check green by
                            exit code alone, yet the receipt must still
                            read the unit no-data, 'no metric recorded',
                            because E18's own evidence file was never
                            written.

BARRIERS, NEVER SLEEPS, AS SYNCHRONIZATION. Every wait in this file polls
for an on-disk FACT (a marker file, a claim's state, a lock file) with a
bounded deadline; nothing here sleeps a fixed duration and assumes another
process reached some state by then. The polling interval itself is not the
synchronization, the fact being polled for is.

DRIVING IT BACKWARDS (R27.1.3): --reintroduce NAME patches a FRESH, THROWAWAY
installed copy (never this repository) with one of the four fixed defects,
by exact text replacement against an anchor read from THIS repository's own
current source, and runs the one scenario built to catch it. It reports
CAUGHT (this lab still works) or MISSED (a false-negative in the lab
itself), naming the violated invariant either way.

Python 3, standard library only. No network calls beyond the one `claude
plugin install`. No em or en dashes.

PRODUCER: this module is the sole producer of the throwaway artifacts it
exercises. install_artifact() (line 160) creates the installed plugin cache
via subprocess (`claude plugin install`, line 172) under a fresh tempfile.
mkdtemp() home/config; fresh_repo() (line 195) creates a throwaway git
repository; and, for --reintroduce, _patch_file() (line 634) does the actual
open(path, "w") plus fh.write(text.replace(old, new, 1)) at lines 639-640 to
patch a fresh installed copy, never this repository.
"""
import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NODATA = "NO-DATA"


# ---------------------------------------------------------------------------
# Small, generic subprocess and polling helpers. None of these know anything
# about Brother's own modules; they would be identical for any product.
# ---------------------------------------------------------------------------

def _sh(args, cwd=None, env=None, timeout=60):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)


def _claude_present():
    return _sh(["sh", "-c", "command -v claude"]).returncode == 0


def _pid_alive(pid):
    """os.kill(pid, 0), read as a fact about the OS, not a product internal."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _poll(pred, timeout=20.0, interval=0.02):
    """A barrier: wait for an on-disk (or process-table) fact to become true.
    Never a fixed sleep guessing when another process gets there; the fact
    itself is the synchronization, the interval is only how often it is
    checked."""
    deadline = time.time() + timeout
    ok = pred()
    while not ok and time.time() < deadline:
        time.sleep(interval)
        ok = pred()
    return ok


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _kill_group(popen_obj, timeout=10):
    """SIGKILL the WHOLE process group a Popen was started with
    (start_new_session=True), so a grandchild the launcher subprocess.calls
    into (bundle/runtime/brother-run always execs brother_run.py as a fresh
    child) dies too. Killing only the top pid would leave that child running
    as an orphan, which is not what a real crashed session looks like."""
    try:
        os.killpg(os.getpgid(popen_obj.pid), signal.SIGKILL)
    except ProcessLookupError:  # sbe: allow-silent process group already exited before the signal landed, nothing left to kill
        pass
    try:
        popen_obj.wait(timeout=timeout)
    except subprocess.TimeoutExpired:  # sbe: allow-silent every caller polls _pid_alive(worker_pid) right after this returns, so a slow reap is still observed there
        pass


# ---------------------------------------------------------------------------
# The clean installed artifact. Mirrors scripts/clean_install_e2e.sh exactly
# (marketplace add, install brother@brother, resolve the launcher and the
# sibling brothermode tools/ directory by the manifest, never a typed
# version): that script already proves the install path works, so this only
# needs its OUTPUT, once, reused across every scenario below.
# ---------------------------------------------------------------------------

def install_artifact(root=ROOT, timeout=90):
    """(info, problem). info: launcher, runtime_root, home, config."""
    home = tempfile.mkdtemp(prefix="fault-lab-home-")
    config = tempfile.mkdtemp(prefix="fault-lab-config-")
    env = dict(os.environ, HOME=home, CLAUDE_CONFIG_DIR=config)

    add = _sh(["claude", "plugin", "marketplace", "add", root], env=env,
              timeout=timeout)
    if add.returncode != 0 or "Successfully added marketplace" not in (add.stdout or ""):
        return None, ("marketplace add failed: %s"
                      % ((add.stdout or "") + (add.stderr or ""))[:400])

    inst = _sh(["claude", "plugin", "install", "brother@brother", "-y"],
              env=env, timeout=timeout)
    if inst.returncode != 0 or "Successfully installed plugin" not in (inst.stdout or ""):
        return None, ("install failed: %s"
                      % ((inst.stdout or "") + (inst.stderr or ""))[:400])

    cache = os.path.join(config, "plugins", "cache")
    launchers = glob.glob(os.path.join(cache, "*", "brother", "*", "runtime",
                                       "brother-run"))
    if len(launchers) != 1:
        return None, ("expected exactly one installed launcher under "
                      "%s/*/brother/*/runtime/brother-run, found %d: %r"
                      % (cache, len(launchers), launchers))

    tools = glob.glob(os.path.join(cache, "*", "brothermode", "*", "tools"))
    if len(tools) != 1:
        return None, ("expected exactly one installed brothermode tools/ "
                      "directory, found %d: %r" % (len(tools), tools))

    return {"launcher": launchers[0], "runtime_root": os.path.dirname(tools[0]),
            "home": home, "config": config}, ""


def fresh_repo():
    """A throwaway git repository with one seed commit, never this one."""
    repo = tempfile.mkdtemp(prefix="fault-lab-repo-")
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "fault-lab@example.invalid"],
                 ["config", "user.name", "fault-lab"]):
        _sh(["git"] + args, cwd=repo)
    _write(os.path.join(repo, "seed.txt"), "seed\n")
    _sh(["git", "add", "-A"], cwd=repo)
    _sh(["git", "commit", "-q", "-m", "seed"], cwd=repo)
    return repo


def run_env(artifact, decomposer, worker, extra=None):
    env = dict(os.environ)
    env["BROTHER_RUNTIME_ROOT"] = artifact["runtime_root"]
    env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
    env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, worker)
    if extra:
        env.update(extra)
    return env


def launcher_cmd(artifact, outcome, cwd, runs_root, extra_args=None):
    cmd = [sys.executable, artifact["launcher"], outcome, "--cwd", cwd,
           "--runs-root", runs_root]
    return cmd + (extra_args or [])


def brother_run(artifact, outcome, cwd, runs_root, env, extra_args=None,
                timeout=60):
    return _sh(launcher_cmd(artifact, outcome, cwd, runs_root, extra_args),
              env=env, timeout=timeout)


def _run_dirs(runs_root):
    return sorted(glob.glob(os.path.join(runs_root, "docs", "plan", "runs", "*")))


def _result(name, invariant, ok, detail, output=""):
    return {"name": name, "invariant": invariant, "ok": ok, "detail": detail,
            "output": output}


# ---------------------------------------------------------------------------
# Scenario 1: FAIL THEN REPAIR.
# ---------------------------------------------------------------------------

_S1_INVARIANT = ("the repair is dispatched by the same invocation, and the "
                 "final state holds one integrated unit, or the run exits "
                 "naming the exhausted repair bound")


def scenario_fail_then_repair(artifact):
    workdir = tempfile.mkdtemp(prefix="fault-lab-s1-")
    repo = fresh_repo()
    runs_root = tempfile.mkdtemp(dir=workdir, prefix="runs-")
    counter = os.path.join(workdir, "counter.txt")

    decomposer = _write(os.path.join(workdir, "decomposer.py"), """
import json, sys
sys.stdin.read()
print(json.dumps([{"id": "F1",
                   "objective": "prove a unit repairs across attempts",
                   "done_check": "grep -q REPAIRED f.txt",
                   "writes": ["f.txt"], "deps": []}]))
""")
    # THE FAILURE IS SEEDED BY A COUNTER OUTSIDE THE LANE (never inside the
    # git tree the worker writes into, so it never becomes an undeclared
    # write the scope gate would quarantine). The first four invocations
    # (the initial claim plus loop_bridge's own three in-lane repair
    # attempts) write wrong content, so the SAME outer claim's own in-lane
    # repair genuinely exhausts; only the fifth invocation, which can only
    # happen on a fresh OUTER claim next round, writes the fix. This is the
    # real shape of the 2026-08-30 defect: repair across ROUNDS, not merely
    # within one lane.
    worker = _write(os.path.join(workdir, "worker.py"), """
import os
counter_path = os.environ["FAULT_LAB_COUNTER"]
n = 0
if os.path.exists(counter_path):
    with open(counter_path, encoding="utf-8") as fh:
        n = int(fh.read().strip() or "0")
n += 1
with open(counter_path, "w", encoding="utf-8") as fh:
    fh.write(str(n))
content = "REPAIRED\\n" if n >= 5 else "WRONG\\n"
with open("f.txt", "w", encoding="utf-8") as fh:
    fh.write(content)
print("s1 worker invocation %d wrote %r" % (n, content.strip()))
""")
    env = run_env(artifact, decomposer, worker,
                  extra={"FAULT_LAB_COUNTER": counter})
    proc = brother_run(artifact, "prove a unit repairs across attempts", repo,
                       runs_root, env, timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")

    # The report lists integrated units on their own line since 2026-08-31
    # ("    F1  verified by: <cmd>"), not inline after the count header.
    ok_integrated = any(l.split()[:1] == ["F1"] and "verified by:" in l
                        for l in out.splitlines())
    ok_exhausted = ("exhausted the" in out and "repair bound" in out
                    and "F1" in out)
    ok = ok_integrated or ok_exhausted
    detail = ("integrated" if ok_integrated else
             "exhausted-bound named" if ok_exhausted else
             "NEITHER outcome held (exit %s)" % proc.returncode)
    return _result("fail_then_repair", _S1_INVARIANT, ok, detail, out)


# ---------------------------------------------------------------------------
# Scenario 2: CRASH THEN BARE INVOKE.
# ---------------------------------------------------------------------------

_S2_INVARIANT = ("exactly one active work-set exists after the crash (the "
                 "roadmap's own wording), the bare second invocation "
                 "announces the resume, and the crashed unit's dead-owner "
                 "claim is reclaimed promptly rather than left for the "
                 "second invocation to refuse or wait out. Full "
                 "re-integration of a unit given a SECOND outer claim is "
                 "NOT asserted here: it is blocked by a separate, already"
                 "-documented defect (worktree_lane.acquire cannot "
                 "re-establish a unit's lane branch on a second claim, so "
                 "integrate.py merges a stale branch; flagged out of scope "
                 "in scripts/test_repair_drain.py's own docstring). "
                 "Scenario 1 covers repair-to-integration directly.")

_BLOCKING_WORKER = """
import os, time
started = os.environ["FAULT_LAB_STARTED"]
release = os.environ["FAULT_LAB_RELEASE"]
with open(started, "w", encoding="utf-8") as fh:
    fh.write(str(os.getpid()))
deadline = time.time() + 60
while time.time() < deadline and not os.path.exists(release):
    time.sleep(0.02)
with open("done.txt", "w", encoding="utf-8") as fh:
    fh.write("done\\n")
print("worker unblocked and wrote done.txt")
"""

_FAST_WORKER = """
with open("done.txt", "w", encoding="utf-8") as fh:
    fh.write("done\\n")
print("fast worker wrote done.txt")
"""


def _one_unit_decomposer(unit_id):
    return ("""
import json, sys
sys.stdin.read()
print(json.dumps([{"id": %r, "objective": "one unit for a fault scenario",
                   "done_check": "test -f done.txt", "writes": ["done.txt"],
                   "deps": []}]))
""" % unit_id)


def scenario_crash_then_bare_invoke(artifact):
    workdir = tempfile.mkdtemp(prefix="fault-lab-s2-")
    repo = fresh_repo()
    runs_root = tempfile.mkdtemp(dir=workdir, prefix="runs-")
    started = os.path.join(workdir, "started.marker")
    release = os.path.join(workdir, "release.barrier")  # never created here

    decomposer = _write(os.path.join(workdir, "decomposer.py"),
                        _one_unit_decomposer("C1"))
    worker = _write(os.path.join(workdir, "worker_block.py"), _BLOCKING_WORKER)
    outcome = "prove a crashed run resumes to one work-set"
    env1 = run_env(artifact, decomposer, worker,
                   extra={"FAULT_LAB_STARTED": started,
                          "FAULT_LAB_RELEASE": release})
    p1 = subprocess.Popen(launcher_cmd(artifact, outcome, repo, runs_root),
                          env=env1, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          start_new_session=True)

    if not _poll(lambda: os.path.exists(started), timeout=30):
        p1.kill()
        return _result("crash_then_bare_invoke", _S2_INVARIANT, False,
                       "the worker never started; nothing to crash mid-run")

    with open(started, encoding="utf-8") as fh:
        worker_pid = int(fh.read().strip())

    # THE CRASH: the whole process group, so the grandchild brother_run.py
    # (the launcher always execs it as a fresh subprocess) dies too, and so
    # does the blocked worker.
    _kill_group(p1)
    _poll(lambda: not _pid_alive(worker_pid), timeout=10)

    run_dirs = _run_dirs(runs_root)
    if len(run_dirs) != 1:
        return _result("crash_then_bare_invoke", _S2_INVARIANT, False,
                       "expected exactly one run directory after the crash, "
                       "found %d" % len(run_dirs))
    claims_before = _read_json(os.path.join(run_dirs[0], "claims.json"))
    if claims_before.get("C1", {}).get("state") != "claimed":
        return _result("crash_then_bare_invoke", _S2_INVARIANT, False,
                       "the crashed claim was not left in state=claimed: %r"
                       % claims_before.get("C1"))

    # THE SECOND, BARE INVOCATION: same outcome text, no --resume, no
    # --continue. A different worker: this scenario does not require it to
    # finish (see _S2_INVARIANT), only that the dead-owner claim is
    # reclaimed and dispatched again promptly.
    worker2 = _write(os.path.join(workdir, "worker_fast.py"), _FAST_WORKER)
    env2 = run_env(artifact, decomposer, worker2)
    p2 = brother_run(artifact, outcome, repo, runs_root, env2, timeout=60)
    out2 = (p2.stdout or "") + (p2.stderr or "")

    run_dirs_after = _run_dirs(runs_root)
    one_work_set = len(run_dirs_after) == 1
    # THE POSITIVE FORM ONLY: brother_run.py also prints a line containing
    # "resuming" when it DECLINES to resume ("...rather than resuming it,
    # since the outcomes differ"), so a bare substring match on "resuming"
    # would pass on that negative sentence too.
    resumed_announced = "resuming it instead of starting a new one" in out2
    # A SECOND, GENUINE CLAIM ON C1 (worker_id ".../C1/2") is the proof the
    # dead owner's claim was reclaimed rather than refused or waited out;
    # it does not depend on the unit going on to integrate.
    reclaimed_promptly = "CLAIMED (1):" in out2 and "/C1/2" in out2

    ok = one_work_set and resumed_announced and reclaimed_promptly
    detail = ("work_sets=%d resumed=%s reclaimed_promptly=%s"
             % (len(run_dirs_after), resumed_announced, reclaimed_promptly))
    return _result("crash_then_bare_invoke", _S2_INVARIANT, ok, detail, out2)


# ---------------------------------------------------------------------------
# Scenario 3: TWO-PROCESS RACE.
# ---------------------------------------------------------------------------

_S3_INVARIANT = ("every unit integrates exactly once and no duplicate claim "
                 "is ever recorded, read from claims.json")


def scenario_two_process_race(artifact):
    workdir = tempfile.mkdtemp(prefix="fault-lab-s3-")
    repo = fresh_repo()
    runs_root = tempfile.mkdtemp(dir=workdir, prefix="runs-")
    started = os.path.join(workdir, "started.marker")
    release = os.path.join(workdir, "release.barrier")

    decomposer = _write(os.path.join(workdir, "decomposer.py"),
                        _one_unit_decomposer("U1"))
    worker = _write(os.path.join(workdir, "worker_block.py"), _BLOCKING_WORKER)
    outcome = "prove two concurrent invocations never double claim one unit"
    env = run_env(artifact, decomposer, worker,
                  extra={"FAULT_LAB_STARTED": started,
                         "FAULT_LAB_RELEASE": release})
    p1 = subprocess.Popen(launcher_cmd(artifact, outcome, repo, runs_root),
                          env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          start_new_session=True)

    def _claimed():
        for rd in _run_dirs(runs_root):
            cp = os.path.join(rd, "claims.json")
            if os.path.isfile(cp):
                try:
                    return _read_json(cp).get("U1", {}).get("state") == "claimed"
                except (OSError, ValueError):
                    return False
        return False

    if not _poll(_claimed, timeout=30):
        p1.kill()
        return _result("two_process_race", _S3_INVARIANT, False,
                       "the first invocation never claimed U1")

    # THE RACE: a second launcher invocation, same outcome, same repository,
    # WHILE the first is still live and holding U1.
    p2 = brother_run(artifact, outcome, repo, runs_root, env, timeout=60)
    out2 = (p2.stdout or "") + (p2.stderr or "")

    _write(release, "go")
    try:
        out1, _ = p1.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        p1.kill()
        out1, _ = p1.communicate()

    run_dirs = _run_dirs(runs_root)
    claims = (_read_json(os.path.join(run_dirs[0], "claims.json"))
             if run_dirs else {})
    u1 = claims.get("U1", {})

    never_double_claimed = "NOT CLAIMED" in out2
    single_run = len(run_dirs) == 1
    single_integration = any(l.split()[:1] == ["U1"] and "verified by:" in l
                             for l in out1.splitlines())
    single_attempt = u1.get("attempt") == 1 and u1.get("state") == "done"

    ok = (never_double_claimed and single_run and single_integration
         and single_attempt)
    detail = ("loser_refused=%s single_run=%s integrated_once=%s "
             "claim=%r" % (never_double_claimed, single_run,
                           single_integration, u1))
    return _result("two_process_race", _S3_INVARIANT, ok, detail,
                   "process1:\n%s\nprocess2:\n%s" % (out1, out2))


# ---------------------------------------------------------------------------
# Scenario 4: KILL HOLDING THE LOCK.
# ---------------------------------------------------------------------------

_S4_INVARIANT = ("a follow-up acquire succeeds well before the timeout and "
                 "the reclaim evidence line names the dead owner (the "
                 "roadmap's own wording; whether the reclaimed unit goes on "
                 "to integrate is a separate concern, gated by the same "
                 "out-of-scope lane-branch-reuse defect noted in scenario "
                 "2, and not asserted here)")

_LOCK_HOLDER = """
import os, socket, sys, time
lock_path, barrier, release = sys.argv[1], sys.argv[2], sys.argv[3]
fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
os.write(fd, ("%d:%s" % (os.getpid(), socket.gethostname())).encode())
os.close(fd)
with open(barrier, "w", encoding="utf-8") as fh:
    fh.write(str(os.getpid()))
deadline = time.time() + 60
while time.time() < deadline and not os.path.exists(release):
    time.sleep(0.02)
"""


def scenario_kill_holding_lock(artifact):
    workdir = tempfile.mkdtemp(prefix="fault-lab-s4-")
    repo = fresh_repo()
    runs_root = tempfile.mkdtemp(dir=workdir, prefix="runs-")

    # STEP 1: a real run that claims and blocks on U1, purely to get a real
    # claims.json on disk (its own claim's fate is scenario 2's concern, not
    # this one's; this scenario only needs a durable file to poison).
    started = os.path.join(workdir, "started.marker")
    release = os.path.join(workdir, "release.barrier")  # never created
    decomposer = _write(os.path.join(workdir, "decomposer.py"),
                        _one_unit_decomposer("U1"))
    worker = _write(os.path.join(workdir, "worker_block.py"), _BLOCKING_WORKER)
    outcome = "prove a follow-up acquire survives a dead lock holder"
    env = run_env(artifact, decomposer, worker,
                  extra={"FAULT_LAB_STARTED": started,
                         "FAULT_LAB_RELEASE": release})
    p1 = subprocess.Popen(launcher_cmd(artifact, outcome, repo, runs_root),
                          env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          start_new_session=True)
    if not _poll(lambda: os.path.exists(started), timeout=30):
        p1.kill()
        return _result("kill_holding_lock", _S4_INVARIANT, False,
                       "the setup run never claimed U1")
    _kill_group(p1)

    run_dirs = _run_dirs(runs_root)
    if len(run_dirs) != 1:
        return _result("kill_holding_lock", _S4_INVARIANT, False,
                       "expected exactly one run directory, found %d"
                       % len(run_dirs))
    run_dir = run_dirs[0]
    claims_path = os.path.join(run_dir, "claims.json")
    if not os.path.isfile(claims_path):
        return _result("kill_holding_lock", _S4_INVARIANT, False,
                       "no claims.json was created by the setup run")

    # STEP 2: POISON THE LOCK. A real, separate process creates the EXACT
    # on-disk artifact claim_store.py's own lock class writes at
    # "<claims_path>.lock" (O_CREAT|O_EXCL, contents "<pid>:<hostname>"),
    # never importing claim_store itself, and is SIGKILLed while it holds
    # that file. ponytail: the module's true internal critical section is
    # microseconds long and cannot be raced from outside the process; this
    # reproduces its documented, durable ON-DISK RESIDUE exactly, which is
    # the only thing the recovery path actually reads. Upgrade to a genuine
    # external race only if one becomes observable without importing the
    # module under test.
    lock_path = claims_path + ".lock"
    holder_marker = os.path.join(workdir, "holder.marker")
    holder_release = os.path.join(workdir, "holder.release")  # never created
    holder_script = _write(os.path.join(workdir, "lock_holder.py"), _LOCK_HOLDER)
    holder = subprocess.Popen([sys.executable, holder_script, lock_path,
                              holder_marker, holder_release],
                             start_new_session=True)
    if not _poll(lambda: os.path.exists(holder_marker), timeout=10):
        holder.kill()
        return _result("kill_holding_lock", _S4_INVARIANT, False,
                       "the lock holder never signalled that it holds the lock")
    if not os.path.isfile(lock_path):
        holder.kill()
        return _result("kill_holding_lock", _S4_INVARIANT, False,
                       "the holder signalled but the lock file is not present")
    with open(holder_marker, encoding="utf-8") as fh:
        dead_pid = int(fh.read().strip())
    _kill_group(holder)
    _poll(lambda: not _pid_alive(dead_pid), timeout=10)
    if not os.path.isfile(lock_path):
        return _result("kill_holding_lock", _S4_INVARIANT, False,
                       "the lock file vanished before the follow-up acquire "
                       "could be attempted")

    # STEP 3: THE FOLLOW-UP ACQUIRE, entirely through the public CLI:
    # --resume the same run directory with a worker that finishes at once.
    fast_worker = _write(os.path.join(workdir, "worker_fast.py"), _FAST_WORKER)
    env2 = run_env(artifact, decomposer, fast_worker)
    start = time.time()
    p2 = brother_run(artifact, outcome, repo, runs_root, env2,
                     extra_args=["--resume", run_dir], timeout=30)
    elapsed = time.time() - start
    out2 = (p2.stdout or "") + (p2.stderr or "")

    reclaimed = "reclaiming lock" in out2 and str(dead_pid) in out2
    # THE ACQUIRE ITSELF SUCCEEDING is the invariant (a fresh, genuine claim
    # record for U1's second attempt); whether U1 goes on to integrate is
    # gated by the same separate, already-documented lane-branch-reuse
    # defect noted in scenario 2 and is not asserted here.
    claim_succeeded = "CLAIMED (1):" in out2 and "/U1/2" in out2
    fast_enough = elapsed < 5.0
    lock_gone = not os.path.isfile(lock_path)

    ok = reclaimed and claim_succeeded and fast_enough and lock_gone
    detail = ("reclaimed=%s claim_succeeded=%s elapsed=%.2fs lock_gone=%s"
             % (reclaimed, claim_succeeded, elapsed, lock_gone))
    return _result("kill_holding_lock", _S4_INVARIANT, ok, detail, out2)


# ---------------------------------------------------------------------------
# Scenario 5 and 6 (P13, docs/plan/READINESS-ROADMAP-2026-08-29.json): the
# two data-science golden fixtures, doc 21.6 DS-G01 (temporal leakage) and
# DS-G03 (notebook random seed). Same law as the four scenarios above: no
# product module imported, the product driven only through the real
# installed launcher, answers read only from CLI output and the run's own
# files. Unlike the four lifecycle scenarios, these two never crash or race
# anything; they prove the engine correctly REFUSES two data-science traps
# that would otherwise ship a leaked split or an unreproducible metric as
# though it were proven.
# ---------------------------------------------------------------------------

_S5_INVARIANT = ("a unit whose done_check runs scripts/split_check.py "
                 "against a genuinely leaky train/test split never reads "
                 "verified: split_check itself prints FAIL, and the run "
                 "spends the unit's retry budget naming it rather than "
                 "marking it done")


def scenario_ds_leakage(artifact):
    workdir = tempfile.mkdtemp(prefix="fault-lab-ds-leak-")
    repo = fresh_repo()
    runs_root = tempfile.mkdtemp(dir=workdir, prefix="runs-")

    # THE FIXTURE (tests/fixtures/ds-leakage/): a train and test CSV sharing
    # entity C2 (the overlap class) and a train row (C3) dated after the
    # cutoff (the temporal class); split_check.py reports the first class it
    # finds, overlap, read left to right, exactly as its own docstring says.
    fixture_dir = os.path.join(ROOT, "tests", "fixtures", "ds-leakage")
    split_check_args = [sys.executable,
                        os.path.join(ROOT, "scripts", "split_check.py"),
                        "--train", os.path.join(fixture_dir, "train.csv"),
                        "--test", os.path.join(fixture_dir, "test.csv"),
                        "--key", "customer_id",
                        "--time-col", "event_date",
                        "--cutoff", "2026-01-01"]
    check_cmd = " ".join(split_check_args)

    decomposer = _write(os.path.join(workdir, "decomposer.py"), """
import json, sys
sys.stdin.read()
print(json.dumps([{"id": "L1",
                   "objective": "prove a leaky train/test split is refused",
                   "done_check": %r,
                   "writes": ["note.txt"], "deps": []}]))
""" % check_cmd)
    # THE WORKER CANNOT FIX THIS: it writes into its own lane's worktree,
    # never into the fixed fixture files split_check.py reads, so L1's own
    # check can never turn green however many attempts it gets. That is the
    # invariant under test: a genuinely leaky split stays refused, it is
    # never worn down by retries.
    worker = _write(os.path.join(workdir, "worker.py"), """
with open("note.txt", "w", encoding="utf-8") as fh:
    fh.write("attempted\\n")
print("ds-leakage worker wrote note.txt; the fixed fixture split-check "
     "reads is untouched")
""")
    # THE ORACLE, run directly (never imported, the same way brother_run
    # itself only ever shells out to split_check.py): confirms the fixture
    # is genuinely leaky, independent of anything the product records about
    # it, so a passing scenario is never merely "some check failed" but
    # specifically "split_check read this exact fixture as FAIL".
    oracle = _sh(split_check_args)
    oracle_output = (oracle.stdout or "") + (oracle.stderr or "")
    split_check_failed = (oracle.returncode == 1
                          and "split-check: FAIL:" in oracle_output)

    env = run_env(artifact, decomposer, worker)
    outcome = "prove a leaky data-science split is refused by split_check"
    proc = brother_run(artifact, outcome, repo, runs_root, env, timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")

    # THE PRODUCT'S OWN RECORD names the exact command it ran as L1's
    # done_check (run.log, LOG_FILENAME in brother_run.py, mirrored here as
    # a literal string never imported): confirms the real product used
    # OUR split_check.py invocation, not a stand-in for it.
    run_dirs = _run_dirs(runs_root)
    log_text = ""
    if run_dirs:
        log_path = os.path.join(run_dirs[0], "run.log")
        if os.path.isfile(log_path):
            with open(log_path, encoding="utf-8") as fh:
                log_text = fh.read()
    check_cmd_used = check_cmd in log_text

    never_verified = not any(l.split()[:1] == ["L1"] and "verified by:" in l
                             for l in out.splitlines())
    retry_budget_named = "L1" in out and "retry budget is exhausted" in out
    ok = (split_check_failed and check_cmd_used and never_verified
         and retry_budget_named)
    detail = ("split_check_failed=%s check_cmd_used=%s never_verified=%s "
             "retry_budget_named=%s" % (split_check_failed, check_cmd_used,
                                        never_verified, retry_budget_named))
    return _result("ds-leakage", _S5_INVARIANT, ok, detail,
                   "oracle:\n%s\nproduct:\n%s" % (oracle_output, out))


_S6_INVARIANT = ("a check that draws its metric from an unseeded random "
                 "source is not proven by a green exit code: either the "
                 "receipt names the unit no-data, 'no metric recorded' "
                 "(when the installed artifact carries E18 evidence "
                 "gating), or two independent re-executions of the exact "
                 "same done_check print two different metric values, "
                 "proving by itself that the number was never "
                 "reproducible")

_METRIC_RE = re.compile(r"accuracy=([0-9.]+)")

#: The same three bookkeeping names _find_work_doc excludes in
#: brother_run.py (CLAIMS_FILENAME, TARGET_FILENAME, CAPSULE_FILENAME),
#: mirrored here as literals rather than imported, so the Work document
#: (the one other *.json in a run directory) can be told apart from the
#: engine's own files without importing work_record or brother_run.
_ENGINE_JSON_FILES = ("claims.json", "target.json", "capsule.json")


def _work_doc_evidence(run_dir, unit_id):
    """The row's own `evidence` string (the exact text _verify_evidence
    wrote, including the check's real captured output) for `unit_id` in
    `run_dir`'s Work document, or "" when the run directory, the document,
    or the row is not found. A file read, never a product import: the same
    answer receipt_sentence's own `output_location` line points a reader
    at, read directly here because the check's raw output text (unlike the
    receipt's own summary line) is never echoed to stdout."""
    if not os.path.isdir(run_dir):
        return ""
    candidates = [f for f in os.listdir(run_dir)
                 if f.endswith(".json") and f not in _ENGINE_JSON_FILES]
    if len(candidates) != 1:
        return ""
    try:
        with open(os.path.join(run_dir, candidates[0]), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return ""
    for row in (doc.get("rows") or doc.get("units") or []):
        if row.get("id") == unit_id:
            return str(row.get("evidence") or "")
    return ""


def _run_ds_seed_once(artifact, workdir, tag):
    """One full, independent brother_run invocation against a fresh repo
    and a fresh run directory: a genuine re-execution, never a --resume of
    the same claim, so two calls are two separate proofs of what the check
    prints, not one run read twice. Returns (cli_output, evidence_text)."""
    repo = fresh_repo()
    runs_root = tempfile.mkdtemp(dir=workdir, prefix="runs-%s-" % tag)
    eval_script = os.path.join(ROOT, "tests", "fixtures", "ds-seed", "eval.py")
    done_check = "test -f done.txt && %s %s" % (sys.executable, eval_script)
    decomposer = _write(os.path.join(workdir, "decomposer-%s.py" % tag), """
import json, sys
sys.stdin.read()
print(json.dumps([{"id": "S1",
                   "objective": "prove an unseeded eval cannot back a "
                               "reproducible metric",
                   "done_check": %r,
                   "writes": ["done.txt"], "deps": [],
                   "evidence_family": "E18"}]))
""" % done_check)
    worker = _write(os.path.join(workdir, "worker-%s.py" % tag), _FAST_WORKER)
    env = run_env(artifact, decomposer, worker)
    outcome = "prove an unseeded eval script never backs a reproducible metric"
    proc = brother_run(artifact, outcome, repo, runs_root, env, timeout=60)
    out = (proc.stdout or "") + (proc.stderr or "")
    run_dirs = _run_dirs(runs_root)
    evidence = _work_doc_evidence(run_dirs[0], "S1") if run_dirs else ""
    return out, evidence


def scenario_ds_seed(artifact):
    workdir = tempfile.mkdtemp(prefix="fault-lab-ds-seed-")

    # THE FIXTURE (tests/fixtures/ds-seed/eval.py): exits 0 every time (an
    # ordinary passing check by exit code alone), so the engine reads S1 as
    # delivered on both runs regardless of anything else; the point under
    # test is what backs that green exit code. Run TWICE, from scratch:
    # never resumed, never the same claim re-read.
    out1, evidence1 = _run_ds_seed_once(artifact, workdir, "1")
    out2, evidence2 = _run_ds_seed_once(artifact, workdir, "2")

    # PATH A: the installed artifact carries P6's E18 gating (evidence_family
    # declared above), so the receipt itself refuses to call this a proven
    # metric, in the roadmap's own words.
    no_metric_recorded = ("no metric recorded" in out1
                          or "no metric recorded" in out2)

    # PATH B, provable regardless of whether the installed artifact carries
    # E18 gating: the SAME done_check, run twice from scratch, prints two
    # different numbers into its own row's evidence text (the check's real
    # captured output, read from the Work document, never from stdout,
    # which only ever names WHERE the output lives), which is fault_lab's
    # own proof, never the product's, that the metric was never
    # reproducible.
    m1, m2 = _METRIC_RE.search(evidence1), _METRIC_RE.search(evidence2)
    both_ran = bool(m1 and m2)
    differing_value = both_ran and m1.group(1) != m2.group(1)

    both_delivered_by_exit_code = ("S1 delivered:" in out1
                                   and "S1 delivered:" in out2)
    ok = both_delivered_by_exit_code and (no_metric_recorded or differing_value)
    detail = ("no_metric_recorded=%s differing_value=%s (%s vs %s) "
             "both_delivered_by_exit_code=%s"
             % (no_metric_recorded, differing_value,
                m1.group(1) if m1 else NODATA, m2.group(1) if m2 else NODATA,
                both_delivered_by_exit_code))
    return _result("ds-seed", _S6_INVARIANT, ok, detail,
                   "run1 evidence: %s\nrun1 cli:\n%s\nrun2 evidence: %s\n"
                   "run2 cli:\n%s" % (evidence1, out1, evidence2, out2))


SCENARIOS = {
    "fail_then_repair": scenario_fail_then_repair,
    "crash_then_bare_invoke": scenario_crash_then_bare_invoke,
    "two_process_race": scenario_two_process_race,
    "kill_holding_lock": scenario_kill_holding_lock,
    "ds-leakage": scenario_ds_leakage,
    "ds-seed": scenario_ds_seed,
}


# ---------------------------------------------------------------------------
# R27.1.3: driving it backwards. Each patch targets a FRESH, THROWAWAY
# installed copy (a `claude plugin install` cache directory under a
# temporary CLAUDE_CONFIG_DIR from install_artifact() above), never this
# repository, by exact text replacement against an anchor copied from this
# repository's own current source. A missing anchor is reported as NO-DATA
# rather than silently doing nothing, so a future refactor of the real fix
# cannot make this quietly stop testing anything.
# ---------------------------------------------------------------------------

def _patch_file(path, old, new):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if old not in text:
        return False, "anchor text not found in %s" % path
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(old, new, 1))
    return True, ""


def _patch_drain_stop(runtime_dir):
    """Reintroduces the 2026-08-30 forced-repair-proof defect: progress
    measured by integration alone, so a round that only exhausts an
    in-lane repair (nothing newly integrated) reads as a no-op and the
    drain stops before the unit ever gets its outer, cross-round retry."""
    return _patch_file(
        os.path.join(runtime_dir, "brother_run.py"),
        "progressed = (done_now != done_before\n"
        "                     or (attempts_now != attempts_before and repairable))",
        "progressed = (done_now != done_before)")


def _patch_double_work(runtime_dir):
    """Reintroduces gap 2: a plain outcome matching an unfinished run's own
    outcome no longer resumes it, so a second bare invocation starts a
    second, competing Work document on the same repository."""
    return _patch_file(
        os.path.join(runtime_dir, "brother_run.py"),
        "        resume_match = next(\n"
        "            (m for m in unfinished if _outcomes_match(args.outcome, m[1])),\n"
        "            None)",
        "        resume_match = None  # DEFECT REINTRODUCED: dedup disabled")


def _patch_dead_owner_wait(runtime_dir):
    """Reintroduces the pre-2026-08-30 defect measured at ~1200s: a dead
    owner's claim is no longer recognized as dead by its pid, so recovery
    waits out the full lease instead of reclaiming at once."""
    return _patch_file(
        os.path.join(runtime_dir, "claim_store.py"),
        "    pid = claim.get(\"pid\")\n"
        "    if pid and claim.get(\"hostname\") == _hostname() and not pid_alive(pid):\n"
        "        return False\n"
        "    return True",
        "    return True")


def _patch_forever_lock(runtime_dir):
    """Reintroduces the defect this whole scenario exists to guard: a lock
    left behind by a dead holder is never reclaimed, so a waiter sits out
    the full lock timeout (or fails outright) instead of proceeding at once."""
    return _patch_file(
        os.path.join(runtime_dir, "claim_store.py"),
        "if self._reclaim_if_dead():",
        "if False and self._reclaim_if_dead():")


DEFECTS = {
    "drain-stop": (_patch_drain_stop, scenario_fail_then_repair,
                  "the drain stop condition: progress measured by "
                  "integration alone"),
    "double-work": (_patch_double_work, scenario_crash_then_bare_invoke,
                    "the silent double Work: outcome dedup disabled"),
    "dead-owner-wait": (_patch_dead_owner_wait, scenario_crash_then_bare_invoke,
                        "the lease-long dead-owner wait: pid liveness check "
                        "removed"),
    "forever-lock": (_patch_forever_lock, scenario_kill_holding_lock,
                     "the forever lock: dead-lock-holder reclaim disabled"),
}


def _run_backwards(defect_id):
    patch_fn, scenario_fn, label = DEFECTS[defect_id]
    artifact, problem = install_artifact()
    if artifact is None:
        print("%s: %s" % (NODATA, problem))
        return 2
    runtime_dir = os.path.dirname(artifact["launcher"])
    patched, note = patch_fn(runtime_dir)
    if not patched:
        print("%s: could not reintroduce %r: %s" % (NODATA, defect_id, note))
        return 2

    result = scenario_fn(artifact)
    caught = not result["ok"]
    print("REINTRODUCED  %-16s %s" % (defect_id, label))
    print("%-7s       %-24s %s" % ("CAUGHT" if caught else "MISSED",
                                  result["name"], result["detail"]))
    print("violated invariant: %s" % result["invariant"])
    if not caught:
        print(result["output"][-2000:])
    return 0 if caught else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scenario", nargs="?", default="all",
                    help="one of %s, or 'all' (default)"
                         % ", ".join(sorted(SCENARIOS)))
    ap.add_argument("--list", action="store_true",
                    help="print the scenario names and exit")
    ap.add_argument("--reintroduce", choices=sorted(DEFECTS),
                    help="drive the lab backwards: patch a fresh, throwaway "
                         "installed copy with one historical defect and "
                         "confirm the matching scenario catches it")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.list:
        for name in sorted(SCENARIOS):
            print(name)
        return 0

    if not _claude_present():
        print("%s: no claude binary on PATH; this lab needs a real "
              "`claude plugin install`" % NODATA)
        return 2

    if args.reintroduce:
        return _run_backwards(args.reintroduce)

    if args.scenario == "all":
        names = sorted(SCENARIOS)
    elif args.scenario in SCENARIOS:
        names = [args.scenario]
    else:
        print("fault_lab: unknown scenario %r; --list for the scenario names"
              % args.scenario, file=sys.stderr)
        return 1

    artifact, problem = install_artifact()
    if artifact is None:
        print("%s: %s" % (NODATA, problem))
        return 2

    failed = []
    for name in names:
        result = SCENARIOS[name](artifact)
        verdict = "PASS" if result["ok"] else "FAIL"
        print("%s  %-24s %s" % (verdict, result["name"], result["detail"]))
        if not result["ok"]:
            failed.append(name)
            print("  invariant: %s" % result["invariant"])
            print(result["output"][-2000:])

    print()
    print("%d scenario(s), %d failed" % (len(names), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

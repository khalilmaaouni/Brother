#!/usr/bin/env python3
"""Acceptance test for capability area 2: interrupt and redirect without
losing state (G1-M3.5 of docs/plan/READINESS-ROADMAP-2026-08-29.json,
node G1-M3, following the template G1-M3.3 left behind).

Area 2's own definition (docs/plan/CAPABILITY-AREAS.json): a contributor
starts a task, interrupts it mid-way with a new instruction, and checks
that the original state and partial progress are not silently discarded.
It fails when the redirect drops earlier context, restarts from scratch,
or corrupts the in-progress work instead of resuming it.

THE REAL MACHINERY UNDER TEST is this estate's own claim/reconcile spine
(scripts/claim_store.py), the exact piece the directive built to survive a
session dying mid-unit: CLAIM BEFORE SPAWN, an exclusive lease that expires
rather than lasting forever, and a reconcile that REPORTS an abandoned
claim rather than silently forgetting it (claim_store.reconcile's own
docstring: "This is the crash-recovery seam. It NEVER acts ... because
deciding that a dead session's unit may be retried is a judgement"). This
test drives exactly that real API against a real repository, then proves
the redirected attempt actually lands a change via scripts/loop_bridge.py,
the same real spine area 1 drives.

REAL REPOSITORY, NOT A FIXTURE: a git repository actually initialized in a
temp directory, same pattern as scripts/acceptance_1.py.

THE SHAPE, reusing area 1's template with one addition it had nothing to
say about yet: state that must survive an interruption is proven by
reading it back through the SAME real persistence the tool uses (the
claims file on disk), not by trusting a return value.

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      the interrupted claim was visible in reconcile, the redirect
               was recorded as a reclaim (not a silent restart), and the
               redirected work actually landed
  1  FAIL      any of the above did not hold
  2  NO-DATA   the spine's own machinery is not present in this checkout

Usage: python3 scripts/acceptance_2.py [--explain] [--calibrate]
--calibrate forces this test red by deleting the claim store between the
interruption and the reconcile call, which is the mechanical shape of
"the redirect drops earlier context": the state that must have survived
is made to vanish on purpose, and this test passes its own calibration
only if it correctly reports that as a failure.

origin: invoked directly as its own CLI (main(), below, `python3
scripts/acceptance_2.py [--explain] [--calibrate]`, this file's own line
38), by a human or a CI runner checking capability area 2. It is also
reached through scripts/acceptance.py's run_area(), which subprocess.run()s
this script path when someone runs `python3 scripts/acceptance.py --area
2` (scripts/acceptance.py, run_area around lines 55-61), the same dispatch
every sibling acceptance_N.py in this suite goes through. Nothing else
calls into this module (verified: grep -rl acceptance_2 scripts
bundle/runtime finds only scripts/test_acceptance.py, a test harness, and
this file itself).

PRODUCER: this module is the sole producer of every file it writes
directly. build_real_repo()'s seed README.md is written inline (lines
92-93, a plain open(...,"w") plus fh.write("seed\n")), and
build_scripted_worker() writes the redirected worker script (lines
101-108). Both live inside the tempfile.TemporaryDirectory opened at line
138 and are deleted when that with-block exits. The claim store itself
(claims.json, line 140) is written by scripts/claim_store.py's own
acquire()/reconcile(), called from this module (lines 117-119, 163,
183-184) rather than written here directly; this module's own writes are
only the seed repo and worker script named above.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP_BRIDGE = os.path.join(HERE, "loop_bridge.py")

# ponytail: a short real TTL plus a real sleep past it, rather than a mocked
# clock. claim_store.acquire/reconcile both take an optional `clock` callable,
# so a fake clock was available; a real one is simpler and the actual delay is
# a few hundred milliseconds, well inside a acceptance test's normal budget.
INTERRUPT_TTL_SECONDS = 0.15
INTERRUPT_WAIT_SECONDS = 0.5
TIME_BUDGET_SECONDS = 30.0

TEMPLATE = """area 2 template addition to G1-M3.3's shape:
  - state that must survive an interruption is read back through the SAME
    real persistence the tool uses (claim_store's own file on disk), never
    through a return value or an in-memory object the test still holds
  - the interrupt is a real lease expiry (a short TTL plus a real sleep
    past it), not a mocked clock, because the property under test is what
    a second, later process sees on disk
  - the redirect is proven twice: once as a RECORD (reclaimed_from names
    the abandoned owner, so the takeover is never silent) and once as an
    OUTCOME (the redirected work actually lands via the real spine)
What area 1's shape got wrong that this corrects: nothing did; area 1 had
no interruption to model. What this area adds for the next ones: proving
"not silently discarded" needs a positive check (reconcile SEES it), not
only the absence of a crash."""


def sh(args, cwd=None, timeout=60):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def build_real_repo(tmp):
    """A real git repository the tool has never worked in before."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "acceptance-test"]):
        sh(["git"] + args, repo)
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "-m", "seed"], repo)
    return repo


def build_scripted_worker(tmp, filename):
    """The redirected unit's own small change: write `filename` and commit."""
    worker = os.path.join(tmp, "worker.sh")
    with open(worker, "w", encoding="utf-8") as fh:
        fh.write(
            "#!/bin/sh\n"
            "cat >/dev/null\n"
            "echo 'redirected work landed' > {}\n"
            "git add -A && git commit -qm 'redirected small change'\n"
            .format(filename))
    os.chmod(worker, 0o755)
    return worker


def _start_then_interrupt(claim_store, store, unit_id, first_owner):
    """'Starts a task, interrupts it mid-way': a real exclusive claim is
    taken and then never released, and a real lease expiry stands in for
    the crash (a session that died mid-unit does not release either)."""
    claim, problem = claim_store.acquire(store, unit_id, first_owner,
                                         work_id="W-interrupt-redirect",
                                         ttl=INTERRUPT_TTL_SECONDS)
    if claim is None:
        return None, "NO-DATA: could not even start the task: %s" % problem
    time.sleep(INTERRUPT_WAIT_SECONDS)
    return claim, ""


def _run(explain, drop_state_before_reconcile):
    if not os.path.exists(LOOP_BRIDGE):
        return 2, "NO-DATA: scripts/loop_bridge.py is not present in this checkout"

    sys.path.insert(0, HERE)
    try:
        import claim_store
        import work_record as wr
    except ImportError as exc:
        return 2, "NO-DATA: could not import the claim/work spine: %s" % exc

    prefix = "acceptance-2-calibrate-" if drop_state_before_reconcile else "acceptance-2-"
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        repo = build_real_repo(tmp)
        store = os.path.join(tmp, "claims.json")
        artifact = os.path.join(repo, "redirected.txt")
        worker = build_scripted_worker(tmp, "redirected.txt")

        start = time.monotonic()

        # 1. "Starts a task": a real exclusive claim, taken by session-a.
        first_claim, problem = _start_then_interrupt(claim_store, store,
                                                      "U1", "session-a")
        if first_claim is None:
            return 2, problem

        # THE FORCED BAD STATE (--calibrate only): delete the claim store
        # between the interruption and the reconcile call, the mechanical
        # shape of "the redirect drops earlier context" -- the state that
        # must have survived is made to vanish on purpose.
        if drop_state_before_reconcile:
            os.remove(store)

        # 2. "Checks that the original state and partial progress are not
        # silently discarded": reconcile must SEE the abandoned claim, by
        # name, with its former owner. A positive check, not an absence of
        # a crash.
        findings, problem = claim_store.reconcile(store)
        if findings is None:
            return 1, ("FAIL: reconcile could not read the claim store after "
                       "the interruption (%s): the interrupted state is not "
                       "recoverable, which is exactly what must not happen"
                       % problem)
        seen = next((f for f in findings if f["unit_id"] == "U1"), None)
        if seen is None or seen.get("status") != "abandoned":
            return 1, ("FAIL: the interrupted claim on U1 was not visible in "
                       "reconcile (findings=%s): the original state was "
                       "silently discarded" % findings)
        if seen.get("owner") != "session-a":
            return 1, ("FAIL: reconcile reported the abandoned claim under "
                       "owner %r, not the session that actually started it"
                       % seen.get("owner"))

        # 3. "Interrupts it mid-way with a new instruction": a second
        # session redirects onto the same unit. This must succeed (a dead
        # session must not block forever) and must RECORD the takeover
        # rather than silently restarting as if nothing had happened.
        redirect_claim, problem = claim_store.acquire(
            store, "U1", "session-b", work_id="W-interrupt-redirect", ttl=60)
        if redirect_claim is None:
            return 1, ("FAIL: the redirect could not even take the unit over "
                       "(%s), so an interrupted session blocks all further "
                       "progress" % problem)
        if redirect_claim.get("reclaimed_from") != "session-a":
            return 1, ("FAIL: the redirect landed but did not record what it "
                       "took over from (reclaimed_from=%r): a silent restart "
                       "is indistinguishable from a resumed one"
                       % redirect_claim.get("reclaimed_from"))

        if explain:
            print(TEMPLATE)

        # 4. The redirected instruction actually lands, through the same
        # real spine area 1 drives, under the new owner and the SAME claim
        # store (proving the takeover is not just bookkeeping).
        record, problems = wr.create(
            "the redirected instruction lands after an interrupted attempt",
            [{"id": "U1", "done_check": "test -f redirected.txt",
              "owns": ["redirected.txt"]}],
            store=os.path.join(tmp, "wr-store"))
        if problems:
            return 2, "NO-DATA: could not build a work_record plan: %s" % problems

        proc = sh([sys.executable, LOOP_BRIDGE,
                   "--plan", record["path"], "--claims", store,
                   "--owner", "session-b", "--cwd", repo,
                   # harness pins slots so the test does not measure free disk
                   "--slots", "2",
                   "--worker-cmd", "sh", worker],
                  timeout=int(TIME_BUDGET_SECONDS) + 30)
        elapsed = time.monotonic() - start
        landed = os.path.isfile(artifact)
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        last = tail[-1] if tail else "(no output)"

        if proc.returncode != 0:
            return 1, ("FAIL: the redirect was recorded but the redirected "
                       "work never ran: loop_bridge exited %d: %s"
                       % (proc.returncode, last))
        if not landed:
            return 1, ("FAIL: loop_bridge exited 0 but %s never landed"
                       % artifact)
        if elapsed > TIME_BUDGET_SECONDS:
            return 1, ("FAIL: the redirect landed but took %.2fs, over the "
                       "%.0fs budget" % (elapsed, TIME_BUDGET_SECONDS))
        return 0, ("PASS: an interrupted claim was seen by reconcile "
                   "(owner=session-a), the redirect recorded reclaimed_from "
                   "correctly, and the redirected work landed in %.2fs "
                   "(budget %.0fs), artifact=%s"
                   % (elapsed, TIME_BUDGET_SECONDS, artifact))


def run(explain=False):
    return _run(explain, drop_state_before_reconcile=False)


def calibrate():
    """G1-M3.5.2: force this test red once. Deletes the claim store between
    the interruption and the reconcile call -- the state that must survive
    an interruption is made to vanish on purpose -- and passes only if this
    test correctly reads that as a failure rather than as an empty, clean
    reconcile."""
    code, evidence = _run(explain=False, drop_state_before_reconcile=True)
    if code == 1:
        return 0, ("PASS: calibration deleted the claim store between the "
                   "interruption and reconcile (simulating dropped state) "
                   "and this test correctly read it as failed (%s): a "
                   "green reading of this test means something" % evidence)
    if code == 2:
        return 1, ("FAIL: calibration could not run at all (%s), so nothing "
                   "was proven about this test's ability to fail" % evidence)
    return 1, ("FAIL: calibration could not force this test red (got %s): a "
               "green reading of this test would be decoration" % evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 2: interrupt and "
                    "redirect without losing state.")
    parser.add_argument("--explain", action="store_true",
                        help="also print the template this area leaves behind")
    parser.add_argument("--calibrate", action="store_true",
                        help="prove this test can fail, instead of running it")
    args = parser.parse_args(argv)
    if args.calibrate:
        code, evidence = calibrate()
    else:
        code, evidence = run(explain=args.explain)
    print(evidence)
    return code


if __name__ == "__main__":
    sys.exit(main())

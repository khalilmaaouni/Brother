#!/usr/bin/env python3
"""Acceptance test for capability area 1: time to first useful action
(G1-M3.3 of docs/plan/READINESS-ROADMAP-2026-08-29.json, node G1-M3).

Area 1's own definition (docs/plan/CAPABILITY-AREAS.json): a contributor
points the tool at a real repository it has never seen and asks for one
small, correct change, then times how long until something useful actually
lands. It fails when the change does not land inside a fixed time budget,
or the tool needed a manual step the test did not script.

REAL REPOSITORY, NOT A FIXTURE: a git repository actually initialized in a
temp directory, exactly the pattern scripts/test_spine.py already uses and
proves works end to end. "Not a fixture" means no mocked filesystem and no
stubbed objects, not that the directory must be permanent.

THE TOOL UNDER TEST is this estate's own real command line: scripts/
loop_bridge.py, the same spine that takes a canonical Work document
(scripts/work_record.py) and a real worker command from "claimed" to
"merged onto canonical" with no manual step between. Driving the real
scheduler is the whole point; a mocked scheduler would prove nothing about
whether a change actually lands.

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      the change landed, correct, inside the time budget
  1  FAIL      it did not land, landed wrong, or missed the budget
  2  NO-DATA   the spine's own machinery is not present in this checkout

Usage: python3 scripts/acceptance_1.py [--explain] [--calibrate]
--explain also prints the template this shape leaves for the ten areas
after it (G1-M3.3.2). --calibrate (G1-M3.4.2) forces this test red once,
using loop_bridge's own --null-worker flag (claims and releases without
doing work, standard library only, no network), and passes only if this
test correctly reports that forced failure as FAIL: a green reading of
this test is decoration until it has been shown capable of going red.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP_BRIDGE = os.path.join(HERE, "loop_bridge.py")

# ponytail: a generous, fixed budget rather than a measured baseline. The
# machinery under test is a scripted shell worker plus git, which finishes
# in well under a second on this machine; raise this only if a slower CI
# runner makes it flaky, never to hide a real regression.
TIME_BUDGET_SECONDS = 30.0

TEMPLATE = """area 1 template, for the ten areas after it to reuse:
  - a REAL repository: git init in a temp directory, not a mock object
  - a scripted worker (a shell script, no human in the loop) that performs
    the one small change the area asks for
  - the estate's own real command line (loop_bridge.py) drives claim to
    merge; the test never open-codes the scheduler
  - the done_check is re-verified against the file the worker actually
    produced on disk, never against the worker's claimed exit code alone
  - a fixed wall-clock budget, generous enough that only a genuine miss
    trips it
What this shape got wrong on first contact: nothing yet; this is the
first area run, so there is nothing prior to compare against."""


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


def build_scripted_worker(tmp):
    """The 'one small, correct change': add greeting.txt and commit it.
    Scripted end to end, so no manual step is needed to make it run."""
    worker = os.path.join(tmp, "worker.sh")
    with open(worker, "w", encoding="utf-8") as fh:
        fh.write(
            "#!/bin/sh\n"
            "cat >/dev/null\n"
            "echo 'hello from the tool' > greeting.txt\n"
            "git add -A && git commit -qm 'one small change'\n")
    os.chmod(worker, 0o755)
    return worker


def run(explain=False):
    """(exit_code, evidence_line)."""
    if not os.path.exists(LOOP_BRIDGE):
        return 2, "NO-DATA: scripts/loop_bridge.py is not present in this checkout"

    sys.path.insert(0, HERE)
    import work_record as wr  # local import: only needed on the real path

    with tempfile.TemporaryDirectory(prefix="acceptance-1-") as tmp:
        repo = build_real_repo(tmp)
        worker = build_scripted_worker(tmp)
        artifact = os.path.join(repo, "greeting.txt")

        record, problems = wr.create(
            "one small, correct change lands on a real repository",
            [{"id": "U1", "done_check": "test -f greeting.txt",
              "owns": ["greeting.txt"]}],
            store=os.path.join(tmp, "wr-store"))
        if problems:
            return 2, "NO-DATA: could not build a work_record plan: %s" % problems

        claims = os.path.join(tmp, "claims.json")
        start = time.monotonic()
        proc = sh([sys.executable, LOOP_BRIDGE,
                   "--plan", record["path"], "--claims", claims,
                   "--owner", "acceptance-1", "--cwd", repo,
                   # harness pins slots so the test does not measure free disk
                   "--slots", "2",
                   "--worker-cmd", "sh", worker],
                  timeout=int(TIME_BUDGET_SECONDS) + 30)
        elapsed = time.monotonic() - start

        if explain:
            print(TEMPLATE)

        landed = os.path.isfile(artifact)
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        last = tail[-1] if tail else "(no output)"

        if proc.returncode != 0:
            return 1, ("FAIL: loop_bridge exited %d after %.2fs: %s"
                       % (proc.returncode, elapsed, last))
        if not landed:
            return 1, ("FAIL: loop_bridge exited 0 but %s never landed"
                       % artifact)
        if elapsed > TIME_BUDGET_SECONDS:
            return 1, ("FAIL: landed correctly but took %.2fs, over the "
                       "%.0fs budget" % (elapsed, TIME_BUDGET_SECONDS))
        return 0, ("PASS: one small change landed in %.2fs (budget %.0fs), "
                   "artifact=%s" % (elapsed, TIME_BUDGET_SECONDS, artifact))


def calibrate():
    """G1-M3.4.2: force this test red once, so a green reading means
    something. Runs the same real repo and spine as run(), but claims the
    unit with loop_bridge's own --null-worker (claims and releases without
    doing work), which guarantees greeting.txt is never produced. PASSES
    only if that forced failure is correctly read as a failure here."""
    if not os.path.exists(LOOP_BRIDGE):
        return 2, "NO-DATA: scripts/loop_bridge.py is not present in this checkout"

    sys.path.insert(0, HERE)
    import work_record as wr

    with tempfile.TemporaryDirectory(prefix="acceptance-1-calibrate-") as tmp:
        repo = build_real_repo(tmp)
        artifact = os.path.join(repo, "greeting.txt")

        record, problems = wr.create(
            "a deliberately unworked unit, to prove this test can fail",
            [{"id": "U1", "done_check": "test -f greeting.txt",
              "owns": ["greeting.txt"]}],
            store=os.path.join(tmp, "wr-store"))
        if problems:
            return 2, "NO-DATA: could not build a work_record plan: %s" % problems

        claims = os.path.join(tmp, "claims.json")
        proc = sh([sys.executable, LOOP_BRIDGE,
                   "--plan", record["path"], "--claims", claims,
                   "--owner", "acceptance-1-calibrate", "--cwd", repo,
                   # harness pins slots so the test does not measure free disk
                   "--slots", "2",
                   "--null-worker", "--max-attempts", "1"], timeout=60)
        landed = os.path.isfile(artifact)

        if proc.returncode != 0 and not landed:
            return 0, ("PASS: calibration forced a broken run (--null-worker) "
                       "and this test correctly read it as failed "
                       "(loop_bridge exit %d, artifact never landed): a "
                       "green reading of this test means something"
                       % proc.returncode)
        return 1, ("FAIL: calibration could not force this test red "
                   "(loop_bridge exit %d, landed=%s): a green reading of "
                   "this test would be decoration" % (proc.returncode, landed))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 1: time to first "
                    "useful action.")
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

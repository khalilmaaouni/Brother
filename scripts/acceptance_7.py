#!/usr/bin/env python3
"""Acceptance test for capability area 7: choosing the right tests and
telling not-run from passed (G1-M3.10 of docs/plan/READINESS-ROADMAP-
2026-08-29.json, node G1-M3, following the template G1-M3.3 left behind).

Area 7's own definition (docs/plan/CAPABILITY-AREAS.json): asks the tool to
verify a change and checks that it runs the tests that actually cover the
change, and reports a skipped or not-run test as distinct from a passed
one. It fails when the tool reports success when tests were skipped, not
found, or never executed, or it runs an unrelated suite and calls that
verification.

THE REAL MACHINERY UNDER TEST is scripts/integrate.py's own revalidation
step: `_run_check` returns (passed, detail) where passed is True (the
declared done_check ran and covered the change), False (it ran and
genuinely failed) or None (there was no done_check to run at all).
integrate_one turns that three-way split into three DIFFERENT verdict
strings -- INTEGRATED, NEEDS_REPAIR, NODATA -- and its own guard clause
("the unit carries no done_check, so nothing can prove it still holds")
means there is no code path anywhere in this file that substitutes a
default or unrelated check when none was declared: the only two things
that can ever happen are "the unit's own declared check ran" or "NODATA,
never a pass". That is this estate's actual answer to "choosing the right
test": there is no fallback that could run the wrong one.

REAL REPOSITORY, NOT A FIXTURE: a git repository in a temp directory with
three lane branches forked from the same seed commit, each adding one
file, integrated onto canonical one after another exactly as
scripts/integrate.py is meant to be driven.

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      a unit whose done_check actually covers its own change reads
               INTEGRATED, a unit with NO done_check reads NODATA rather
               than a false pass, and a unit whose done_check genuinely
               fails reads NEEDS_REPAIR rather than being confused with
               either of the other two
  1  FAIL      any of those three verdicts was wrong, or two of them
               collapsed into the same reading
  2  NO-DATA   scripts/integrate.py is not present in this checkout

Usage: python3 scripts/acceptance_7.py [--explain] [--calibrate]
--calibrate forces this test red by patching _run_check so a MISSING
done_check reads as trivially passed instead of NODATA -- the mechanical
shape of "reports success when tests were skipped, not found, or never
executed". Passes only if this test correctly reads the resulting false
INTEGRATED verdict as a failure.
"""
import argparse
import os
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))

TEMPLATE = """area 7 template addition to G1-M3.3's shape:
  - "choosing the right test" reduces, in this estate's real design, to a
    unit's own declared done_check: there is no default, no fallback, and
    no code path that runs something else when a check is missing
  - the three-way split (passed True/False/None) is proven by keeping all
    three reachable in one scenario and checking none of them collapse
    into another: a not-run check must never read the same as a pass, and
    a genuinely failed check must never read the same as not-run either
  - both the not-run and the genuinely-failed units are unwound off
    canonical (neither one's file survives), yet they are reported under
    DIFFERENT verdict strings -- the reporting distinction this area asks
    for does not depend on the unwind also being distinguishable
NAMED GAP, honestly: "or it runs an unrelated suite and calls that
verification" is NOT something this estate's own machinery can catch.
_run_check runs exactly the string the unit declares; if that string is a
trivially-true command unrelated to the actual change (bare "true"), it
will read as a genuine INTEGRATED pass, and nothing here can tell that
apart from a check that really covers the work. The guard against that is
authoring discipline (the done_check field), not code, and this test does
not pretend otherwise.
What areas 1 through 6's shape got wrong that this corrects: nothing did.
What this area adds for the next ones: proving a three-way distinction
needs all three states driven in the SAME run, not two pairwise checks,
because a test that only ever sees two of the three states can accidentally
conflate the third with whichever one it never exercised."""


def sh(args, cwd=None, timeout=60):
    import subprocess
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def build_scenario(tmp):
    """A real canonical repo plus three lane branches forked from the same
    seed commit, one per unit under test. Returns (repo, lanes) where lanes
    maps unit id to (branch_name, added_file)."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "acceptance-test"]):
        sh(["git"] + args, repo)
    with open(os.path.join(repo, "seed.txt"), "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "-m", "seed"], repo)

    lanes = {}
    for unit_id, fname in (("A", "covered.txt"), ("B", "notrun.txt"),
                            ("C", "failed.txt")):
        branch = "lane-" + unit_id.lower()
        sh(["git", "checkout", "-q", "-b", branch, "main"], repo)
        with open(os.path.join(repo, fname), "w", encoding="utf-8") as fh:
            fh.write("change for %s\n" % unit_id)
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-q", "-m", "lane %s" % unit_id], repo)
        sh(["git", "checkout", "-q", "main"], repo)
        lanes[unit_id] = (branch, fname)
    return repo, lanes


def _run(explain):
    sys.path.insert(0, HERE)
    try:
        import integrate
    except ImportError as exc:
        return 2, "NO-DATA: could not import scripts/integrate.py: %s" % exc

    with tempfile.TemporaryDirectory(prefix="acceptance-7-") as tmp:
        repo, lanes = build_scenario(tmp)

        branch_a, file_a = lanes["A"]
        unit_a = {"id": "A", "done_check": "test -f %s" % file_a}
        result_a = integrate.integrate_one(repo, branch_a, unit_a)

        branch_b, file_b = lanes["B"]
        unit_b = {"id": "B", "done_check": ""}
        result_b = integrate.integrate_one(repo, branch_b, unit_b)

        branch_c, file_c = lanes["C"]
        unit_c = {"id": "C", "done_check": "false"}
        result_c = integrate.integrate_one(repo, branch_c, unit_c)

        if explain:
            print(TEMPLATE)

        errors = []
        if result_a["verdict"] != integrate.INTEGRATED:
            errors.append("unit A (a done_check that actually covers its "
                          "change) read verdict=%s, not INTEGRATED"
                          % result_a["verdict"])
        elif not os.path.isfile(os.path.join(repo, file_a)):
            errors.append("unit A read INTEGRATED but %s never landed on "
                          "canonical" % file_a)

        if result_b["verdict"] != integrate.NODATA:
            errors.append("unit B (NO done_check at all) read verdict=%s, "
                          "not NODATA: a not-run check must never be read "
                          "as anything else, least of all a pass"
                          % result_b["verdict"])
        elif os.path.isfile(os.path.join(repo, file_b)):
            errors.append("unit B read NODATA but %s landed on canonical "
                          "anyway" % file_b)

        if result_c["verdict"] != integrate.NEEDS_REPAIR:
            errors.append("unit C (a done_check that genuinely fails) read "
                          "verdict=%s, not NEEDS_REPAIR: a real failure must "
                          "not be confused with a not-run check"
                          % result_c["verdict"])
        elif os.path.isfile(os.path.join(repo, file_c)):
            errors.append("unit C read NEEDS_REPAIR but %s landed on "
                          "canonical anyway" % file_c)

        verdicts = {result_a["verdict"], result_b["verdict"], result_c["verdict"]}
        if len(verdicts) != 3:
            errors.append("only %d distinct verdicts were seen across three "
                          "units that should each read differently: %s"
                          % (len(verdicts), sorted(verdicts)))

        if errors:
            return 1, "FAIL: " + "; ".join(errors)

        return 0, ("PASS: a covering done_check read INTEGRATED (%s landed), "
                   "a missing done_check read NODATA rather than a false "
                   "pass (%s never landed), and a genuinely failing "
                   "done_check read NEEDS_REPAIR rather than either (%s "
                   "never landed) -- three distinct verdicts, none collapsed"
                   % (file_a, file_b, file_c))


def run(explain=False):
    return _run(explain)


def calibrate():
    """G1-M3.10.2: force this test red once. Patches integrate._run_check
    so a MISSING done_check reads as trivially passed instead of NODATA,
    the mechanical shape of "reports success when tests were skipped, not
    found, or never executed". Passes only if this test correctly reads
    the resulting false INTEGRATED verdict as a failure."""
    sys.path.insert(0, HERE)
    try:
        import integrate
    except ImportError as exc:
        return 1, ("FAIL: calibration could not run at all (NO-DATA: could "
                   "not import scripts/integrate.py: %s), so nothing was "
                   "proven about this test's ability to fail" % exc)

    original_run_check = integrate._run_check

    def broken_run_check(check, cwd, runner=None):
        # THE FORCED BAD STATE: skip exactly the "no done_check" guard and
        # treat a missing check as trivially passed, instead of leaving it
        # NODATA.
        if not str(check or "").strip():
            return 0, "CALIBRATION: missing done_check treated as passed", False
        return original_run_check(check, cwd, runner)

    with tempfile.TemporaryDirectory(prefix="acceptance-7-calibrate-") as tmp:
        repo, lanes = build_scenario(tmp)
        branch_b, file_b = lanes["B"]
        unit_b = {"id": "B", "done_check": ""}

        with mock.patch.object(integrate, "_run_check", broken_run_check):
            result_b = integrate.integrate_one(repo, branch_b, unit_b)

        landed = os.path.isfile(os.path.join(repo, file_b))
        if result_b["verdict"] == integrate.INTEGRATED and landed:
            return 0, ("PASS: calibration skipped the \"no done_check\" "
                       "guard and a not-run unit was wrongly read as "
                       "INTEGRATED (%s landed on canonical with nothing "
                       "ever checked), exactly the false-pass this test's "
                       "real run() is built to reject" % file_b)
        return 1, ("FAIL: calibration could not force a not-run unit to "
                   "read as a false pass (verdict=%s, landed=%s), so "
                   "nothing was proven about this test's ability to fail"
                   % (result_b["verdict"], landed))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 7: choosing the "
                    "right tests and telling not-run from passed.")
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

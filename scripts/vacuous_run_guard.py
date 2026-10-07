#!/usr/bin/env python3
"""A unittest run that proved nothing must not exit 0.

WHY THIS FILE EXISTS, measured 2026-09-21 on this branch. Unit R4 ("no test
reads or spends real machine state") owns two suites and carries this
done_check:

    env -i PATH="$PATH" HOME=$(mktemp -d) python3 scripts/test_jev_checks.py &&
    env -i PATH="$PATH" HOME=$(mktemp -d) python3 scripts/test_jev_seam.py

Both suites decorate EVERY TestCase class with one skipUnless, so a single
missing input skips the whole suite. Relocating the tree one directory away
(no sibling data/) was enough: the run printed "OK (skipped=48)" and exited
0, and the same happened for the other suite at 99. The chain above reads
that as proof. R4's recorded evidence on the repository of record is, in its
own words, "OK (skipped=48) and OK (skipped=99)": a run in which nothing ran,
filed as the proof that something held.

WHY THE FIX BELONGS HERE AND NOT IN THE RUNNER. check_all.sh already refuses
this: its run_check demotes a skip whose reason says NO-DATA to a NO-DATA
verdict rather than a PASS, and it invokes both suites with -v so the reasons
are visible. So the control existed, and it existed in ONE CALLER. Every
other caller (a done_check chain, a hand run, a release cut session recording
evidence) lost it. A verdict that depends on which runner wrapped the check is
a statement about the invocation, not about the subject. Putting the guard in
the suite makes the verdict a property of the run itself, and check_all.sh's
own logic then agrees with it rather than being the only thing holding it.

THE TWO POPULATIONS ARE DIFFERENT, and that is why this is not a contradiction
of run_check's rule. run_check judges a SUBSET skipping: some tests ran, so the
run proved something, and only a NO-DATA reason demotes it. This guard judges
the WHOLE suite skipping: nothing ran, so nothing was proved, whatever the
reason said. An all-skipped run is refused here even when every reason is a
deliberate skip, because the caller is about to record it as evidence.

FAIL DIRECTION. Every unknown refuses. A result object that cannot be read,
a negative or missing count, a skip list that is not a list: all return 1 with
NO-DATA, never 0. Nothing here can turn an unreadable run into a pass.
"""
import sys
import unittest


def classify(tests_run, skipped_count, was_successful, reasons=()):
    """Return (exit_code, verdict_line). Pure, so it can be tested without a suite.

    Kept separate from run() on purpose: the interesting behaviour is the
    decision, and a decision reachable only by building a real TestResult is a
    decision nobody writes the awkward cases for.
    """
    # A non integer or negative count is a corrupt reading, not a small one.
    try:
        tests_run = int(tests_run)
        skipped_count = int(skipped_count)
    except (TypeError, ValueError):
        return 1, "NO-DATA: the run's own counts could not be read, so this run proves nothing"
    if tests_run < 0 or skipped_count < 0 or skipped_count > tests_run:
        return 1, ("NO-DATA: counts are not consistent (ran %r, skipped %r), so this run proves nothing"
                   % (tests_run, skipped_count))

    if not was_successful:
        # A real failure already decides the exit code; say so plainly and do
        # not let the skip arithmetic below dress it up as a NO-DATA.
        return 1, "FAILED: at least one test failed or errored"

    if tests_run == 0:
        return 1, "NO-DATA: no test ran at all, so this run proves nothing"

    if skipped_count == tests_run:
        detail = ""
        try:
            uniq = sorted({str(r)[:90] for r in reasons if r})
            if uniq:
                detail = "; reason(s): " + " | ".join(uniq[:3])
        except TypeError:
            detail = "; reason(s) unreadable"
        return 1, ("NO-DATA: all %d test(s) skipped, so this run proves nothing%s"
                   % (tests_run, detail))

    if skipped_count:
        # A subset skipped. The run still proved something, so it passes, but
        # the count is printed because a reader recording evidence should see
        # how much of the suite was actually exercised.
        return 0, "OK: %d test(s) ran, %d skipped" % (tests_run - skipped_count, skipped_count)
    return 0, "OK: %d test(s) ran" % tests_run


def run(module="__main__"):
    """Drop-in for unittest.main() in a suite's __main__ block.

    Calls sys.exit itself so the caller's last line stays `run()`.
    """
    prog = unittest.main(module=module, exit=False)
    result = getattr(prog, "result", None)
    if result is None:
        print("NO-DATA: unittest returned no result object, so this run proves nothing")
        sys.exit(1)
    skipped = getattr(result, "skipped", [])
    reasons = []
    try:
        reasons = [entry[1] for entry in skipped]
    except (TypeError, IndexError, KeyError):
        reasons = []
    code, line = classify(getattr(result, "testsRun", None), len(skipped),
                          result.wasSuccessful(), reasons)
    print(line)
    sys.exit(code)


def selftest():
    cases = [
        ("a clean full run passes", classify(48, 0, True) == (0, "OK: 48 test(s) ran")),
        ("a partial skip still passes and says how much ran",
         classify(48, 6, True)[0] == 0 and "42 test(s) ran, 6 skipped" in classify(48, 6, True)[1]),
        ("the whole suite skipped is refused",
         classify(48, 48, True)[0] == 1),
        ("the whole suite skipped says NO-DATA, never OK",
         classify(48, 48, True)[1].startswith("NO-DATA")),
        ("a deliberate all skip is refused too, reason notwithstanding",
         classify(3, 3, True, ["on purpose"])[0] == 1),
        ("the refusal carries the reason a reader needs",
         "registry" in classify(2, 2, True, ["NO-DATA: registry missing"])[1]),
        ("exactly one test, skipped, is refused", classify(1, 1, True)[0] == 1),
        ("exactly one test, run, passes", classify(1, 0, True)[0] == 0),
        ("an empty suite is refused", classify(0, 0, True)[0] == 1),
        ("an empty suite says NO-DATA", classify(0, 0, True)[1].startswith("NO-DATA")),
        ("a real failure is a FAILED, not a NO-DATA",
         classify(48, 0, False)[1].startswith("FAILED")),
        ("a real failure wins over an all skipped reading",
         classify(48, 48, False) == (1, "FAILED: at least one test failed or errored")),
        ("a non integer count is refused", classify("many", 0, True)[0] == 1),
        ("a None count is refused", classify(None, 0, True)[0] == 1),
        ("a negative count is refused", classify(-1, 0, True)[0] == 1),
        ("more skipped than ran is refused", classify(2, 5, True)[0] == 1),
        ("unreadable reasons do not crash the refusal", classify(2, 2, True, 7)[0] == 1),
        ("a reason that is None is dropped, not printed",
         "None" not in classify(2, 2, True, [None, "real one"])[1]),
    ]
    bad = [name for name, ok in cases if not ok]
    if bad:
        print("selftest: FAILED: " + ", ".join(bad))
        return 1
    print("selftest: %d cases, OK" % len(cases))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print("usage: import this module and call run() from a suite's __main__ block")
    sys.exit(2)

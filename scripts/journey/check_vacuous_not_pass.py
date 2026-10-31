#!/usr/bin/env python3
"""J2's C.vacuous-single-unit-not-pass row: a unit whose test is `assert True` must not read PASS.

Until this existed the row carried only `measured_by: PROOF1` and no check, so it read "the row
declares no check, so nothing measured it" and the PROOF1 unit could never close however well its
four sub units were built. The same shape as J1's D.six-verbs-only before
scripts/journey/check_six_verbs_only.py: a unit's claim and a row's measurement are different
things.

WHAT IT MEASURES, and why it is the INSTALLED door rather than this repository's. PROOF1.a and
PROOF1.b put two readers in scripts/receipt_door.py, `self_revert_gap` and `zero_tests_gap`, and
scripts/test_proof_can_fail.py exercises both DIRECTLY. Neither was reachable from a verdict: the
chain in `receipts_for` called dependency_gap, e18_gap, numbers_gap and red_check_gap and nothing
else, so a hollow green still printed PASS with both functions sitting green in their own suite.
A reader nothing calls enforces nothing, so this drives `receipts_for` itself, in the tree a
stranger installs.

BOTH DIRECTIONS, DELIBERATELY. Three probes, not one:

  hollow   a one unit row whose check still exits 0 with its own change reverted  must NOT be PASS
  zero     a row whose captured log says the check collected no tests at all     must NOT be PASS
  real     a row whose check FAILS with its own change reverted                  must STILL be PASS

The third is what makes the other two worth anything. A door that refused every green would
satisfy a one directional check perfectly, and would be useless.

WHERE IT LOOKS. The journey walker exports CLAUDE_CONFIG_DIR for the throwaway sandbox it
installed into, so the door is found BY NAME under there, the same rule check_six_verbs_only.py
follows. Its own runtime directory goes on sys.path because the door imports five siblings from
beside itself.

FAIL DIRECTION. An absent or unimportable door is NO-DATA, exit 2, never a pass: a verdict nobody
could reach has not been shown to be honest.

    python3 scripts/journey/check_vacuous_not_pass.py
    python3 scripts/journey/check_vacuous_not_pass.py --selftest

EXIT 0 PASS, 1 FAIL, 2 NO-DATA.
"""
import argparse
import os
import sys

DOOR = "receipt_door.py"
#: The exact phrases PROOF1 put in the door for the two hollow readings. Read back here rather
#: than re-worded, so a rename in the door fails this row instead of silently passing it.
PASSED_ANYWAY = "would have passed anyway"
ZERO_TESTS = "zero tests ran"
#: A row reaches the verified branch only once every earlier reason a PASS could be hollow is
#: cleared, so each probe carries the facts those branches read: measured-False precheck, a
#: non-empty per-unit file list, a command, and a captured exit 0.
CHECK = "python3 -m pytest -q"
CHANGED = ["mathlib.py"]


def _row(unit, self_exit, stderr="", output=None):
    """One Work document row: a one unit task, DONE, green, with its own self revert stamped."""
    row = {"id": unit, "status": "DONE", "done_check": CHECK,
           "check_passed_before": False, "files_changed_by_unit": list(CHANGED),
           "check_without_dependencies": [
               {"unit": unit, "kind": "self", "files": list(CHANGED),
                "revision": "deadbeefcafe", "exit_code": self_exit,
                "stderr": stderr, "note": ""}]}
    if output is not None:
        row["check_output"] = output
    return row


def _claims(unit):
    return {unit: {"evidence": {"check_command": CHECK, "exit_code": 0}}}


def _state(door, row):
    """The one receipt `receipts_for` writes for `row`, or None when it wrote none."""
    got = door.receipts_for({"rows": [row]}, _claims(row["id"]), [])
    return got[0] if got else None


def installed_door(config):
    """(the imported door module, "") or (None, the reason it could not be read)."""
    if not config:
        return None, ("no CLAUDE_CONFIG_DIR in the environment, so there is no installed door to "
                      "reach a verdict with")
    if not os.path.isdir(config):
        return None, "%s is not a directory, so no install can be read" % config
    found = ""
    for base, dirs, files in os.walk(config):
        if DOOR in files:
            found = base
            break
        dirs[:] = [d for d in dirs if not d.startswith(".")]
    if not found:
        return None, ("no %s under the installed config, so the verdict a stranger's run would "
                      "reach cannot be exercised" % DOOR)
    sys.path.insert(0, found)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("installed_receipt_door",
                                                      os.path.join(found, DOOR))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as exc:                                    # noqa: BLE001
        return None, "%s could not be imported: %s: %s" % (DOOR, type(exc).__name__, exc)
    if not hasattr(mod, "receipts_for"):
        return None, "the installed %s has no receipts_for, so no verdict can be reached" % DOOR
    return mod, ""


def judge(door):
    """(exit code, one line). Pure in the door, so the selftest judges this and not a copy."""
    hollow = _state(door, _row("U-hollow", 0))
    zero = _state(door, _row("U-zero", 1, "AssertionError: 2 != 3", "Ran 0 tests in 0.000s\n\nOK\n"))
    real = _state(door, _row("U-real", 1, "AssertionError: 2 != 3"))
    if hollow is None or zero is None or real is None:
        return 2, ("NO-DATA: the installed door wrote no receipt for one of the three probes, so "
                   "nothing was measured about its verdicts")
    bad = []
    if str(hollow.get("state")) == "verified":
        bad.append("a check that still passes with its own change reverted read PASS")
    elif PASSED_ANYWAY not in str(hollow.get("reason")):
        bad.append("the hollow green was refused but not for the recorded reason %r: %r"
                   % (PASSED_ANYWAY, str(hollow.get("reason"))[:120]))
    if str(zero.get("state")) == "verified":
        bad.append("a check that collected no tests at all read PASS")
    elif ZERO_TESTS not in str(zero.get("reason")):
        bad.append("the zero test green was refused but not for the recorded reason %r: %r"
                   % (ZERO_TESTS, str(zero.get("reason"))[:120]))
    if str(real.get("state")) != "verified":
        bad.append("a check that FAILS with its own change reverted lost its PASS (%s: %s), which "
                   "would turn every real proof in the estate into NO-DATA"
                   % (real.get("state"), str(real.get("reason"))[:120]))
    if bad:
        return 1, "FAIL: " + "; ".join(bad)
    return 0, ("PASS: the installed door refuses a one unit green whose check passes anyway (%s) "
               "and one that ran no tests (%s), and keeps the PASS where the check really fails"
               % (PASSED_ANYWAY, ZERO_TESTS))


def selftest():
    """Six cases over stub doors, so each branch of judge is shown able to answer on its own."""
    class Stub(object):
        def __init__(self, verdicts):
            self.verdicts = verdicts

        def receipts_for(self, record, claims, refused):
            uid = record["rows"][0]["id"]
            got = self.verdicts.get(uid)
            return [] if got is None else [got]

    honest = {"U-hollow": {"state": "no-data", "reason": "%s: it does not exercise the work"
                                                         % PASSED_ANYWAY},
              "U-zero": {"state": "no-data", "reason": "%s: the check collected no tests at all"
                                                       % ZERO_TESTS},
              "U-real": {"state": "verified", "reason": ""}}
    cases = []

    def case(label, verdicts, want):
        got, line = judge(Stub(verdicts))
        cases.append((label, want, got, got == want, line))

    def swap(uid, value):
        out = dict(honest)
        out[uid] = value
        return out

    case("an honest door is PASS", honest, 0)
    case("a hollow green left verified is FAIL", swap("U-hollow", {"state": "verified"}), 1)
    case("a hollow green refused for another reason is FAIL",
         swap("U-hollow", {"state": "no-data", "reason": "some other reason"}), 1)
    case("a zero test green left verified is FAIL", swap("U-zero", {"state": "verified"}), 1)
    case("a real pass downgraded is FAIL",
         swap("U-real", {"state": "no-data", "reason": "refused everything"}), 1)
    case("a door that writes no receipt is NO-DATA", swap("U-real", None), 2)

    bad = 0
    for label, want, got, ok, line in cases:
        bad += 0 if ok else 1
        print("%-52s want %d got %d %s" % (label, want, got, "ok" if ok else "MISMATCH"))
        if not ok and line:
            print("      " + line)
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.environ.get("CLAUDE_CONFIG_DIR", ""),
                    help="the installed sandbox's config dir (default: CLAUDE_CONFIG_DIR)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    door, why = installed_door(args.config)
    if door is None:
        print("NO-DATA: " + why)
        return 2
    code, line = judge(door)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

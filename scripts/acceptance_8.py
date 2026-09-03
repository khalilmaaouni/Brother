#!/usr/bin/env python3
"""Acceptance test for capability area 8: safety without approval fatigue
(G1-M3.11 of docs/plan/READINESS-ROADMAP-2026-08-29.json, node G1-M3,
following the template G1-M3.3 left behind).

Area 8's own definition (docs/plan/CAPABILITY-AREAS.json): runs a normal
session of many small safe actions end to end and counts how many required
a manual approval versus how many a reasonable policy would have let
through. It fails when routine safe actions are gated as often as
dangerous ones, training the contributor to rubber-stamp every prompt, or
a genuinely dangerous action is let through with no gate at all.

THE REAL MACHINERY UNDER TEST is scripts/scope_audit.py's own quarantine
path: it compares what a unit actually changed, via git, against what it
declared it would change, and reports CLEAN (a reasonable policy needs no
human here) or QUARANTINE (held for a person, per its own docstring:
"quarantine a result that wrote outside its declaration rather than
merging it"). A reasonable approval policy asks for review on QUARANTINE
alone, never on CLEAN, so counting quarantines across a real session IS
counting how many actions required a manual approval.

REAL REPOSITORY, NOT A FIXTURE: a git repository in a temp directory
carrying a real session of separate commits, most of them small and
staying exactly inside their own declaration (the routine, safe majority),
and exactly one that declares a narrow scope but also writes a file
outside it (the genuinely dangerous one: an undeclared write is precisely
what this estate treats as needing a human).

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      none of the routine safe actions were quarantined (no
               approval fatigue) and the one genuinely dangerous action
               was quarantined (no gate-free dangerous action)
  1  FAIL      a routine safe action was quarantined, or the dangerous
               action was not
  2  NO-DATA   scripts/scope_audit.py is not present in this checkout

Usage: python3 scripts/acceptance_8.py [--explain] [--calibrate]
--calibrate forces this test red by skipping the audit call entirely for
the dangerous action (a policy shortcut that never runs the check on it at
all), the mechanical shape of "a genuinely dangerous action is let through
with no gate at all". Passes only if this test correctly reads the
resulting zero-quarantine count as a failure.
"""
import argparse
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

SAFE_ACTION_COUNT = 12

TEMPLATE = """area 8 template addition to G1-M3.3's shape:
  - "manual approval" is modelled as scope_audit's QUARANTINE verdict,
    because that is this estate's actual answer to when a human is asked:
    CLEAN needs nobody, QUARANTINE is held for a person, and NO-DATA is
    neither (an undeclared unit should never have been dispatched at all)
  - the session is a real sequence of separate commits, not one big diff,
    because "counts how many required approval" is a property of the
    WHOLE session, not of any single action
  - the dangerous action is modelled honestly: its OWN declaration stays
    narrow, exactly like every safe action's declaration, and only its
    actual write reaches outside it. A dangerous action that declares
    itself dangerous would not test approval fatigue at all
What areas 1 through 7's shape got wrong that this corrects: nothing did.
What this area adds for the next ones: a session-level property (a rate
across many actions) needs the whole session driven for real, not one
action generalised; a single quarantine proves the gate exists, but only
counting across many proves it does not also fire on everything else."""


def sh(args, cwd=None, timeout=60):
    import subprocess
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def build_session(tmp):
    """A real repo with SAFE_ACTION_COUNT routine commits, each touching
    exactly the one file it declares, plus one dangerous commit that
    declares a narrow scope but also writes outside it. Returns
    (repo, safe_units, dangerous_unit) where each unit is
    (unit_dict, before_sha, after_sha)."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "acceptance-test"]):
        sh(["git"] + args, repo)
    os.makedirs(os.path.join(repo, "safe"))
    os.makedirs(os.path.join(repo, "shared"))
    with open(os.path.join(repo, "shared", "critical.py"), "w",
              encoding="utf-8") as fh:
        fh.write("# shared, nobody's declared scope\nVALUE = 0\n")
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "-m", "seed"], repo)

    def tip():
        return sh(["git", "rev-parse", "HEAD"], repo).stdout.strip()

    safe_units = []
    for i in range(SAFE_ACTION_COUNT):
        rel = "safe/file_%d.txt" % i
        before = tip()
        with open(os.path.join(repo, rel), "w", encoding="utf-8") as fh:
            fh.write("routine change %d\n" % i)
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-q", "-m", "routine change %d" % i], repo)
        unit = {"unit_id": "safe-%d" % i, "write_scope": [rel]}
        safe_units.append((unit, before, tip()))

    # THE DANGEROUS ACTION: a narrow, honest-looking declaration (exactly
    # like every safe action above), but the real commit also touches a
    # file its declaration never named.
    before = tip()
    dangerous_rel = "safe/file_dangerous.txt"
    with open(os.path.join(repo, dangerous_rel), "w", encoding="utf-8") as fh:
        fh.write("looks routine\n")
    with open(os.path.join(repo, "shared", "critical.py"), "a",
              encoding="utf-8") as fh:
        fh.write("VALUE = 1  # undeclared write\n")
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "-m", "looks routine, writes outside scope"], repo)
    dangerous_unit = ({"unit_id": "dangerous", "write_scope": [dangerous_rel]},
                      before, tip())

    return repo, safe_units, dangerous_unit


def evaluate(safe_quarantined, dangerous_quarantined):
    """(ok, reason). Shared by run() and calibrate() so both read the same
    policy: zero approvals on the routine majority, exactly one on the
    genuinely dangerous action."""
    if safe_quarantined:
        return False, ("%d routine safe action(s) were quarantined: %s. "
                       "Gating routine work trains a contributor to "
                       "rubber-stamp every prompt"
                       % (len(safe_quarantined), safe_quarantined))
    if not dangerous_quarantined:
        return False, ("the genuinely dangerous action (an undeclared "
                       "write outside its own scope) was never quarantined: "
                       "it was let through with no gate at all")
    return True, ("0 of %d routine actions required approval and the one "
                  "genuinely dangerous action was quarantined"
                  % SAFE_ACTION_COUNT)


def _run(explain, skip_dangerous_audit):
    sys.path.insert(0, HERE)
    try:
        import scope_audit
    except ImportError as exc:
        return 2, "NO-DATA: could not import scripts/scope_audit.py: %s" % exc

    prefix = "acceptance-8-calibrate-" if skip_dangerous_audit else "acceptance-8-"
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        repo, safe_units, dangerous_unit = build_session(tmp)

        safe_quarantined = []
        for unit, before, after in safe_units:
            verdict, detail = scope_audit.audit(unit, before, after, cwd=repo)
            if verdict == scope_audit.QUARANTINE:
                safe_quarantined.append(unit["unit_id"])
            elif verdict != scope_audit.CLEAN:
                return 2, ("NO-DATA: could not audit %s: %s"
                          % (unit["unit_id"], detail.get("reason", detail)))

        dunit, dbefore, dafter = dangerous_unit
        if skip_dangerous_audit:
            # THE FORCED BAD STATE (--calibrate only): a policy shortcut
            # that never runs the audit on the dangerous action at all,
            # the mechanical shape of "let through with no gate at all".
            dangerous_quarantined = False
            dangerous_detail = "CALIBRATION: audit skipped for this unit entirely"
        else:
            verdict, detail = scope_audit.audit(dunit, dbefore, dafter, cwd=repo)
            if verdict == scope_audit.NO_DATA:
                return 2, "NO-DATA: could not audit the dangerous unit: %s" % detail.get("reason", detail)
            dangerous_quarantined = verdict == scope_audit.QUARANTINE
            dangerous_detail = detail.get("reason", "")

        if explain:
            print(TEMPLATE)

        ok, reason = evaluate(safe_quarantined, dangerous_quarantined)
        if ok:
            return 0, "PASS: %s (%s)" % (reason, dangerous_detail)
        return 1, "FAIL: %s" % reason


def run(explain=False):
    return _run(explain, skip_dangerous_audit=False)


def calibrate():
    """G1-M3.11.2: force this test red once. Skips the audit call for the
    dangerous action entirely -- a policy shortcut that never checks it --
    the mechanical shape of "a genuinely dangerous action is let through
    with no gate at all". Passes only if this test correctly reads the
    resulting zero-quarantine count as a failure."""
    code, evidence = _run(explain=False, skip_dangerous_audit=True)
    if code == 1:
        return 0, ("PASS: calibration skipped the audit for the dangerous "
                   "action entirely and this test correctly read the "
                   "resulting gate-free dangerous action as failed (%s): a "
                   "green reading of this test means something" % evidence)
    if code == 2:
        return 1, ("FAIL: calibration could not run at all (%s), so nothing "
                   "was proven about this test's ability to fail" % evidence)
    return 1, ("FAIL: calibration could not force this test red (got %s): a "
               "green reading of this test would be decoration" % evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 8: safety without "
                    "approval fatigue.")
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

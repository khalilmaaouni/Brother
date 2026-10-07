#!/usr/bin/env python3
"""An UNEXPECTED RAISE inside a selftest must still print a verdict a human can read.

THE DEFECT, measured 2026-09-21. Every selftest in scripts/loop builds its case list EAGERLY:

    cases = [("a thing holds", some_expression_evaluated_right_now), ...]

so a single expression that raises takes the whole run with it. The function then dies with a
traceback and NEVER PRINTS A VERDICT, while exiting 1. One is the exact exit code an honest
failure returns, which is what makes this class dangerous rather than merely untidy: the pipeline
reading the exit code and the human reading the text describe the same run differently, and the
human is handed a stack trace where the verdict belongs. Confirmed on loop_report.py first, then
on loop_heartbeat.py, stage_log.py, commit_scan.py and model_call.py, all four at exit 1 with
`RuntimeError` as the last line.

HOW THIS DIFFERS FROM scripts/test_verdict_matches_exit.py, its sibling, which must be read first.
That file breaks a property the module DELIBERATELY checks (a guard the cases already cover) and
asserts the run prints FAILED and exits non zero. It measures whether a FALSE ANSWER is reported
honestly. This file injects something no case checks at all, a RAISE, and asserts the run still
reports rather than aborts. It measures whether an ABANDONED QUESTION is reported honestly. A
module can pass either one and fail the other: loop_report.py passed the sibling on the day it
crashed here, because a module whose guards all work can still be killed by an expression beside
them. Neither file subsumes the other and neither should grow the other's mutation table.

THE SUBJECTS ARE DISCOVERED FROM THE TREE, never from a list kept here. A list is a thing someone
has to remember to extend, and the whole point of a class detector is that the module added
tomorrow is covered by nobody remembering anything. Any scripts/loop module that answers
--selftest is a subject the moment it lands.

UNMEASURED IS NOT A PASS. A module with no injectable anchor, or one that is not green as shipped
(so a red after injection could not be attributed to the injection), is reported UNMEASURED by
name and counted against the run. Silence about a module nobody could measure is how a detector
becomes decoration.

Run: python3 scripts/test_selftests_report_rather_than_crash.py
"""
import os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")

# The anchor is the case list itself, so the injected raise sits exactly where the real defect
# lives: inside an eagerly evaluated case expression, not merely somewhere in the function. A
# module that builds its cases another way is UNMEASURED rather than injected in some other spot,
# because a raise in a different place would be a different question from the one being asked.
ANCHOR = "    cases = ["
INJECTED = ('    cases = [("an injected raise stands in for the expression nobody expected to '
            'raise", (_ for _ in ()).throw(RuntimeError("injected by the crash detector"))),')

VERDICT = re.compile(r"\bFAILED\b|\bFAIL\b")
TRACEBACK = "Traceback (most recent call last)"
OK_WORDS = re.compile(r"\bOK\b|\bPASS\b|0 finding")


def run(path, cwd=None):
    """Exit code AND text together. Never through a pipe: an exit code read after a pipe is the
    pipe's, and this estate misread three that way in one evening."""
    try:
        r = subprocess.run([sys.executable, path, "--selftest"],
                           capture_output=True, text=True, timeout=300, cwd=cwd)
    except subprocess.SubprocessError as exc:
        return None, str(exc)[:160]
    return r.returncode, r.stdout + r.stderr


def subjects():
    """Every scripts/loop module that answers --selftest, read off the tree."""
    out = []
    for name in sorted(os.listdir(LOOP)):
        if not name.endswith(".py"):
            continue
        body = open(os.path.join(LOOP, name), encoding="utf-8").read()
        if '"--selftest"' in body or "'--selftest'" in body:
            out.append(name)
    return out


def check(name):
    """None = UNMEASURED, False = crashes without a verdict, True = reports one."""
    body = open(os.path.join(LOOP, name), encoding="utf-8").read()
    if ANCHOR not in body:
        return None, "%s: no eagerly built case list to inject into, so this subject is UNMEASURED" % name

    tmp = tempfile.mkdtemp()
    try:
        # THE WHOLE scripts/ TREE, not only loop/. A module reads its siblings by a path relative
        # to itself, so copying only loop/ leaves it importing the REAL tree while this file
        # mutates a copy, and every injection then appears to survive. That two copy trap caught
        # this estate four times in one night, in three different files, and it always looks
        # exactly like the code being fine.
        shutil.copytree(HERE, os.path.join(tmp, "scripts"))
        target = os.path.join(tmp, "scripts", "loop", name)

        # As shipped first. A module already red for its own reasons cannot tell us what the
        # injection did, so it is UNMEASURED rather than counted as either answer.
        code_ok, text_ok = run(target)
        if code_ok != 0 or not OK_WORDS.search(text_ok):
            return None, ("%s: not green as shipped (exit %s), so a red after injection could not "
                          "be attributed to the injection. UNMEASURED" % (name, code_ok))

        with open(target, "w", encoding="utf-8") as fh:
            fh.write(body.replace(ANCHOR, INJECTED, 1))
        code, text = run(target)

        # TWO GUARDS, ONE BAD STATE EACH, so a fixture can isolate either. An earlier draft had a
        # third that refused any traceback in the output, and no fixture could tell it apart from
        # the verdict guard: a crashing module trips both at once, which proves neither. The
        # traceback now only sharpens the message it was going to produce anyway.
        if code == 0:
            return False, ("%s: a raising case exited 0, so an abandoned question reads as a pass"
                           % name)
        if not VERDICT.search(text):
            return False, ("%s: exited %s after a raising case without printing a verdict a human "
                           "can read%s. The pipeline reads that code as an ordinary failure while "
                           "the human gets a stack trace where the verdict belongs"
                           % (name, code, ", its output ending in a traceback"
                              if TRACEBACK in text else ""))
        return True, "%s: a raising case prints a verdict and exits %s" % (name, code)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    rows = [(n,) + check(n) for n in subjects()]
    if not rows:
        print("NO-DATA: no scripts/loop module answers --selftest, so nothing was measured here.")
        return 2
    # NOT APPLICABLE IS NOT NO-DATA, and collapsing them made this check unable to ever pass.
    # A module with no eagerly built case list cannot exhibit the defect being hunted, so there is
    # nothing to measure and nothing missing: that is NOT APPLICABLE and it does not block.
    # A module that was already red as shipped is different: the answer is genuinely unknown, so it
    # stays UNMEASURED and it DOES block, because an unknown must never read as a pass.
    bad = [r for r in rows if r[1] is False]
    na = [r for r in rows if r[1] is None and "no eagerly built case list" in r[2]]
    unmeasured = [r for r in rows if r[1] is None and r not in na]
    for name, ok, why in rows:
        if ok is None:
            label = "NOT-APPL" if "no eagerly built case list" in why else "UNMEASURED"
        else:
            label = "ok" if ok else "CRASHES"
        print("  %-10s %s" % (label, why))
    print()
    if bad or unmeasured:
        print("%d selftest(s) abandon the question instead of answering it, %d unmeasured, out of "
              "%d. A run whose verdict and exit code disagree has told the pipeline and the "
              "human two different things about it." % (len(bad), len(unmeasured), len(rows)))
        return 1
    print("PASS: all %d measurable selftest(s) report a readable verdict when a case raises"
          "%s" % (len(rows) - len(na), ", %d not applicable" % len(na) if na else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

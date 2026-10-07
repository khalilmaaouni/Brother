#!/usr/bin/env python3
"""A check's PRINTED verdict and its EXIT CODE must agree, verified from outside.

THE GAP THIS CLOSES, named by a mutation sweep on 2026-09-21 and not closable from inside. Every
selftest in this estate ends:

    return 1 if bad else 0

Inverting that line survives every mutation sweep, because a check cannot detect the inversion of
its own exit code BY RUNNING ITSELF: it reports its cases correctly, prints FAILED, and then exits
0, and the sweep that runs it sees exit 0 and calls the mutation survived. The sweep is not wrong;
it is structurally incapable of seeing this, the same way a scale cannot weigh itself.

So the check has to come from outside, and this is it. For each module it runs the selftest twice:
once as shipped, and once with a deliberately broken property, and it asserts the PAIR agrees.

  as shipped        prints OK or an equivalent, and exits 0
  deliberately red  prints FAILED, and exits NON ZERO

An inverted exit code breaks the second pair and this file says so. That is the whole idea: the
verdict is in the text, the gate is in the code, and nothing but an outside reader can confirm the
two say the same thing.

WHY THE TEXT AND NOT ONLY THE CODE. A gate that exits non zero while printing OK is just as broken
as one that exits 0 while printing FAILED, and the second is the dangerous direction: a pipeline
reads the code, a human reads the text, and they would disagree about the same run.

Run: python3 scripts/test_verdict_matches_exit.py
"""
import os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")

#: module, and a mutation that MUST make its selftest fail. Each is a property the module's own
#: cases already cover, so this file never needs to know why it fails, only that it does.
#: THE CHECK MUST BE THE ONE THAT COVERS THE PROPERTY, not merely the module's own --selftest.
#: Measured while writing this file: land_batch.py --selftest covers four old helpers and NONE of
#: the seven decision functions, which live in a separate suite. Mutating a decision function and
#: running the wrong check reported "a broken property did not fail", which was true of that check
#: and false of the code. A verifier that names the wrong check measures nothing, so each subject
#: names its own.
SUBJECTS = [
    ("loop_heartbeat.py", None, 'return "ALARM", "no heartbeat could be read', 'return "PRODUCING", "no heartbeat could be read'),
    ("model_router.py", None, "if m[\"transport\"] in THIRD_PARTY and m[\"privacy\"] != PUBLIC:", "if False:"),
    # FX-31.5: the empty answer guard moved into the adapters' judge; the property the selftest still covers in this
    # file is that the adapter's verdict is the attempt's, so the anchor is the line that carries it across.
    ("model_call.py", None, 'return Attempt(name, verdict.ok, verdict.answer', 'return Attempt(name, True, verdict.answer'),
    ("loop_report.py", None, 'if ledger_rows is None:', 'if False and ledger_rows is None:'),
    ("stage_log.py", None, 'if d >= 0:', 'if True:'),
    ("commit_scan.py", None, 'DASHES.findall(added)', '[]'),
    ("land_batch.py", "test_land_batch_loop_guard.py",
     'if no_bisect or len(landed) <= 1: return []', 'if True: return []'),
]

OK_WORDS = re.compile(r"\bOK\b|\bPASS\b|0 finding")
BAD_WORDS = re.compile(r"\bFAILED\b|\bFAIL\b")


def run(path, cwd=None, external=None):
    """Exit code AND text, captured together. Never through a pipe: a code after a pipe belongs to
    the pipe, and this estate misread three that way in one evening."""
    try:
        argv = [sys.executable, external] if external else [sys.executable, path, "--selftest"]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300, cwd=cwd)
    except subprocess.SubprocessError as exc:
        return None, str(exc)[:120]
    return r.returncode, (r.stdout + r.stderr)


def check(name, external, old, new):
    src = os.path.join(LOOP, name)
    if not os.path.isfile(src):
        return None, "%s does not exist" % name
    body = open(src, encoding="utf-8").read()
    if old not in body:
        # NO-DATA, never a pass: if the anchor moved, this file is measuring nothing and must say so
        return None, "%s: the mutation anchor is gone, so this subject is UNMEASURED" % name

    tmp = tempfile.mkdtemp()
    try:
        # THE WHOLE scripts/ TREE, not only loop/. An external suite reads its subject by a path
        # relative to ITSELF, so copying only loop/ left it reading the REAL module while this file
        # mutated a copy, and the mutation appeared to survive. That is the two copy trap this
        # estate hit four separate times tonight, in three different files, and it always looks
        # like the code being fine.
        shutil.copytree(HERE, os.path.join(tmp, "scripts"))
        clean = os.path.join(tmp, "scripts", "loop", name)
        ext = os.path.join(tmp, "scripts", external) if external else None
        code_ok, text_ok = run(clean, external=ext)
        if code_ok != 0 or not OK_WORDS.search(text_ok):
            return False, "%s as shipped: exit %s but text %s" % (
                name, code_ok, "says OK" if OK_WORDS.search(text_ok) else "does NOT say OK")

        with open(clean, "w", encoding="utf-8") as f:
            f.write(body.replace(old, new, 1))
        code_bad, text_bad = run(clean, external=ext)
        said_failed = bool(BAD_WORDS.search(text_bad))
        if code_bad == 0 and said_failed:
            return False, ("%s: printed FAILED and EXITED 0. The verdict and the exit code "
                           "disagree, so a pipeline and a human read the same run differently" % name)
        if code_bad == 0:
            return False, "%s: a broken property did not fail at all (exit 0, no FAILED)" % name
        if not said_failed:
            return False, "%s: exited %s without printing a verdict a human can read" % (name, code_bad)
        return True, "%s: OK at exit 0, FAILED at exit %s" % (name, code_bad)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    rows = [(n,) + check(n, e, o, w) for n, e, o, w in SUBJECTS]
    unmeasured = [r for r in rows if r[1] is None]
    bad = [r for r in rows if r[1] is False]
    for name, ok, why in rows:
        print("  %-9s %s" % ("UNMEASURED" if ok is None else ("ok" if ok else "FAIL"), why))
    print()
    if bad or unmeasured:
        print("%d disagreement(s), %d unmeasured. A verdict that does not match its exit code is "
              "the one defect a check cannot find in itself." % (len(bad), len(unmeasured)))
        return 1
    print("PASS: all %d check(s) agree, printed verdict and exit code, in both directions" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())

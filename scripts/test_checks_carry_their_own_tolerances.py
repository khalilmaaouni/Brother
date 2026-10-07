#!/usr/bin/env python3
"""A check's verdict must be a statement about its SUBJECT, never about its INVOCATION.

THE DEFECT THIS HUNTS. The same tree, judged twice by the same file, came back with two different
verdicts because the caller passed a different flag, started from a different directory, or had a
different HOME. Three instances measured on 2026-09-21:

  - a --max style budget flag decided whether a subject passed, and the recorded verdict never said
    it had been widened, so a PASS at a loosened tolerance is indistinguishable from a real one;
  - a check that COULD NOT LOOK at something reported the same word as a check that looked and found
    nothing, so a hole read as a skip and a skip read as a pass;
  - a check passed with the developer's HOME and behaved differently with an empty one, which means
    it was measuring this laptop rather than the tree.

The estate's law is that NO-DATA IS NEVER A PASS and every unknown fails toward refusing. A tolerance
living in a caller flag rather than in the file is the same defect in a different coat: the recorded
verdict stops implying that the property held.

WHAT IT DOES. Every check is run under its plainest invocation and again under hostile ones, and the
pair is compared on STRICTNESS, not on equality:

  plain     repo root, the environment as it comes
  HOME      the same, with HOME pointed at an empty directory
  CWD       the same, started from an empty directory instead of the repo root
  FLAG      the same, with every numeric tolerance flag the file declares pushed to an extreme
  ENV       the same, with every numeric tolerance the file reads from the environment pushed out

A hostile run may REFUSE where the plain run passed: that is the strict direction and it is allowed.
A hostile run may never PASS where the plain run refused. That is the whole assertion.

HOW THIS DIFFERS FROM ITS TWO SIBLINGS, so none of the three duplicates another:

  scripts/test_verdict_matches_exit.py runs ONE invocation of a check and asks whether its printed
  words and its exit code agree with each other. It is about internal consistency, and it mutates the
  subject to force a red. This file never mutates a subject: it holds the subject fixed and varies
  the INVOCATION, which is the one axis that file holds constant. A check can agree with itself
  perfectly in both files and still be loosened from the command line.

  scripts/test_selftests_report_rather_than_crash.py DOES NOT EXIST in this tree as of 2026-09-21 (it
  may be mid flight in another worker's lane). Its named subject is a check that dies instead of
  reporting. Where it lands, the boundary is: that file asks whether a check answers at all, this one
  asks whether the answer depends on who asked. The overlap is the UNMEASURED row below, and this
  file treats an unanswerable check as UNMEASURED rather than trying to diagnose why.

A CHECK THAT CANNOT BE INVOKED IS UNMEASURED AND IS NAMED, NEVER COUNTED AS A PASS. Measured while
writing this: scripts/audit_brother_loop.py --selftest did not terminate inside 100 seconds, so it
has no readable verdict at all and nothing this file reports about it can be called green.

Run:  python3 scripts/test_checks_carry_their_own_tolerances.py            (scan this tree)
      python3 scripts/test_checks_carry_their_own_tolerances.py --selftest (prove the instrument)

--selftest changes the SUBJECT SET (temporary fixtures instead of the tree), never a tolerance, and
there is deliberately no flag in this file that can widen anything. The scan root comes from __file__
so that a check added to the tree tomorrow is covered without anyone editing a list here.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

#: A subject gets this long to answer. 45 s because the slowest honest selftest measured in this tree
#: finished well inside a second and the one that blew past it never finished at all: the bar exists to
#: separate "slow" from "never", not to rank speed. A subject that overruns is UNMEASURED, not a pass.
SUBJECT_TIMEOUT_S = 45

#: The verdict vocabulary this estate actually prints. "0 finding" is here because several gates report
#: a clean sweep as a count rather than a word.
SAYS_OK = re.compile(r"\bOK\b|\bPASS(?:ED|ING)?\b|\b0 finding")
SAYS_BAD = re.compile(r"\bFAIL(?:ED|URE)?\b|\bNO-DATA\b|\bREFUSED\b|\bBLOCKED\b|\bWITHHELD\b|\bSTOP\b")

#: Numeric tolerances read from the environment, with their in-file default.
ENV_TOLERANCE = re.compile(
    r"os\.(?:environ\.get|getenv)\(\s*[\"']([A-Z][A-Z_0-9]*)[\"']\s*,\s*[\"']?(-?\d+(?:\.\d+)?)[\"']?\s*\)")
#: Numeric tolerances declared as command line flags, with their in-file default.
FLAG_TOLERANCE = re.compile(
    r"add_argument\(\s*[\"'](--(?:max|min|tolerance|budget|threshold|limit)[\w-]*)[\"']"
    r"(?=[^)]*?type\s*=\s*(?:int|float))[^)]*?default\s*=\s*(-?\d+(?:\.\d+)?)", re.S)

#: A name saying the number is a FLOOR is widened by dropping it to zero; everything else is a ceiling
#: and is widened by multiplying. Both directions mean the same thing: give the subject more rope.
_FLOOR = re.compile(r"MIN|FLOOR|LEAST|REMAINING", re.I)

PERMISSIVE, REFUSING = 0, 1


def widen(name, default):
    """The hostile value for one tolerance. A floor goes to 0, a ceiling goes up by a thousandfold."""
    if _FLOOR.search(name):
        return "0"
    try:
        return str(int(abs(float(default)) * 1000) + 1000)
    except ValueError:
        return "1000000"


def tolerances(source):
    """Every numeric tolerance a check reads from its invocation: (env knobs, flag knobs), each widened.

    Read from the SOURCE rather than from a list kept here, so a tolerance added to a check tomorrow is
    pushed on by this file without anyone remembering to register it.
    """
    env = {n: widen(n, d) for n, d in ENV_TOLERANCE.findall(source)}
    flags = []
    for flag, default in FLAG_TOLERANCE.findall(source):
        flags += [flag, widen(flag, default)]
    return env, flags


def rank(code, text):
    """PERMISSIVE, REFUSING, or None when the run produced no verdict a reader could act on.

    A run that exits 0 while printing FAIL counts as REFUSING here, deliberately. That disagreement is
    a real defect and it is scripts/test_verdict_matches_exit.py's subject, not this one; treating it
    as refusing keeps this file from reporting the same fault twice under a different name.
    """
    if code is None:
        # A CHECK THAT NEVER ANSWERED HAS NO VERDICT, and an exit code invented for it would be one.
        # Measured 2026-09-21: scripts/audit_brother_loop.py --selftest still had not returned after 120 s,
        # and an earlier draft of this file scored the timeout as code 124, which reads as "refusing", which
        # let a check that cannot be run at all be counted among the well behaved. A hole is not a refusal.
        return None
    ok, bad = SAYS_OK.search(text), SAYS_BAD.search(text)
    if code != 0 or bad:
        return REFUSING
    if ok:
        return PERMISSIVE
    return None


def run(path, cwd, env, extra=()):
    """Exit code AND text together. NEVER through a pipe: an exit code after a pipe belongs to the pipe,
    and this estate misread three that way in one evening."""
    try:
        r = subprocess.run([sys.executable, path, "--selftest"] + list(extra),
                           capture_output=True, text=True, timeout=SUBJECT_TIMEOUT_S, cwd=cwd, env=env)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return None, "did not answer inside %d s" % SUBJECT_TIMEOUT_S
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "could not be launched: %s" % str(exc)[:160]


def discover(root):
    """Every check under root, found from the TREE. A check is a file that offers --selftest."""
    found = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for n in sorted(names):
            if not n.endswith(".py") or n == os.path.basename(__file__):
                continue
            p = os.path.join(base, n)
            try:
                if re.search(r"[\"']--selftest[\"']", open(p, encoding="utf-8", errors="replace").read()):
                    found.append(p)
            except OSError:
                continue
    return sorted(found)


def audit_one(path, repo_root, empty_home, empty_cwd, base_env):
    """One subject, one row: (relative path, verdict, detail).

    verdict is "loosened", "unmeasured", "strict-shift" or "steady". THE FOUR GUARDS ARE INDEPENDENT and
    each row reaches exactly one of them, so a fixture can trip one at a time and each is therefore
    proved. A fixture that trips two guards proves neither.
    """
    rel = os.path.relpath(path, os.path.dirname(repo_root.rstrip("/")) or repo_root)
    source = open(path, encoding="utf-8", errors="replace").read()
    env_knobs, flag_knobs = tolerances(source)

    _plain_code, _plain_text = run(path, repo_root, base_env)
    plain = rank(_plain_code, _plain_text)
    _plain_why = ("timed out or would not launch (%s)" % _plain_text[:70]) if _plain_code is None \
        else "exited %s printing no verdict word" % _plain_code
    if plain is None:
        # GUARD 1: no readable verdict under the plainest invocation there is. Nothing can be compared
        # against nothing, so this subject is UNMEASURED and says so. It is never a pass.
        return rel, "unmeasured", "no readable verdict under the plain invocation: %s" % _plain_why

    hostile = []
    hostile.append(("HOME", run(path, repo_root, dict(base_env, HOME=empty_home))))
    hostile.append(("CWD", run(path, empty_cwd, base_env)))
    if flag_knobs:
        hostile.append(("FLAG " + " ".join(flag_knobs), run(path, repo_root, base_env, flag_knobs)))
    if env_knobs:
        hostile.append(("ENV " + " ".join("%s=%s" % kv for kv in sorted(env_knobs.items())),
                        run(path, repo_root, dict(base_env, **env_knobs))))

    shifts = []
    for how, (code, text) in hostile:
        got = rank(code, text)
        if got is not None and got < plain:
            # GUARD 2: the dangerous direction. The plain invocation REFUSED and a hostile one PASSED,
            # so the caller, not the subject, decided the verdict.
            return rel, "loosened", "%s turned a refusal into a pass" % how
        if got != plain:
            shifts.append("%s -> %s" % (how, "refuses" if got == REFUSING else "no verdict"))
    if shifts:
        # GUARD 3: a difference in the STRICT direction only. Allowed, and still worth naming: a check
        # that refuses without the developer's HOME was reading something outside the tree.
        return rel, "strict-shift", "; ".join(shifts)
    # GUARD 4: the verdict did not move at all. This is what a check about its subject looks like.
    return rel, "steady", "same verdict under every invocation"


def audit(root, base_env=None):
    """Every check under root, audited. Returns the rows; prints nothing.

    base_env is the environment the PLAIN invocation gets. The tree scan never passes one, so a real run
    always uses the environment as it comes. Only the selftest supplies one, so that its HOME fixture is
    measured against a HOME this file controls rather than against whatever this laptop happens to hold:
    an earlier draft asserted that ~/Library exists, which made the instrument's own proof a statement
    about the developer's machine, which is the defect this file exists to find.
    """
    repo_root = os.path.dirname(root.rstrip("/")) or root
    env = dict(os.environ) if base_env is None else dict(base_env)
    rows = []
    with tempfile.TemporaryDirectory() as empty_home, tempfile.TemporaryDirectory() as empty_cwd:
        for path in discover(root):
            rows.append(audit_one(path, repo_root, empty_home, empty_cwd, env))
    return rows


def report(rows, out=None):
    """Print the rows and return the exit code. Loosened and unmeasured both refuse."""
    out = out or sys.stdout
    loosened = [r for r in rows if r[1] == "loosened"]
    unmeasured = [r for r in rows if r[1] == "unmeasured"]
    shifted = [r for r in rows if r[1] == "strict-shift"]
    for name, verdict, why in rows:
        if verdict != "steady":
            print("  %-12s %-52s %s" % (verdict.upper(), name, why), file=out)
    print(file=out)
    print("%d check(s) examined: %d steady, %d strict-shift, %d LOOSENED, %d UNMEASURED"
          % (len(rows), len(rows) - len(loosened) - len(unmeasured) - len(shifted),
             len(shifted), len(loosened), len(unmeasured)), file=out)
    if loosened or unmeasured:
        print("A verdict a caller can widen is a statement about the caller. An UNMEASURED check is a "
              "hole, and a hole is never a pass.", file=out)
        return 1
    if not rows:
        # An empty population composed into a pass once already on this estate. Never again.
        print("NO-DATA: no check was found to examine, which proves nothing", file=out)
        return 1
    print("PASS: every check's verdict was the same, or stricter, under every hostile invocation", file=out)
    return 0


# ---------------------------------------------------------------- the instrument's own proof

#: Fixtures, ONE GUARD EACH. Written to a temp root and audited exactly as a real check would be, so the
#: proof drives main's real path and not a helper beside it.
_F_STEADY = '''import sys
print("selftest: OK")
sys.exit(0)
'''
_F_LOOSE_FLAG = '''import argparse, sys
p = argparse.ArgumentParser(); p.add_argument("--selftest", action="store_true")
p.add_argument("--max-age-hours", type=float, default=24.0)
a = p.parse_args()
if a.max_age_hours > 24.0:
    print("PASS"); sys.exit(0)
print("FAILED: too old"); sys.exit(1)
'''
_F_LOOSE_ENV = '''import os, sys
if float(os.environ.get("FIXTURE_CEILING", "5")) > 5:
    print("PASS"); sys.exit(0)
print("FAILED: over the ceiling"); sys.exit(1)
'''
_F_UNMEASURED = '''import sys
print("...thinking about it...")
sys.exit(0)
'''
#: HOME sensitive WITHOUT reading anything that happens to be on this laptop: the marker is planted by the
#: selftest in a HOME it owns, so the fixture behaves identically on every machine and under an empty HOME.
_F_HOME = '''import os, sys
if os.path.isfile(os.path.join(os.environ.get("HOME", ""), "planted-by-the-selftest")):
    print("selftest: OK"); sys.exit(0)
print("FAILED: the planted marker is not in HOME"); sys.exit(1)
'''


def selftest():
    cases, bad = 0, []

    def check(label, got, want):
        nonlocal cases
        cases += 1
        if got != want:
            bad.append("%s: got %r, wanted %r" % (label, got, want))

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "scripts")
        os.makedirs(root)
        for name, body in (("steady.py", _F_STEADY), ("loose_flag.py", _F_LOOSE_FLAG),
                           ("loose_env.py", _F_LOOSE_ENV), ("unmeasured.py", _F_UNMEASURED),
                           ("home_bound.py", _F_HOME)):
            with open(os.path.join(root, name), "w", encoding="utf-8") as f:
                # the literal string is what discover() looks for, so the fixture is found the same
                # way a real check is: from the tree, not from a list
                f.write('SELFTEST_FLAG = "--selftest"\n' + body)
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        open(os.path.join(home, "planted-by-the-selftest"), "w").close()
        plain_env = dict(os.environ, HOME=home)
        got = {os.path.basename(n): v for n, v, _ in audit(root, plain_env)}

        check("all five fixtures were discovered from the tree", len(got), 5)
        # each fixture trips exactly ONE guard, so each guard is proved on its own
        check("a steady check is steady", got.get("steady.py"), "steady")
        check("a flag that widens a tolerance is LOOSENED", got.get("loose_flag.py"), "loosened")
        check("an env knob that widens a tolerance is LOOSENED", got.get("loose_env.py"), "loosened")
        check("a check with no readable verdict is UNMEASURED", got.get("unmeasured.py"), "unmeasured")
        check("a check that needs the real HOME shifts strictly", got.get("home_bound.py"), "strict-shift")

        import io
        code = report(audit(root, plain_env), out=io.StringIO())
        check("a root holding a loosened check exits non zero", code, 1)

        only = os.path.join(tmp, "only")
        os.makedirs(os.path.join(only, "scripts"))
        with open(os.path.join(only, "scripts", "steady.py"), "w", encoding="utf-8") as f:
            f.write('SELFTEST_FLAG = "--selftest"\n' + _F_STEADY)
        check("a root holding only steady checks exits 0",
              report(audit(os.path.join(only, "scripts"), plain_env), out=io.StringIO()), 0)
        check("an EMPTY population is NO-DATA and refuses, never a pass",
              report([], out=io.StringIO()), 1)
        # A check that never answered reaches rank() as a None code, and that must be UNMEASURED and not
        # a refusal. Asserted directly because the entry point route to it costs SUBJECT_TIMEOUT_S of
        # real waiting, and a %d second fixture in a selftest is a fixture nobody runs.
        check("a check that never answered has no verdict", rank(None, "did not answer"), None)
        check("a check that never answered is not read as a refusal",
              rank(None, "FAILED") is REFUSING, False)

    for line in bad:
        print("  FAILED " + line)
    print("selftest: %d cases, %s" % (cases, "OK" if not bad else "FAILED (%d)" % len(bad)))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv[1:]:
        return selftest()
    return report(audit(HERE))


if __name__ == "__main__":
    sys.exit(main())

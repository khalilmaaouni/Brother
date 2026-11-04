#!/usr/bin/env python3
"""J1's D.doctor-fixes-work: the INSTALLED doctor's advice can be followed, and its own repair holds.

Until this existed the row carried only `measured_by: FRUN1.c and FRUN1.f` and no check, so it read NO-DATA
however the doctor's remediation read.

WHAT IT RUNS, on the installed plugin a stranger has, from a fresh project folder (the folder they are in):
  1. doctor --json, read only. It must answer with its checks; a doctor that crashes or answers unreadably has
     shown nothing, which is a FAIL of the doctor (the install was readable).
  2. Every `python3 <file>.py` its checks tell the person to run must exist as written from that folder
     (absolute, or relative to it). A remediation naming a script the person does not have is advice nobody can
     follow, which is the FRUN1.c defect.
  3. No remediation names a retired plugin (the one plugin installed is "brother"), the FRUN1.f defect.
  4. doctor --fix runs (exit 0 or 1, never a crash) and afterwards doctor --json reports no MORE failed checks
     than before: a repair may fix nothing, but it must never make the install worse.

FAIL DIRECTION. No install, or no doctor in it: NO-DATA, exit 2, never a pass.

    python3 -B scripts/journey/check_doctor_fixes.py
    python3 -B scripts/journey/check_doctor_fixes.py --selftest

EXIT 0 PASS, 1 FAIL, 2 NO-DATA.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_install_paths import COMMANDED, installed_root  # noqa: E402

DOCTOR = "doctor.py"
#: Plugin ids and commands from before the one plugin, which an install can no longer act on.
RETIRED = re.compile(r"brothermode-marketplace|/plugin (?:update|install) brother(?:mode|sbe|ds)\b|"
                     r"brother(?:mode|sbe|ds)@", re.IGNORECASE)


def installed_doctor(root):
    """The installed doctor (under a brothermode/scripts folder of the install), or None."""
    for base, dirs, files in os.walk(root or ""):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if DOCTOR in files and base.replace(os.sep, "/").endswith("brothermode/scripts"):
            return os.path.join(base, DOCTOR)
    return None


def ask(doctor, args, cwd, timeout=300):
    """(exit code, parsed --json body or None, raw text). A doctor that cannot be started is (None, None, why)."""
    try:
        done = subprocess.run([sys.executable, "-B", doctor] + list(args), cwd=cwd, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, repr(exc)
    try:
        body = json.loads(done.stdout)
    except ValueError:
        body = None
    return done.returncode, body if isinstance(body, dict) else None, done.stdout + done.stderr


def failed(body):
    return sum(1 for c in body.get("checks") or [] if isinstance(c, dict) and c.get("status") == "FAIL")


def judge(before, fix_code, after, cwd):
    """(exit code, one line) over the three doctor answers. Pure, so the selftest judges THIS and not a copy.
    before and after are parsed --json bodies (None when unreadable); fix_code is --fix's exit code."""
    if not isinstance(before, dict) or not isinstance(before.get("checks"), list):
        return 1, "FAIL: the installed doctor gave no readable --json answer, so a stranger cannot be told what is wrong"
    bad = []
    for c in before["checks"]:
        if not isinstance(c, dict) or c.get("status") == "PASS":
            continue
        text = str(c.get("message") or "")
        for rel in sorted(set(COMMANDED.findall(text))):
            if not os.path.isfile(rel if os.path.isabs(rel) else os.path.join(cwd, rel)):
                bad.append("%s says run %s, which is not there from the folder a stranger is in" % (c.get("key"), rel))
        if RETIRED.search(text):
            bad.append("%s names a retired plugin: %s" % (c.get("key"), RETIRED.search(text).group(0)))
    if fix_code not in (0, 1):
        bad.append("doctor --fix ended with exit %r, not a report" % (fix_code,))
    elif not isinstance(after, dict) or not isinstance(after.get("checks"), list):
        bad.append("after --fix the doctor gave no readable --json answer")
    elif failed(after) > failed(before):
        bad.append("--fix left %d failed check(s) where there were %d" % (failed(after), failed(before)))
    if bad:
        return 1, "FAIL: %d doctor problem(s): %s" % (len(bad), "; ".join(bad[:4]))
    return 0, ("PASS: every remedy the installed doctor names can be followed from the stranger's folder, none "
               "names a retired plugin, and --fix left %d failed check(s) where there were %d"
               % (failed(after), failed(before)))


def selftest():
    cases = []

    def case(label, got, want):
        cases.append((label, want, got, got == want))

    cwd = tempfile.mkdtemp(prefix="doctor-cwd-")
    present = os.path.join(cwd, "present.py")
    open(present, "w").close()

    def body(*checks):
        return {"checks": [dict(key="k%d" % i, status=s, message=m) for i, (s, m) in enumerate(checks)]}

    ok = body(("FAIL", "run: python3 %s" % present), ("PASS", "fine"))
    case("followable advice and a fix that holds is PASS", judge(ok, 0, ok, cwd)[0], 0)
    case("advice naming a relative script that is not there is FAIL",
         judge(body(("FAIL", "run that: python3 scripts/install.py")), 0, body(), cwd)[0], 1)
    case("advice in a SKIP counts too", judge(body(("SKIP", "Run: python3 scripts/setup.py")), 0, body(), cwd)[0], 1)
    case("a retired plugin in the advice is FAIL",
         judge(body(("FAIL", "/plugin update brothermode")), 0, body(), cwd)[0], 1)
    case("an unreadable doctor is FAIL", judge(None, 0, ok, cwd)[0], 1)
    case("a fix that crashes is FAIL", judge(ok, 3, ok, cwd)[0], 1)
    case("a fix that leaves more failures is FAIL",
         judge(body(), 1, body(("FAIL", "worse")), cwd)[0], 1)
    case("a passing check's words are not advice", judge(body(("PASS", "python3 scripts/x.py")), 0, body(), cwd)[0], 0)
    bad = 0
    for label, want, got, good in cases:
        bad += 0 if good else 1
        print("%-56s want %d got %d %s" % (label, want, got, "ok" if good else "MISMATCH"))
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.environ.get("CLAUDE_CONFIG_DIR", ""))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    doctor = installed_doctor(installed_root(args.config))
    if doctor is None:
        print("NO-DATA: no installed %s under %r, so no doctor answer could be read" % (DOCTOR, args.config))
        return 2
    home = os.environ.get("HOME", "")
    cwd = tempfile.mkdtemp(prefix="doctor-project-", dir=home if os.path.isdir(home) else None)
    subprocess.run(["git", "init", "-q", cwd], capture_output=True, text=True, timeout=60)
    _code, before, _raw = ask(doctor, ["--json"], cwd)
    fix_code, _body, _raw = ask(doctor, ["--fix"], cwd)
    _code, after, _raw = ask(doctor, ["--json"], cwd)
    code, line = judge(before, fix_code, after, cwd)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

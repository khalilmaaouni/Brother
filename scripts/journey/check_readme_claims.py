#!/usr/bin/env python3
"""J1's D.readme-claims-executed: every command the README tells a stranger to run on an INSTALLED plugin runs.

Until this existed the row carried only `measured_by: README118` and no check, so it read NO-DATA however the
README's claims about an install drifted.

WHAT IT EXECUTES, and what it does not. The README's install-side claims are the commands it says to run "from an
installed plugin root": each backticked `python3 runtime/...` span. Each one is RUN, from the installed plugin
root, and must exit 0. A claim nobody executed is not evidence, so this never greps the README for a word and
calls that a pass. It does NOT judge the README's other prose: tying every sentence to a register with a proof is
README118's register (docs/assurance/DOC-CLAIMS.md), which has not landed, and this row says so in its own words
rather than borrowing that unit's name for a narrower measurement.

FAIL DIRECTION. No install: NO-DATA. A README that names no installed-root command: NO-DATA, exit 2, because
there was nothing to execute, which is never a pass. One command that exits non zero: FAIL, naming it.

    python3 -B scripts/journey/check_readme_claims.py
    python3 -B scripts/journey/check_readme_claims.py --selftest

EXIT 0 PASS, 1 FAIL, 2 NO-DATA.
"""
import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_install_paths import installed_root  # noqa: E402

README = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "README.md")
#: A backticked command the README gives for an installed plugin root: python3, optional -B, then runtime/...
INSTALLED_COMMAND = re.compile(r"`(python3\s+(?:-B\s+)?runtime/[^`]+)`")


def claims(text):
    """The installed-root commands the README names, in order, each once."""
    return list(dict.fromkeys(m.strip() for m in INSTALLED_COMMAND.findall(text or "")))


def execute(command, root, timeout=300):
    """(exit code, last line) of one README command run from the installed root; (None, why) if it cannot start."""
    argv = shlex.split(command)
    argv[0] = sys.executable
    try:
        done = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, repr(exc)
    said = (done.stdout + done.stderr).strip().splitlines()
    return done.returncode, said[-1][:120] if said else ""


def judge(commands, results):
    """(exit code, one line). results maps each command to (exit code, last line). Pure, for the selftest."""
    if not commands:
        return 2, ("NO-DATA: the README names no command to run from an installed plugin root, so no claim of "
                   "its was executed against the install")
    bad = ["%s exited %s (%s)" % (c, results.get(c, (None, ""))[0], results.get(c, (None, ""))[1])
           for c in commands if results.get(c, (None, ""))[0] != 0]
    if bad:
        return 1, "FAIL: %d README claim(s) did not hold on the install: %s" % (len(bad), "; ".join(bad[:3]))
    return 0, ("PASS: all %d command(s) the README gives for an installed plugin root ran from the install and "
               "exited 0: %s" % (len(commands), "; ".join(commands)))


def selftest():
    cases = []

    def case(label, got, want):
        cases.append((label, want, got, got == want))

    text = "run `python3 bundle/runtime/x.py` here, or `python3 runtime/verify_runtime.py` from an installed root"
    case("only the installed-root command is a claim", claims(text) == ["python3 runtime/verify_runtime.py"], True)
    case("a command named twice is executed once", len(claims(text + " `python3 runtime/verify_runtime.py`")), 1)
    case("every claim exiting 0 is PASS", judge(["a"], {"a": (0, "PASS")})[0], 0)
    case("one claim exiting 1 is FAIL", judge(["a", "b"], {"a": (0, ""), "b": (1, "FAIL")})[0], 1)
    case("a claim that could not start is FAIL", judge(["a"], {"a": (None, "boom")})[0], 1)
    case("no claim at all is NO-DATA", judge([], {})[0], 2)
    root = tempfile.mkdtemp(prefix="readme-root-")
    os.makedirs(os.path.join(root, "runtime"))
    with open(os.path.join(root, "runtime", "ok.py"), "w") as fh:
        fh.write("print('fine')\n")
    case("execute runs the command from the installed root", execute("python3 runtime/ok.py", root)[0], 0)
    case("a command the install lacks does not exit 0", execute("python3 runtime/gone.py", root)[0] != 0, True)
    bad = 0
    for label, want, got, ok in cases:
        bad += 0 if ok else 1
        print("%-52s want %s got %s %s" % (label, want, got, "ok" if ok else "MISMATCH"))
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.environ.get("CLAUDE_CONFIG_DIR", ""))
    ap.add_argument("--readme", default=README)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    root = installed_root(args.config)
    if root is None:
        print("NO-DATA: no installed plugin under %r, so no README claim could be executed against it" % args.config)
        return 2
    try:
        with open(args.readme, encoding="utf-8") as fh:
            commands = claims(fh.read())
    except OSError as exc:
        print("NO-DATA: the README could not be read (%s)" % exc)
        return 2
    code, line = judge(commands, {c: execute(c, root) for c in commands})
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

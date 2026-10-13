#!/usr/bin/env python3
"""Run plugin/runtime/brother's tests, minus the 3 known real-multiprocess-fork
files (test_openrouter_ledger.py, test_leases.py, test_dispatch_semaphore.py).

Those three spawn real OS processes to prove real concurrency guarantees;
run together with the other 52 test files in one discovery, they can blow
required_fast.sh's wall-clock budget (a known, already-documented failure
mode in this repo: real multiprocess tests stacked together under load).
They run isolated, in the full battery only, never here.

Exit code is the real unittest result's exit code, not this wrapper's.

AN EMPTY HOME, ON EVERY HOST (2026-10-11). The suite runs with HOME set to a
fresh empty folder and BROTHERME_CONFIG unset, which is how GitHub's Linux
runner sees it: its home folder holds no Brother setup. Two facade tests read
this machine's ~/.brotherme consent file, passed on every Mac that had one,
and failed the first time they ran without it (hub canary run 38148660856).
A test that needs a config, a vault or a store makes its own.

THE LAST LINE NAMES THE FAILING TESTS (2026-10-11). required_fast.sh keeps
only the tail of a failing row and its summary keeps only the last line. The
child's unittest headers went to stderr at once while the tests' own prints
sat in a stdout buffer flushed at exit, so that tail was test chatter and the
canary's FAIL never said which test. The child now runs unbuffered, its
output is copied through as it arrives, and the last line printed names
every FAIL and ERROR header unittest wrote.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

HEAVY = {"test_openrouter_ledger.py", "test_leases.py", "test_dispatch_semaphore.py"}
HEADER = re.compile(r"^(FAIL|ERROR|UNEXPECTED SUCCESS): (.+)$")


def summary(code, ran, failing):
    head = "plugin_runtime_fast_discover: %s exit %d, %s" % (
        "PASS" if code == 0 else "FAIL", code, ran or "no 'Ran N tests' line")
    if code == 0:
        return head
    return "%s: %s" % (head, "; ".join(failing) or
                       "unittest named no failing test, read the output above")


def main():
    mods = []
    for path in sorted(glob.glob("plugin/runtime/brother/**/test_*.py", recursive=True)):
        if os.path.basename(path) in HEAVY:
            continue
        mods.append(path[:-3].replace("/", "."))
    if not mods:
        print("plugin_runtime_fast_discover: no test modules found", file=sys.stderr)
        return 1
    home = tempfile.mkdtemp(prefix="plugin-runtime-home-")
    env = dict(os.environ, HOME=home, PYTHONUNBUFFERED="1")
    env.pop("BROTHERME_CONFIG", None)
    failing, ran = [], ""
    try:
        proc = subprocess.Popen([sys.executable, "-m", "unittest"] + mods, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
        for line in proc.stdout:
            sys.stdout.write(line)
            found = HEADER.match(line.rstrip("\n"))
            if found:
                failing.append("%s %s" % found.groups())
            elif line.startswith("Ran "):
                ran = line.strip()
        code = proc.wait()
    except OSError as exc:
        code, ran = 1, "the suite could not start: %s" % exc
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print(summary(code, ran, failing))
    return code


if __name__ == "__main__":
    sys.exit(main())

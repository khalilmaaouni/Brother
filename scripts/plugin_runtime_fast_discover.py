#!/usr/bin/env python3
"""Run plugin/runtime/brother's tests, minus the 3 known real-multiprocess-fork
files (test_openrouter_ledger.py, test_leases.py, test_dispatch_semaphore.py).

Those three spawn real OS processes to prove real concurrency guarantees;
run together with the other 52 test files in one discovery, they can blow
required_fast.sh's wall-clock budget (a known, already-documented failure
mode in this repo: real multiprocess tests stacked together under load).
They run isolated, in the full battery only, never here.

Exit code is the real unittest result's exit code, not this wrapper's.
"""
import glob
import os
import subprocess
import sys

HEAVY = {"test_openrouter_ledger.py", "test_leases.py", "test_dispatch_semaphore.py"}


def main():
    mods = []
    for path in sorted(glob.glob("plugin/runtime/brother/**/test_*.py", recursive=True)):
        if os.path.basename(path) in HEAVY:
            continue
        mods.append(path[:-3].replace("/", "."))
    if not mods:
        print("plugin_runtime_fast_discover: no test modules found", file=sys.stderr)
        return 1
    return subprocess.call([sys.executable, "-m", "unittest"] + mods)


if __name__ == "__main__":
    sys.exit(main())

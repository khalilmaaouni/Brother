"""Shared entry guard for the acceptance checks in this directory.

WHY THIS EXISTS. These checks are committed BEFORE the work, so every one of
them runs against a tree where its subject does not exist yet. A check that
crashes on a missing file (ModuleNotFoundError, FileNotFoundError) is not a
failing check, it is an unrunnable one, and Brother's engine refuses a unit
whose check cannot run rather than treating that as a red. Measured here on
2026-09-05: six units refused before any worker started, for exactly that
reason. So a missing subject must exit 1 with a plain sentence, which is an
ordinary failing check, and the check must pass once the work lands.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def require(*relative_paths):
    """Exit 1, cleanly, naming the first path that is not there yet."""
    for path in relative_paths:
        if not os.path.exists(os.path.join(ROOT, path)):
            print("NOT YET: %s does not exist" % path)
            sys.exit(1)


def fail(message):
    print("FAIL: %s" % message)
    sys.exit(1)

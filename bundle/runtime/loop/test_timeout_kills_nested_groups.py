#!/usr/bin/env python3
"""run_group's timeout kills a child's NESTED process group too (2026-10-03: a closer killed at 1800 s left ACC2's done
check, which it ran in its own group, running orphaned for half an hour).

Run: python3 -B scripts/loop/test_timeout_kills_nested_groups.py
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_batch as LB  # noqa: E402

PARENT = """
import subprocess, sys, time
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
open(sys.argv[1], "w").write(str(g.pid))
time.sleep(60)
"""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class TimeoutKillsNestedGroups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nested-group-")

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def test_a_grandchild_in_its_own_group_dies_with_the_timeout(self):
        try:   # inside the landing sandbox ps cannot run at all (execvp: Operation not permitted): no tree to read
            listed = str(os.getpid()) in __import__("subprocess").run(["ps", "-p", str(os.getpid()), "-o", "pid="], capture_output=True, text=True, timeout=10).stdout
        except OSError:
            listed = False
        if not listed:
            self.skipTest("NO-DATA: no process listing here (sandboxed), so a descendant cannot be found or killed")
        pidf = os.path.join(self.tmp, "pid")
        r = LB.run_group([sys.executable, "-c", PARENT, pidf], 2, dict(os.environ))
        self.assertEqual(r.returncode, 124)
        self.assertIn("NO-DATA: timed out after 2s", r.stderr)
        pid = int(open(pidf).read())
        for _ in range(30):
            if not alive(pid): break
            time.sleep(0.1)
        try:
            self.assertFalse(alive(pid), "the nested group outlived the timeout")
        finally:
            if alive(pid): os.kill(pid, 9)

    def test_the_listing_never_names_this_process_group(self):
        self.assertNotIn(os.getpgrp(), LB.descendant_groups(os.getppid()))


if __name__ == "__main__":
    unittest.main()

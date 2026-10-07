#!/usr/bin/env python3
"""stop_loop.sh --drain pauses and waits for runners to leave at their checkpoints, and never kills (plan E step 1).

Run: python3 -B scripts/loop/test_stop_drain.py
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, "stop_loop.sh")

HONOURS_PAUSE = "import os, sys, time\nwhile not os.path.exists(os.path.expanduser('~/.claude/evidence/LOOP-PAUSE.txt')): time.sleep(0.2)\nsys.exit(3)\n"
IGNORES_PAUSE = "import time\nwhile True: time.sleep(0.2)\n"


class Drain(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="stop-drain-", dir=os.path.expanduser("~/.claude/brother-scratch"))
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))
        os.makedirs(os.path.join(self.home, ".claude", "bin"))
        self.tag = "stop-drain-fixture-%d" % os.getpid()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            try: os.killpg(p.pid, signal.SIGKILL)
            except OSError: pass
            p.wait()
        shutil.rmtree(self.home, ignore_errors=True)

    def runner(self, body):
        path = os.path.join(self.home, ".claude", "bin", "unit_runner.py")
        with open(path, "w") as fh: fh.write(body)
        p = subprocess.Popen([sys.executable, path, self.tag], env=dict(os.environ, HOME=self.home), start_new_session=True)
        self.procs.append(p); time.sleep(0.6)
        return p

    def drain(self, *args):
        env = dict(os.environ, HOME=self.home, STOP_LOOP_ONLY=self.tag)
        return subprocess.run(["bash", STOP, "--drain"] + list(args), env=env, capture_output=True, text=True, timeout=120)

    def test_a_runner_that_honours_the_pause_drains_and_nothing_is_killed(self):
        p = self.runner(HONOURS_PAUSE)
        r = self.drain("30")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("DRAINED", r.stdout)
        self.assertEqual(p.wait(timeout=10), 3, "the runner left on its own, at its checkpoint, with its own exit code")
        self.assertTrue(os.path.exists(os.path.join(self.home, ".claude", "evidence", "LOOP-PAUSE.txt")), "the loop stays PAUSED")

    def test_a_runner_that_does_not_leave_is_named_and_never_killed(self):
        p = self.runner(IGNORES_PAUSE)
        r = self.drain("5")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("DRAIN INCOMPLETE", r.stdout)
        self.assertIn(str(p.pid), r.stdout)
        self.assertIsNone(p.poll(), "a drain never kills: the runner must still be alive")

    def test_a_bad_bound_pauses_nothing(self):
        r = self.drain("ten")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "evidence", "LOOP-PAUSE.txt")))


if __name__ == "__main__":
    unittest.main()

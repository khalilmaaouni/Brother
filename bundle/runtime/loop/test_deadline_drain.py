#!/usr/bin/env python3
"""The driver pauses before its deadline so runners leave at a checkpoint, and its end clears only that pause (plan E step 1).

The blocks run from loop_until.sh's own source, between their BEGIN and END markers, so the test reads what ships.
Run: python3 -B scripts/loop/test_deadline_drain.py
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "loop_until.sh"), encoding="utf-8").read()


def block(name):
    start = SRC.index("# %s BEGIN" % name)
    return SRC[start:SRC.index("# %s END" % name, start)]


class DeadlineDrain(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="deadline-drain-", dir=os.path.expanduser("~/.claude/brother-scratch"))
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))
        self.pause = os.path.join(self.home, ".claude", "evidence", "LOOP-PAUSE.txt")
        self.log = os.path.join(self.home, "log")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def drain(self, left, lead="1200", already="", first_left=36000):
        first = "" if first_left is None else "DRAIN_FIRST_LEFT=%d; " % first_left
        script = '%sNOW=1000; STOP_EPOCH=$((1000 + %s)); STOP_HHMM=07:15; LOG=%s; DEADLINE_DRAIN=%s\n%s\necho "DRAIN=$DEADLINE_DRAIN"' % (
            first, left, self.log, already or "0", block("DEADLINE DRAIN"))
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                              env=dict(os.environ, HOME=self.home, BROTHER_DRAIN_LEAD_S=lead))

    def test_inside_the_lead_the_pause_is_written_and_tagged(self):
        r = self.drain(600)
        self.assertIn("DEADLINE DRAIN: paused 600s before 07:15", r.stdout)
        self.assertTrue(open(self.pause).read().startswith("deadline drain:"))

    def test_a_run_that_starts_inside_the_lead_never_drains(self):
        r = self.drain(120, first_left=None)
        self.assertFalse(os.path.exists(self.pause), "a two minute run must not pause before its first pass")
        self.assertIn("DRAIN=0", r.stdout)

    def test_outside_the_lead_nothing_is_written(self):
        r = self.drain(5000)
        self.assertFalse(os.path.exists(self.pause))
        self.assertIn("DRAIN=0", r.stdout)

    def test_an_owner_pause_is_never_overwritten(self):
        open(self.pause, "w").write("owner hold\n")
        self.drain(10)
        self.assertEqual(open(self.pause).read(), "owner hold\n")

    def test_it_writes_once(self):
        r = self.drain(10, already="1")
        self.assertFalse(os.path.exists(self.pause))
        self.assertNotIn("DEADLINE DRAIN", r.stdout)

    def clear(self):
        return subprocess.run(["bash", "-c", block("DRAIN PAUSE CLEARED")], capture_output=True, text=True, timeout=30,
                              env=dict(os.environ, HOME=self.home))

    def test_the_end_clears_its_own_drain_pause(self):
        open(self.pause, "w").write("deadline drain: 07:15 in 600s\n")
        self.clear()
        self.assertFalse(os.path.exists(self.pause))

    def test_the_end_leaves_an_owner_pause(self):
        open(self.pause, "w").write("owner hold for the merge\n")
        self.clear()
        self.assertTrue(os.path.exists(self.pause))

    def test_the_clear_sits_after_the_runner_stop(self):
        self.assertLess(SRC.index("if ! stop_runners; then"), SRC.index("# DRAIN PAUSE CLEARED BEGIN"))


if __name__ == "__main__":
    unittest.main()

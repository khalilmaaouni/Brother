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


#: a lander: this module's run_group over a stand-in gate (`exec sleep 300`, the LR1.d reviewer's repro shape), writing
#: the gate's pid where the test can read it; argv: the land_batch folder, the pid file, and "handler" to install a TERM
#: handler of the lander's own first (it writes a file and exits 7, as a caller's clean up would)
LANDER = """
import os, signal, sys
sys.path.insert(0, sys.argv[1])
import land_batch as LB
if sys.argv[3] == "handler":
    def own(signum, frame):
        open(sys.argv[2] + ".own", "w").write("ran")
        os._exit(7)
    signal.signal(signal.SIGTERM, own)
LB.run_group(["sh", "-c", "echo $$ > " + sys.argv[2] + "; exec sleep 300"], 7200, dict(os.environ))
"""


class ASignalToTheLanderReachesTheGate(unittest.TestCase):
    """2026-10-21, LR1.d review: run_group starts its child in a session of its own, so a TERM to the caller's group
    never reached a boxed gate, which ran on, parent 1, for up to its 7200 s bound. Each fixture sends ONE signal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="run-group-signal-")

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def lander(self, mode="plain"):
        pidf = os.path.join(self.tmp, "gate.pid")
        p = __import__("subprocess").Popen([sys.executable, "-B", "-c", LANDER, os.path.dirname(os.path.abspath(__file__)),
                                            pidf, mode])
        self.addCleanup(lambda: p.poll() is None and p.kill())
        for _ in range(100):
            if os.path.isfile(pidf) and open(pidf).read().strip():
                break
            time.sleep(0.1)
        gate = int(open(pidf).read())
        self.addCleanup(lambda: alive(gate) and os.kill(gate, 9))
        self.assertTrue(alive(gate))
        return p, gate, pidf

    def gate_dies(self, gate):
        for _ in range(50):
            if not alive(gate):
                return True
            time.sleep(0.1)
        return False

    def test_a_term_kills_the_gate_and_the_lander_still_dies_by_term(self):
        import signal
        p, gate, _ = self.lander()
        os.kill(p.pid, signal.SIGTERM)
        self.assertEqual(p.wait(timeout=30), -signal.SIGTERM)
        self.assertTrue(self.gate_dies(gate), "the gate outlived a TERM to its lander")

    def test_an_interrupt_kills_the_gate_too(self):
        import signal
        p, gate, _ = self.lander()
        os.kill(p.pid, signal.SIGINT)
        p.wait(timeout=30)
        self.assertTrue(self.gate_dies(gate), "the gate outlived an INT to its lander")

    def test_the_landers_own_handler_still_runs_after_the_gate_is_killed(self):
        import signal
        p, gate, pidf = self.lander("handler")
        os.kill(p.pid, signal.SIGTERM)
        self.assertEqual(p.wait(timeout=30), 7)
        self.assertTrue(os.path.isfile(pidf + ".own"), "the caller's own TERM handler never ran")
        self.assertTrue(self.gate_dies(gate))

    def test_a_term_kills_a_nested_group_of_the_gate_too(self):
        import signal, subprocess
        try:   # inside the landing sandbox ps cannot run, so no descendant can be found (the same NO-DATA as above)
            listed = str(os.getpid()) in subprocess.run(["ps", "-p", str(os.getpid()), "-o", "pid="], capture_output=True, text=True, timeout=10).stdout
        except OSError:
            listed = False
        if not listed:
            self.skipTest("NO-DATA: no process listing here (sandboxed), so a nested group cannot be found or killed")
        pidf = os.path.join(self.tmp, "grandchild.pid")
        code = ("import os, sys\nsys.path.insert(0, sys.argv[1])\nimport land_batch as LB\n"
                "LB.run_group([sys.executable, '-c', sys.argv[3], sys.argv[2]], 7200, dict(os.environ))\n")
        p = subprocess.Popen([sys.executable, "-B", "-c", code, os.path.dirname(os.path.abspath(__file__)), pidf, PARENT])
        self.addCleanup(lambda: p.poll() is None and p.kill())
        for _ in range(100):
            if os.path.isfile(pidf) and open(pidf).read().strip():
                break
            time.sleep(0.1)
        grandchild = int(open(pidf).read())
        self.addCleanup(lambda: alive(grandchild) and os.kill(grandchild, 9))
        os.kill(p.pid, signal.SIGTERM)
        p.wait(timeout=30)
        self.assertTrue(self.gate_dies(grandchild), "a nested group of the gate outlived a TERM to its lander")

    def test_the_previous_handlers_are_back_after_a_normal_return(self):
        import signal
        mine = lambda s, f: None   # noqa: E731
        before = signal.signal(signal.SIGTERM, mine)
        self.addCleanup(signal.signal, signal.SIGTERM, before)
        r = LB.run_group([sys.executable, "-c", "pass"], 30, dict(os.environ))
        self.assertEqual(r.returncode, 0)
        self.assertIs(signal.getsignal(signal.SIGTERM), mine)

    def test_a_call_off_the_main_thread_installs_nothing_and_runs(self):
        import signal, threading
        held = {}
        before = signal.getsignal(signal.SIGTERM)
        t = threading.Thread(target=lambda: held.update(r=LB.run_group([sys.executable, "-c", "pass"], 30, dict(os.environ))))
        t.start(); t.join(60)
        self.assertEqual(held["r"].returncode, 0)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)


if __name__ == "__main__":
    unittest.main()

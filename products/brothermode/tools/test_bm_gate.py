#!/usr/bin/env python3
"""Tests for bm_gate, the admission gate.

Thresholds are pinned through the environment for every case, because a test that reads the
real load average is really testing whatever else happened to be running, and would have passed
or failed at random on the night this tool was written (the machine was at 0.3 GB free).

Run: python3 tools/test_bm_gate.py      (unittest output, exit 0 or 1)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bm_gate.py")
# Thresholds a loaded machine can still satisfy, so admission is decided by the LOGIC under test.
PERMISSIVE = {"BM_GATE_LOAD_ADMIT": "9999", "BM_GATE_LOAD_RESUME": "9999",
              "BM_GATE_DISK_HEAVY_GB": "0", "BM_GATE_DISK_ANY_GB": "0"}
# Thresholds nothing can satisfy, to prove the refusal path fires.
HOSTILE = {"BM_GATE_LOAD_ADMIT": "0", "BM_GATE_LOAD_RESUME": "0",
           "BM_GATE_DISK_HEAVY_GB": "999999", "BM_GATE_DISK_ANY_GB": "999999"}


def run(cwd, argv, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    p = subprocess.run([sys.executable, GATE] + argv, cwd=cwd, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class AdmissionGate(unittest.TestCase):
    """One gate store, walked through the cases in order.

    The methods are NUMBERED because these cases share state and hand tokens
    forward: the release case needs the token the admission case printed, and
    the capacity case only means anything while the first lease is still held.
    unittest runs methods alphabetically rather than in source order, so the
    ordering has to live in the names.

    Where the original script silently skipped a dependent case when an earlier
    one produced no token, this asserts instead. A case that quietly does not
    run is the silent-success shape the gates in this project exist to remove.
    """

    token = None
    token2 = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-gate-")
        os.makedirs(os.path.join(cls.tmp, ".brothermode"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_an_unknown_class_is_refused_as_bad_input(self):
        # Never admitted by accident.
        code, out = run(self.tmp, ["ask", "not-a-class", "--lane", "l"], PERMISSIVE)
        self.assertEqual(code, 2, "unknown class refused: exit %d" % code)

    def test_02_a_willing_machine_admits_and_prints_a_token(self):
        code, out = run(self.tmp, ["ask", "suite", "--lane", "lane-a"], PERMISSIVE)
        self.assertTrue(code == 0 and out.startswith("GO "),
                        "willing machine admits: exit %d: %s" % (code, out[:80]))
        parts = out.split()
        self.assertGreater(len(parts), 1, "admission printed no token: %r" % out[:80])
        type(self).token = parts[1]

    def test_03_a_second_lane_waits_while_the_first_holds_the_slot(self):
        # capacity: suite is 1, so a SECOND lane must wait while the first holds it
        code, out = run(self.tmp, ["ask", "suite", "--lane", "lane-b"], PERMISSIVE)
        self.assertTrue(code == 75 and "at capacity" in out,
                        "second suite waits: exit %d: %s" % (code, out[:90]))

    def test_04_releasing_frees_the_slot(self):
        self.assertIsNotNone(self.token, "no token from the admission case to release")
        run(self.tmp, ["done", self.token], PERMISSIVE)
        code, out = run(self.tmp, ["ask", "suite", "--lane", "lane-b"], PERMISSIVE)
        self.assertEqual(code, 0, "slot frees after done: exit %d: %s" % (code, out[:80]))
        type(self).token2 = out.split()[1]

    def test_05_a_pressured_machine_refuses_with_a_retry_number(self):
        # a retry number rather than a wall
        code, out = run(self.tmp, ["ask", "build", "--lane", "lane-c"], HOSTILE)
        self.assertTrue(code == 75 and out.startswith("WAIT "),
                        "pressure refuses: exit %d: %s" % (code, out[:90]))
        try:
            secs = int(out.split()[1])
        except (IndexError, ValueError):
            self.fail("retry seconds not parseable from %r" % out[:60])
        self.assertTrue(30 <= secs <= 300, "retry is a bounded number: got %d" % secs)

    def test_06_a_running_lane_is_told_to_yield_at_its_own_safe_point(self):
        self.assertIsNotNone(self.token2, "no token from the release case to check")
        code, out = run(self.tmp, ["check", self.token2], HOSTILE)
        self.assertTrue(code == 75 and "PAUSE" in out,
                        "check says PAUSE under pressure: %s" % out[:80])
        code, out = run(self.tmp, ["check", self.token2], PERMISSIVE)
        self.assertTrue(code == 0 and "CONTINUE" in out,
                        "check says CONTINUE when calm: %s" % out[:80])

    def test_07_an_unknown_token_is_never_told_to_continue(self):
        code, out = run(self.tmp, ["check", "deadbeef"], PERMISSIVE)
        self.assertTrue(code == 75 and "PAUSE" in out,
                        "unknown token pauses: %s" % out[:80])

    def test_08_a_lease_nobody_released_stops_counting_past_its_ttl(self):
        state = os.path.join(self.tmp, ".brothermode", "gate.json")
        with open(state, "w") as f:
            json.dump({"leases": [{"token": "stale1234", "class": "suite", "lane": "dead-lane",
                                   "paths": [], "at": time.time() - (46 * 60)}]}, f)
        code, out = run(self.tmp, ["ask", "suite", "--lane", "lane-d"], PERMISSIVE)
        self.assertEqual(code, 0, "expired lease frees capacity: exit %d: %s" % (code, out[:90]))
        self.assertIn("expired", out.lower(), "expiry is reported, not silent: %s" % out[:90])

    def test_09_forecast_without_a_fence_store_is_no_data_never_clear(self):
        code, out = run(self.tmp, ["forecast", "--paths", "a/b.swift"], PERMISSIVE)
        self.assertTrue(code == 2 and "NO-DATA" in out,
                        "forecast without a store is NO-DATA: %s" % out[:90])


if __name__ == "__main__":
    unittest.main(verbosity=1)

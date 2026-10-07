#!/usr/bin/env python3
"""closing_pass quotes every unit's own NOT CLOSED reason and records it in the failure ledger (owner 2026-10-01).

Run: python3 -B scripts/loop/test_close_reason_captured.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_batch as LB  # noqa: E402

CLOSER_OUT = ("FX-06 running its done_check: python3 x.py\n"
              "FX-06 NOT CLOSED: the done_check exited 1: FAIL: test_ends\n"
              "FX-49 NOT CLOSED: its done_check is not a shape this tool will execute: a && b\n"
              "CLOSED  0 unit(s): none\n")


class FakeSh:
    def __init__(self, closer_rc=1, closer_out=CLOSER_OUT):
        self.calls, self.rc, self.out = [], closer_rc, closer_out

    def __call__(self, cmd, log, timeout=None):
        self.calls.append(cmd)
        if any(str(c).endswith("close_unit.py") for c in cmd):
            return subprocess.CompletedProcess(cmd, self.rc, self.out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


BOXED = []   # the ledger script's argv as record_close_red hands it to boxed() (review 17: tree code runs boxed)


def fake_boxed(argv, log, timeout, env, cwd=None, root=None, seed=()):
    """boxed() stand in: the ledger child records one row of its own taxonomy into the --path file it was given."""
    BOXED.append(list(argv))
    with open(argv[argv.index("--path") + 1], "a", encoding="utf-8") as fh:
        fh.write('{"class": "close"}\n')
    return subprocess.CompletedProcess(argv, 0, "recorded close\n", "")


def ledger_calls(sh):
    return [c for c in BOXED if any(str(x).endswith("failure_ledger.py") for x in c)]


class ClosingPassCapturesReasons(unittest.TestCase):
    def setUp(self):
        self._root, self._boxed, self._ledger, self._attempts = LB.code_root, LB.boxed, LB.FAILURE_LEDGER, LB.CLOSE_ATTEMPTS
        LB.code_root = lambda frozen=None: "/code"   # the lander's code root takes the frozen copy since review 15 (2026-10-03)
        LB.boxed = fake_boxed
        self.tmp = tempfile.mkdtemp(prefix="close-reason-")
        LB.FAILURE_LEDGER = os.path.join(self.tmp, "ledger.jsonl")   # never the estate's own ledger
        LB.CLOSE_ATTEMPTS = os.path.join(self.tmp, "attempts.jsonl")
        del BOXED[:]
        _scope = os.environ.pop("BROTHER_SCOPE", None)   # a run's scope never leaks into these cases (2026-10-04)
        self.addCleanup(lambda: os.environ.__setitem__("BROTHER_SCOPE", _scope) if _scope is not None else None)

    def tearDown(self):
        LB.code_root, LB.boxed, LB.FAILURE_LEDGER, LB.CLOSE_ATTEMPTS = self._root, self._boxed, self._ledger, self._attempts
        shutil.rmtree(self.tmp, True)

    def rows(self):
        with open(LB.FAILURE_LEDGER, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def test_all_eligible_reasons_are_quoted_per_unit(self):
        sh2 = FakeSh()
        out = LB.closing_pass(["FX-06"], sh2, "/log", "hub", "b")
        self.assertIn("CLOSE-RED FX-06: the done_check exited 1: FAIL: test_ends", out)
        self.assertIn("CLOSE-RED FX-49: its done_check is not a shape this tool will execute: a && b", out)
        self.assertFalse(any("CLOSED  0 unit(s)" in l for l in out), out)

    def test_every_refusal_reaches_the_failure_ledger(self):
        sh = FakeSh()
        LB.closing_pass(["FX-06"], sh, "/log", "hub", "b")
        rows = ledger_calls(sh)
        units = sorted(c[c.index("--unit") + 1] for c in rows)
        self.assertEqual(units, ["FX-06", "FX-49"])   # once per unit per pass: the sweep no longer reruns a named unit (2026-10-03)
        self.assertTrue(all(c[c.index("record") + 1] == "auto:close" for c in rows))
        self.assertTrue(any("FAIL: test_ends" in c[c.index("record") + 2] for c in rows))
        # the TRUSTED lander wrote the estate rows: the child's class, the lander's own unit and detail
        written = self.rows()
        self.assertEqual(sorted(r["unit"] for r in written), ["FX-06", "FX-49"])
        self.assertTrue(all(r["class"] == "close" and r["sub"] == r["unit"] for r in written))

    def test_a_child_class_of_the_wrong_shape_records_nothing(self):
        def lying(argv, log, timeout, env, cwd=None, root=None, seed=()):
            with open(argv[argv.index("--path") + 1], "a", encoding="utf-8") as fh:
                fh.write('{"class": "close\\nINJECTED"}\n')
            return subprocess.CompletedProcess(argv, 0, "", "")
        LB.boxed = lying
        self.assertIs(LB.record_close_red("U1", "red", None), False)
        self.assertFalse(os.path.exists(LB.FAILURE_LEDGER))

    def test_a_named_unit_without_a_reason_line_still_says_something(self):
        sh = FakeSh(1, "Traceback: boom\nCLOSED  0 unit(s): none\n")
        out = LB.closing_pass(["U1"], sh, "/log", "hub", "b")
        self.assertIn("CLOSE-RED U1: Traceback: boom", out)

    def test_a_ledger_failure_never_raises(self):
        def sh(cmd, log, timeout=None):
            if any(str(x).endswith("failure_ledger.py") for x in cmd): raise OSError("no ledger")
            return subprocess.CompletedProcess(cmd, 1, CLOSER_OUT, "")
        out = LB.closing_pass(["FX-06"], sh, "/log", "hub", "b")
        self.assertIn("CLOSE-RED FX-06: the done_check exited 1: FAIL: test_ends", out)

    def test_a_green_close_records_nothing(self):
        sh = FakeSh(0, "FX-06 DONE: OK\nCLOSED  1 unit(s): FX-06\n")
        LB.closing_pass(["FX-06"], sh, "/log", "hub", "b")
        self.assertEqual(ledger_calls(sh), [])


if __name__ == "__main__":
    unittest.main()

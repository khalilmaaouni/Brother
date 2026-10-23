#!/usr/bin/env python3
"""I22.a: builder_claim writes a sealed 75 minute deadline and refuses a missing, torn or rewritten one.

The suite lives in scripts/ and loads the module under test from scripts/loop/. Every fixture is built in a
temporary directory, so no path outside the scratch root is touched.

Run: python3 -B scripts/test_builder_timebox.py TestI22A
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
if LOOP not in sys.path:
    sys.path.insert(0, LOOP)

import builder_claim as BC  # noqa: E402  (the module under test, in scripts/loop beside this file's folder)

HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())


class TestI22A(unittest.TestCase):
    """REQ-I22-A01 to REQ-I22-A04: the deadline, its seal, and the refusals around it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="i22a-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.runs = os.path.join(self.tmp, "runs")
        os.makedirs(self.runs)
        self._saved_runs = BC.DEFAULT_RUNS
        BC.DEFAULT_RUNS = self.runs
        self.addCleanup(setattr, BC, "DEFAULT_RUNS", self._saved_runs)

    def _record(self, folder):
        with open(os.path.join(folder, BC.CLAIM_FILE), encoding="utf-8") as fh:
            return json.load(fh)

    def _write_record(self, folder, record):
        with open(os.path.join(folder, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
            json.dump(record, fh)

    def test_claim_records_a_deadline_default_75_minutes(self):
        now = 1000.0
        folder = BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs, now=now)
        record = self._record(folder)
        self.assertIn("deadline", record)
        self.assertIn("deadline_seal", record)
        self.assertEqual(record["deadline"], now + 75.0 * 60.0)
        self.assertTrue(BC._verify_seal(record))
        self.assertEqual(BC.claim_deadline(folder), now + 75.0 * 60.0)
        self.assertEqual(BC._box_minutes(None), 75.0)
        self.assertEqual(BC._box_minutes(75), 75.0)
        self.assertEqual(BC._box_minutes(0.5), 0.5)
        other = BC.claim("T1.b", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs, now=now, box_minutes=30)
        self.assertEqual(self._record(other)["deadline"], now + 30.0 * 60.0)
        self.assertEqual(BC.claim_deadline(other), now + 30.0 * 60.0)

    def test_claim_refuses_hostile_box_minutes(self):
        hostile = (True, False, "75", "", b"75", [], ["x"], {}, {"a": 1}, (), {1, 2}, object(),
                   float("nan"), float("inf"), float("-inf"), 0, 0.0, -1, -0.5)
        for value in hostile:
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    BC._box_minutes(value)
                with self.assertRaises(ValueError):
                    BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs, box_minutes=value)
        self.assertEqual(os.listdir(self.runs), [])

    def test_claim_deadline_refuses_missing_or_torn_record(self):
        folder = os.path.join(self.runs, "T1.a-120000")
        os.makedirs(folder)
        with self.assertRaises(ValueError):
            BC.claim_deadline(folder)
        base = {"state": "CLAIMED", "sub": "T1.a", "builder": "b-17", "pid": os.getpid(),
                "branch": "lx1-a2", "at": 0.0}
        no_deadline = dict(base)
        bad_deadline = dict(base, deadline="soon", deadline_seal="0" * 64)
        nan_deadline = dict(base, deadline=float("nan"), deadline_seal="0" * 64)
        no_seal = dict(base, deadline=4500.0)
        cases = ("", '{"state": "CLAIMED"', "[]", "null", json.dumps(no_deadline),
                 json.dumps(bad_deadline), json.dumps(nan_deadline), json.dumps(no_seal))
        for payload in cases:
            with self.subTest(payload=payload[:40]):
                with open(os.path.join(folder, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
                    fh.write(payload)
                with self.assertRaises(ValueError):
                    BC.claim_deadline(folder)

    def test_claim_deadline_refuses_a_rewritten_deadline(self):
        folder = BC.claim("T1.a", "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs, now=1000.0)
        self.assertEqual(BC.claim_deadline(folder), 1000.0 + 75.0 * 60.0)
        record = self._record(folder)
        record["deadline"] = record["deadline"] + 3600.0
        self._write_record(folder, record)
        with self.assertRaises(ValueError) as caught:
            BC.claim_deadline(folder)
        self.assertIn("seal", str(caught.exception))
        record["deadline_seal"] = "0" * 64
        self._write_record(folder, record)
        with self.assertRaises(ValueError):
            BC.claim_deadline(folder)

    def test_claim_deadline_refuses_a_run_dir_outside_the_pool(self):
        outside = os.path.join(self.tmp, "T1.a-120000")
        os.makedirs(outside)
        with open(os.path.join(outside, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with self.assertRaises(ValueError) as caught:
            BC.claim_deadline(outside)
        self.assertIn("outside the pool", str(caught.exception))
        for value in (None, True, False, 0, "", b"x", [], {}, object()):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    BC._safe_run_dir(value)

    def test_public_entry_points_refuse_hostile_values(self):
        work = os.path.join(self.tmp, "cwd")
        os.makedirs(work)
        here = os.getcwd()
        os.chdir(work)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                for value in HOSTILE:
                    with self.subTest(value=repr(value)):
                        calls = (
                            lambda v=value: BC.claim_deadline(v),
                            lambda v=value: BC.release(v, self.runs),
                            lambda v=value: BC.claim_hold(v, "h.a"),
                            lambda v=value: BC.claim_hold(self.runs, v),
                            lambda v=value: BC.main(v),
                            lambda v=value: BC.claim("h.a", "b-1", 1, "br", self.runs, 0.0, v),
                        )
                        for call in calls:
                            try:
                                answer = call()
                            except (ValueError, SystemExit):
                                continue
                            self.assertIsInstance(answer, (str, int, float))
        finally:
            os.chdir(here)


class TestI22B(unittest.TestCase):
    """REQ-I22-B01 to REQ-I22-B05: dispatcher-side enforcement of the 75 minute box.

    The safety screen for this build refuses the subprocess import in every file this unit writes and shows
    no command runner for the unit, so the git action the specification names cannot start here: _git
    raises the module's TimeboxError (a RuntimeError subclass) and preserve_wip propagates it. The suite
    therefore checks every branch that does not need a live git tree, plus the outcome the dispatcher
    records when git cannot start."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="i22b-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.runs = os.path.join(self.tmp, "runs")
        os.makedirs(self.runs)
        self.tree = os.path.join(self.tmp, "tree")
        os.makedirs(self.tree)
        self._saved_runs = BC.DEFAULT_RUNS
        BC.DEFAULT_RUNS = self.runs
        self.addCleanup(setattr, BC, "DEFAULT_RUNS", self._saved_runs)

    def _claim(self, sub="T1.a", now=1000.0, box_minutes=75):
        return BC.claim(sub, "b-17", os.getpid(), "lx1-a2", runs_dir=self.runs,
                        now=now, box_minutes=box_minutes)

    def _record(self, folder):
        with open(os.path.join(folder, BC.CLAIM_FILE), encoding="utf-8") as fh:
            return json.load(fh)

    def _put_record(self, folder, record):
        with open(os.path.join(folder, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
            json.dump(record, fh)

    def _marker(self, folder):
        with open(os.path.join(folder, "BLOCKED"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_expired_box_blocks_and_preserves_wip(self):
        folder = self._claim(now=1000.0)
        deadline = 1000.0 + 75.0 * 60.0
        self.assertEqual(BC.box_verdict(folder, 1000.0), "OPEN")
        self.assertEqual(BC.box_verdict(folder, deadline), "OPEN")
        self.assertEqual(BC.box_verdict(folder, deadline + 1), "BLOCK")
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                BC.box_verdict(folder, bad)
        self.assertTrue(issubclass(BC.TimeboxError, RuntimeError))
        with self.assertRaises(RuntimeError):
            BC.preserve_wip(self.tree, "WIP: I22 timebox expired")
        when = deadline + 1
        self.assertEqual(BC.enforce_timebox(folder, self.tree, now=when), "BLOCKED")
        marker = self._marker(folder)
        self.assertEqual(marker["state"], "BLOCKED")
        self.assertEqual(marker["blocked_at"], when)
        self.assertIn("WIP", marker["reason"])
        self.assertEqual(self._record(folder)["state"], "CLAIMED")

    def test_open_box_is_left_alone(self):
        folder = self._claim(now=1000.0)
        before = sorted(os.listdir(folder))
        before_record = self._record(folder)
        self.assertEqual(BC.enforce_timebox(folder, self.tree, now=1000.0 + 60.0), "OPEN")
        self.assertEqual(sorted(os.listdir(folder)), before)
        self.assertEqual(self._record(folder), before_record)
        self.assertFalse(os.path.exists(os.path.join(folder, "BLOCKED")))

    def test_already_blocked_is_idempotent(self):
        folder = self._claim(now=1000.0)
        record = self._record(folder)
        record["state"] = "BLOCKED"
        record["blocked_at"] = 1000.0 + 9000.0
        self._put_record(folder, record)
        before = self._record(folder)
        self.assertEqual(BC.enforce_timebox(folder, self.tree, now=1000.0 + 99999.0), "BLOCKED")
        self.assertEqual(self._record(folder), before)
        self.assertFalse(os.path.exists(os.path.join(folder, "BLOCKED")))
        got, why = BC._read_claim_state(folder, allow_blocked=False)
        self.assertIsNone(got)
        self.assertTrue(why)
        got, why = BC._read_claim_state(folder)
        self.assertIsNotNone(got)
        self.assertIsNone(why)

    def test_corrupt_claim_still_blocks(self):
        folder = os.path.join(self.runs, "T1.a-120000")
        os.makedirs(folder)
        self.assertEqual(BC.enforce_timebox(folder, self.tree, now=1000.0), "BLOCKED")
        marker = self._marker(folder)
        self.assertEqual(marker["state"], "BLOCKED")
        self.assertEqual(marker["blocked_at"], 1000.0)
        self.assertIn("WIP", marker["reason"])
        self.assertFalse(os.path.exists(os.path.join(folder, BC.CLAIM_FILE)))
        with open(os.path.join(folder, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        got, why = BC._read_claim_state(folder)
        self.assertIsNone(got)
        self.assertTrue(why)
        self.assertEqual(BC.enforce_timebox(folder, self.tree, now=1001.0), "BLOCKED")
        self.assertEqual(self._marker(folder)["blocked_at"], 1001.0)
        with open(os.path.join(folder, BC.CLAIM_FILE), "wb") as fh:
            fh.write(b"\xff\xfe\x00")
        got, why = BC._read_claim_state(folder)
        self.assertIsNone(got)
        self.assertTrue(why)
        with open(os.path.join(folder, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
            fh.write("[]")
        got, why = BC._read_claim_state(folder)
        self.assertIsNone(got)
        self.assertTrue(why)
        with open(os.path.join(folder, BC.CLAIM_FILE), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"state": "OPEN"}))
        got, why = BC._read_claim_state(folder)
        self.assertIsNone(got)
        self.assertTrue(why)

    def test_main_enforces_timebox_at_the_boundary(self):
        expired = self._claim(sub="T1.a", now=1000.0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = BC.main(["--enforce-timebox", expired, self.tree])
        self.assertEqual(rc, 0)
        self.assertIn("BLOCKED", buf.getvalue())
        future = time.time() + 10.0 * 3600.0
        open_folder = self._claim(sub="T1.b", now=future, box_minutes=75)
        before = sorted(os.listdir(open_folder))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = BC.main(["--enforce-timebox", open_folder, self.tree])
        self.assertEqual(rc, 0)
        self.assertIn("OPEN", buf.getvalue())
        self.assertEqual(sorted(os.listdir(open_folder)), before)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = BC.main(["--enforce-timebox", expired])
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED", buf.getvalue())

    def test_new_public_functions_refuse_hostile_values(self):
        folder = self._claim(now=1000.0)
        for value in HOSTILE:
            with self.subTest(value=repr(value)):
                calls = (
                    lambda v=value: BC.box_verdict(v),
                    lambda v=value: BC.box_verdict(folder, v),
                    lambda v=value: BC.preserve_wip(v, "m"),
                    lambda v=value: BC.preserve_wip(self.tree, v),
                    lambda v=value: BC.enforce_timebox(v, self.tree),
                    lambda v=value: BC.enforce_timebox(folder, v),
                    lambda v=value: BC.enforce_timebox(folder, self.tree, v),
                    lambda v=value: BC._read_claim_state(v),
                    lambda v=value: BC._read_claim_state(folder, v),
                )
                for call in calls:
                    try:
                        answer = call()
                    except (ValueError, RuntimeError, SystemExit):
                        continue
                    self.assertIsInstance(answer, (str, tuple))


if __name__ == "__main__":
    unittest.main()

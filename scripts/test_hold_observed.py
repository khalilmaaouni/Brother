#!/usr/bin/env python3
"""D-7 (U11, B5-03, objection 14): every HOLD or PAUSE the one control reader sees is recorded in the run's history.

loop_hold.reason() is the one reader every paid route asks (the driver, runners, waves). When it returns a reason and
the run's proof history exists ($BROTHER_RUN_DIR/proof/events.jsonl), it appends one hold-observed row carrying kind,
detail, where, pid and observed_at, so a run the owner paused never reads as unattended. When that append fails it
creates $BROTHER_RUN_DIR/proof-events-failed (a second file in a second directory, which acceptance reads as NO-DATA)
and writes one stderr line. It never creates a missing history, and nothing is appended after proof-end took the end
snapshot (proof/evidence.json exists, or the history already ends with its end marker).

Every case drives the entry point, reason() itself or loop_hold.py run as a script, with one condition each.
Run: python3 -B scripts/test_hold_observed.py
"""
import contextlib
import importlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, LOOP)
import loop_hold  # noqa: E402  the loop's own copy, never a same-named file elsewhere on the path


class HoldObserved(unittest.TestCase):
    def setUp(self):
        root = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
        os.makedirs(root, exist_ok=True)
        self.box = tempfile.mkdtemp(prefix="run-hold-observed-", dir=root)
        self.ev = os.path.join(self.box, "evidence")
        self.run = os.path.join(self.box, "run-RB-x")
        self.proof = os.path.join(self.run, "proof")
        os.makedirs(self.ev)
        os.makedirs(self.proof)
        self.events = os.path.join(self.proof, "events.jsonl")
        with open(self.events, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "start-marker", "detail": "proof start", "observed_at": "2026-01-01T00:00:00+00:00"}) + "\n")
        self.saved = os.environ.get("BROTHER_RUN_DIR")
        os.environ["BROTHER_RUN_DIR"] = self.run
        importlib.reload(loop_hold)

    def tearDown(self):
        os.chmod(self.proof, 0o755)
        if os.path.exists(self.events):
            os.chmod(self.events, 0o644)
        shutil.rmtree(self.box, ignore_errors=True)
        if self.saved is None:
            os.environ.pop("BROTHER_RUN_DIR", None)
        else:
            os.environ["BROTHER_RUN_DIR"] = self.saved

    def pause(self, text="owner: going out"):
        with open(os.path.join(self.ev, "LOOP-PAUSE.txt"), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")

    def rows(self):
        with open(self.events, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def test_a_pause_is_recorded_once_with_its_fields(self):
        self.pause()
        why = loop_hold.reason(self.ev, where="unit_runner start")
        self.assertTrue(why.startswith("PAUSE: owner: going out"))
        rows = self.rows()
        self.assertEqual([r["kind"] for r in rows], ["start-marker", "hold-observed"])
        row = rows[1]
        self.assertEqual(row["detail"], why)
        self.assertEqual(row["where"], "unit_runner start")
        self.assertEqual(row["pid"], os.getpid())
        self.assertTrue(row["observed_at"].endswith("+00:00"))

    def test_a_hold_is_recorded_too(self):
        with open(os.path.join(self.ev, "LOOP-HOLD.txt"), "w", encoding="utf-8") as fh:
            fh.write("owner: stop the loop\n")
        loop_hold.reason(self.ev)
        self.assertEqual([r["kind"] for r in self.rows()], ["start-marker", "hold-observed"])
        self.assertIn("HOLD: owner: stop the loop", self.rows()[1]["detail"])

    def test_no_control_file_records_nothing(self):
        self.assertIsNone(loop_hold.reason(self.ev))
        self.assertEqual([r["kind"] for r in self.rows()], ["start-marker"])

    def test_an_unwritable_history_creates_the_failure_file_and_says_so_once(self):
        self.pause()
        os.chmod(self.events, stat.S_IRUSR)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            why = loop_hold.reason(self.ev, where="driver")
        self.assertIsNotNone(why)                     # the hold still holds: recording never changes the answer
        self.assertTrue(os.path.isfile(os.path.join(self.run, "proof-events-failed")))
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1, err.getvalue())
        self.assertIn("proof-events-failed", err.getvalue())

    def test_a_missing_history_is_never_created(self):
        os.remove(self.events)
        self.pause()
        loop_hold.reason(self.ev)
        self.assertFalse(os.path.lexists(self.events))
        self.assertFalse(os.path.lexists(os.path.join(self.run, "proof-events-failed")))

    def test_nothing_is_appended_after_the_end_snapshot(self):
        with open(os.path.join(self.proof, "evidence.json"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        before = open(self.events, "rb").read()
        self.pause()
        loop_hold.reason(self.ev)
        self.assertEqual(open(self.events, "rb").read(), before)

    def test_nothing_is_appended_after_the_end_marker(self):
        with open(self.events, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "end-marker", "detail": "proof end", "observed_at": "2026-01-01T00:00:09+00:00"}) + "\n")
        before = open(self.events, "rb").read()
        self.pause()
        loop_hold.reason(self.ev)
        self.assertEqual(open(self.events, "rb").read(), before)

    def test_no_run_directory_records_nothing(self):
        os.environ.pop("BROTHER_RUN_DIR")
        self.pause()
        self.assertIsNotNone(loop_hold.reason(self.ev))
        self.assertEqual([r["kind"] for r in self.rows()], ["start-marker"])

    def test_gate_records_where_it_was_asked(self):
        self.pause()
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as stop:
                loop_hold.gate(self.ev, where="probe_wave")
        self.assertEqual(stop.exception.code, loop_hold.HELD_EXIT)
        self.assertEqual(self.rows()[1]["where"], "probe_wave")

    def test_the_script_entry_point_records_with_the_where_it_was_given(self):
        home = os.path.join(self.box, "home")
        os.makedirs(os.path.join(home, ".claude"))
        os.symlink(self.ev, os.path.join(home, ".claude", "evidence"))
        self.pause()
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "loop_hold.py"), "driver"], capture_output=True, text=True,
                           env=dict(os.environ, HOME=home, BROTHER_RUN_DIR=self.run), timeout=60)
        self.assertEqual(r.returncode, loop_hold.HELD_EXIT, r.stdout + r.stderr)
        self.assertEqual([(x["kind"], x["where"]) for x in self.rows()[1:]], [("hold-observed", "driver")])


if __name__ == "__main__":
    unittest.main()

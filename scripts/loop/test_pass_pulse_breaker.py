#!/usr/bin/env python3
"""Tests for the BREAKER pulse line (FX-11.7): scripts/loop/pass_pulse.py breaker_lines and pulse.

Run: python3 -B scripts/loop/test_pass_pulse_breaker.py
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import breaker  # noqa: E402
import pass_pulse  # noqa: E402

KEY = "claude:default:opus55"
NODATA_ARG = "BREAKER NO-DATA: the run start is not a number"


class _Scratch(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pulse-breaker-state-")
        self.ev = tempfile.mkdtemp(prefix="pulse-breaker-ev-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.ev, True)
        p = mock.patch.dict(os.environ, {"BROTHER_OR_STATE_ROOT": self.root, "PULSE_EVIDENCE": self.ev,
                                         "BROTHER_BREAKER": "on"})
        p.start()
        self.addCleanup(p.stop)

    def trip(self, key=KEY):
        now = time.time()
        for i in range(3):
            breaker.record(key, "LIMIT", "call-%d" % i, detail="HTTP 429 from OpenRouter", now=now)

    def lines(self, run_start=0.0, env=None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return pass_pulse.breaker_lines(run_start, env)


class BreakerLines(_Scratch):
    def test_off_prints_breaker_off(self):
        for value in ("off", "", " OFF "):
            with mock.patch.dict(os.environ, {"BROTHER_BREAKER": value}):
                self.assertEqual(self.lines(), (["BREAKER off"], []))
        self.assertEqual(self.lines(0.0, {"BROTHER_OR_STATE_ROOT": self.root}), (["BREAKER off"], []))
        self.assertFalse(os.path.exists(os.path.join(self.root, "breakers.json")))

    def test_no_state_reads_closed(self):
        self.assertEqual(self.lines(), (["BREAKER closed"], []))
        self.assertEqual(self.lines(0), (["BREAKER closed"], []))

    def test_open_key_is_a_warn(self):
        self.trip()
        lines, warns = self.lines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^BREAKER claude:default:opus55 open until \d\d:\d\d \(LIMIT\)$")
        self.assertEqual(len(warns), 1)
        self.assertTrue(warns[0].startswith("WARN BREAKER-OPEN: claude:default:opus55 open until "), warns)

    def test_quarantine_is_alarm_and_warn(self):
        with open(os.path.join(self.root, "breakers.json"), "wb") as fh:
            fh.write(b"not json")
        with contextlib.redirect_stderr(io.StringIO()):
            breaker.admit(["bridge:u:m"], "c1")
        lines, warns = self.lines(time.time() - 60)
        self.assertIn("BREAKER closed", lines)
        alarms = [x for x in lines if x.startswith("BREAKER ALARM: corrupt breaker file quarantined as breakers.corrupt-")]
        self.assertEqual(len(alarms), 1, lines)
        self.assertEqual(len(warns), 1, warns)
        self.assertTrue(warns[0].startswith("WARN BREAKER-ALARM: corrupt breaker file quarantined as breakers.corrupt-"))
        self.assertEqual(self.lines(time.time() + 3600), (["BREAKER closed"], []))

    def test_unknown_switch_is_nodata(self):
        with mock.patch.dict(os.environ, {"BROTHER_BREAKER": "yes"}):
            lines, warns = self.lines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("BREAKER NO-DATA: BROTHER_BREAKER=yes"), lines)
        self.assertEqual(warns, [])
        lines, warns = self.lines(0.0, {"BROTHER_BREAKER": "maybe", "BROTHER_OR_STATE_ROOT": self.root})
        self.assertTrue(lines[0].startswith("BREAKER NO-DATA: BROTHER_BREAKER=maybe"), lines)
        self.assertEqual(warns, [])

    def test_unreadable_state_is_nodata(self):
        not_a_dir = os.path.join(self.root, "afile")
        with open(not_a_dir, "wb") as fh:
            fh.write(b"x")
        lines, warns = self.lines(0.0, {"BROTHER_BREAKER": "on", "BROTHER_OR_STATE_ROOT": not_a_dir})
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("BREAKER NO-DATA"), lines)
        self.assertEqual(warns, [])

    def test_raising_summary_is_nodata(self):
        with mock.patch.object(breaker, "summary", side_effect=RuntimeError("boom")):
            self.assertEqual(self.lines(), (["BREAKER NO-DATA: RuntimeError"], []))

    def test_hostile_arguments_refused(self):
        for bad in (True, False, None, b"x", "1", [], {}, float("nan"), float("inf"), float("-inf")):
            self.assertEqual(self.lines(bad), ([NODATA_ARG], []), repr(bad))
        for env in ("x", [1], 5, b"on", {"BROTHER_BREAKER": 5}, {"BROTHER_BREAKER": b"on"},
                    {"BROTHER_BREAKER": "on", "BROTHER_OR_STATE_ROOT": 7}):
            lines, warns = self.lines(0.0, env)
            self.assertEqual(len(lines), 1, repr(env))
            self.assertTrue(lines[0].startswith("BREAKER NO-DATA"), (env, lines))
            self.assertEqual(warns, [])


class PulseLine(_Scratch):
    def run_pulse(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = pass_pulse.pulse(time.time() - 60, 3, "")
        return code, out.getvalue()

    def read(self, name):
        with open(os.path.join(self.ev, name), "rb") as fh:
            return fh.read().decode("utf-8", "replace")

    def test_pulse_prints_breaker_line_after_losses(self):
        self.trip()
        code, out = self.run_pulse()
        self.assertEqual(code, 0)
        self.assertLess(out.index("LOSSES"), out.index("BREAKER claude:default:opus55 open until"))
        self.assertIn("WARN BREAKER-OPEN: claude:default:opus55 open until", out)
        self.assertIn("BREAKER claude:default:opus55 open until", self.read("LOOP-PULSE.md"))
        self.assertIn("WARN BREAKER-OPEN: claude:default:opus55", self.read("LOOP-WARN.txt"))

    def test_pulse_off_prints_breaker_off(self):
        with mock.patch.dict(os.environ, {"BROTHER_BREAKER": "off"}):
            code, out = self.run_pulse()
        self.assertEqual(code, 0)
        self.assertIn("BREAKER off", out.splitlines())

    def test_pulse_survives_raising_summary(self):
        with mock.patch.object(breaker, "summary", side_effect=RuntimeError("boom")):
            code, out = self.run_pulse()
        self.assertEqual(code, 0)
        self.assertIn("BREAKER NO-DATA: RuntimeError", out.splitlines())


if __name__ == "__main__":
    unittest.main()

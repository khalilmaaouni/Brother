#!/usr/bin/env python3
"""LIMIT-05 must fail toward DRAIN. An unknown run window never reads as RUN.

WHY THIS ONE. run_window.py is the single place every unattended tick learns the stop hour,
and its own docstring records the incident that created it: a watchdog carrying its own copy
of the drain time kept 10:30 after the founder moved the stop to 16:00. The expensive failure
is not a crash, it is a QUIET fail-open: a window that cannot be read reporting RUN, so an
unattended night keeps admitting new long units past the hard stop and nobody sees it until
morning. Nothing downstream re-checks the hour, so this verdict is the only guard there is.

Measured against the module as shipped (2026-09-21): a missing plan, a plan with no window
key, a window with a malformed timestamp, and a window whose drain_start is AFTER its
hard_stop all print DRAIN NO-DATA and exit 2. This test pins that direction, plus the STOP
boundary being inclusive, because an exclusive one admits work in the last second of the run.

Run: python3 scripts/test_run_window_guard.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import run_window  # noqa: E402

GOOD = {"drain_start": "2026-09-21T14:00:00+09:00", "hard_stop": "2026-09-21T16:00:00+09:00"}


def call(plan_path, now):
    """run_window.main as the tick calls it, with its stdout captured. Returns (exit code, text)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = run_window.main(["--plan", plan_path, "--now", now])
    return code, buf.getvalue()


class RunWindowFailsTowardDrain(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="run-window-guard-")
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)

    def plan(self, payload):
        """A plan file built here, so this test reads no live repository document."""
        path = os.path.join(self.dir, "plan.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def test_a_readable_window_still_answers_all_three_phases(self):
        # the guard must not be satisfied by a module that simply always says DRAIN
        path = self.plan({"window": GOOD})
        for now, expected in (("2026-09-21T09:00:00+09:00", "RUN"),
                              ("2026-09-21T15:00:00+09:00", "DRAIN"),
                              ("2026-09-21T17:00:00+09:00", "STOP")):
            code, text = call(path, now)
            self.assertEqual(code, 0, text)
            self.assertTrue(text.startswith(expected), "%s at %s: %r" % (expected, now, text))

    def test_the_hard_stop_second_itself_is_STOP(self):
        # exclusive would admit a new long unit in the final second of the run
        code, text = call(self.plan({"window": GOOD}), GOOD["hard_stop"])
        self.assertEqual(code, 0, text)
        self.assertTrue(text.startswith("STOP"), text)

    def test_every_unreadable_window_is_DRAIN_NO_DATA_and_exit_2(self):
        cases = {
            "absent plan file": os.path.join(self.dir, "there-is-no-such-plan.json"),
            "not json at all": self.plan("{this is not json"),
            "no window key": self.plan({"units": []}),
            "window is not a mapping": self.plan({"window": "16:00"}),
            "missing hard_stop": self.plan({"window": {"drain_start": GOOD["drain_start"]}}),
            "malformed timestamp": self.plan({"window": dict(GOOD, hard_stop="four in the afternoon")}),
            "drain after stop": self.plan({"window": {"drain_start": GOOD["hard_stop"],
                                                      "hard_stop": GOOD["drain_start"]}}),
        }
        for why, path in cases.items():
            with self.subTest(why=why):
                code, text = call(path, "2026-09-21T09:00:00+09:00")
                # 09:00 is inside the RUN phase of a GOOD window, so a fail-open shows up here as RUN
                self.assertEqual(code, 2, "%s must exit 2, got %s: %r" % (why, code, text))
                self.assertTrue(text.startswith("DRAIN"), "%s: %r" % (why, text))
                self.assertIn("NO-DATA", text, why)
                self.assertNotIn("RUN ", text, "%s read as RUN: %r" % (why, text))


if __name__ == "__main__":
    unittest.main()

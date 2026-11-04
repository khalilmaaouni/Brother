#!/usr/bin/env python3
"""CUT2.a edges at the driver's entry points (drive and main): a second ask follows a running cut and writes
nothing, a stopped run is resumed with no flag, a fresh one starts cold, and a missing or empty release notes file
stops the walk before any phase up to the cut runs. Fixtures and a fake runner only, through test_cut_driver's
Fixture: nothing here runs a real cut, rehearsal, export, push, tag or gh call.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_driver as D  # noqa: E402
from test_cut_driver import GREEN, V, Fixture, Runner  # noqa: E402


class TheDoorChoosesTheMode(Fixture):
    def lock(self):
        lock = D.state_path(V, self.cand)[:-5] + ".lock"
        os.makedirs(self.ev, exist_ok=True)
        with open(lock, "w") as fh:
            fh.write("%d\n" % os.getppid())
        return lock

    def saved(self, *names):
        path = D.state_path(V, self.cand)
        D.save_state(path, self.state(self.passed(*names)))
        return path

    def test_a_second_ask_follows_the_running_cut_and_writes_nothing(self):
        path = self.saved("ready", "freeze")
        lock = self.lock()
        with open(path, "rb") as fh:
            before = fh.read()
        code, lines, runner = self.drive()
        self.assertEqual(code, 2)
        self.assertTrue(lines[0].startswith("FOLLOW: another driver holds"), lines)
        self.assertIn("at the preflight phase", lines[0])
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual([c for c in runner.calls if c[0] != "git"], [])
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertTrue(os.path.exists(lock))

    def test_a_follow_with_no_state_yet_names_the_first_phase(self):
        self.lock()
        code, lines, _ = self.drive()
        self.assertEqual(code, 2)
        self.assertIn("at the first phase", lines[0])
        self.assertFalse(os.path.exists(D.state_path(V, self.cand)))

    def test_a_stopped_run_is_resumed_through_main_with_no_flag(self):
        path = self.saved("ready", "freeze", "preflight")
        runner = Runner(GREEN)
        out = io.StringIO()
        argv = ["run", "--version", V, "--body", self.body, "--release-branch", "main"]
        with mock.patch("sys.stdout", out), mock.patch.object(D, "power_refusal", lambda power=None: ""), \
                mock.patch.object(D, "_default_sleeps", lambda start, end: ""):
            D.main(argv, root=self.root, runner=runner)
        self.assertEqual(runner.saw("cut_preflight.py"), [])
        self.assertTrue(runner.saw("cut_rehearsal.py"))
        with open(path) as fh:
            self.assertEqual(json.load(fh)["mode"], "RESUME")

    def test_a_first_run_is_cold_and_says_so_in_its_state(self):
        code, lines, runner = self.drive()
        self.assertTrue(runner.saw("cut_preflight.py"))
        with open(D.state_path(V, self.cand)) as fh:
            self.assertEqual(json.load(fh)["mode"], "COLD")

    def test_a_missing_or_empty_body_stops_before_any_phase(self):
        empty = os.path.join(self.tmp, "EMPTY.md")
        open(empty, "w").close()
        for body in (os.path.join(self.tmp, "absent.md"), empty, ""):
            self.body = body
            code, lines, runner = self.drive(seed_ready=False)
            self.assertEqual(code, 2, body)
            self.assertTrue(lines[0].startswith("PHASE ready NO-DATA"), lines)
            self.assertIn("release notes file", lines[0], body)
            self.assertEqual(len(lines), 1, lines)
            self.assertEqual([c for c in runner.calls if c[0] != "git"], [], body)
            self.assertFalse(os.path.exists(D.state_path(V, self.cand)[:-5] + ".lock"), body)

    def test_a_resumed_run_with_no_body_never_starts_the_rehearsal(self):
        self.saved("ready", "freeze", "preflight")
        self.body = os.path.join(self.tmp, "absent.md")
        code, lines, runner = self.drive(seed_ready=False)
        self.assertEqual(code, 2)
        self.assertTrue(lines[0].startswith("PHASE rehearsal NO-DATA"), lines)
        self.assertEqual(runner.saw("cut_rehearsal.py"), [])


if __name__ == "__main__":
    unittest.main()

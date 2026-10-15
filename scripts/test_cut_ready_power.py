#!/usr/bin/env python3
"""FR0.f (docs/plan/specs/FR0.md): the power and lid rows of cut_ready and
the cut driver's power refusal and sleep note, driven through the fake runner
and throwaway git repository of scripts/test_cut_driver.py.

Covers REQ-FR0-29 to REQ-FR0-34. Python 3.9, standard library only. No
network. No em or en dashes.
"""
import datetime
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_driver as D  # noqa: E402
import cut_ready as R  # noqa: E402
import test_cut_driver as T  # noqa: E402

V = "9.9.9"
HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())
SLEEP_TEXT = "a sleep at 1760000000 fell inside this phase"
FIRST = datetime.datetime.strptime("2026-10-12 01:02:03 +0900", "%Y-%m-%d %H:%M:%S %z").timestamp()
SECOND = datetime.datetime.strptime("2026-10-12 02:00:00 +0900", "%Y-%m-%d %H:%M:%S %z").timestamp()
SLEEP_LOG = (
    "2026-10-12 01:02:03 +0900 Sleep          Entering Sleep state due to 'Idle Sleep'\n"
    "2026-10-12 01:02:04 +0900 Wake           Wake from Normal Sleep [CDNVA]\n"
    "2026-10-12 01:02:05 +0900 DarkWake       DarkWake from Normal Sleep\n"
    "2026-10-12 01:02:06 +0900 Wake Requests  [process=mDNSResponder]\n"
    "2026-10-12 01:02:07 +0900 Sleep          this line never says it entered sleep\n"
    "not a pmset line at all\n"
    "2026-10-12 02:00:00 +0900 Sleep          Entering Sleep state due to 'Low Power Sleep'\n"
)


class _SleepAfter(object):
    """A sleeps seam that answers '' for the first `after` calls and the note after that."""

    def __init__(self, after, text):
        self.after = after
        self.text = text
        self.calls = 0

    def __call__(self, start, end):
        self.calls += 1
        return self.text if self.calls > self.after else ""


class TestFR0F(T.Fixture):
    """FR0.f: refuse on battery or a closed lid before any phase; a Sleep inside a sensitive window is NO-DATA."""

    def load(self):
        state, why = D.load_state(D.state_path(V, self.cand), self.cand, self.tree, time.time())
        self.assertEqual(why, "")
        return state

    # REQ-FR0-29
    def test_battery_refuses_the_start_and_runs_no_phase(self):
        code, lines, runner = self.drive(power=lambda: ("battery", "open"))
        self.assertEqual(code, 2, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("NO-DATA: "), lines)
        self.assertIn("battery", lines[0])
        for probe in ("cut_ready.py", "close_ceremony_check.py", "cut_preflight.py", "cut_rehearsal.py", "cut.py"):
            self.assertEqual(runner.saw(probe), [], probe)
        self.assertFalse(os.path.exists(D.state_path(V, self.cand)),
                         "no state entry is written for a start that never began")

    # REQ-FR0-29
    def test_a_closed_lid_and_an_unreadable_source_refuse(self):
        for pair in (("ac", "closed"), ("unreadable", "open")):
            code, lines, runner = self.drive(power=lambda p=pair: p)
            self.assertEqual(code, 2, (pair, lines))
            self.assertEqual(len(lines), 1, (pair, lines))
            self.assertTrue(lines[0].startswith("NO-DATA: "), (pair, lines))
            for probe in ("close_ceremony_check.py", "cut_rehearsal.py"):
                self.assertEqual(runner.saw(probe), [], (pair, probe))

    # REQ-FR0-30
    def test_a_sleep_line_inside_a_rehearsal_window_reads_no_data_never_pass_or_fail(self):
        code, lines, _runner = self.drive(sleeps=_SleepAfter(3, SLEEP_TEXT))
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines[0].startswith("PHASE freeze PASS "), lines)
        self.assertTrue(lines[1].startswith("PHASE preflight PASS "), lines)
        self.assertTrue(lines[2].startswith("PHASE rehearsal NO-DATA "), lines)
        self.assertIn("1760000000", lines[2])
        self.assertIn("rerun it", lines[2])
        for name in ("adopt", "cut", "after"):
            self.assertFalse(any(ln.startswith("PHASE %s " % name) for ln in lines), lines)
        self.assertEqual(self.load()["phases"]["rehearsal"]["verdict"], "NO-DATA")

    # REQ-FR0-31
    def test_the_log_parser_reads_real_line_shapes_and_the_window_edges(self):
        times = R.sleep_times(SLEEP_LOG)
        self.assertEqual(len(times), 2, times)
        self.assertAlmostEqual(times[0], FIRST, places=3)
        self.assertAlmostEqual(times[1], SECOND, places=3)
        self.assertEqual(R.first_sleep_in(SLEEP_LOG, FIRST, FIRST), times[0])
        self.assertEqual(R.first_sleep_in(SLEEP_LOG, SECOND, SECOND), times[1])
        self.assertIsNone(R.first_sleep_in(SLEEP_LOG, FIRST - 10.0, FIRST - 1.0))
        self.assertIsNone(R.first_sleep_in(SLEEP_LOG, SECOND + 1.0, SECOND + 10.0))
        self.assertEqual(R.first_sleep_in(SLEEP_LOG, FIRST - 1.0, SECOND + 1.0), times[0])
        self.assertEqual(R.sleep_times("2026-10-12 01:02:04 +0900 Wake  Wake from Normal Sleep\n"), [])
        self.assertEqual(R.sleep_times("garbage\n"), [])
        for bad in (None, 0, True, [], b"x", object()):
            with self.assertRaises(ValueError):
                R.sleep_times(bad)
        for bad in (float("nan"), float("inf"), True, None, "12"):
            with self.assertRaises(ValueError):
                R.first_sleep_in(SLEEP_LOG, bad, 10.0)

    # REQ-FR0-32
    def test_the_cut_and_after_phases_keep_their_verdict_and_note_the_sleep(self):
        self.assertEqual(tuple(D.SLEEP_SENSITIVE), ("ready", "freeze", "preflight", "rehearsal"))
        self.assertEqual(tuple(R.SLEEP_SENSITIVE), tuple(D.SLEEP_SENSITIVE))
        for name in ("adopt", "cut", "after"):
            self.assertNotIn(name, D.SLEEP_SENSITIVE)
            self.assertEqual(D.sleep_note(name, 0.0, 5.0, sleeps=lambda a, b: SLEEP_TEXT), "",
                             "%s keeps its own verdict" % name)
        self.assertEqual(D.sleep_note("rehearsal", 0.0, 5.0, sleeps=lambda a, b: SLEEP_TEXT), SLEEP_TEXT)
        _code, lines, _runner = self.drive(sleeps=_SleepAfter(4, SLEEP_TEXT))
        adopt = [ln for ln in lines if ln.startswith("PHASE adopt ")]
        self.assertEqual(len(adopt), 1, lines)
        self.assertIn(SLEEP_TEXT, adopt[0])
        self.assertNotIn("rerun it", adopt[0])
        self.assertFalse(any(ln.startswith("PHASE rehearsal NO-DATA ") for ln in lines), lines)

    # REQ-FR0-33
    def test_power_lost_between_phases_stops_before_the_next_phase_and_resumes(self):
        seen = []

        def power():
            seen.append(1)
            return ("ac", "open") if len(seen) <= 2 else ("battery", "open")

        code, lines, runner = self.drive(power=power)
        self.assertEqual(code, 2, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("PHASE freeze NO-DATA "), lines)
        self.assertIn("battery", lines[0])
        self.assertEqual(runner.saw("close_ceremony_check.py"), [],
                         "the phase the power was lost before never ran")
        self.assertEqual(self.load()["phases"]["freeze"]["verdict"], "NO-DATA")
        code2, lines2, runner2 = self.drive(power=lambda: ("ac", "open"))
        self.assertEqual(code2, 2, lines2)
        self.assertTrue(any(ln.startswith("PHASE freeze PASS ") for ln in lines2), lines2)
        self.assertTrue(runner2.saw("close_ceremony_check.py"), lines2)

    # REQ-FR0-34
    def test_cut_ready_has_the_power_and_lid_rows_and_a_non_mac_host_is_no_data(self):
        rows = R.power_rows(source=lambda: "ac", lid=lambda: "open")
        self.assertEqual([r["name"] for r in rows], ["power-source", "lid"])
        self.assertEqual([r["verdict"] for r in rows], ["PASS", "PASS"])
        self.assertEqual(R.power_rows(source=lambda: "battery", lid=lambda: "open")[0]["verdict"], "FAIL")
        self.assertEqual(R.power_rows(source=lambda: "unreadable", lid=lambda: "open")[0]["verdict"], "FAIL")
        self.assertEqual([r["verdict"] for r in R.power_rows(source=lambda: "no-data", lid=lambda: "unreadable")],
                         ["NO-DATA", "NO-DATA"])
        self.assertEqual(R.power_rows(source=lambda: "ac", lid=lambda: "closed")[1]["verdict"], "FAIL")
        self.assertEqual(R.power_rows(source=0, lid=lambda: "open")[0]["verdict"], "FAIL")
        self.assertEqual(R.lid_state(lambda cmd: '"AppleClamshellState" = No\n'), "open")
        self.assertEqual(R.lid_state(lambda cmd: '"AppleClamshellState" = Yes\n'), "closed")
        self.assertEqual(R.lid_state(lambda cmd: "no such key\n"), "unreadable")
        self.assertEqual(R.lid_state("not a runner"), "unreadable")


class TestFR0FHostileInput(unittest.TestCase):
    """The hostile input contract: every new public function returns or refuses, never crashes."""

    def test_every_new_public_function_refuses_hostile_input(self):
        for value in HOSTILE:
            self.assertIsInstance(R.lid_state(value), str)
            self.assertIsInstance(D.power_refusal(value), str)
            self.assertIsInstance(D.sleep_note(value, value, value, value), str)
            try:
                rows = R.power_rows(value, value)
            except Exception as exc:
                self.fail("power_rows(%r, %r) raised %r" % (value, value, exc))
            self.assertEqual([r["name"] for r in rows], ["power-source", "lid"])
            try:
                parsed = R.sleep_times(value)
            except ValueError:
                parsed = []
            self.assertIsInstance(parsed, list)
            try:
                first = R.first_sleep_in(value, value, value)
            except ValueError:
                first = None
            self.assertTrue(first is None or isinstance(first, float))


if __name__ == "__main__":
    unittest.main()

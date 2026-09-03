#!/usr/bin/env python3
"""Calibration for scripts/memory_ab.py, benchmark rows D03 and D04.

The property under test is not that tasks run. It is that the harness can TELL
THE DIFFERENCE when memory changes an outcome, says NO DIFFERENCE when it does
not, and writes NO-DATA rather than zero for every measure it cannot take. The
indifferent-runner case is as load bearing as the sensitive one: a harness that
only ever reports lift is an advertisement, not an instrument.

No em or en dashes anywhere in this file.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_ab as ab  # noqa: E402

PY = sys.executable

# A runner whose output depends on BM_MEMORY: the lesson token appears only ON.
SENSITIVE = [PY, "-c",
             "import os,json;"
             "on=os.environ.get('BM_MEMORY')=='on';"
             "print(json.dumps({'output':'lesson-applied' if on else 'no idea','tokens_out':120 if on else 100}))"]
# A runner that ignores memory entirely.
INDIFFERENT = [PY, "-c",
               "import json;print(json.dumps({'output':'same either way'}))"]
# The check: succeed only when the lesson token is in the output.
CHECK = [PY, "-c", "import sys;sys.exit(0 if 'lesson-applied' in sys.stdin.read() else 1)"]

def task(tid="t1", check=None):
    return {"id": tid, "prompt": "fix the thing", "check": check or CHECK}


class TheHarnessDetectsAMemoryLift(unittest.TestCase):
    def test_a_memory_sensitive_task_gains_under_ON_and_only_under_ON(self):
        rows = ab.run([task()], SENSITIVE, timeout_s=30)
        summary = ab.report(rows, out=open(os.devnull, "w"))
        self.assertEqual(summary["gained"], ["t1"])
        self.assertEqual(summary["lost"], [])

    def test_token_delta_is_computed_when_both_sides_report(self):
        rows = ab.run([task()], SENSITIVE, timeout_s=30)
        self.assertEqual(ab.report(rows, out=open(os.devnull, "w"))["token_delta"], 20)


class TheHarnessRefusesToInventALift(unittest.TestCase):
    """The other direction, and the one an advertisement would skip."""

    def test_an_indifferent_runner_reports_NO_difference(self):
        rows = ab.run([task()], INDIFFERENT, timeout_s=30)
        summary = ab.report(rows, out=open(os.devnull, "w"))
        self.assertEqual(summary["gained"], [])
        self.assertEqual(summary["lost"], [])

    def test_absent_tokens_are_NO_DATA_never_zero(self):
        rows = ab.run([task()], INDIFFERENT, timeout_s=30)
        for r in rows:
            self.assertIsInstance(r["tokens_out"], str)
            self.assertTrue(r["tokens_out"].startswith("NO-DATA"))
        self.assertIsNone(ab.report(rows, out=open(os.devnull, "w"))["token_delta"])

    def test_the_unwired_measures_are_NO_DATA_with_a_reason(self):
        rows = ab.run([task()], SENSITIVE, timeout_s=30)
        for r in rows:
            for field in ("repeat_mistakes", "unsupported_claims", "human_corrections"):
                self.assertTrue(str(r[field]).startswith("NO-DATA"), (field, r[field]))


class ATaskThatSucceedsBothWaysIsNotALift(unittest.TestCase):
    """Found by calibration, not foresight: a report rewritten to count every ON
    success as gained passed all eight original tests, because no fixture
    succeeded under BOTH modes. This is that fixture, and the case that makes
    gained mean gained."""

    def test_success_in_both_modes_lands_in_same_not_gained(self):
        always = [PY, "-c", "import sys;sys.exit(0)"]
        rows = ab.run([task("t-easy", check=always)], INDIFFERENT, timeout_s=30)
        summary = ab.report(rows, out=open(os.devnull, "w"))
        self.assertEqual(summary["gained"], [])
        self.assertEqual(summary["lost"], [])
        self.assertEqual(summary["same"], 1)


class OneBrokenTaskCostsNothingElse(unittest.TestCase):
    def test_a_crashing_runner_is_recorded_and_the_next_task_still_measures(self):
        crash = [PY, "-c", "import sys;sys.exit(3)"]
        rows = ab.run([task("t-crash")], crash, timeout_s=30)
        self.assertFalse(rows[0]["success"])
        self.assertIn("exited 3", rows[0]["error"])
        good = ab.run([task("t-good")], SENSITIVE, timeout_s=30)
        self.assertEqual(ab.report(good, out=open(os.devnull, "w"))["gained"], ["t-good"])

    def test_malformed_runner_output_is_an_error_not_a_crash(self):
        garbage = [PY, "-c", "print('this is not json')"]
        rows = ab.run([task()], garbage, timeout_s=30)
        self.assertFalse(rows[0]["success"])
        self.assertIn("no usable JSON", rows[0]["error"])

    def test_a_one_sided_pair_is_dropped_not_averaged(self):
        rows = ab.run([task()], SENSITIVE, timeout_s=30)
        self.assertEqual(ab.pair(rows[:1]), {})


if __name__ == "__main__":
    unittest.main()

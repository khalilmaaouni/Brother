"""LIMIT-05 calibration: the phase comes from the plan's window, and an
unknown window never reads as RUN."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_window as N  # noqa: E402

WINDOW = {"drain_start": "2026-09-18T15:00:00+09:00",
          "hard_stop": "2026-09-18T16:00:00+09:00"}


class NightTick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plan = os.path.join(self.tmp.name, "wbs.json")

    def tearDown(self):
        self.tmp.cleanup()

    def tick(self, doc, now):
        with open(self.plan, "w", encoding="utf-8") as fh:
            fh.write(doc if isinstance(doc, str) else json.dumps(doc))
        out = io.StringIO()
        with redirect_stdout(out):
            code = N.main(["--plan", self.plan, "--now", now])
        return code, out.getvalue().split()[0]

    def test_phases_follow_the_plan_window(self):
        doc = {"window": WINDOW}
        self.assertEqual(self.tick(doc, "2026-09-18T10:30:00+09:00"), (0, "RUN"))
        self.assertEqual(self.tick(doc, "2026-09-18T15:00:00+09:00"), (0, "DRAIN"))
        self.assertEqual(self.tick(doc, "2026-09-18T16:00:00+09:00"), (0, "STOP"))

    def test_a_moved_window_is_honoured_without_touching_the_tick(self):
        doc = {"window": dict(WINDOW, drain_start="2026-09-18T10:30:00+09:00")}
        self.assertEqual(self.tick(doc, "2026-09-18T10:31:00+09:00"), (0, "DRAIN"))

    def test_unknown_window_is_drain_never_run(self):
        for doc in ("{broken", {}, {"window": {"hard_stop": "x"}},
                    {"window": dict(WINDOW, drain_start="2026-09-18T17:00:00+09:00")}):
            self.assertEqual(self.tick(doc, "2026-09-18T09:00:00+09:00"), (2, "DRAIN"), doc)

    def test_missing_plan_is_drain(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = N.main(["--plan", os.path.join(self.tmp.name, "none.json")])
        self.assertEqual((code, out.getvalue().split()[0]), (2, "DRAIN"))


if __name__ == "__main__":
    unittest.main()

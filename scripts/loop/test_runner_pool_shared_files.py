#!/usr/bin/env python3
"""LS1.c: a build READY to land keeps its files busy, so the pool never starts a sibling on the same file (rule S3).

The pool built its busy files from live runners only; a READY build waiting to land is in flight too (in_progress
already counts it), and a sibling started on its file is built against a tree that moves when it lands.

Run: python3 -B scripts/loop/test_runner_pool_shared_files.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner_pool as RP  # noqa: E402

TOUCH = {"A.a": {"scripts/x.py", "scripts/test_x.py"}, "B.a": {"scripts/x.py"}, "C.a": {"scripts/other.py"},
         "U.a": None}
SPEC_OF = {"A": "docs/plan/specs/A.md", "B": "docs/plan/specs/B.md", "C": "docs/plan/specs/C.md", "U": "docs/plan/specs/U.md"}


def touch(sub, spec_path):
    return TOUCH[sub]


class ReadyBuildsKeepTheirFilesBusy(unittest.TestCase):
    def test_a_sibling_on_a_ready_builds_file_is_held(self):
        busy, unknown = RP.busy_paths([("A", "A.a")], SPEC_OF, touch=touch)
        self.assertEqual(RP.touch_conflict(TOUCH["B.a"], busy, unknown), "TOUCH: shares scripts/x.py with A.a")

    def test_a_unit_on_other_files_still_starts(self):
        busy, unknown = RP.busy_paths([("A", "A.a")], SPEC_OF, touch=touch)
        self.assertEqual(RP.touch_conflict(TOUCH["C.a"], busy, unknown), "")

    def test_nothing_in_flight_holds_nothing(self):
        busy, unknown = RP.busy_paths([], SPEC_OF, touch=touch)
        self.assertEqual((busy, unknown), ({}, []))
        self.assertEqual(RP.touch_conflict(TOUCH["B.a"], busy, unknown), "")

    def test_an_unknown_touch_set_in_flight_is_named_and_holds(self):
        """Fail closed, as for a live runner: an in flight build whose files are unknown holds every start."""
        busy, unknown = RP.busy_paths([("U", "U.a")], SPEC_OF, touch=touch)
        self.assertEqual(unknown, ["U.a"])
        self.assertIn("unknown touch set", RP.touch_conflict(TOUCH["C.a"], busy, unknown))

    def test_the_pass_adds_ready_builds_to_busy(self):
        """The pass itself (not only the helper) folds READY builds into busy: read from the pool's own source."""
        src = open(os.path.join(HERE, "runner_pool.py"), encoding="utf-8").read()
        self.assertIn("busy.update(_b); unknown_alive.extend(_unk)", src)
        self.assertIn("x[\"id\"] in _ready - alive_units", src)


if __name__ == "__main__":
    unittest.main()

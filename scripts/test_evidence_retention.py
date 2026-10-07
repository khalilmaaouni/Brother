#!/usr/bin/env python3
"""evidence_retention's own suite. Drives the pure functions with fixtures; deletes nothing."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence_retention as E  # noqa: E402

FAKE = {"rel-1.0.13": (1_200_000_000, 30.0), "night-old": (411_000_000, 12.0),
        "unit-runs": (900_000_000, 0.1), "probe-round": (5_000_000, 0.2)}
NOW = 1_000_000.0


def call(**kw):
    return E.candidates(root="/x", now=NOW, listdir=lambda r: list(FAKE),
                        getmtime=lambda p: NOW - FAKE[os.path.basename(p)][1] * 86400,
                        sizer=lambda p: FAKE[os.path.basename(p)][0], **kw)


class ItReportsAndNeverSweeps(unittest.TestCase):
    def test_the_largest_candidate_is_first(self):
        self.assertEqual(call(older_days=0)[0][0], "rel-1.0.13")

    def test_the_loops_own_state_is_protected(self):
        self.assertTrue(all(n != "unit-runs" for n, _, _ in call(older_days=0)))

    def test_protected_covers_the_live_files_the_loop_reads(self):
        for name in ("unit-runs", "land-batch", "brother-loop.jsonl", "brother-failures.jsonl"):
            self.assertIn(name, E.PROTECTED)

    def test_an_age_filter_excludes_recent_runs(self):
        self.assertEqual([n for n, _, _ in call(older_days=7)], ["rel-1.0.13", "night-old"])

    def test_an_unreadable_root_reports_nothing_rather_than_crashing(self):
        self.assertEqual(E.candidates(root="/no/such/dir"), [])

    def test_sizes_read_in_human_units(self):
        self.assertTrue(E.human(1_200_000_000).endswith("GB"))
        self.assertTrue(E.human(900).endswith("B"))

    def test_an_unreadable_file_does_not_fail_the_whole_report(self):
        def walk(p):
            yield p, [], ["a", "missing"]
        self.assertIsInstance(E.size_of("/x", walk=walk), int)


if __name__ == "__main__":
    unittest.main(verbosity=1)

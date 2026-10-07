#!/usr/bin/env python3
"""test_release_plan_live: the release plan on THIS tree states the counts the launch board reads and its page is
current (CV1.d). Kept apart from test_cv1_cut_rehearsed.py on purpose: this goes red after any unit closure until the
plan is regenerated (python3 -B scripts/gen_release_plan.py), which is the alarm it exists to raise, and the later CV1
sub units extend that file, so their builds must never pay for a closure elsewhere (review 2026-10-04). Where this tree
ships no docs/plan (the public export) every case skips, stated: a skip is NO-DATA, never a pass.

Python 3.9, standard library only. No network. No em or en dashes."""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import gen_release_plan as G  # noqa: E402

LAUNCH = os.path.join(ROOT, G.LAUNCH)


@unittest.skipUnless(os.path.isfile(LAUNCH), "the plan files are not shipped in this tree")
class ReleasePlanLive(unittest.TestCase):
    def test_the_real_plan_states_the_counts_the_board_reads(self):
        plan, launch, _ = G.load(ROOT)
        self.assertEqual(G.stale_counts(plan, launch), [], "regenerate: python3 -B scripts/gen_release_plan.py")

    def test_the_real_check_passes_on_this_tree(self):
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "gen_release_plan.py"), "--check"],
                           cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS: the plan covers every open unit and the page is current", r.stdout)


if __name__ == "__main__":
    unittest.main()

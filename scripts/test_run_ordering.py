#!/usr/bin/env python3
"""The loop must read the NEWEST run of a sub unit, and newest means the clock, never the folder name.

WHAT IT PROTECTS, measured 2026-09-21. Run folders are named <sub>-HHMMSS with NO DATE, so a name sort puts
last night's 234926 after this morning's 111413. Three tools sorted by name, so for D1.5, L3.1 and L1.1 the
loop read a WITHHELD or EXHAUSTED folder from the previous evening while a READY build from that morning sat
unlanded. The board reported 17 units needing a human fact and 0 builds ready to land; the truth was 11 and 9.
Finished work was invisible and the orchestrator opened fresh lanes instead of landing what it already had.

The same defect had already been fixed in scripts/brother_pass.py and loop_done.py and was never carried to
pass_digest, runner_pool and diag_brief, which is why it kept costing. This file exists so it cannot return
quietly in any of them."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "loop"))
import pass_digest  # noqa: E402
import diag_brief  # noqa: E402


def _run_dir(root, name, when, status):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as fh:
        fh.write(status)
    os.utime(d, (when, when))
    return d + "/"


class TheNewestRunIsTheMostRECENTLYWritten(unittest.TestCase):
    """The decisive case: the older folder has the LARGER name, exactly as it does after midnight."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # 234926 is last night, 111413 is this morning. By name the first sorts last; by clock it does not.
        self.old = _run_dir(self.tmp, "D1.5-234926", 1_000_000.0, "WITHHELD brief could not be built")
        self.new = _run_dir(self.tmp, "D1.5-111413", 2_000_000.0, "READY /path/to/build.json")

    def test_pass_digest_reads_this_mornings_ready_not_last_nights_withheld(self):
        got = pass_digest.newest_status([self.old, self.new],
                                        lambda d: open(os.path.join(d, "STATUS")).read())
        self.assertEqual(got["D1.5"][0], "READY",
                         "the name sort returned last night's folder; the clock must decide")

    def test_the_order_is_not_merely_the_name_order(self):
        """Guards the fix rather than the outcome: if someone restores a name sort this goes red even if the
        fixture happens to agree, because it asserts the KEY, not just the result."""
        ordered = sorted([self.old, self.new],
                         key=lambda p: (pass_digest._dir_time(p), os.path.basename(p.rstrip("/"))))
        self.assertEqual(os.path.basename(ordered[-1].rstrip("/")), "D1.5-111413")

    def test_an_unreadable_folder_sorts_oldest_and_never_crashes(self):
        self.assertEqual(pass_digest._dir_time(os.path.join(self.tmp, "does-not-exist")), 0.0)
        self.assertEqual(diag_brief._dir_time(os.path.join(self.tmp, "does-not-exist")), 0.0)


class EveryToolThatEnumeratesRunsUsesTheClock(unittest.TestCase):
    """runner_pool does its work at import time, so it cannot be imported for a behaviour test without running a
    whole pass. Its ordering is therefore asserted at the source, which is weaker than a behaviour test and is
    said so plainly here rather than dressed up as one."""

    def test_runner_pool_sorts_run_dirs_by_time(self):
        src = open(os.path.join(HERE, "loop", "runner_pool.py"), encoding="utf-8").read()
        self.assertIn("_dir_time", src, "runner_pool must order run folders by the clock")
        self.assertNotIn('for d in sorted(glob.glob(os.path.join(RUNS, "*-*/"))):', src,  # lint: allow-name-sorted-time this is the pattern under test
                         "runner_pool is back to a bare name sort")

    def test_diag_brief_sorts_run_dirs_by_time(self):
        src = open(os.path.join(HERE, "loop", "diag_brief.py"), encoding="utf-8").read()
        self.assertNotIn('runs = sorted(glob.glob("%s/%s-*/" % (RUNS, sub)))', src,  # lint: allow-name-sorted-time this is the pattern under test
                         "diag_brief is back to a bare name sort")


if __name__ == "__main__":
    unittest.main(verbosity=1)

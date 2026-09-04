#!/usr/bin/env python3
"""temp_residue.py driven BOTH ways, because a pruner nobody drove backwards
is a claim: it must remove the stale Brother tree, keep the fresh one, keep
the read-only one it had to chmod first, and never touch a stranger's file.
"""
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import temp_residue as TR  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

import tempfile  # noqa: E402


class Pruner(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="temp-residue-case-")

    def make(self, name, age_seconds, mode=None):
        path = os.path.join(self.root, name)
        os.mkdir(path)
        with open(os.path.join(path, "payload"), "w") as handle:
            handle.write("x")
        if mode is not None:
            os.chmod(path, mode)
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_matches_only_brother_prefixes(self):
        mine = self.make("brother-run-abc", 0)
        stranger = self.make("someone-elses-cache", 0)
        found = [p for _n, p, _m in TR.brother_entries(self.root)]
        self.assertIn(mine, found)
        self.assertNotIn(stranger, found)

    def test_prunes_stale_keeps_fresh_and_stranger(self):
        stale = self.make("brother-lane-old", 7200)
        fresh = self.make("brother-lane-live", 60)
        stranger = self.make("someone-elses-cache", 7200)
        removed = TR.prune(TR.brother_entries(self.root), 3600.0)
        self.assertEqual(removed, [stale])
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.isdir(fresh))
        self.assertTrue(os.path.isdir(stranger))

    def test_prunes_a_read_only_tree(self):
        locked = self.make("brother-test-locked", 7200, mode=0o500)
        removed = TR.prune(TR.brother_entries(self.root), 3600.0)
        self.assertEqual(removed, [locked])
        self.assertFalse(os.path.exists(locked))

    def test_unreadable_root_is_no_data_never_a_pass(self):
        missing = os.path.join(self.root, "gone")
        os.environ["BROTHER_TEMP_ROOT"] = missing
        self.addCleanup(os.environ.pop, "BROTHER_TEMP_ROOT", None)
        self.assertIsNone(TR.brother_entries(missing))
        self.assertEqual(TR.main(["--label", "self"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
